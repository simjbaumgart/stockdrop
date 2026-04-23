"""
StockDrop: Deep Research Backfill Script
=========================================
Standalone script that processes BUY-rated stocks through Deep Research
when the main tool has stopped before completing them.

Looks at today's (or yesterday's) recommendations, sorted by conviction
and risk/reward ratio, and runs deep research one-by-one using the
council data already gathered.

Usage:
    python scripts/run_deep_research_backfill.py [OPTIONS]

Options:
    --date YYYY-MM-DD   Override target date (default: today, fallback yesterday)
    --dry-run           Show candidates without executing deep research
    --limit N           Process only the first N candidates
"""

import sys
import os
import json
import sqlite3
import time
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from dotenv import load_dotenv

# Load environment variables before any app imports
load_dotenv()

# Ensure app imports work from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.deep_research_service import deep_research_service
from app.database import update_deep_research_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "subscribers.db")

CONVICTION_ORDER = {"HIGH": 0, "MODERATE": 1, "LOW": 2}

MISSING_VERDICT_FILTER = """
    (deep_research_verdict IS NULL 
     OR deep_research_verdict = '' 
     OR deep_research_verdict = '-' 
     OR deep_research_verdict LIKE 'UNKNOWN%%' 
     OR deep_research_verdict = 'ERROR_PARSING')
"""


# ---------------------------------------------------------------------------
# Database Queries
# ---------------------------------------------------------------------------

