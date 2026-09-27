"""Build a browsable monthly snapshot under docs/performance/<as-of>-package/.

Reads subscribers.db in read-only mode, filters to the last N days,
strips every free-text LLM column, renders charts, drafts case-study
markdowns, and writes a curated landing README.

Usage:
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24 --since-days 30
  python scripts/build_monthly_snapshot.py --as-of 2026-05-24 --db subscribers.db --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from app.services.snapshot.aggregates import (  # noqa: E402
    build_monthly_summary,
    compute_headline_stats,
)
from app.services.snapshot.case_studies import (  # noqa: E402
    draft_case_study,
    pick_candidates,
)
from app.services.snapshot.charts import (  # noqa: E402
    chart_pnl_distribution,
    chart_score_vs_outcome,
    chart_sector_breakdown,
    chart_verdict_distribution,
)
from app.services.snapshot.db_export import (  # noqa: E402
    export_snapshot_data,
    load_decisions,
    load_positions,
)
from app.services.snapshot.render import (  # noqa: E402
    render_data_readme,
    render_package_readme,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_snapshot")


_SLOT_FILES = {
    "best":    "01-best-trade.md",
    "worst":   "02-worst-trade.md",
    "avoided": "03-avoided-correctly.md",
    "open":    "04-still-open.md",
}


def _write_manifest(out_dir: Path, file_rows: dict[str, int]) -> None:
    rows = [{"file": f, "rows": n} for f, n in file_rows.items()]
    df = pd.DataFrame(rows)
    df["generated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    df.to_csv(out_dir / "data" / "manifest.csv", index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="subscribers.db")
    parser.add_argument(
        "--out",
        default=None,
        help="Output dir (default: docs/performance/<as-of>-package/)",
    )
    parser.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    parser.add_argument("--since-days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    out_dir = Path(args.out) if args.out else (
        REPO_ROOT / "docs" / "performance" / f"{args.as_of}-package"
    )

    logger.info("Building snapshot: db=%s, as_of=%s, since_days=%d, out=%s",
                db_path, args.as_of, args.since_days, out_dir)

    if args.dry_run:
        logger.info("--dry-run: no files will be written")
        return 0

    (out_dir / "charts").mkdir(parents=True, exist_ok=True)
    (out_dir / "case-studies").mkdir(parents=True, exist_ok=True)
    (out_dir / "data").mkdir(parents=True, exist_ok=True)

    # Load + export raw data
    decisions = load_decisions(db_path, since_days=args.since_days, as_of=args.as_of)
    positions = load_positions(db_path)
    export_snapshot_data(db_path, out_dir / "data", since_days=args.since_days, as_of=args.as_of)

    # Aggregates
    summary = build_monthly_summary(decisions, positions)
    summary.to_csv(out_dir / "data" / "monthly_summary.csv", index=False)

    # Charts
    chart_verdict_distribution(decisions, out_dir / "charts" / "verdict-distribution.png")
    chart_sector_breakdown(decisions, out_dir / "charts" / "sector-breakdown.png")
    chart_pnl_distribution(positions, out_dir / "charts" / "pnl-distribution.png")
    chart_score_vs_outcome(decisions, positions, out_dir / "charts" / "score-vs-outcome.png")

    # Case studies
    candidates = pick_candidates(decisions, positions)
    for slot, filename in _SLOT_FILES.items():
        md = draft_case_study(slot, candidates[slot])
        (out_dir / "case-studies" / filename).write_text(md)
        logger.info("draft written: %s (candidate=%s)", filename,
                    candidates[slot]["ticker"] if candidates[slot] else "None")

    # READMEs
    stats = compute_headline_stats(decisions, positions, as_of=args.as_of, since_days=args.since_days)
    (out_dir / "README.md").write_text(render_package_readme(stats))
    (out_dir / "data" / "README.md").write_text(render_data_readme(
        as_of=args.as_of,
        since_days=args.since_days,
        n_decisions=len(decisions),
        n_positions=len(positions),
        n_summary=len(summary),
    ))

    _write_manifest(out_dir, {
        "decisions.csv": len(decisions),
        "positions.csv": len(positions),
        "monthly_summary.csv": len(summary),
    })

    logger.info("done. open: %s/README.md", out_dir)
    logger.info("NEXT: review case-studies/*.md and replace the 'Takeaway' placeholder before committing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
