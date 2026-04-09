"""
Deep dive into Seeking Alpha ANALYSIS extra fields.

Explores: summary, structuredInsights, quickInsights, themes,
          likesCount, commentCount, isPaywalled, disclosure

Usage:
  python3 tests/test_seeking_alpha_extra_fields.py
"""

import os
import sys
import time
import json

sys.path.append(os.getcwd())

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.seeking_alpha_service import SeekingAlphaService

TEST_TICKERS = ["AAPL", "TSLA", "NVDA", "PLTR", "SOFI"]
FETCH_SIZE = 5

EXTRA_FIELDS = [
    "summary",
    "structuredInsights",
    "quickInsights",
    "themes",
    "likesCount",
    "commentCount",
    "isPaywalled",
    "isLockedPro",
    "disclosure",
    "audioDuration",
    "isTranscript",
    "isEarningsSlides",
]


def truncate(val, max_len=200):
    s = str(val)
    return s[:max_len] + "..." if len(s) > max_len else s


def main():
    api_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
    if not api_key:
        print("[ABORT] RAPIDAPI_KEY_SEEKING_ALPHA not set.")
        sys.exit(1)

    svc = SeekingAlphaService()

    print("=" * 70)
    print("SEEKING ALPHA — EXTRA FIELDS DEEP DIVE")
    print("=" * 70)

    # Track field availability across all articles
    field_stats = {f: {"present": 0, "non_empty": 0, "samples": []} for f in EXTRA_FIELDS}
    total_articles = 0

    for ticker in TEST_TICKERS:
        print(f"\n{'─' * 70}")
        print(f"  {ticker}")
        print(f"{'─' * 70}")

        list_resp = svc._call_endpoint("analysis/v2/list", {"id": ticker, "size": FETCH_SIZE})
        if not list_resp or "data" not in list_resp:
            print("  [EMPTY] No list response")
            time.sleep(0.5)
            continue

        items = list_resp["data"][:FETCH_SIZE]

        for i, item in enumerate(items):
            item_id = item.get("id")
            if not item_id:
                continue

            time.sleep(0.3)
            detail = svc._call_endpoint("analysis/v2/get-details", {"id": item_id})
            if not detail or "data" not in detail:
                print(f"  [{i}] id={item_id} -> no detail")
                continue

            attrs = detail["data"].get("attributes", {})
            title = attrs.get("title", "???")[:60]
            total_articles += 1

            print(f"\n  [{i}] {title}")

            for field in EXTRA_FIELDS:
                val = attrs.get(field)

                # Track stats
                if val is not None:
                    field_stats[field]["present"] += 1

                is_non_empty = (
                    val is not None
                    and val != ""
                    and val != []
                    and val != {}
                    and val is not False
                )

                if is_non_empty:
                    field_stats[field]["non_empty"] += 1
                    # Keep first 3 samples per field
                    if len(field_stats[field]["samples"]) < 3:
                        field_stats[field]["samples"].append({
                            "ticker": ticker,
                            "title": title,
                            "value": val,
                        })

                # Print value
                if val is None:
                    print(f"      {field}: null")
                elif isinstance(val, (dict, list)):
                    if val:
                        print(f"      {field}: ({type(val).__name__}, len={len(val)}) {truncate(json.dumps(val, indent=None), 150)}")
                    else:
                        print(f"      {field}: (empty {type(val).__name__})")
                elif isinstance(val, bool):
                    print(f"      {field}: {val}")
                elif isinstance(val, (int, float)):
                    print(f"      {field}: {val}")
                elif isinstance(val, str):
                    if len(val) > 0:
                        print(f"      {field}: ({len(val)} chars) {truncate(val, 150)}")
                    else:
                        print(f"      {field}: (empty string)")
                else:
                    print(f"      {field}: {truncate(val)}")

        time.sleep(1)

    # ===================================================================
    # FIELD AVAILABILITY SUMMARY
    # ===================================================================
    print("\n" + "=" * 70)
    print("FIELD AVAILABILITY SUMMARY")
    print(f"Total articles inspected: {total_articles}")
    print("=" * 70)

    print(f"\n  {'Field':<22} {'Present':>8} {'Non-Empty':>10} {'Rate':>6}  Notes")
    print(f"  {'─'*22} {'─'*8} {'─'*10} {'─'*6}  {'─'*30}")

    for field in EXTRA_FIELDS:
        stats = field_stats[field]
        present = stats["present"]
        non_empty = stats["non_empty"]
        rate = f"{non_empty/total_articles*100:.0f}%" if total_articles > 0 else "—"

        # Determine usefulness note
        if non_empty == total_articles:
            note = "ALWAYS available"
        elif non_empty >= total_articles * 0.8:
            note = "Usually available"
        elif non_empty >= total_articles * 0.3:
            note = "Sometimes available"
        elif non_empty > 0:
            note = "Rarely available"
        else:
            note = "Never populated"

        print(f"  {field:<22} {present:>5}/{total_articles:<2} {non_empty:>7}/{total_articles:<2} {rate:>5}  {note}")

    # ===================================================================
    # SAMPLE VALUES FOR INTERESTING FIELDS
    # ===================================================================
    interesting_fields = ["summary", "structuredInsights", "quickInsights", "themes", "disclosure"]

    for field in interesting_fields:
        samples = field_stats[field]["samples"]
        if not samples:
            continue

        print(f"\n{'─' * 70}")
        print(f"  SAMPLE VALUES: {field}")
        print(f"{'─' * 70}")

        for j, sample in enumerate(samples):
            print(f"\n  Sample {j+1} ({sample['ticker']} — {sample['title']})")
            val = sample["value"]
            if isinstance(val, (dict, list)):
                print(f"  {json.dumps(val, indent=4)[:2000]}")
            else:
                print(f"  {str(val)[:2000]}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
