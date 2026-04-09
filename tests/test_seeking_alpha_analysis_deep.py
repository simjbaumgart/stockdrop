"""
Deep dive into Seeking Alpha ANALYSIS endpoint.

Questions to answer:
  1. How long are analysis articles? (char count, word count)
  2. Are they consistent across tickers? (always get data?)
  3. Do we always get dates? What format?
  4. What fields are available beyond title/content/publishOn?
  5. How fresh are they? (days since publish)

Usage:
  python3 tests/test_seeking_alpha_analysis_deep.py
"""

import os
import sys
import time
import json
from datetime import datetime, timezone

sys.path.append(os.getcwd())

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.seeking_alpha_service import SeekingAlphaService

# Diverse set: mega-cap, mid-cap, small-cap, different sectors
TEST_TICKERS = ["AAPL", "TSLA", "APA", "NVDA", "KO", "PLTR", "SOFI", "XOM", "DIS", "RIVN"]

FETCH_SIZE = 10  # request more to see what we actually get back


def parse_date(date_str):
    """Parse SA date string to datetime."""
    if not date_str:
        return None
    try:
        # Handle formats like: 2026-04-08T17:34:22-04:00
        from datetime import datetime
        # Python 3.9 doesn't support fromisoformat with timezone well, workaround:
        import re
        clean = re.sub(r'\.000', '', date_str)
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def main():
    api_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
    if not api_key:
        print("[ABORT] RAPIDAPI_KEY_SEEKING_ALPHA not set.")
        sys.exit(1)

    svc = SeekingAlphaService()
    now = datetime.now(timezone.utc)

    print("=" * 70)
    print("SEEKING ALPHA — ANALYSIS ENDPOINT DEEP DIVE")
    print(f"Date: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Tickers: {TEST_TICKERS}")
    print(f"Requested size: {FETCH_SIZE}")
    print("=" * 70)

    all_stats = []

    for ticker in TEST_TICKERS:
        print(f"\n{'─' * 70}")
        print(f"  {ticker}")
        print(f"{'─' * 70}")

        # Step 1: Raw list call
        list_resp = svc._call_endpoint("analysis/v2/list", {"id": ticker, "size": FETCH_SIZE})

        if not list_resp:
            print(f"  [EMPTY] No response from list endpoint")
            all_stats.append({"ticker": ticker, "count": 0, "articles": []})
            time.sleep(0.5)
            continue

        if "data" not in list_resp:
            print(f"  [ERROR] No 'data' key. Keys: {list(list_resp.keys())}")
            all_stats.append({"ticker": ticker, "count": 0, "articles": []})
            time.sleep(0.5)
            continue

        items = list_resp["data"]
        print(f"  Items returned: {len(items)}")

        # Step 2: Check what fields the LIST gives us (before detail fetch)
        if items:
            first_list_item = items[0]
            print(f"\n  [LIST ITEM KEYS] {sorted(first_list_item.keys())}")
            list_attrs = first_list_item.get("attributes", {})
            if list_attrs:
                print(f"  [LIST ATTRIBUTES KEYS] {sorted(list_attrs.keys())}")
                # Show what we get from the list alone
                print(f"    title (from list): {list_attrs.get('title', 'N/A')[:80]}")
                print(f"    publishOn (from list): {list_attrs.get('publishOn', 'N/A')}")

        # Step 3: Fetch details for each article
        ticker_articles = []
        for i, item in enumerate(items):
            item_id = item.get("id")
            if not item_id:
                continue

            time.sleep(0.3)
            detail = svc._call_endpoint("analysis/v2/get-details", {"id": item_id})

            if not detail:
                print(f"  [{i}] id={item_id} -> detail returned None")
                continue

            data = detail.get("data", {})
            attrs = data.get("attributes", {})

            title = attrs.get("title", "")
            content = attrs.get("content", "")
            publish_on = attrs.get("publishOn", "")
            word_count = len(content.split()) if content else 0
            char_count = len(content) if content else 0

            # Check all available attribute keys
            if i == 0:
                print(f"\n  [DETAIL ATTRIBUTES KEYS] {sorted(attrs.keys())}")

            # Parse date and calc freshness
            pub_date = parse_date(publish_on)
            if pub_date:
                days_ago = (now - pub_date.replace(tzinfo=timezone.utc)).days
            else:
                days_ago = None

            article_info = {
                "id": item_id,
                "title": title,
                "publishOn": publish_on,
                "days_ago": days_ago,
                "char_count": char_count,
                "word_count": word_count,
                "has_content": bool(content and len(content) > 0),
                "has_date": bool(publish_on),
            }
            ticker_articles.append(article_info)

            # Print each article summary
            freshness = f"{days_ago}d ago" if days_ago is not None else "no date"
            print(f"  [{i}] {freshness:>8} | {word_count:>5} words | {char_count:>6} chars | {title[:55]}")

        # Ticker summary
        all_stats.append({"ticker": ticker, "count": len(ticker_articles), "articles": ticker_articles})

        if ticker_articles:
            chars = [a["char_count"] for a in ticker_articles]
            words = [a["word_count"] for a in ticker_articles]
            dates_present = sum(1 for a in ticker_articles if a["has_date"])
            content_present = sum(1 for a in ticker_articles if a["has_content"])
            ages = [a["days_ago"] for a in ticker_articles if a["days_ago"] is not None]

            print(f"\n  TICKER SUMMARY:")
            print(f"    Articles:       {len(ticker_articles)}")
            print(f"    Has content:    {content_present}/{len(ticker_articles)}")
            print(f"    Has date:       {dates_present}/{len(ticker_articles)}")
            print(f"    Chars:          min={min(chars)}, max={max(chars)}, avg={sum(chars)//len(chars)}")
            print(f"    Words:          min={min(words)}, max={max(words)}, avg={sum(words)//len(words)}")
            if ages:
                print(f"    Freshness:      newest={min(ages)}d, oldest={max(ages)}d, avg={sum(ages)//len(ages)}d")

        time.sleep(1)

    # ===================================================================
    # OVERALL SUMMARY
    # ===================================================================
    print("\n" + "=" * 70)
    print("OVERALL SUMMARY")
    print("=" * 70)

    total_articles = sum(s["count"] for s in all_stats)
    tickers_with_data = sum(1 for s in all_stats if s["count"] > 0)
    tickers_empty = sum(1 for s in all_stats if s["count"] == 0)

    all_articles = [a for s in all_stats for a in s["articles"]]
    all_chars = [a["char_count"] for a in all_articles if a["has_content"]]
    all_words = [a["word_count"] for a in all_articles if a["has_content"]]
    all_ages = [a["days_ago"] for a in all_articles if a["days_ago"] is not None]
    all_dates = sum(1 for a in all_articles if a["has_date"])
    all_content = sum(1 for a in all_articles if a["has_content"])

    print(f"\n  Tickers tested:    {len(TEST_TICKERS)}")
    print(f"  Tickers with data: {tickers_with_data}/{len(TEST_TICKERS)}")
    print(f"  Tickers empty:     {tickers_empty}")
    print(f"  Total articles:    {total_articles}")

    if all_articles:
        print(f"\n  Content present:   {all_content}/{len(all_articles)} ({all_content/len(all_articles)*100:.0f}%)")
        print(f"  Date present:      {all_dates}/{len(all_articles)} ({all_dates/len(all_articles)*100:.0f}%)")

    if all_chars:
        print(f"\n  CONTENT LENGTH (chars):")
        print(f"    Min:    {min(all_chars):,}")
        print(f"    Max:    {max(all_chars):,}")
        print(f"    Avg:    {sum(all_chars)//len(all_chars):,}")
        print(f"    Median: {sorted(all_chars)[len(all_chars)//2]:,}")

    if all_words:
        print(f"\n  CONTENT LENGTH (words):")
        print(f"    Min:    {min(all_words):,}")
        print(f"    Max:    {max(all_words):,}")
        print(f"    Avg:    {sum(all_words)//len(all_words):,}")
        print(f"    Median: {sorted(all_words)[len(all_words)//2]:,}")

    if all_ages:
        print(f"\n  FRESHNESS (days since publish):")
        print(f"    Newest: {min(all_ages)}d")
        print(f"    Oldest: {max(all_ages)}d")
        print(f"    Avg:    {sum(all_ages)//len(all_ages)}d")

    # Per-ticker consistency table
    print(f"\n  PER-TICKER BREAKDOWN:")
    print(f"  {'Ticker':<8} {'Count':>6} {'Avg Words':>10} {'Avg Chars':>10} {'Dates':>6} {'Newest':>8}")
    print(f"  {'─'*8} {'─'*6} {'─'*10} {'─'*10} {'─'*6} {'─'*8}")
    for s in all_stats:
        t = s["ticker"]
        c = s["count"]
        if c == 0:
            print(f"  {t:<8} {c:>6}       —          —      —        —")
            continue
        avg_w = sum(a["word_count"] for a in s["articles"]) // c
        avg_c = sum(a["char_count"] for a in s["articles"]) // c
        dates = sum(1 for a in s["articles"] if a["has_date"])
        ages = [a["days_ago"] for a in s["articles"] if a["days_ago"] is not None]
        newest = f"{min(ages)}d" if ages else "—"
        print(f"  {t:<8} {c:>6} {avg_w:>10} {avg_c:>10} {dates:>4}/{c} {newest:>8}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
