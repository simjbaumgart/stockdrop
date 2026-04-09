"""
Side-by-side comparison: SA summary field vs our _clean_html output.

For each article:
  1. Show the pre-built summary bullets
  2. Show the cleaned full content (first ~500 words)
  3. Compare: length, info density, what's lost/gained

Usage:
  python3 tests/test_summary_vs_cleaned.py
"""

import os
import sys
import time
import re
from datetime import datetime, timezone

sys.path.append(os.getcwd())

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.services.seeking_alpha_service import SeekingAlphaService

# 3 tickers, 3 articles each = 9 comparisons
TEST_TICKERS = ["AAPL", "TSLA", "PLTR"]
ARTICLES_PER_TICKER = 3


def word_count(text):
    return len(text.split()) if text else 0


def char_count(text):
    return len(text) if text else 0


def extract_key_signals(text):
    """Scan text for actionable trading signals."""
    signals = []
    # Price targets
    targets = re.findall(r'\$\d[\d,.]*', text)
    if targets:
        signals.append(f"Price targets: {', '.join(targets[:5])}")
    # Ratings
    ratings = re.findall(r'\b(Strong Buy|Buy|Sell|Strong Sell|Hold|Overweight|Underweight|Outperform|Underperform)\b', text, re.IGNORECASE)
    if ratings:
        signals.append(f"Ratings: {', '.join(set(r.title() for r in ratings))}")
    # Percentages
    pcts = re.findall(r'\d+(?:\.\d+)?%', text)
    if pcts:
        signals.append(f"Percentages mentioned: {len(pcts)}")
    # Company mentions (tickers)
    tickers = re.findall(r'\b[A-Z]{2,5}\b', text)
    # filter common words
    common = {'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'HAS', 'ITS', 'NOW', 'MAY', 'NEW', 'CEO', 'CFO', 'IPO', 'GDP', 'EPS', 'YOY', 'QOQ', 'FCF', 'DCF', 'ETF', 'SEC', 'FED', 'USA', 'INC', 'LTD', 'LLC'}
    tickers = [t for t in set(tickers) if t not in common and len(t) >= 2]
    if tickers:
        signals.append(f"Tickers: {', '.join(sorted(tickers)[:8])}")
    return signals


def main():
    api_key = os.getenv("RAPIDAPI_KEY_SEEKING_ALPHA")
    if not api_key:
        print("[ABORT] RAPIDAPI_KEY_SEEKING_ALPHA not set.")
        sys.exit(1)

    svc = SeekingAlphaService()
    now = datetime.now(timezone.utc)

    print("=" * 80)
    print("SUMMARY vs CLEANED CONTENT — SIDE-BY-SIDE COMPARISON")
    print(f"Date: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 80)

    comparisons = []

    for ticker in TEST_TICKERS:
        print(f"\n{'━' * 80}")
        print(f"  {ticker}")
        print(f"{'━' * 80}")

        list_resp = svc._call_endpoint("analysis/v2/list", {"id": ticker, "size": ARTICLES_PER_TICKER})
        if not list_resp or "data" not in list_resp:
            print("  [EMPTY] No list response")
            time.sleep(0.5)
            continue

        items = list_resp["data"][:ARTICLES_PER_TICKER]

        for i, item in enumerate(items):
            item_id = item.get("id")
            if not item_id:
                continue

            time.sleep(0.3)
            detail = svc._call_endpoint("analysis/v2/get-details", {"id": item_id})
            if not detail or "data" not in detail:
                print(f"  [{i}] No detail for id={item_id}")
                continue

            attrs = detail["data"].get("attributes", {})
            title = attrs.get("title", "???")
            publish_on = attrs.get("publishOn", "")
            raw_content = attrs.get("content", "")
            summary_bullets = attrs.get("summary", [])

            # Clean the content using our service
            cleaned = svc._clean_html(raw_content)

            # Build summary text
            summary_text = "\n".join(f"  • {b}" for b in summary_bullets) if summary_bullets else "(no summary)"

            # Stats
            raw_chars = char_count(raw_content)
            cleaned_chars = char_count(cleaned)
            cleaned_words = word_count(cleaned)
            summary_chars = sum(char_count(b) for b in summary_bullets)
            summary_words = sum(word_count(b) for b in summary_bullets)

            # Signals
            summary_signals = extract_key_signals(" ".join(summary_bullets))
            cleaned_signals = extract_key_signals(cleaned)

            comp = {
                "ticker": ticker,
                "title": title,
                "raw_chars": raw_chars,
                "cleaned_chars": cleaned_chars,
                "cleaned_words": cleaned_words,
                "summary_chars": summary_chars,
                "summary_words": summary_words,
                "summary_signals": summary_signals,
                "cleaned_signals": cleaned_signals,
                "num_bullets": len(summary_bullets),
            }
            comparisons.append(comp)

            # ── Print comparison ──
            print(f"\n{'─' * 80}")
            print(f"  ARTICLE: {title}")
            print(f"  Date: {publish_on}")
            print(f"{'─' * 80}")

            print(f"\n  ┌─ SUMMARY ({summary_words} words / {summary_chars} chars / {len(summary_bullets)} bullets)")
            print(f"  │")
            for b in summary_bullets:
                # Wrap long bullets
                print(f"  │  • {b}")
            print(f"  │")
            if summary_signals:
                print(f"  │  Signals: {' | '.join(summary_signals)}")
            print(f"  └─")

            print(f"\n  ┌─ CLEANED CONTENT ({cleaned_words} words / {cleaned_chars} chars)")
            print(f"  │")
            # Show first ~400 words of cleaned content
            words = cleaned.split()
            preview = " ".join(words[:400])
            # Print in wrapped lines
            line_width = 100
            for start in range(0, len(preview), line_width):
                chunk = preview[start:start + line_width]
                print(f"  │  {chunk}")
            if len(words) > 400:
                print(f"  │  ... [{len(words) - 400} more words]")
            print(f"  │")
            if cleaned_signals:
                print(f"  │  Signals: {' | '.join(cleaned_signals)}")
            print(f"  └─")

            # Quick verdict
            print(f"\n  COMPARISON:")
            print(f"    Raw HTML:     {raw_chars:>7,} chars")
            print(f"    Cleaned:      {cleaned_chars:>7,} chars  ({cleaned_words} words)")
            print(f"    Summary:      {summary_chars:>7,} chars  ({summary_words} words)")
            print(f"    Compression:  {summary_chars/cleaned_chars*100:.1f}% of cleaned" if cleaned_chars > 0 else "    Compression: N/A")

            # Signal comparison
            summary_signal_set = set()
            cleaned_signal_set = set()
            for s in summary_signals:
                if s.startswith("Price"):
                    summary_signal_set.add("price_targets")
                if s.startswith("Rating"):
                    summary_signal_set.add("ratings")
            for s in cleaned_signals:
                if s.startswith("Price"):
                    cleaned_signal_set.add("price_targets")
                if s.startswith("Rating"):
                    cleaned_signal_set.add("ratings")

            in_both = summary_signal_set & cleaned_signal_set
            only_summary = summary_signal_set - cleaned_signal_set
            only_cleaned = cleaned_signal_set - summary_signal_set

            if in_both:
                print(f"    Signals in both:       {', '.join(in_both)}")
            if only_summary:
                print(f"    Only in summary:       {', '.join(only_summary)}")
            if only_cleaned:
                print(f"    Only in full content:  {', '.join(only_cleaned)}")

        time.sleep(0.5)

    # ===================================================================
    # OVERALL COMPARISON
    # ===================================================================
    if not comparisons:
        return

    print("\n" + "=" * 80)
    print("OVERALL COMPARISON")
    print(f"Articles compared: {len(comparisons)}")
    print("=" * 80)

    avg_cleaned_words = sum(c["cleaned_words"] for c in comparisons) / len(comparisons)
    avg_summary_words = sum(c["summary_words"] for c in comparisons) / len(comparisons)
    avg_cleaned_chars = sum(c["cleaned_chars"] for c in comparisons) / len(comparisons)
    avg_summary_chars = sum(c["summary_chars"] for c in comparisons) / len(comparisons)

    compressions = [c["summary_chars"] / c["cleaned_chars"] for c in comparisons if c["cleaned_chars"] > 0]

    print(f"\n  {'Metric':<25} {'Cleaned Content':>18} {'Summary':>18} {'Savings':>10}")
    print(f"  {'─'*25} {'─'*18} {'─'*18} {'─'*10}")
    print(f"  {'Avg words':<25} {avg_cleaned_words:>15.0f}   {avg_summary_words:>15.0f}   {(1-avg_summary_words/avg_cleaned_words)*100:>7.0f}%")
    print(f"  {'Avg chars':<25} {avg_cleaned_chars:>15.0f}   {avg_summary_chars:>15.0f}   {(1-avg_summary_chars/avg_cleaned_chars)*100:>7.0f}%")
    print(f"  {'Avg compression':<25} {'100%':>18} {sum(compressions)/len(compressions)*100:>15.1f}%")

    # Signal coverage
    articles_with_targets_summary = sum(1 for c in comparisons if any("Price" in s for s in c["summary_signals"]))
    articles_with_targets_cleaned = sum(1 for c in comparisons if any("Price" in s for s in c["cleaned_signals"]))
    articles_with_ratings_summary = sum(1 for c in comparisons if any("Rating" in s for s in c["summary_signals"]))
    articles_with_ratings_cleaned = sum(1 for c in comparisons if any("Rating" in s for s in c["cleaned_signals"]))

    print(f"\n  SIGNAL COVERAGE:")
    print(f"  {'Signal':<25} {'Cleaned':>18} {'Summary':>18}")
    print(f"  {'─'*25} {'─'*18} {'─'*18}")
    print(f"  {'Price targets':<25} {articles_with_targets_cleaned:>15}/{len(comparisons)}   {articles_with_targets_summary:>15}/{len(comparisons)}")
    print(f"  {'Buy/Sell/Hold ratings':<25} {articles_with_ratings_cleaned:>15}/{len(comparisons)}   {articles_with_ratings_summary:>15}/{len(comparisons)}")

    # Token estimation (rough: 1 token ≈ 4 chars)
    total_cleaned_tokens = sum(c["cleaned_chars"] for c in comparisons) / 4
    total_summary_tokens = sum(c["summary_chars"] for c in comparisons) / 4
    print(f"\n  TOKEN ESTIMATION (for these {len(comparisons)} articles):")
    print(f"    Cleaned content: ~{total_cleaned_tokens:,.0f} tokens")
    print(f"    Summaries only:  ~{total_summary_tokens:,.0f} tokens")
    print(f"    Savings:         ~{total_cleaned_tokens - total_summary_tokens:,.0f} tokens ({(1-total_summary_tokens/total_cleaned_tokens)*100:.0f}%)")

    print(f"\n  VERDICT:")
    if avg_summary_words < avg_cleaned_words * 0.1:
        print(f"    Summaries are ~{avg_cleaned_words/avg_summary_words:.0f}x shorter than cleaned content")
    print(f"    Summary captures core thesis + price targets in ~{avg_summary_words:.0f} words")
    print(f"    Full cleaned content adds granular data, charts context, and deeper reasoning")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