def fetch_candidates(date_str: str) -> List[Dict]:
    """
    Fetch BUY recommendations for a given date that are missing a deep research verdict.
    Returns a list of candidate dicts sorted by conviction (HIGH first), then R/R descending.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = f"""
            SELECT * FROM decision_points
            WHERE date(timestamp) = ?
            AND recommendation = 'BUY'
            AND {MISSING_VERDICT_FILTER}
            ORDER BY
                CASE conviction
                    WHEN 'HIGH' THEN 0
                    WHEN 'MODERATE' THEN 1
                    WHEN 'LOW' THEN 2
                    ELSE 3
                END ASC,
                COALESCE(risk_reward_ratio, 0) DESC
        """
        cursor.execute(query, (date_str,))
        rows = cursor.fetchall()
        conn.close()

        candidates = [dict(row) for row in rows]

        # Deduplicate: keep the latest entry per symbol (highest id)
        unique: Dict[str, Dict] = {}
        for c in candidates:
            sym = c["symbol"]
            if sym not in unique or c["id"] > unique[sym]["id"]:
                unique[sym] = c

        # Re-sort after dedup (dict order may differ)
        deduped = sorted(
            unique.values(),
            key=lambda x: (
                CONVICTION_ORDER.get(x.get("conviction", "LOW"), 3),
                -(x.get("risk_reward_ratio") or 0),
            ),
        )
        return deduped

    except Exception as e:
        print(f"[ERROR] Database query failed: {e}")
        return []


def resolve_date(explicit_date: Optional[str]) -> str:
    """
    Determine which date to process.
    If explicit_date is provided, use it directly.
    Otherwise: try today first; if no candidates, fall back to yesterday.
    """
    if explicit_date:
        return explicit_date

    today_str = datetime.now().strftime("%Y-%m-%d")
    candidates = fetch_candidates(today_str)
    if candidates:
        return today_str

    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"[Info] No candidates found for today ({today_str}). Falling back to yesterday ({yesterday_str}).")
    return yesterday_str


# ---------------------------------------------------------------------------
# Context Building
# ---------------------------------------------------------------------------

def load_council_reports(symbol: str, date_str: str) -> Dict:
    """
    Load saved council reports from disk.
    Tries council2.json first (Phase 1+2: includes bull/bear/risk).
    Falls back to council1.json (Phase 1 only: technical, news, sentiment, etc.).
    Returns a merged dict with all available agent reports.
    """
    from app.utils.ticker_paths import safe_ticker_path
    council_dir = "data/council_reports"
    council2_path = f"{council_dir}/{safe_ticker_path(symbol)}_{date_str}_council2.json"
    council1_path = f"{council_dir}/{safe_ticker_path(symbol)}_{date_str}_council1.json"

    merged = {}

    # Load council1 (Phase 1 agents)
    if os.path.exists(council1_path):
        try:
            with open(council1_path, "r") as f:
                merged = json.load(f)
            print(f"  > Loaded council1 (Phase 1): {council1_path} ({len(merged)} agents)")
        except Exception as e:
            print(f"  > Warning: Could not read council1: {e}")
    else:
        print(f"  > Warning: No council1 report found at {council1_path}")

    # Load council2 (Phase 1+2 — adds bull/bear/risk on top)
    if os.path.exists(council2_path):
        try:
            with open(council2_path, "r") as f:
                council2_data = json.load(f)
            # Merge: council2 keys override council1 (council2 is a superset)
            merged.update(council2_data)
            phase2_keys = [k for k in council2_data if k not in ("technical", "news", "market_sentiment", "economics", "competitive", "seeking_alpha")]
            print(f"  > Loaded council2 (Phase 1+2): {council2_path} (Phase 2 agents: {phase2_keys})")
        except Exception as e:
            print(f"  > Warning: Could not read council2: {e}")
    else:
        print(f"  > Info: No council2 report found (bull/bear/risk unavailable from file)")

    return merged


def load_news_context(symbol: str, date_str: str) -> List[Dict]:
    """
    Load the full cached news context from data/news/.
    Returns news as a list of article dicts for the deep research prompt.
    The file contains pre-formatted headlines + summaries from all sources
    (Benzinga, Alpha Vantage, Finnhub, yfinance, TradingView).
    """
    from app.utils.ticker_paths import safe_ticker_path
    news_path = f"data/news/{safe_ticker_path(symbol)}_{date_str}_news_context.txt"
    if os.path.exists(news_path):
        try:
            with open(news_path, "r") as f:
                content = f.read()
            if content.strip():
                chars = len(content)
                tokens_est = chars // 4
                print(f"  > Loaded news context: {news_path} ({chars:,} chars, ~{tokens_est:,} tokens)")
                # Pass the full text as a single article block.
                # The deep research prompt formats raw_news items by headline + content.
                return [{
                    "headline": f"News Context for {symbol} ({date_str})",
                    "source": "Aggregated (Benzinga, Alpha Vantage, Finnhub, yfinance, TradingView)",
                    "source_type": "WIRE",
                    "summary": "",
                    "content": content,
                    "datetime_str": date_str,
                }]
        except Exception as e:
            print(f"  > Warning: Could not read news context: {e}")
    else:
        print(f"  > Info: No news context file found at {news_path}")
    return []


def build_deep_research_context(candidate: Dict, council_reports: Dict, news_items: List[Dict]) -> Dict:
    """
    Build the context dict expected by deep_research_service.execute_deep_research().
    Loads FULL untruncated council data (Phase 1+2) and news context.

    Data sources:
      - PM Decision: from DB (decision_points row)
      - Bull/Bear/Risk: from council2.json (Phase 2) — full text, no truncation
      - Technical/News/Sentiment/Competitive/SeekingAlpha: from council1.json (Phase 1) — full text
      - News articles: from data/news/ context file — full text
      - Transcript summary: extracted from the news agent report
    """
    # Extract transcript summary from the news agent report if available
    transcript_summary = _extract_transcript_summary(council_reports.get("news", ""))

    # Build the full council context string for supplementary data
    # This gives deep research access to ALL agent reports (untruncated)
    supplementary_agents = {}
    for key in ("market_sentiment", "economics", "competitive", "seeking_alpha", "risk"):
        if key in council_reports and council_reports[key]:
            supplementary_agents[key] = council_reports[key]

    return {
        "pm_decision": {
            "action": candidate.get("recommendation"),
            "conviction": candidate.get("conviction"),
            "drop_type": candidate.get("drop_type"),
            "entry_price_low": candidate.get("entry_price_low"),
            "entry_price_high": candidate.get("entry_price_high"),
            "stop_loss": candidate.get("stop_loss"),
            "take_profit_1": candidate.get("take_profit_1"),
            "take_profit_2": candidate.get("take_profit_2"),
            "upside_percent": candidate.get("upside_percent"),
            "downside_risk_percent": candidate.get("downside_risk_percent"),
            "risk_reward_ratio": candidate.get("risk_reward_ratio"),
            "pre_drop_price": candidate.get("pre_drop_price"),
            "entry_trigger": candidate.get("entry_trigger"),
            "reason": (candidate.get("reasoning") or "")[:500],
            "key_factors": [],
        },
        # Bull/Bear: full untruncated text from council2.json (or fallback)
        "bull_case": council_reports.get("bull", "Not available — council Phase 2 data not saved to file."),
        "bear_case": council_reports.get("bear", "Not available — council Phase 2 data not saved to file."),
        # Technical data: full agent report from council1.json
        "technical_data": council_reports.get("technical", {}),
        "drop_percent": candidate.get("drop_percent", 0.0),
        # Full news context from data/news/
        "raw_news": news_items,
        "transcript_summary": transcript_summary,
        "transcript_date": None,
        "data_depth": {},
        # Supplementary agent reports (full, untruncated)
        # These are extra council data not in the standard live flow,
        # giving deep research more context for the backfill scenario.
        "supplementary_council_reports": supplementary_agents,
    }


def _extract_transcript_summary(news_report: str) -> str:
    """
    Extracts the 'Extended Transcript Summary' section from the News Agent output.
    Mirrors StockService._extract_transcript_summary().
    """
    if not news_report or not isinstance(news_report, str):
        return "No transcript summary available from backfill."

    marker = "Extended Transcript Summary"
    if marker in news_report:
        start = news_report.index(marker)
        rest = news_report[start + len(marker):]
        end_markers = [
            "## Key Drivers", "### Key Drivers", "## Narrative Check",
            "### Narrative Check", "## Top 5 Sources", "### Top 5 Sources",
            "## MACRO CHECK", "NEEDS_ECONOMICS",
        ]
        end_pos = len(rest)
        for em in end_markers:
            if em in rest:
                pos = rest.index(em)
                end_pos = min(end_pos, pos)

        summary = rest[:end_pos].strip()
        if len(summary) > 100:
            return summary

    return "No transcript summary available from backfill."


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def process_candidate(candidate: Dict, date_str: str, index: int, total: int) -> Dict:
    """
    Run deep research for a single candidate.
    Returns a result summary dict.
    """
    symbol = candidate["symbol"]
    decision_id = candidate["id"]
    conviction = candidate.get("conviction", "N/A")
    rr = candidate.get("risk_reward_ratio", 0)

    print(f"\n{'='*60}")
    print(f"  [{index}/{total}] {symbol}")
    print(f"  Conviction: {conviction} | R/R: {rr} | Decision ID: {decision_id}")
    print(f"{'='*60}")

    # 1. Load council data (council1 + council2) and news
    council_reports = load_council_reports(symbol, date_str)
    news_items = load_news_context(symbol, date_str)

    # 2. Build context
    context = build_deep_research_context(candidate, council_reports, news_items)

    # 3. Execute deep research (synchronous)
    print(f"  > Starting Deep Research for {symbol}...")
    start_time = time.time()

    result = deep_research_service.execute_deep_research(
        symbol=symbol,
        context=context,
        decision_id=decision_id,
    )

    elapsed = time.time() - start_time
    elapsed_min = int(elapsed // 60)
    elapsed_sec = int(elapsed % 60)

    if result:
        # 4. Handle completion (DB update + file save) — reuse existing logic
        task_payload = {"symbol": symbol, "decision_id": decision_id}
        deep_research_service._handle_completion(task_payload, result)

        review_verdict = result.get("review_verdict", "UNKNOWN")
        action = result.get("action", "N/A")
        dr_conviction = result.get("conviction", "N/A")

        print(f"  > DONE ({elapsed_min}m {elapsed_sec}s) — Verdict: {review_verdict}, Action: {action}, Conviction: {dr_conviction}")

        return {
            "symbol": symbol,
            "status": "SUCCESS",
            "review_verdict": review_verdict,
            "action": action,
            "conviction": dr_conviction,
            "elapsed": f"{elapsed_min}m {elapsed_sec}s",
        }
    else:
        print(f"  > FAILED ({elapsed_min}m {elapsed_sec}s) — Deep Research returned no result.")
        return {
            "symbol": symbol,
            "status": "FAILED",
            "review_verdict": "-",
            "action": "-",
            "conviction": "-",
            "elapsed": f"{elapsed_min}m {elapsed_sec}s",
        }


def print_summary(results: List[Dict], date_str: str):
    """Print a final summary table of all processed candidates."""
    print(f"\n{'='*70}")
    print(f"  DEEP RESEARCH BACKFILL — SUMMARY ({date_str})")
    print(f"{'='*70}")
    print(f"  {'Symbol':<10} {'Status':<10} {'Verdict':<12} {'Action':<12} {'Conviction':<12} {'Time'}")
    print(f"  {'-'*10} {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*8}")

    for r in results:
        print(
            f"  {r['symbol']:<10} {r['status']:<10} {r['review_verdict']:<12} "
            f"{r['action']:<12} {r['conviction']:<12} {r['elapsed']}"
        )

    successes = sum(1 for r in results if r["status"] == "SUCCESS")
    failures = sum(1 for r in results if r["status"] == "FAILED")
    print(f"\n  Total: {len(results)} | Success: {successes} | Failed: {failures}")
    print(f"{'='*70}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="StockDrop: Run Deep Research on BUY-rated stocks that are missing a verdict."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date in YYYY-MM-DD format (default: today, fallback yesterday)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show candidates without executing deep research",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of candidates to process",
    )
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  StockDrop — Deep Research Backfill")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Check that the deep research service is enabled
    if not deep_research_service.is_running:
        print("\n[ERROR] Deep Research Service is disabled (GEMINI_API_KEY not set).")
        print("Set GEMINI_API_KEY in your .env file and try again.")
        sys.exit(1)

    # Resolve date
    date_str = resolve_date(args.date)
    print(f"\n[Info] Target date: {date_str}")

    # Fetch candidates
    candidates = fetch_candidates(date_str)

    if not candidates:
        print(f"\n[Info] No BUY candidates found for {date_str} that need Deep Research.")
        print("All stocks either already have a verdict or none were rated BUY.")
        sys.exit(0)

    # Apply limit
    if args.limit and args.limit < len(candidates):
        candidates = candidates[: args.limit]
        print(f"[Info] Limited to first {args.limit} candidate(s).")

    # Display candidates
    print(f"\n[Info] Found {len(candidates)} candidate(s) for Deep Research:\n")
    print(f"  {'#':<4} {'Symbol':<10} {'Conviction':<12} {'R/R':<8} {'Drop%':<8} {'Decision ID'}")
    print(f"  {'-'*4} {'-'*10} {'-'*12} {'-'*8} {'-'*8} {'-'*11}")

    for i, c in enumerate(candidates, 1):
        rr = c.get("risk_reward_ratio") or 0
        rr_str = f"{rr:.1f}" if rr else "N/A"
        drop = c.get("drop_percent") or 0
        drop_str = f"{drop:.1f}%" if drop else "N/A"
        conviction_str = c.get("conviction") or "N/A"
        print(f"  {i:<4} {c['symbol']:<10} {conviction_str:<12} {rr_str:<8} {drop_str:<8} {c['id']}")

    if args.dry_run:
        print("\n[Dry Run] No deep research executed. Remove --dry-run to process.")
        sys.exit(0)

    # Process each candidate sequentially
    print(f"\nStarting Deep Research processing ({len(candidates)} stocks)...")
    print("Each stock takes 2-5 minutes. 60s cooldown between API calls.\n")

    results = []
    for i, candidate in enumerate(candidates, 1):
        result = process_candidate(candidate, date_str, i, len(candidates))
        results.append(result)

        # Rate limit: 60s cooldown between API calls (skip after last)
        if i < len(candidates):
            print(f"\n  [Cooldown] Waiting 60s before next candidate...")
            time.sleep(60)

    # Need to wait for the research to actually finish so background threads dont get killed
    deep_research_service.wait_for_completion()

    # Final summary
    print_summary(results, date_str)


if __name__ == "__main__":
    main()
