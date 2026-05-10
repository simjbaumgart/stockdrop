import math

import numpy as np
import pytest

from app.services.analytics.intervals import (
    mean_ci,
    pearson_ci,
    proportion_se,
    spearman_ci,
    wilson_ci,
)


def test_wilson_ci_endpoints():
    # All successes: upper bound at 1, lower < 1
    lo, hi = wilson_ci(10, 10)
    assert hi == pytest.approx(1.0, abs=1e-6)
    assert lo < 1.0

    # 50/50 with n=20 should bracket 0.5
    lo, hi = wilson_ci(10, 20)
    assert lo < 0.5 < hi
    # Wilson is tighter than ±0.22 for normal approx
    assert (hi - lo) < 0.5

    # n=0 returns (None, None)
    assert wilson_ci(0, 0) == (None, None)


def test_wilson_ci_small_n():
    # n=3 with 3 wins: CI doesn't collapse to (1,1) — Wilson keeps a non-trivial lower bound
    lo, hi = wilson_ci(3, 3)
    assert hi == pytest.approx(1.0, abs=1e-6)
    assert lo < 0.7  # honest about uncertainty at small n


def test_proportion_se():
    assert proportion_se(0, 0) is None
    assert proportion_se(50, 100) == pytest.approx(math.sqrt(0.25 / 100))


def test_mean_ci_basic():
    rng = np.random.default_rng(seed=7)
    sample = rng.normal(loc=0.05, scale=0.10, size=200)
    out = mean_ci(sample)
    assert out["n"] == 200
    assert out["mean"] == pytest.approx(sample.mean())
    # CI must bracket the SAMPLE mean by construction
    assert out["ci_low"] < out["mean"] < out["ci_high"]
    # SE = std/sqrt(n)
    assert out["se"] == pytest.approx(sample.std(ddof=1) / math.sqrt(200), rel=1e-6)
    # 95% CI half-width should be roughly 1.96*SE for n=200 (df=199)
    half = (out["ci_high"] - out["ci_low"]) / 2
    assert half == pytest.approx(1.972 * out["se"], rel=0.01)


def test_mean_ci_handles_small_samples():
    out = mean_ci([])
    assert out["n"] == 0 and out["mean"] is None
    out = mean_ci([0.1])
    assert out["n"] == 1 and out["mean"] == pytest.approx(0.1)
    assert out["se"] is None and out["ci_low"] is None


def test_pearson_ci_perfect_corr_returns_none():
    # |r|=1 makes Fisher z undefined
    assert pearson_ci(1.0, 100) == (None, None)
    assert pearson_ci(-1.0, 100) == (None, None)


def test_pearson_ci_brackets():
    # With a moderate r and large n, CI should be tight and centered near r
    lo, hi = pearson_ci(0.5, 200)
    assert lo is not None and hi is not None
    assert lo < 0.5 < hi
    assert (hi - lo) < 0.3  # tight at n=200


def test_pearson_ci_small_n_returns_none():
    assert pearson_ci(0.5, 3) == (None, None)


def test_spearman_ci_small_n_returns_none():
    assert spearman_ci(0.5, 5) == (None, None)


def test_spearman_ci_basic():
    lo, hi = spearman_ci(0.6, 100)
    assert lo is not None and hi is not None
    assert lo < 0.6 < hi
