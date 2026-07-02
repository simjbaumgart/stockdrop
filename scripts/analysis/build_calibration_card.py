"""Build the calibration card from realized decision outcomes.

Reads decision_points ⋈ decision_outcomes and computes per-bucket base rates,
writing a machine-readable ``data/calibration_card.json`` and a human-readable
``data/calibration_card.md``. Buckets with n < MIN_N are suppressed so the
prompt never gets fed noise. This is Phase 1 of the calibration feedback loop
(docs/proposals/PLAN_calibration_feedback_option1.md).

Primary label horizon is 4 weeks (locked with Simon); 1w is carried as context.

Usage:
    python -m scripts.analysis.build_calibration_card [--min-n N]
"""
import argparse
import datetime
import json
import os
import statistics as st
import sys
from collections import defaultdict
from typing import Callable, Dict, List, Optional

# Allow running as a standalone script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.database import get_outcomes_joined  # noqa: E402

DEFAULT_MIN_N = 20
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
JSON_PATH = os.path.join(DATA_DIR, "calibration_card.json")
MD_PATH = os.path.join(DATA_DIR, "calibration_card.md")


def _mean(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(st.mean(vals), 4) if vals else None


def bucketize(rows: List[dict], keyfn: Callable[[dict], str],
              min_n: int) -> Dict[str, dict]:
    """Group rows by keyfn, emit base-rate stats for buckets with n >= min_n.

    Only rows with a matured 4w label count — that's the card's primary horizon.
    """
    groups: Dict[str, list] = defaultdict(list)
    for r in rows:
        if r.get("ret_4w") is None:
            continue
        groups[keyfn(r)].append(r)

    out: Dict[str, dict] = {}
    for key, rs in groups.items():
        if len(rs) < min_n:
            continue
        recovered = [r["recovered_4w"] for r in rs if r.get("recovered_4w") is not None]
        out[key] = {
            "n": len(rs),
            "recovery_rate_4w": round(sum(recovered) / len(recovered), 3) if recovered else None,
            "mean_ret_4w": _mean([r["ret_4w"] for r in rs]),
            "mean_ret_1w": _mean([r.get("ret_1w") for r in rs]),
        }
    return out


def build_card(rows: List[dict], min_n: int = DEFAULT_MIN_N) -> dict:
    return {
        "as_of": datetime.date.today().isoformat(),
        "min_n": min_n,
        "primary_horizon": "4w",
        "total_labeled": sum(1 for r in rows if r.get("ret_4w") is not None),
        # Enum-valued keys are upper-cased so a NULL fallback ("UNKNOWN") and a
        # stray-cased row ("unknown") collapse into one bucket instead of
        # splitting n and emitting two inconsistent base rates for the prompts.
        "by_drop_type": bucketize(rows, lambda r: (r.get("drop_type") or "UNKNOWN").upper(), min_n),
        "by_earnings": bucketize(
            rows, lambda r: "earnings" if r.get("is_earnings_drop") else "non_earnings", min_n),
        "by_pm_verdict": bucketize(rows, lambda r: (r.get("recommendation") or "UNKNOWN").upper(), min_n),
        "by_dr_verdict": bucketize(rows, lambda r: (r.get("deep_research_verdict") or "UNKNOWN").upper(), min_n),
        "by_gatekeeper_tier": bucketize(rows, lambda r: (r.get("gatekeeper_tier") or "UNKNOWN").upper(), min_n),
    }


_SECTION_TITLES = {
    "by_drop_type": "By drop_type",
    "by_earnings": "By earnings vs non-earnings",
    "by_pm_verdict": "By PM verdict",
    "by_dr_verdict": "By Deep Research verdict",
    "by_gatekeeper_tier": "By gatekeeper tier",
}


def render_md(card: dict) -> str:
    lines = [
        "# Calibration card",
        "",
        f"_As of {card['as_of']} · primary horizon {card['primary_horizon']} · "
        f"min n = {card['min_n']} · {card['total_labeled']} labeled decisions_",
        "",
        "All rates are 4-week unless noted. Buckets with n < min_n are suppressed.",
        "",
    ]
    for section, title in _SECTION_TITLES.items():
        buckets = card.get(section) or {}
        lines.append(f"## {title}")
        if not buckets:
            lines.append("_(no bucket met the min-n threshold)_")
            lines.append("")
            continue
        lines.append("| bucket | n | recovery_4w | mean_ret_4w | mean_ret_1w |")
        lines.append("|---|---:|---:|---:|---:|")
        for key, s in sorted(buckets.items(), key=lambda kv: -kv[1]["n"]):
            rr = f"{s['recovery_rate_4w']:.0%}" if s["recovery_rate_4w"] is not None else "—"
            m4 = f"{s['mean_ret_4w']:+.1%}" if s["mean_ret_4w"] is not None else "—"
            m1 = f"{s['mean_ret_1w']:+.1%}" if s["mean_ret_1w"] is not None else "—"
            lines.append(f"| {key} | {s['n']} | {rr} | {m4} | {m1} |")
        lines.append("")
    return "\n".join(lines)


def run(min_n: int = DEFAULT_MIN_N) -> dict:
    rows = get_outcomes_joined()
    card = build_card(rows, min_n=min_n)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(JSON_PATH, "w") as f:
        json.dump(card, f, indent=2)
    with open(MD_PATH, "w") as f:
        f.write(render_md(card))
    n_buckets = sum(len(card.get(s) or {}) for s in _SECTION_TITLES)
    print(f"[build_calibration_card] wrote {JSON_PATH} "
          f"({card['total_labeled']} labeled, {n_buckets} buckets ≥ n{min_n})")
    return card


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the calibration card")
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N,
                    help=f"Suppress buckets below this n (default {DEFAULT_MIN_N})")
    args = ap.parse_args()
    run(min_n=args.min_n)
