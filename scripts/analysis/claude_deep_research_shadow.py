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
from app.services.analytics.dr_compare_metrics import (
    verdict_agreement,
    action_agreement,
)
from app.services.analytics.dr_level_compare import compare_levels
from app.services.analytics.dr_compare_report import write_shadow_report

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


def _build_council_news(council: dict, date_str: str) -> list:
    """Reconstruct raw_news from the council2 news-agent summary string.

    The original article list is paywalled and not persisted, so we synthesise a
    single pseudo-article whose ``summary`` field contains the news agent's full
    report text. This narrows (but does not close) the context gap vs. the live
    pipeline — Claude still performs its own web search for current news.

    Keys used match what build_individual_prompt() reads:
        headline, summary, source, source_type, datetime_str
    (content is left absent; the prompt falls back to summary when content is empty.)
    """
    news_text = council.get("news", "") or ""
    if not isinstance(news_text, str):
        news_text = str(news_text)
    news_text = news_text.strip()
    if not news_text:
        return []
    return [
        {
            "headline": "Council news summary (reconstructed from shadow)",
            "summary": news_text,
            "source": "council",
            "source_type": "WIRE",
            "datetime_str": date_str or "Unknown",
        }
    ]


def _rebuild_context(row: dict) -> dict:
    """Reconstruct the deep-research context for a stored decision.

    VERIFIED: there are no report_data_json / raw_data_json columns. Context is
    rebuilt from the DB row (pm_decision) + the council2 file (bull/bear/technical),
    mirroring the live backfill at app/services/stock_service.py:776-803.
    raw_news cannot be reconstructed (paywalled list not persisted) -> []. Claude
    web-searches for news itself, which partly compensates; recorded as a caveat.

    Step 2b additions:
    - sensor_summaries: built from council1/council2 sensor keys (technical, news,
      market_sentiment, competitive, seeking_alpha) using condense_sensor_report().
      All five keys are available in council files, unlike the live pipeline where
      seeking_alpha is absent from report_data.
    - disagreement_points: best-effort from bull/bear first ~200 chars each.
      Included only when both are available; the Claude prompt falls back to
      full-text bull/bear analysis when this key is absent.
    """
    from app.utils.ticker_paths import safe_ticker_path
    from app.services.claude_dr_prompts import condense_sensor_report
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

    # ── sensor_summaries (Step 2b) ──────────────────────────────────────────
    _sensor_map = {
        "Technical Analysis": council.get("technical", ""),
        "News Analysis": council.get("news", ""),
        "Market Sentiment": council.get("market_sentiment", ""),
        "Competitive Landscape": council.get("competitive", ""),
        "Seeking Alpha": council.get("seeking_alpha", ""),
    }
    sensor_summaries = {
        name: condense_sensor_report(text)
        for name, text in _sensor_map.items()
        if text and (text.strip() if isinstance(text, str) else False)
    }

    # ── disagreement_points (Step 2b) — best-effort, no LLM ───────────────
    bull = council.get("bull", "") or ""
    bear = council.get("bear", "") or ""
    disagreement_points: list = []
    if bull and bear and bull != "Not available from shadow." and bear != "Not available from shadow.":
        # Cheap heuristic: opening of each case often states the core thesis.
        # Provide as a short prompt hint so Claude targets the gap, not as a
        # structured list (we can't reliably extract structured disagreements
        # without an LLM call).
        bull_lead = bull.strip()[:200].replace("\n", " ")
        bear_lead = bear.strip()[:200].replace("\n", " ")
        disagreement_points = [
            f"Bull opening thesis: {bull_lead}",
            f"Bear opening thesis: {bear_lead}",
        ]

    context: dict = {
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
        "bull_case": bull or "Not available from shadow.",
        "bear_case": bear or "Not available from shadow.",
        "technical_data": council.get("technical", {}),
        "drop_percent": row.get("drop_percent", 0) or 0,
        "raw_news": _build_council_news(council, date_str),
        "transcript_summary": "No transcript summary available from shadow.",
        "transcript_date": None,
        "data_depth": {},
        "_council_files_found": bool(council),  # surfaced in the shadow record
        "_news_caveat": (
            "raw_news reconstructed from council2 news-agent summary string — "
            "not the original article list (paywalled, not persisted). "
            "Claude web-searches for current news itself."
        ),
    }
    if sensor_summaries:
        context["sensor_summaries"] = sensor_summaries
    if disagreement_points:
        context["disagreement_points"] = disagreement_points
    return context


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
    level_comparisons: list = []  # parallel to summary; one entry per decision
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

        gem_verdict = row.get("deep_research_review_verdict") or ""
        gem_action = row.get("deep_research_action") or ""
        cl_verdict = (claude or {}).get("review_verdict") or ""
        cl_action = (claude or {}).get("action") or ""

        # ── Gemini DR levels (expanded: all deep_research_* level columns) ─────
        # Note: entry_price_low/high and stop_loss come from the PM-written
        # columns (updated by DR when it fires), not from deep_research_entry_*.
        # We read both sets and prefer the deep_research_* columns when present,
        # which is what the live pipeline writes. If deep_research_* are absent
        # (older rows) the PM columns serve as fallback.
        gem_entry_low = row.get("deep_research_entry_low") or row.get("entry_price_low")
        gem_entry_high = row.get("deep_research_entry_high") or row.get("entry_price_high")
        gem_stop = row.get("deep_research_stop_loss") or row.get("stop_loss")

        record = {
            "decision_id": row["id"],
            "symbol": symbol,
            "gemini": {
                "review_verdict": gem_verdict,
                "action": gem_action,
                "score": row.get("deep_research_score"),
                "reason": row.get("deep_research_reason"),
                # ── entry / stop (original fields kept for backward compat) ──
                "entry_low": gem_entry_low,
                "entry_high": gem_entry_high,
                "stop_loss": gem_stop,
                # ── expanded level fields (Step 3b) ───────────────────────────
                "take_profit_1": row.get("deep_research_tp1"),
                "take_profit_2": row.get("deep_research_tp2"),
                "sell_price_low": row.get("deep_research_sell_price_low"),
                "sell_price_high": row.get("deep_research_sell_price_high"),
                "ceiling_exit": row.get("deep_research_ceiling_exit"),
                "risk_reward_ratio": row.get("deep_research_rr_ratio"),
                "entry_trigger": row.get("deep_research_entry_trigger"),
                "exit_trigger": row.get("deep_research_exit_trigger"),
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
            # Raw string-equality flags kept for per-decision readability
            "agree_verdict": (gem_verdict == cl_verdict),
            "agree_action": (gem_action == cl_action),
            # Flat keys used by metrics aggregation across the summary list
            "gemini_verdict": gem_verdict,
            "claude_verdict": cl_verdict,
            "gemini_action": gem_action,
            "claude_action": cl_action,
        }
        # ── Step 3b: buy/sell level comparison (always anchored on shadow) ────
        gem_levels = {
            "entry_price_low": record["gemini"]["entry_low"],
            "entry_price_high": record["gemini"]["entry_high"],
            "stop_loss": record["gemini"]["stop_loss"],
            "take_profit_1": record["gemini"].get("take_profit_1"),
            "take_profit_2": record["gemini"].get("take_profit_2"),
            "sell_price_low": record["gemini"].get("sell_price_low"),
            "sell_price_high": record["gemini"].get("sell_price_high"),
            "ceiling_exit": record["gemini"].get("ceiling_exit"),
            "risk_reward_ratio": record["gemini"].get("risk_reward_ratio"),
            "entry_trigger": record["gemini"].get("entry_trigger"),
            "exit_trigger": record["gemini"].get("exit_trigger"),
        }
        cl_data = record["claude"]
        claude_levels = {
            "entry_price_low": cl_data.get("entry_price_low"),
            "entry_price_high": cl_data.get("entry_price_high"),
            "stop_loss": cl_data.get("stop_loss"),
            "take_profit_1": cl_data.get("take_profit_1"),
            "take_profit_2": cl_data.get("take_profit_2"),
            "sell_price_low": cl_data.get("sell_price_low"),
            "sell_price_high": cl_data.get("sell_price_high"),
            "ceiling_exit": cl_data.get("ceiling_exit"),
            "risk_reward_ratio": cl_data.get("risk_reward_ratio"),
            "entry_trigger": cl_data.get("entry_trigger"),
            "exit_trigger": cl_data.get("exit_trigger"),
        }
        record["level_comparison"] = compare_levels(
            gem_levels, claude_levels, anchored=True
        )

        fname = os.path.join(OUT_DIR, f"shadow_{symbol}_{row['id']}.json")
        with open(fname, "w") as f:
            json.dump(record, f, indent=2)
        summary.append({k: record[k] for k in
                        ("decision_id", "symbol",
                         "agree_verdict", "agree_action",
                         "gemini_verdict", "claude_verdict",
                         "gemini_action", "claude_action",
                         "cost_usd_est", "latency_s")})
        level_comparisons.append(record.get("level_comparison"))
        print(f"  verdict gemini={record['gemini']['review_verdict']} "
              f"claude={record['claude'].get('review_verdict')} "
              f"agree={record['agree_verdict']} "
              f"cost=${est_cost} {latency}s sources={len(meta.get('source_urls', []))}")

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    v_metrics = verdict_agreement(summary)
    a_metrics = action_agreement(summary)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_payload = {
        "decisions": summary,
        "verdict_metrics": v_metrics,
        "action_metrics": a_metrics,
    }
    with open(os.path.join(OUT_DIR, f"_summary_{stamp}.json"), "w") as f:
        json.dump(summary_payload, f, indent=2)

    # ── Step 3b: write markdown report ───────────────────────────────────────
    report_path = write_shadow_report(
        out_dir=OUT_DIR,
        stamp=stamp,
        summary_rows=summary,
        level_comparisons=level_comparisons,
        verdict_metrics=v_metrics,
        action_metrics=a_metrics,
    )
    print(f"\nMarkdown report: {report_path}")

    n = len(summary)
    total_cost = round(sum(s["cost_usd_est"] for s in summary), 2)
    avg_latency = round(sum(s["latency_s"] for s in summary) / max(n, 1), 1)

    def _fmt_matrix(cm: dict) -> str:
        labels = cm.get("labels", [])
        matrix = cm.get("matrix", {})
        if not labels:
            return "  (no data)"
        col_w = max(len(lbl) for lbl in labels) + 2
        header = " " * (col_w + 2) + "  ".join(f"{lbl:>{col_w}}" for lbl in labels)
        lines = [header]
        for g in labels:
            row_cells = "  ".join(f"{matrix[g].get(c, 0):>{col_w}}" for c in labels)
            lines.append(f"  gem={g:<{col_w}} {row_cells}")
        return "\n".join(lines)

    print(f"\n=== SHADOW SUMMARY ({n} decisions) ===")
    print(f"\n-- VERDICT AGREEMENT (sentinel-excluded) --")
    print(f"  n={v_metrics['n']}  n_excluded(sentinels)={v_metrics['n_excluded']}")
    print(f"  raw_agreement={v_metrics['raw_agreement']:.3f}  "
          f"Cohen's κ={v_metrics['kappa']:.3f}")
    print("  Confusion matrix (rows=gemini, cols=claude):")
    print(_fmt_matrix(v_metrics["confusion"]))

    print(f"\n-- ACTION AGREEMENT --")
    print(f"  n={a_metrics['n']}  n_excluded={a_metrics['n_excluded']}")
    print(f"  raw_agreement={a_metrics['raw_agreement']:.3f}  "
          f"Cohen's κ={a_metrics['kappa']:.3f}")
    print("  Confusion matrix (rows=gemini, cols=claude):")
    print(_fmt_matrix(a_metrics["confusion"]))

    print(f"\nTotal est cost: ${total_cost}   Avg latency: {avg_latency}s")
    print(f"Per-decision JSON + summary in {OUT_DIR}/")


if __name__ == "__main__":
    main()
