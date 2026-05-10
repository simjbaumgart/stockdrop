import numpy as np
import pandas as pd
import pytest

from app.services.analytics.stats import (
    correlation,
    pairwise_welch,
    recovery_stats,
    rr_by_group,
    top_rr_decisions,
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


def test_rr_by_group_descriptives_and_omnibus():
    rng = np.random.default_rng(seed=11)
    df = pd.DataFrame({
        "intent": ["BUY"] * 30 + ["AVOID"] * 30,
        "risk_reward_ratio": np.concatenate([
            rng.normal(loc=2.5, scale=0.4, size=30),
            rng.normal(loc=0.8, scale=0.3, size=30),
        ]),
    })
    out = rr_by_group(df, "intent", "risk_reward_ratio", min_n=5)
    assert len(out["per_group"]) == 2
    grp_means = {g["group"]: g["mean"] for g in out["per_group"]}
    assert grp_means["BUY"] > grp_means["AVOID"]
    assert out["anova_p"] < 0.001
    assert out["kw_p"] < 0.001
    assert out["pairwise"], "expected pairwise comparisons"
    assert out["pairwise"][0]["welch_p"] < 0.001


def test_rr_by_group_no_difference():
    rng = np.random.default_rng(seed=12)
    df = pd.DataFrame({
        "intent": ["BUY"] * 30 + ["AVOID"] * 30,
        "risk_reward_ratio": rng.normal(loc=1.5, scale=0.4, size=60),
    })
    out = rr_by_group(df, "intent", "risk_reward_ratio", min_n=5)
    # Same population: omnibus p should be > 0.05 most of the time
    assert out["anova_p"] > 0.05
    assert out["kw_p"] > 0.05


def test_rr_by_group_skips_tiny_groups():
    df = pd.DataFrame({
        "intent": ["BUY"] * 10 + ["AVOID"] * 2,
        "risk_reward_ratio": [2.0] * 10 + [0.5, 0.5],
    })
    out = rr_by_group(df, "intent", "risk_reward_ratio", min_n=5)
    # Per-group descriptives are still emitted for tiny groups
    assert len(out["per_group"]) == 2
    # But omnibus is None because only one group meets min_n
    assert out["anova_f"] is None
    assert out["kw_h"] is None


def test_top_rr_decisions_filters_and_sorts():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "symbol": ["A", "B", "C", "D", "E"],
        "decision_date": pd.to_datetime(["2026-02-01"] * 5),
        "recommendation": ["BUY"] * 5,
        "intent": ["ENTER_NOW"] * 5,
        "drop_percent": [-5.0] * 5,
        "price_at_decision": [100.0] * 5,
        "risk_reward_ratio": [3.0, 1.0, np.nan, 5.0, 2.0],
    })
    out = top_rr_decisions(df, "risk_reward_ratio", n=3)
    assert list(out["symbol"]) == ["D", "A", "E"]  # sorted desc
    out_min = top_rr_decisions(df, "risk_reward_ratio", n=10, min_value=2.5)
    assert list(out_min["symbol"]) == ["D", "A"]


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
