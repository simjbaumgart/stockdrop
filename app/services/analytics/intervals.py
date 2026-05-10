"""Confidence-interval helpers for the analytics layer.

All functions are pure and side-effect free. They handle small-sample edge
cases (returning None/NaN rather than crashing) so the caller can blanket-apply
them across the cohort without per-row defensive code.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np
from scipy import stats


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a proportion.

    Robust at small n (much better than the naive normal approximation).
    Returns (low, high) on the [0, 1] scale, or (None, None) when n == 0.
    """
    if n <= 0:
        return (None, None)
    z = float(stats.norm.ppf(1 - alpha / 2))
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    low = max(0.0, center - half)
    high = min(1.0, center + half)
    return (float(low), float(high))


def proportion_se(successes: int, n: int) -> Optional[float]:
    """Binomial standard error of a proportion."""
    if n <= 0:
        return None
    p = successes / n
    return float(np.sqrt(p * (1 - p) / n))


def mean_ci(values: Sequence[float], alpha: float = 0.05) -> dict:
    """t-distribution mean and 95% CI for a sample.

    Returns a dict with `mean`, `se`, `ci_low`, `ci_high`. All None if n < 2.
    """
    arr = np.asarray([v for v in values if v is not None and not np.isnan(v)], dtype=float)
    n = len(arr)
    if n < 2:
        return {
            "mean": float(arr.mean()) if n == 1 else None,
            "se": None, "ci_low": None, "ci_high": None, "n": n,
        }
    mean = float(arr.mean())
    se = float(arr.std(ddof=1) / np.sqrt(n))
    half = float(stats.t.ppf(1 - alpha / 2, df=n - 1)) * se
    return {
        "mean": mean,
        "se": se,
        "ci_low": mean - half,
        "ci_high": mean + half,
        "n": int(n),
    }


def pearson_ci(r: Optional[float], n: int, alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """95% CI for a Pearson correlation via Fisher z-transformation.

    Returns (None, None) for n < 4 or |r| >= 1 (transformation undefined).
    """
    if r is None or n < 4 or abs(r) >= 1.0:
        return (None, None)
    z = float(np.arctanh(r))
    se = 1.0 / np.sqrt(n - 3)
    z_crit = float(stats.norm.ppf(1 - alpha / 2))
    return (float(np.tanh(z - z_crit * se)), float(np.tanh(z + z_crit * se)))


def spearman_ci(rho: Optional[float], n: int, alpha: float = 0.05) -> Tuple[Optional[float], Optional[float]]:
    """Approximate 95% CI for Spearman ρ using Fisher z with Bonett-Wright SE.

    Standard practice for small/moderate n; not exact but widely accepted.
    """
    if rho is None or n < 10 or abs(rho) >= 1.0:
        return (None, None)
    z = float(np.arctanh(rho))
    # Bonett-Wright correction: SE = sqrt((1 + rho^2/2) / (n - 3))
    se = float(np.sqrt((1.0 + rho * rho / 2.0) / (n - 3)))
    z_crit = float(stats.norm.ppf(1 - alpha / 2))
    return (float(np.tanh(z - z_crit * se)), float(np.tanh(z + z_crit * se)))
