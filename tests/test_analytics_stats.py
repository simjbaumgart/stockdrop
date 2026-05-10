import numpy as np
import pandas as pd
import pytest

from app.services.analytics.stats import (
    correlation,
    pairwise_welch,
    recovery_stats,
)


def test_pairwise_welch_detects_clear_difference():
    rng = np.random.default_rng(seed=0)
    df = pd.DataFrame({
        "intent": ["BUY"] * 30 + ["AVOID"] * 30,
        "return_4w": np.concatenate([
            rng.normal(loc=0.10, scale=0.05, size=30),
            rng.normal(loc=-0.05, scale=0.05, size=30),
        ]),
    })
    out = pairwise_welch(df, group_col="intent", value_col="return_4w", min_n=5)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["welch_p"] < 0.001
    assert row["mwu_p"] < 0.001
    assert row["cohen_d"] is not None
    assert abs(row["cohen_d"]) > 1.5  # large effect
    assert bool(row["significant"]) is True


def test_pairwise_welch_no_difference():
    rng = np.random.default_rng(seed=1)
    df = pd.DataFrame({
        "intent": ["BUY"] * 30 + ["AVOID"] * 30,
        "return_4w": rng.normal(loc=0.05, scale=0.05, size=60),
    })
    out = pairwise_welch(df, group_col="intent", value_col="return_4w", min_n=5)
    assert len(out) == 1
    row = out.iloc[0]
    # Same distribution: should NOT cross 0.05 reliably
    assert row["welch_p"] > 0.05


def test_pairwise_welch_skips_small_groups():
    df = pd.DataFrame({
        "intent": ["BUY"] * 10 + ["AVOID"] * 2,
        "return_4w": [0.1] * 10 + [0.0, 0.1],
    })
    out = pairwise_welch(df, group_col="intent", value_col="return_4w", min_n=5)
    assert out.empty


def test_correlation_perfect_positive():
    df = pd.DataFrame({
        "rr": np.linspace(0, 5, 50),
        "ret": np.linspace(0, 5, 50) * 0.1,
    })
    out = correlation(df, x_col="rr", y_col="ret")
    assert out["n"] == 50
    assert out["pearson_r"] == pytest.approx(1.0, abs=1e-6)
    assert out["pearson_p"] < 1e-6
    assert out["spearman_rho"] == pytest.approx(1.0, abs=1e-6)
    assert out["regression_slope"] == pytest.approx(0.1, abs=1e-6)
    assert len(out["points"]) == 50


def test_correlation_zero():
    rng = np.random.default_rng(seed=2)
    df = pd.DataFrame({
        "rr": rng.uniform(0, 5, 200),
        "ret": rng.normal(0, 0.1, 200),
    })
    out = correlation(df, x_col="rr", y_col="ret")
    assert out["n"] == 200
    assert abs(out["pearson_r"]) < 0.2  # not perfectly zero but close


def test_correlation_handles_few_points():
    df = pd.DataFrame({"rr": [1, 2, 3], "ret": [0.1, 0.2, 0.3]})
    out = correlation(df, x_col="rr", y_col="ret")
    assert out["n"] == 3
    assert out["pearson_r"] is None  # below n=5 threshold


def test_recovery_stats_counts_and_percentiles():
    df = pd.DataFrame({
        "intent": ["BUY"] * 4 + ["AVOID"] * 3,
        "days_to_recover": [2.0, 5.0, 10.0, 20.0, np.nan, 1.0, 3.0],
        "post_recover_5d": [0.05, 0.10, np.nan, 0.02, np.nan, 0.0, 0.01],
    })
    out = recovery_stats(df, group_col="intent")
    assert len(out) == 2
    buy = out[out["group"] == "BUY"].iloc[0]
    assert buy["n_total"] == 4
    assert buy["n_recovered"] == 4
    assert buy["recovery_rate"] == 1.0
    assert buy["p50_days"] == 7.5
    assert buy["post_recover_5d_mean"] == pytest.approx((0.05 + 0.10 + 0.02) / 3)
    avoid = out[out["group"] == "AVOID"].iloc[0]
    assert avoid["n_total"] == 3
    assert avoid["n_recovered"] == 2
    assert avoid["recovery_rate"] == pytest.approx(2 / 3)
