#!/usr/bin/env python3
"""Manual/backfill runner for daily news digests.

Usage:
    python scripts/news_digest/run_daily.py                      # today, both sources
    python scripts/news_digest/run_daily.py --date 2026-04-20    # backfill a specific date
    python scripts/news_digest/run_daily.py --source ft          # just FT
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.news_digest_schema import SOURCES  # noqa: E402
from app.services.news_digest_service import ensure_daily_digest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    p.add_argument("--source", choices=list(SOURCES) + ["all"], default="all")
    args = p.parse_args()

    sources = SOURCES if args.source == "all" else (args.source,)
    ok = 0
    for s in sources:
        result = ensure_daily_digest(s, args.date)
        if result is None:
            print(f"[{s}] {args.date}: no digest produced")
        else:
            ol = result.get("one_liner", "").strip()
            print(f"[{s}] {args.date}: ok — {ol}")
            ok += 1
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
