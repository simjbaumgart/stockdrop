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


def main():
    args = parse_args()
    print(f"[ab-test] args: {args}")
    # rest of pipeline added in later tasks


if __name__ == "__main__":
    main()
