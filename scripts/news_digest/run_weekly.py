#!/usr/bin/env python3
"""Manual/backfill runner for FT weekly digest.

Finimize weekly is written by the Cowork scheduler — we only generate FT weekly,
which pulls in the last 3 Finimize weeklies as context.

Usage:
    python scripts/news_digest/run_weekly.py                    # current ISO week
    python scripts/news_digest/run_weekly.py --week 2026-W17    # a specific week
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.news_digest_service import ensure_ft_weekly_digest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _current_iso_week() -> str:
    y, w, _ = datetime.now().isocalendar()
    return f"{y}-W{int(w):02d}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--week", default=_current_iso_week(), help="ISO week, e.g. 2026-W17")
    args = p.parse_args()

    result = ensure_ft_weekly_digest(args.week)
    if result is None:
        print(f"[ft-weekly] {args.week}: no digest produced")
        return 1
    print(f"[ft-weekly] {args.week}: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
