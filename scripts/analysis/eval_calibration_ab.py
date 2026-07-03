"""Evaluate the calibration A/B (Phase 3, Stage 1 shadow).

Joins ``calibration_shadow_runs`` to realized ``decision_outcomes`` and prints a
control-vs-treatment metric table with confidence intervals. This is the
*analysis* half of Phase 3 — the shadow rows are produced by the live pipeline
(not yet wired; see PLAN §7). The script degrades gracefully (prints "no data")
until rows accrue, so it is safe to run at any time.

Primary metric: mean realized 4w return per BUY, control vs treatment (paired
on the same candidate pool). Secondary: win rate, verdict-flip rate + direction.

Usage:
    python -m scripts.analysis.eval_calibration_ab
"""
import math
import os
import sqlite3
import sys
from typing import List, Optional, Tuple

# Allow running as a standalone script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

DB_NAME = os.getenv("DB_PATH", "subscribers.db")

# Verdicts that constitute an actionable "BUY" for return accounting.
BUY_LIKE = {"BUY", "BUY_LIMIT"}


def _mean_ci(vals: List[float]) -> Tuple[Optional[float], Optional[float], int]:
    """Mean and 95% half-width (normal approx). Returns (mean, half_width, n)."""
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n == 0:
        return None, None, 0
    mean = sum(vals) / n
    if n < 2:
        return mean, None, n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    half = 1.96 * math.sqrt(var / n)
    return mean, half, n


def _arm_stats(rows: List[dict], verdict_key: str) -> dict:
    """Realized 4w stats for the BUY subset of one arm."""
    buys = [r for r in rows
            if (r.get(verdict_key) or "").upper() in BUY_LIKE
            and r.get("ret_4w") is not None]
    rets = [r["ret_4w"] for r in buys]
    recovered = [r["recovered_4w"] for r in buys if r.get("recovered_4w") is not None]
    mean, half, n = _mean_ci(rets)
    return {
        "n_buys": n,
        "mean_ret_4w": mean,
        "ci_half": half,
        "win_rate_4w": (sum(recovered) / len(recovered)) if recovered else None,
    }


def load_rows() -> List[dict]:
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.*, o.ret_4w, o.recovered_4w
            FROM calibration_shadow_runs s
            LEFT JOIN decision_outcomes o ON s.decision_id = o.decision_id
            ORDER BY s.timestamp ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError as e:
        print(f"[eval_calibration_ab] table not ready: {e}")
        return []


def _fmt(v: Optional[float], pct: bool = True, signed: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return (f"{v:+.2%}" if signed else f"{v:.1%}")
    return f"{v:.3f}"


def run() -> dict:
    rows = load_rows()
    print(f"\n=== Calibration A/B eval — {len(rows)} shadow rows ===")
    if not rows:
        print("No shadow rows yet. Wire the live shadow path (PLAN §7.1) to accrue data.")
        return {"n_rows": 0}

    flips = [r for r in rows if r.get("verdict_flipped")]
    flip_rate = len(flips) / len(rows)
    to_buy = sum(1 for r in flips
                 if (r.get("treatment_verdict") or "").upper() in BUY_LIKE
                 and (r.get("control_verdict") or "").upper() not in BUY_LIKE)
    to_caution = sum(1 for r in flips
                     if (r.get("control_verdict") or "").upper() in BUY_LIKE
                     and (r.get("treatment_verdict") or "").upper() not in BUY_LIKE)

    control = _arm_stats(rows, "control_verdict")
    treatment = _arm_stats(rows, "treatment_verdict")

    print(f"Verdict-flip rate: {flip_rate:.1%} ({len(flips)}/{len(rows)})  "
          f"[→caution: {to_caution}, →buy: {to_buy}]")
    matured = sum(1 for r in rows if r.get("ret_4w") is not None)
    print(f"Matured (4w) outcomes available: {matured}/{len(rows)}\n")

    print(f"{'arm':<10} {'n_buys':>7} {'mean_ret_4w':>14} {'95% CI':>18} {'win_rate':>9}")
    for name, s in (("control", control), ("treatment", treatment)):
        ci = (f"±{s['ci_half']:.2%}" if s["ci_half"] is not None else "—")
        print(f"{name:<10} {s['n_buys']:>7} {_fmt(s['mean_ret_4w'], signed=True):>14} "
              f"{ci:>18} {_fmt(s['win_rate_4w']):>9}")

    if control["mean_ret_4w"] is not None and treatment["mean_ret_4w"] is not None:
        delta = treatment["mean_ret_4w"] - control["mean_ret_4w"]
        print(f"\nTreatment − control mean 4w return: {delta:+.2%}")
        print("(Ship gate per PLAN §7.4: delta CI excludes 0, DR-straight-BUY guardrail "
              "holds, conviction calibration flat-or-up.)")

    return {
        "n_rows": len(rows), "flip_rate": flip_rate,
        "control": control, "treatment": treatment, "matured": matured,
    }


if __name__ == "__main__":
    run()
