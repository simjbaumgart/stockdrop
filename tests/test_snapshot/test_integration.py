"""End-to-end: run the orchestration script against the fixture DB."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_full_snapshot_against_fixture(snapshot_db: Path, tmp_path):
    out_dir = tmp_path / "2026-05-24-package"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "build_monthly_snapshot.py"),
            "--db", str(snapshot_db),
            "--out", str(out_dir),
            "--as-of", "2026-05-24",
            "--since-days", "30",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"script failed: {result.stderr}"

    # Top-level README + four charts
    assert (out_dir / "README.md").exists()
    for chart in (
        "verdict-distribution.png",
        "sector-breakdown.png",
        "pnl-distribution.png",
        "score-vs-outcome.png",
    ):
        assert (out_dir / "charts" / chart).exists(), f"missing chart: {chart}"

    # Case studies
    for case in (
        "01-best-trade.md",
        "02-worst-trade.md",
        "03-avoided-correctly.md",
        "04-still-open.md",
    ):
        assert (out_dir / "case-studies" / case).exists(), f"missing case: {case}"

    # Data
    for f in ("README.md", "decisions.csv", "positions.csv", "monthly_summary.csv", "schema.sql", "manifest.csv"):
        assert (out_dir / "data" / f).exists(), f"missing data file: {f}"

    # Privacy guardrail: no subscribers anywhere
    for path in out_dir.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            assert b"subscribers" not in content or "data/README.md" in str(path), (
                f"subscribers leaked into {path}"
            )

    # decisions.csv has no banned columns
    header = (out_dir / "data" / "decisions.csv").read_text().splitlines()[0]
    for banned in ("reasoning", "deep_research_reason", "deep_research_swot"):
        assert banned not in header, f"banned column {banned} in decisions.csv"
