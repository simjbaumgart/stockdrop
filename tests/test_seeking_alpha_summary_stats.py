"""
Focused analysis on the 'summary' field from Seeking Alpha analysis articles.

Questions:
  1. How many bullet points per summary?
  2. How long is each bullet (chars/words)?
  3. Total summary length vs full content length (compression ratio)?
  4. Does the summary contain actionable info (price targets, ratings)?
  5. Is it dated differently than the article?

Usage:
  python3 tests/test_seeking_alpha_summary_stats.py
"""

import os
import sys
import time
import json
import re
from datetime import datetime, timezone

sys.path.append(os.getcwd())

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.seeking_alpha_service import SeekingAlphaService

TEST_TICKERS = ["AAPL", "TSLA", "NVDA", "PLTR", "SOFI", "XOM", "KO", "DIS"]
FETCH_SIZE = 10


def main():
    api_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
    if not api_key:
        print("[ABORT] RAPIDAPI_KEY_SEEKING_ALPHA not set.")
        sys.exit(1)

    svc = SeekingAlphaService()
    now = datetime.now(timezone.utc)

    print("=" * 70)
    print("SEEKING ALPHA — SUMMARY FIELD ANALYSIS")
    print(f"Date: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)

    all_summaries = []

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

            time.sleep(0.25)
            detail = svc._call_endpoint("analysis/v2/get-details", {"id": item_id})
            if not detail or "data" not in detail:
                continue

            attrs = detail["data"].get("attributes", {})
            title = attrs.get("title", "???")
            publish_on = attrs.get("publishOn", "")
            content = attrs.get("content", "")
            summary = attrs.get("summary")

            # Parse date
            pub_date = None
            if publish_on:
                try:
                    clean = re.sub(r'\.000', '', publish_on)
                    pub_date = datetime.fromisoformat(clean)
                except:
                    pass

            days_ago = (now - pub_date.replace(tzinfo=timezone.utc)).days if pub_date else None

            content_chars = len(content) if content else 0
            content_words = len(content.split()) if content else 0

            if not summary or not isinstance(summary, list):
                print(f"  [{i}] {title[:55]} — NO SUMMARY")
                continue

            # Summary stats
            num_bullets = len(summary)
            bullet_chars = [len(b) for b in summary]
            bullet_words = [len(b.split()) for b in summary]
            total_summary_chars = sum(bullet_chars)
            total_summary_words = sum(bullet_words)
            compression = f"{total_summary_chars/content_chars*100:.1f}%" if content_chars > 0 else "N/A"

            entry = {
                "ticker": ticker,
                "title": title,
                "publish_on": publish_on,
                "days_ago": days_ago,
                "num_bullets": num_bullets,
                "bullet_chars": bullet_chars,
                "bullet_words": bullet_words,
                "total_summary_chars": total_summary_chars,
                "total_summary_words": total_summary_words,
                "content_chars": content_chars,
                "content_words": content_words,
                "compression": total_summary_chars / content_chars if content_chars > 0 else 0,
                "summary_text": summary,
            }
            all_summaries.append(entry)

            # Print
            age = f"{days_ago}d ago" if days_ago is not None else "no date"
            print(f"\n  [{i}] {title[:60]}")
            print(f"      Date: {publish_on}  ({age})")
            print(f"      Bullets: {num_bullets}  |  Summary: {total_summary_words} words / {total_summary_chars} chars  |  Content: {content_words} words / {content_chars} chars  |  Compression: {compression}")
            for j, bullet in enumerate(summary):
                print(f"        [{j+1}] ({len(bullet.split())} words) {bullet[:120]}{'...' if len(bullet) > 120 else ''}")

        time.sleep(0.5)

    # ===================================================================
    # AGGREGATE STATS
    # ===================================================================
    if not all_summaries:
        print("\nNo summaries collected.")
        return

    print("\n" + "=" * 70)
    print("AGGREGATE SUMMARY STATISTICS")
    print(f"Articles with summaries: {len(all_summaries)}")
    print("=" * 70)

    # Bullets per article
    bullet_counts = [s["num_bullets"] for s in all_summaries]
    print(f"\n  BULLETS PER ARTICLE:")
    print(f"    Min:    {min(bullet_counts)}")
    print(f"    Max:    {max(bullet_counts)}")
    print(f"    Avg:    {sum(bullet_counts)/len(bullet_counts):.1f}")
    print(f"    Median: {sorted(bullet_counts)[len(bullet_counts)//2]}")
    from collections import Counter
    dist = Counter(bullet_counts)
    for k in sorted(dist):
        print(f"    {k} bullets: {dist[k]} articles ({dist[k]/len(all_summaries)*100:.0f}%)")

    # Individual bullet length
    all_bullet_words = [w for s in all_summaries for w in s["bullet_words"]]
    all_bullet_chars = [c for s in all_summaries for c in s["bullet_chars"]]
    print(f"\n  INDIVIDUAL BULLET LENGTH (words):")
    print(f"    Min:    {min(all_bullet_words)}")
    print(f"    Max:    {max(all_bullet_words)}")
    print(f"    Avg:    {sum(all_bullet_words)/len(all_bullet_words):.0f}")
    print(f"    Median: {sorted(all_bullet_words)[len(all_bullet_words)//2]}")

    print(f"\n  INDIVIDUAL BULLET LENGTH (chars):")
    print(f"    Min:    {min(all_bullet_chars)}")
    print(f"    Max:    {max(all_bullet_chars)}")
    print(f"    Avg:    {sum(all_bullet_chars)/len(all_bullet_chars):.0f}")
    print(f"    Median: {sorted(all_bullet_chars)[len(all_bullet_chars)//2]}")

    # Total summary length
    total_sw = [s["total_summary_words"] for s in all_summaries]
    total_sc = [s["total_summary_chars"] for s in all_summaries]
    print(f"\n  TOTAL SUMMARY LENGTH PER ARTICLE (words):")
    print(f"    Min:    {min(total_sw)}")
    print(f"    Max:    {max(total_sw)}")
    print(f"    Avg:    {sum(total_sw)/len(total_sw):.0f}")

    print(f"\n  TOTAL SUMMARY LENGTH PER ARTICLE (chars):")
    print(f"    Min:    {min(total_sc)}")
    print(f"    Max:    {max(total_sc)}")
    print(f"    Avg:    {sum(total_sc)/len(total_sc):.0f}")

    # Compression ratio
    compressions = [s["compression"] for s in all_summaries if s["compression"] > 0]
    if compressions:
        print(f"\n  COMPRESSION (summary chars / content chars):")
        print(f"    Min:    {min(compressions)*100:.1f}%")
        print(f"    Max:    {max(compressions)*100:.1f}%")
        print(f"    Avg:    {sum(compressions)/len(compressions)*100:.1f}%")
        print(f"    → On avg, summaries are {(1 - sum(compressions)/len(compressions))*100:.0f}% smaller than full content")

    # Freshness
    ages = [s["days_ago"] for s in all_summaries if s["days_ago"] is not None]
    if ages:
        print(f"\n  ARTICLE FRESHNESS:")
        print(f"    Newest: {min(ages)}d")
        print(f"    Oldest: {max(ages)}d")
        print(f"    Avg:    {sum(ages)/len(ages):.0f}d")

        # Bucket by age
        buckets = {"0-1d": 0, "2-7d": 0, "8-14d": 0, "15-30d": 0, "31-60d": 0, "60d+": 0}
        for a in ages:
            if a <= 1: buckets["0-1d"] += 1
            elif a <= 7: buckets["2-7d"] += 1
            elif a <= 14: buckets["8-14d"] += 1
            elif a <= 30: buckets["15-30d"] += 1
            elif a <= 60: buckets["31-60d"] += 1
            else: buckets["60d+"] += 1
        print(f"\n  AGE DISTRIBUTION:")
        for bucket, count in buckets.items():
            bar = "█" * count
            print(f"    {bucket:>6}: {count:>3} {bar}")

    # Content keywords scan — do summaries contain actionable info?
    print(f"\n  ACTIONABLE CONTENT SCAN:")
    patterns = {
        "Price target": r"\$\d+",
        "Rating (Buy/Sell/Hold)": r"\b(Buy|Sell|Hold|Strong Buy|Overweight|Underweight)\b",
        "Upside/Downside %": r"\d+%\s*(upside|downside)",
        "Rating change": r"(Rating\s*(Upgrade|Downgrade)|upgrade|downgrade)",
    }
    for label, pattern in patterns.items():
        matches = 0
        for s in all_summaries:
            combined = " ".join(s["summary_text"])
            if re.search(pattern, combined, re.IGNORECASE):
                matches += 1
        print(f"    {label}: {matches}/{len(all_summaries)} articles ({matches/len(all_summaries)*100:.0f}%)")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
