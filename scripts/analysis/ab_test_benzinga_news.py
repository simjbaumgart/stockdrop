#!/usr/bin/env python3
"""
A/B test the news pipeline with vs without Benzinga/Massive.

Usage:
    python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL,NVDA,TSLA
    python scripts/analysis/ab_test_benzinga_news.py --from-db 8
    python scripts/analysis/ab_test_benzinga_news.py --tickers AAPL --output-dir /tmp/ab

By default reads up to 10 distinct recent tickers from subscribers.db decision_points.
Writes <ticker>_with.txt, <ticker>_without.txt, <ticker>_metrics.json, and summary.md
to audit_reports/benzinga_ab_test/<UTC-timestamp>/.
"""
from __future__ import annotations  # required for `str | None` and PEP 585 generics on Py 3.9

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the project root importable when run from anywhere
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "subscribers.db"


def tickers_from_db(n: int) -> list[str]:
    """Return up to n most-recent distinct tickers from decision_points."""
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found at {DB_PATH}")
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(
            "SELECT symbol FROM decision_points "
            "ORDER BY id DESC"
        )
        seen, out = set(), []
        for (t,) in cur:
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= n:
                break
        return out
    finally:
        con.close()


def resolve_tickers(args) -> list[str]:
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.from_db:
        tickers = tickers_from_db(args.from_db)
    else:
        tickers = tickers_from_db(args.limit)
    return tickers[: args.limit]


def parse_args():
    p = argparse.ArgumentParser(description="Benzinga news A/B test")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--tickers", help="Comma-separated tickers, e.g. AAPL,NVDA")
    src.add_argument("--from-db", type=int, metavar="N",
                     help="Pull the last N distinct tickers from decision_points")
    p.add_argument("--output-dir", default=None,
                   help="Override output directory (default: audit_reports/benzinga_ab_test/<ts>)")
    p.add_argument("--limit", type=int, default=10,
                   help="Cap on number of tickers (default 10)")
    return p.parse_args()


from contextlib import contextmanager

from app.services.stock_service import StockService
from app.services.benzinga_service import benzinga_service as _bz_singleton


@contextmanager
def benzinga_disabled():
    """Force Benzinga company + market news to return empty for the duration."""
    orig_company = _bz_singleton.get_company_news
    orig_market = _bz_singleton.get_market_news
    _bz_singleton.get_company_news = lambda *a, **kw: []
    _bz_singleton.get_market_news = lambda *a, **kw: []
    try:
        yield
    finally:
        _bz_singleton.get_company_news = orig_company
        _bz_singleton.get_market_news = orig_market


def fetch_both(ticker: str) -> tuple[list[dict], list[dict]]:
    """Return (with_benzinga_news, without_benzinga_news) for a ticker."""
    svc = StockService()
    print(f"  [{ticker}] fetching WITH Benzinga ...")
    with_news = svc.get_aggregated_news(ticker, region="US", exchange="", company_name="")
    print(f"  [{ticker}] fetching WITHOUT Benzinga ...")
    with benzinga_disabled():
        without_news = svc.get_aggregated_news(ticker, region="US", exchange="", company_name="")
    return with_news, without_news


def main():
    args = parse_args()
    tickers = resolve_tickers(args)
    if not tickers:
        raise SystemExit("No tickers resolved.")
    print(f"[ab-test] tickers ({len(tickers)}): {tickers}")

    # TEMP smoke-test — removed in Task 5
    sample = tickers[0]
    w, wo = fetch_both(sample)
    print(f"[ab-test] {sample}: with={len(w)} items, without={len(wo)} items")


if __name__ == "__main__":
    main()
