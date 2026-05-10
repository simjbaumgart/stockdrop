"""Statistical tests over the enriched cohort.

Pure functions over pandas DataFrames; no I/O. Designed to feed the
offline HTML report so significance results stay reproducible.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

from app.services.analytics.intervals import (
    mean_ci,
    pearson_ci,
    proportion_se,
    spearman_ci,
    wilson_ci,
)


def _bh_adjust(pvalues: List[float]) -> List[float]:
    """Benjamini-Hochberg FDR adjustment. Returns adjusted p-values in input order.

    Implementation note: a couple of scipy releases shipped this under different
    names (`scipy.stats.false_discovery_control` only since 1.11); doing it by hand
    avoids version-dependence and keeps the output predictable.
    """
    n = len(pvalues)
    if n == 0:
        return []
    arr = np.asarray(pvalues, dtype=float)
    order = np.argsort(arr)
    ranked = arr[order]
    adj = ranked * n / (np.arange(n) + 1)
    # enforce monotonicity from the largest down
    for i in range(n - 2, -1, -1):
        adj[i] = min(adj[i], adj[i + 1])
    out = np.empty(n)
    out[order] = np.clip(adj, 0.0, 1.0)
    return out.tolist()


def _cohens_d(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    """Pooled standard-deviation Cohen's d. Returns None if either sample is empty."""
    if len(a) < 2 or len(b) < 2:
        return None
    sa, sb = a.std(ddof=1), b.std(ddof=1)
    pooled = np.sqrt(((len(a) - 1) * sa ** 2 + (len(b) - 1) * sb ** 2) / (len(a) + len(b) - 2))
    if pooled == 0:
        return None
    return float((a.mean() - b.mean()) / pooled)


def pairwise_welch(
    df: pd.DataFrame,
    group_col: str,
    value_col: str,
    min_n: int = 5,
) -> pd.DataFrame:
    """All-pairs comparison of `value_col` distributions across `group_col`.

    Runs Welch's t-test (unequal-variance, robust to small samples) and
    Mann-Whitney U (rank-based, makes no normality assumption). Reports
    Cohen's d effect size and BH-adjusted p-values across the family of
    comparisons.

    Groups smaller than `min_n` are dropped; result is empty if fewer than
    two groups remain.
    """
    cols = ["group_a", "group_b", "n_a", "n_b", "mean_a", "mean_b",
            "diff", "cohen_d", "welch_p", "mwu_p", "welch_p_fdr", "mwu_p_fdr",
            "significant"]
    if df.empty or group_col not in df.columns or value_col not in df.columns:
        return pd.DataFrame(columns=cols)

    sub = df.dropna(subset=[group_col, value_col]).copy()
    grouped = {
        str(g): np.asarray(v[value_col].astype(float).values)
        for g, v in sub.groupby(group_col, dropna=False) if len(v) >= min_n
    }
    if len(grouped) < 2:
        return pd.DataFrame(columns=cols)

    rows = []
    for a, b in combinations(grouped.keys(), 2):
        xa, xb = grouped[a], grouped[b]
        try:
            t = stats.ttest_ind(xa, xb, equal_var=False)
            welch_p = float(t.pvalue)
        except Exception:
            welch_p = float("nan")
        try:
            u = stats.mannwhitneyu(xa, xb, alternative="two-sided")
            mwu_p = float(u.pvalue)
        except Exception:
            mwu_p = float("nan")
        rows.append({
            "group_a": a,
            "group_b": b,
            "n_a": int(len(xa)),
            "n_b": int(len(xb)),
            "mean_a": float(xa.mean()),
            "mean_b": float(xb.mean()),
            "diff": float(xa.mean() - xb.mean()),
            "cohen_d": _cohens_d(xa, xb),
            "welch_p": welch_p,
            "mwu_p": mwu_p,
        })
    out = pd.DataFrame(rows)
    out["welch_p_fdr"] = _bh_adjust(out["welch_p"].fillna(1.0).tolist())
    out["mwu_p_fdr"] = _bh_adjust(out["mwu_p"].fillna(1.0).tolist())
    out["significant"] = (out["welch_p_fdr"] < 0.05) | (out["mwu_p_fdr"] < 0.05)
    return out[cols]


