"""Rolling per-verdict forward returns — Tier 3, console only.

Per-verdict performance is regime-dependent (the AVOID finding flipped sign
between April and May-June 2026) and often small-n, so it is NEVER injected
into agent prompts (THREE_TIER_FEEDBACK_PROPOSAL.md). This view exists for the
monthly audit: a finding is promoted to the pinned card or a gate only after
it survives 2-3 audits here.

Horizons are the decision_outcomes columns (1w/2w/4w/8w trading-day marks);
there is deliberately no 21d column.

Usage:
    python -m scripts.analysis.verdict_alpha_rolling [--windows 30 60 90]
"""
import argparse
import datetime
import os
import statistics as st
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.database import get_outcomes_joined  # noqa: E402

HORIZONS = (("1w", "ret_1w"), ("2w", "ret_2w"), ("4w", "ret_4w"), ("8w", "ret_8w"))
DEFAULT_WINDOWS: Tuple[int, ...] = (30, 60, 90)


def _decision_date(row: dict) -> datetime.date:
    return datetime.date.fromisoformat(str(row.get("timestamp") or "")[:10])


def _stats(vals: List[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0, "win_rate": 0.0, "mean": 0.0, "median": 0.0}
    return {
        "n": len(vals),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals), 3),
        "mean": round(st.mean(vals), 4),
        "median": round(st.median(vals), 4),
    }


def compute_view(rows: List[dict], today: datetime.date,
                 windows: Tuple[int, ...] = DEFAULT_WINDOWS) -> dict:
    """Pure aggregation: {window: {by_pm_verdict/by_dr_verdict:
    {verdict: {horizon: stats}}}}."""
    view: Dict[int, dict] = {}
    for window in windows:
        cutoff = today - datetime.timedelta(days=window)
        in_window = []
        for r in rows:
            try:
                if _decision_date(r) >= cutoff:
                    in_window.append(r)
            except ValueError:
                continue
        sections = {}
        for section, key in (("by_pm_verdict", "recommendation"),
                             ("by_dr_verdict", "deep_research_verdict")):
            groups: Dict[str, list] = {}
            for r in in_window:
                verdict = (r.get(key) or "").strip().upper()
                if verdict:
                    groups.setdefault(verdict, []).append(r)
            sections[section] = {
                verdict: {h: _stats([r.get(col) for r in rs]) for h, col in HORIZONS}
                for verdict, rs in groups.items()
            }
        view[window] = sections
    return view


def render_view(view: dict) -> str:
    lines: List[str] = []
    for window in sorted(view):
        lines.append(f"\n=== trailing {window}d decisions ===")
        for section in ("by_pm_verdict", "by_dr_verdict"):
            groups = view[window].get(section) or {}
            lines.append(f"\n-- {section} --")
            if not groups:
                lines.append("(no decisions)")
                continue
            lines.append(f"{'verdict':<24}" + "".join(
                f"{h+' n/win/mean':>22}" for h, _ in HORIZONS))
            for verdict in sorted(groups, key=lambda v: -groups[v]["4w"]["n"]):
                cells = []
                for h, _ in HORIZONS:
                    s = groups[verdict][h]
                    cells.append(f"{s['n']:>5}/{s['win_rate']:>4.0%}/{s['mean']:>+7.1%}"
                                 if s["n"] else f"{'—':>22}".strip().rjust(22))
                lines.append(f"{verdict:<24}" + "".join(f"{c:>22}" for c in cells))
    return "\n".join(lines)


def run(windows: Tuple[int, ...] = DEFAULT_WINDOWS) -> None:
    rows = get_outcomes_joined()
    print(f"[verdict_alpha_rolling] {len(rows)} labeled decisions "
          f"(console-only view — never fed to agents)")
    print(render_view(compute_view(rows, datetime.date.today(), windows)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rolling per-verdict forward returns (Tier 3)")
    ap.add_argument("--windows", type=int, nargs="+", default=list(DEFAULT_WINDOWS),
                    help="Trailing windows in days (default: 30 60 90)")
    args = ap.parse_args()
    run(tuple(args.windows))
