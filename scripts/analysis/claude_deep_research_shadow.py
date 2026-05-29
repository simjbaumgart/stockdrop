"""Verbose shadow: run the 15 most recent Gemini-DR'd decisions through the
Claude provider and dump a full side-by-side (reasoning, sources, cost, latency).

One-off eval — NOT operational backfill. Writes to data/claude_shadow/.

Usage:
    CLAUDE_API_KEY=... python scripts/analysis/claude_deep_research_shadow.py [--limit 15]
"""
import os
import sys
import json
import time
import argparse
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.services.claude_deep_research_service import claude_deep_research_service
from app.services.token_pricing import (
    compute_cost, CLAUDE_WEB_SEARCH_USD_PER_1K,
)

OUT_DIR = "data/claude_shadow"


def _recent_decisions(limit: int):
    """15 most recent decision_points carrying a Gemini DR verdict."""
    conn = sqlite3.connect(os.getenv("DB_PATH", "subscribers.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM decision_points
        WHERE deep_research_review_verdict IS NOT NULL
          AND deep_research_review_verdict != ''
        ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _rebuild_context(row: dict) -> dict:
    """Reconstruct the deep-research context for a stored decision.

    VERIFIED: there are no report_data_json / raw_data_json columns. Context is
    rebuilt from the DB row (pm_decision) + the council2 file (bull/bear/technical),
    mirroring the live backfill at app/services/stock_service.py:776-803.
    raw_news cannot be reconstructed (paywalled list not persisted) -> []. Claude
    web-searches for news itself, which partly compensates; recorded as a caveat.
    """
    from app.utils.ticker_paths import safe_ticker_path
    symbol = row["symbol"]
    date_str = (row.get("timestamp") or "").split(" ")[0]  # 'YYYY-MM-DD'
    safe = safe_ticker_path(symbol)
    council_dir = "data/council_reports"

    council = {}
    for suffix in ("council1", "council2"):  # council2 carries bull/bear/risk
        path = os.path.join(council_dir, f"{safe}_{date_str}_{suffix}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    council.update(json.load(f))
            except Exception as e:
                print(f"  WARN: could not read {path}: {e}")

    return {
        "pm_decision": {
            "action": row.get("recommendation"),
            "conviction": row.get("conviction"),
            "drop_type": row.get("drop_type"),
            "entry_price_low": row.get("entry_price_low"),
            "entry_price_high": row.get("entry_price_high"),
            "stop_loss": row.get("stop_loss"),
            "take_profit_1": row.get("take_profit_1"),
            "risk_reward_ratio": row.get("risk_reward_ratio"),
            "pre_drop_price": row.get("pre_drop_price"),
            "entry_trigger": row.get("entry_trigger"),
            "reason": (row.get("reasoning") or "")[:500],
        },
        "bull_case": council.get("bull", "Not available from shadow."),
        "bear_case": council.get("bear", "Not available from shadow."),
        "technical_data": council.get("technical", {}),
        "drop_percent": row.get("drop_percent", 0) or 0,
        "raw_news": [],   # paywalled list not persisted; Claude searches for news itself
        "transcript_summary": "No transcript summary available from shadow.",
        "transcript_date": None,
        "data_depth": {},
        "_council_files_found": bool(council),  # surfaced in the shadow record
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()

    if not os.getenv("CLAUDE_API_KEY"):
        print("CLAUDE_API_KEY not set."); sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    decisions = _recent_decisions(args.limit)
    print(f"Shadowing {len(decisions)} decisions through Claude...\n")

    summary = []
    spent_usd = 0.0
    COST_CEILING_USD = float(os.getenv("SHADOW_COST_CEILING_USD", "20"))
    for i, row in enumerate(decisions, 1):
        if spent_usd >= COST_CEILING_USD:
            print(f"\nSTOP: cumulative est. cost ${spent_usd:.2f} hit ceiling "
                  f"${COST_CEILING_USD:.2f} after {i-1} decisions. Halting shadow.")
            break
        symbol = row["symbol"]
        print(f"[{i}/{len(decisions)}] {symbol} (decision {row['id']})  "
              f"[spent ${spent_usd:.2f}/${COST_CEILING_USD:.0f}]...")
        context = _rebuild_context(row)
        t0 = time.time()
        try:
            # decision_id=None so the shadow never writes cost rows into prod tables
            claude = claude_deep_research_service.execute_deep_research(symbol, context, None)
        except Exception as e:
            print(f"  ERROR: {e}")
            claude = {"_error": str(e)}
        latency = round(time.time() - t0, 1)

        meta = (claude or {}).get("_claude_research_meta", {})
        usage = meta.get("usage", {})
        token_cost = compute_cost(MODEL_FALLBACK := "claude-opus-4-8",
                                  usage.get("in", 0), usage.get("out", 0)) or 0.0
        search_cost = (meta.get("search_count", 0) / 1000.0) * CLAUDE_WEB_SEARCH_USD_PER_1K
        est_cost = round(token_cost + search_cost, 4)
        spent_usd += est_cost  # cumulative; checked against COST_CEILING_USD at loop top

        record = {
            "decision_id": row["id"],
            "symbol": symbol,
            "gemini": {
                "review_verdict": row.get("deep_research_review_verdict"),
                "action": row.get("deep_research_action"),
                "score": row.get("deep_research_score"),
                "reason": row.get("deep_research_reason"),
                "entry_low": row.get("entry_price_low"),
                "entry_high": row.get("entry_price_high"),
                "stop_loss": row.get("stop_loss"),
            },
            "claude": {k: v for k, v in (claude or {}).items() if k != "_claude_research_meta"},
            "claude_research": {
                "source_urls": meta.get("source_urls", []),
                "search_count": meta.get("search_count"),
                "thinking": meta.get("thinking", ""),
                "usage": usage,
            },
            "cost_usd_est": est_cost,
            "latency_s": latency,
            "agree_verdict": (row.get("deep_research_review_verdict")
                              == (claude or {}).get("review_verdict")),
            "agree_action": (row.get("deep_research_action")
                             == (claude or {}).get("action")),
        }
        fname = os.path.join(OUT_DIR, f"shadow_{symbol}_{row['id']}.json")
        with open(fname, "w") as f:
            json.dump(record, f, indent=2)
        summary.append({k: record[k] for k in
                        ("decision_id", "symbol", "agree_verdict", "agree_action",
                         "cost_usd_est", "latency_s")})
        print(f"  verdict gemini={record['gemini']['review_verdict']} "
              f"claude={record['claude'].get('review_verdict')} "
              f"agree={record['agree_verdict']} "
              f"cost=${est_cost} {latency}s sources={len(meta.get('source_urls', []))}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(OUT_DIR, f"_summary_{stamp}.json"), "w") as f:
        json.dump(summary, f, indent=2)

    n = len(summary)
    v_agree = sum(1 for s in summary if s["agree_verdict"])
    a_agree = sum(1 for s in summary if s["agree_action"])
    total_cost = round(sum(s["cost_usd_est"] for s in summary), 2)
    print(f"\n=== SHADOW SUMMARY ({n} decisions) ===")
    print(f"Verdict agreement: {v_agree}/{n}   Action agreement: {a_agree}/{n}")
    print(f"Total est cost: ${total_cost}   Avg latency: "
          f"{round(sum(s['latency_s'] for s in summary)/max(n,1),1)}s")
    print(f"Per-decision JSON + summary in {OUT_DIR}/")


if __name__ == "__main__":
    main()