def correlation(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    sample_max: int = 1000,
) -> Dict[str, Any]:
    """Pearson + Spearman correlation between `x_col` and `y_col`.

    Returns a dict with both coefficients, p-values, n, mean/std summaries,
    and a list of (x, y) pairs (capped at `sample_max` rows) so a JS scatter
    plot can render directly from the payload.
    """
    out: Dict[str, Any] = {
        "x_col": x_col, "y_col": y_col, "n": 0,
        "pearson_r": None, "pearson_p": None,
        "pearson_ci_low": None, "pearson_ci_high": None,
        "spearman_rho": None, "spearman_p": None,
        "spearman_ci_low": None, "spearman_ci_high": None,
        "x_mean": None, "x_std": None,
        "y_mean": None, "y_std": None,
        "points": [],
        "regression_slope": None, "regression_intercept": None,
    }
    if df.empty or x_col not in df.columns or y_col not in df.columns:
        return out
    sub = df.dropna(subset=[x_col, y_col])[[x_col, y_col]].astype(float)
    n = len(sub)
    out["n"] = int(n)
    if n < 5:
        return out

    x, y = sub[x_col].values, sub[y_col].values
    out["x_mean"] = float(x.mean()); out["x_std"] = float(x.std(ddof=1))
    out["y_mean"] = float(y.mean()); out["y_std"] = float(y.std(ddof=1))

    try:
        pr = stats.pearsonr(x, y)
        out["pearson_r"] = float(pr.statistic); out["pearson_p"] = float(pr.pvalue)
        lo, hi = pearson_ci(out["pearson_r"], n)
        out["pearson_ci_low"] = lo; out["pearson_ci_high"] = hi
    except Exception:
        pass
    try:
        sp = stats.spearmanr(x, y)
        out["spearman_rho"] = float(sp.statistic); out["spearman_p"] = float(sp.pvalue)
        lo, hi = spearman_ci(out["spearman_rho"], n)
        out["spearman_ci_low"] = lo; out["spearman_ci_high"] = hi
    except Exception:
        pass

    # OLS slope+intercept for the regression line drawn on the scatter
    if x.std(ddof=1) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        out["regression_slope"] = float(slope)
        out["regression_intercept"] = float(intercept)

    # Sub-sample points if we'd otherwise inline thousands of pairs
    if n > sample_max:
        rng = np.random.default_rng(seed=42)
        idx = rng.choice(n, size=sample_max, replace=False)
        sub = sub.iloc[idx]
    out["points"] = [{"x": float(a), "y": float(b)} for a, b in sub.values]
    return out


def recovery_stats(df: pd.DataFrame, group_col: str = "intent") -> pd.DataFrame:
    """Per-group recovery descriptives.

    Columns: group, n_total, n_recovered, recovery_rate, p25/p50/p75/p90 of
    days_to_recover, and post-recovery mean returns at +5/+10/+20 days
    (where the `post_recover_*` columns exist on the enriched frame).
    """
    cols = [
        "group", "n_total", "n_recovered",
        "recovery_rate", "recovery_rate_se",
        "recovery_rate_ci_low", "recovery_rate_ci_high",
        "p25_days", "p50_days", "p75_days", "p90_days",
        "post_recover_5d_mean", "post_recover_5d_se",
        "post_recover_5d_ci_low", "post_recover_5d_ci_high",
        "post_recover_10d_mean", "post_recover_10d_se",
        "post_recover_10d_ci_low", "post_recover_10d_ci_high",
        "post_recover_20d_mean", "post_recover_20d_se",
        "post_recover_20d_ci_low", "post_recover_20d_ci_high",
    ]
    if df.empty or group_col not in df.columns:
        return pd.DataFrame(columns=cols)

    rows = []
    for grp, sub in df.groupby(group_col, dropna=False):
        n_total = len(sub)
        rec = sub.dropna(subset=["days_to_recover"]) if "days_to_recover" in sub.columns else pd.DataFrame()
        n_rec = len(rec)
        days = rec["days_to_recover"].astype(float) if n_rec else pd.Series([], dtype=float)
        rate_low, rate_high = wilson_ci(n_rec, n_total)
        row = {
            "group": str(grp) if grp is not None else "(none)",
            "n_total": int(n_total),
            "n_recovered": int(n_rec),
            "recovery_rate": float(n_rec / n_total) if n_total else None,
            "recovery_rate_se": proportion_se(n_rec, n_total),
            "recovery_rate_ci_low": rate_low,
            "recovery_rate_ci_high": rate_high,
            "p25_days": float(days.quantile(0.25)) if n_rec else None,
            "p50_days": float(days.quantile(0.5)) if n_rec else None,
            "p75_days": float(days.quantile(0.75)) if n_rec else None,
            "p90_days": float(days.quantile(0.9)) if n_rec else None,
        }
        for d in (5, 10, 20):
            col = f"post_recover_{d}d"
            if col in sub.columns:
                ci = mean_ci(sub[col].dropna().tolist())
                row[f"{col}_mean"] = ci["mean"]
                row[f"{col}_se"] = ci["se"]
                row[f"{col}_ci_low"] = ci["ci_low"]
                row[f"{col}_ci_high"] = ci["ci_high"]
            else:
                row[f"{col}_mean"] = None
                row[f"{col}_se"] = None
                row[f"{col}_ci_low"] = None
                row[f"{col}_ci_high"] = None
        rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values("n_total", ascending=False).reset_index(drop=True)
