"""Combined-signal analysis: PM R/R + PM recommendation + Seeking Alpha scores.

Tests three things on the cohort × SA-Quant join:

1. **Multivariate OLS**  — does each signal add independent predictive value
   for `return_1w`, or are they redundant once the others are in the model?

2. **Composite filters** — a portfolio simulation under 12 layered filter
   combinations of the three signal families. Reports n, ROI, alpha-vs-SPY.

3. **Cross-tabs**       — SA score band × PM intent → mean realized return,
   so you can see which combinations of council agreement / disagreement
   carry the most signal.

Inputs
------
  • Cohort built by app/services/analytics/payload.compute_dataset
  • SA_Quant_Ranked_Clean.csv (configurable path)

Outputs
-------
  • data/combined_signal_filters.csv — every filter's portfolio aggregate
  • data/combined_signal_ols.csv     — OLS coefficient table
  • data/combined_signal_crosstab.csv — SA-band × PM-intent table
  • Stdout: ranked filter performance, OLS summary, cross-tab matrix
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402
from app.services.analytics.price_cache import get_bars  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("combined")

DEFAULT_SA_PATH = Path(
    "/Users/simonbaumgart/Documents/Claude/Projects/"
    "Investment Ideas and Portfolio/SA_Quant_Ranked_Clean.csv"
)


def parse_rating(s):
    if pd.isna(s):
        return np.nan
    m = re.search(r"([0-9]+\.?[0-9]*)\s*$", str(s))
    return float(m.group(1)) if m else np.nan


def load_combined(start_date: str, sa_path: Path) -> pd.DataFrame:
    ds = compute_dataset(start_date=start_date)
    df = ds["enriched"].copy()
    df["sym"] = df["symbol"].astype(str).str.upper().str.strip()

    sa = pd.read_csv(sa_path)
    sa["sym"]         = sa["Symbol"].astype(str).str.upper().str.strip()
    sa["quant_score"] = sa["Quant Rating"].apply(parse_rating)
    sa["sa_score"]    = sa["SA Analyst Ratings"].apply(parse_rating)
    sa["ws_score"]    = sa["Wall Street Ratings"].apply(parse_rating)
    sa["perf_6m"]     = pd.to_numeric(sa["6M Perf"], errors="coerce")
    merged = df.merge(
        sa[["sym", "Rank", "quant_score", "sa_score", "ws_score", "perf_6m"]],
        on="sym", how="left",
    )
    return merged, ds


def fit_ols(merged: pd.DataFrame) -> pd.DataFrame:
    """OLS regression of return_1w on every signal we have. Standardised
    coefficients so magnitudes are directly comparable.
    """
    cols_continuous = ["risk_reward_ratio", "drop_percent",
                       "Rank", "quant_score", "sa_score", "ws_score", "perf_6m"]
    sub = merged.dropna(subset=["return_1w"]).copy()
    # Build intent dummies (drop AVOID as reference category)
    intent_dum = pd.get_dummies(sub["intent"], prefix="intent", drop_first=True)
    X_parts = [sub[cols_continuous], intent_dum]
    X = pd.concat(X_parts, axis=1)
    # Keep rows where ALL predictors are present
    full_idx = X.dropna().index
    Xf = X.loc[full_idx].astype(float).copy()
    y = sub.loc[full_idx, "return_1w"].astype(float).values
    # Standardize numeric columns (z-score) for comparable beta magnitudes
    z_cols = [c for c in Xf.columns if c in cols_continuous]
    Xf_z = Xf.copy()
    for c in z_cols:
        s = Xf_z[c].std(ddof=0)
        if s > 0:
            Xf_z[c] = (Xf_z[c] - Xf_z[c].mean()) / s
    # OLS via numpy (with intercept)
    Xmat = np.column_stack([np.ones(len(Xf_z)), Xf_z.values])
    n, k = Xmat.shape
    if n <= k:
        return pd.DataFrame()
    beta, *_ = np.linalg.lstsq(Xmat, y, rcond=None)
    yhat = Xmat @ beta
    resid = y - yhat
    rss = float(resid @ resid)
    dof = n - k
    sigma2 = rss / dof
    XtX_inv = np.linalg.inv(Xmat.T @ Xmat)
    se = np.sqrt(np.diag(sigma2 * XtX_inv))
    tvals = beta / se
    pvals = 2 * (1 - scipy_stats.t.cdf(np.abs(tvals), df=dof))
    r2 = 1 - rss / ((y - y.mean()) @ (y - y.mean()))
    feature_names = ["intercept"] + list(Xf_z.columns)
    return pd.DataFrame({
        "feature": feature_names,
        "beta_std": beta,
        "se": se,
        "t": tvals,
        "p": pvals,
    }), r2, n


def spy_returns_at(dates: pd.Series, horizon_days: int = 5) -> Dict[pd.Timestamp, Optional[float]]:
    if dates.empty:
        return {}
    start = pd.Timestamp(dates.min())
    end = pd.Timestamp(dates.max()) + pd.Timedelta(days=horizon_days * 3 + 5)
    spy = get_bars("SPY", start=start, end=end)
    if spy is None or spy.empty:
        return {}
    spy = spy.sort_index()
    out: Dict[pd.Timestamp, Optional[float]] = {}
    for d in dates.dropna().unique():
        d = pd.Timestamp(d).normalize()
        forward = spy.loc[spy.index >= d]
        if forward.empty or len(forward) <= horizon_days:
            out[d] = None
        else:
            close_at = float(forward["Close"].iloc[0])
            close_after = float(forward["Close"].iloc[horizon_days])
            out[d] = (close_after - close_at) / close_at if close_at > 0 else None
    return out


def simulate_portfolio(
    df: pd.DataFrame, mask: pd.Series,
    investment: float, cost_total: float,
    spy_returns: Dict[pd.Timestamp, Optional[float]],
) -> Dict:
    sub = df[mask & df["return_1w"].notna()].copy()
    n = len(sub)
    if n == 0:
        return {"n": 0, "invested": 0, "net_eur": 0, "spy_net_eur": 0,
                "alpha_eur": 0, "roi": 0, "alpha_roi": 0, "win_rate": 0}
    sub["gross"] = investment * sub["return_1w"]
    sub["net"]   = sub["gross"] - cost_total
    sub["spy_r"] = sub["decision_date"].map(
        lambda d: spy_returns.get(pd.Timestamp(d).normalize())
    )
    spy_valid = sub.dropna(subset=["spy_r"])
    if not spy_valid.empty:
        spy_valid = spy_valid.copy()
        spy_valid["spy_net"] = investment * spy_valid["spy_r"] - cost_total
        spy_net_total = float(spy_valid["spy_net"].sum())
        n_spy = len(spy_valid)
    else:
        spy_net_total = 0.0
        n_spy = 0
    invested = n * investment
    net_total = float(sub["net"].sum())
    alpha = net_total - spy_net_total if n_spy == n else net_total - (spy_net_total * n / max(n_spy, 1))
    return {
        "n": n,
        "invested": invested,
        "net_eur": net_total,
        "spy_net_eur": spy_net_total,
        "alpha_eur": alpha,
        "roi": net_total / invested,
        "alpha_roi": alpha / invested,
        "win_rate": float((sub["net"] > 0).mean()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sa-file", default=str(DEFAULT_SA_PATH))
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--investment", type=float, default=750.0)
    parser.add_argument("--cost-in", type=float, default=3.0)
    parser.add_argument("--cost-out", type=float, default=3.0)
    args = parser.parse_args()

    sa_path = Path(args.sa_file)
    if not sa_path.exists():
        logger.error("SA file not found: %s", sa_path); sys.exit(1)

    logger.info("Loading cohort + SA + SPY...")
    merged, ds = load_combined(args.start, sa_path)
    spy = spy_returns_at(merged["decision_date"], horizon_days=5)
    cost_total = args.cost_in + args.cost_out
    inv = args.investment

    has_sa = merged["Rank"].notna()
    has_ret = merged["return_1w"].notna()
    overlap = merged[has_sa & has_ret]
    print("=" * 100)
    print(f"COMBINED SIGNAL ANALYSIS — cohort × SA × SPY")
    print(f"Cohort with SA + return_1w: {len(overlap)} rows")
    print(f"  PM intent breakdown: {dict(overlap['intent'].value_counts())}")
    print("=" * 100)

    # ---------------------------------------------------------------
    # 1. Pairwise correlations of every signal with return_1w
    # ---------------------------------------------------------------
    print()
    print("1. CORRELATIONS — every signal vs return_1w")
    print("-" * 100)
    print(f"  {'signal':<28s} {'n':>4s} {'Pearson r':>11s} {'p':>7s} "
          f"{'Spearman ρ':>12s} {'p':>7s}")
    pairs = [
        ("risk_reward_ratio", "PM R/R"),
        ("drop_percent",     "Drop %"),
        ("Rank",             "SA Rank"),
        ("quant_score",      "SA Quant"),
        ("sa_score",         "SA Analyst"),
        ("ws_score",         "Wall Street"),
        ("perf_6m",          "Past 6M perf"),
    ]
    corr_rows = []
    for col, label in pairs:
        sub = merged.dropna(subset=[col, "return_1w"])
        if len(sub) < 5:
            continue
        pr = scipy_stats.pearsonr(sub[col], sub["return_1w"])
        sp = scipy_stats.spearmanr(sub[col], sub["return_1w"])
        flag = "  ✓" if min(pr.pvalue, sp.pvalue) < 0.05 else ""
        corr_rows.append({"signal": label, "n": len(sub),
                          "pearson_r": pr.statistic, "pearson_p": pr.pvalue,
                          "spearman_rho": sp.statistic, "spearman_p": sp.pvalue})
        print(f"  {label:<28s} {len(sub):>4d} {pr.statistic:>+10.3f} {pr.pvalue:>7.3f} "
              f"{sp.statistic:>+11.3f} {sp.pvalue:>7.3f}{flag}")
    corr_df = pd.DataFrame(corr_rows)

    # ---------------------------------------------------------------
    # 2. Multivariate OLS — does each signal add independent value?
    # ---------------------------------------------------------------
    print()
    print("2. MULTIVARIATE OLS — return_1w ~ all signals (standardized betas)")
    print("-" * 100)
    print("   Each beta is the change in return_1w (decimal) per +1 SD of that")
    print("   signal, holding others constant. p < 0.05 means \"adds independent")
    print("   predictive value beyond what the other signals already capture\".")
    print()
    res = fit_ols(merged)
    if isinstance(res, tuple):
        ols_df, r2, n_ols = res
        ols_df = ols_df.iloc[1:]  # drop intercept for display
        ols_df["sig"] = ols_df["p"].apply(lambda p: "  ✓" if p < 0.05 else "")
        print(f"  n={n_ols}, R² = {r2:.4f}")
        print()
        print(f"  {'feature':<26s} {'β (std)':>10s} {'SE':>9s} {'t':>7s} {'p':>7s}")
        for _, r in ols_df.sort_values("p").iterrows():
            print(f"  {r['feature']:<26s} {r['beta_std']:>+9.4f} {r['se']:>9.4f} "
                  f"{r['t']:>+7.2f} {r['p']:>7.3f}{r['sig']}")
    else:
        ols_df = pd.DataFrame()
        r2, n_ols = None, 0
        print("  (insufficient data for OLS)")

    # ---------------------------------------------------------------
    # 3. Composite filters — portfolio simulation
    # ---------------------------------------------------------------
    print()
    print("3. COMPOSITE FILTERS — same simulation as portfolio_sim.py")
    print(f"   €{inv:.0f}/trade, €{cost_total:.0f} round-trip, hold 1w, exit at close")
    print("-" * 100)

    BUY_INTENTS = ["ENTER_NOW", "ENTER_LIMIT"]

    filters = [
        ("ALL cohort with return_1w",
            merged["return_1w"].notna()),
        ("PM BUY-only (no R/R or SA filter)",
            merged["intent"].isin(BUY_INTENTS)),
        ("PM BUY + R/R > 1.5",
            merged["intent"].isin(BUY_INTENTS) & (merged["risk_reward_ratio"] > 1.5)),
        ("PM BUY + R/R > 2.0",
            merged["intent"].isin(BUY_INTENTS) & (merged["risk_reward_ratio"] > 2.0)),
        ("PM BUY + WS rating ≥ 4.0",
            merged["intent"].isin(BUY_INTENTS) & (merged["ws_score"] >= 4.0)),
        ("PM BUY + WS rating ≥ 4.5",
            merged["intent"].isin(BUY_INTENTS) & (merged["ws_score"] >= 4.5)),
        ("PM BUY + R/R > 1.5 + WS ≥ 4.0",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 1.5)
            & (merged["ws_score"] >= 4.0)),
        ("PM BUY + R/R > 1.5 + WS ≥ 4.5",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 1.5)
            & (merged["ws_score"] >= 4.5)),
        ("PM BUY + R/R > 2.0 + WS ≥ 4.0",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 2.0)
            & (merged["ws_score"] >= 4.0)),
        ("PM BUY + R/R > 1.5 + SA Analyst ≥ 4.0",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 1.5)
            & (merged["sa_score"] >= 4.0)),
        ("PM BUY + R/R > 1.5 + 6M perf > 0",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 1.5)
            & (merged["perf_6m"] > 0)),
        ("PM BUY + R/R > 1.5 + WS ≥ 4.0 + 6M > 0",
            merged["intent"].isin(BUY_INTENTS)
            & (merged["risk_reward_ratio"] > 1.5)
            & (merged["ws_score"] >= 4.0)
            & (merged["perf_6m"] > 0)),
        ("CONTRARIAN: PM BUY + SA Quant < 3.0",
            merged["intent"].isin(BUY_INTENTS) & (merged["quant_score"] < 3.0)),
        ("CONSENSUS: PM BUY + SA Quant ≥ 4.5",
            merged["intent"].isin(BUY_INTENTS) & (merged["quant_score"] >= 4.5)),
    ]
    rows = []
    for name, mask in filters:
        agg = simulate_portfolio(merged, mask, inv, cost_total, spy)
        rows.append({"filter": name, **agg})
    filter_df = pd.DataFrame(rows)
    print(f"  {'filter':<46s} {'n':>3s} {'invested':>11s} "
          f"{'net €':>11s} {'spy €':>11s} {'alpha €':>11s} "
          f"{'ROI':>7s} {'α %':>7s} {'win%':>6s}")
    # Sort by alpha € so the best comes first
    filter_df_sorted = filter_df.sort_values("alpha_eur", ascending=False)
    for _, r in filter_df_sorted.iterrows():
        marker = ""
        if r["n"] >= 5 and r["alpha_eur"] == filter_df["alpha_eur"].max():
            marker = "  ◀ MAX α"
        print(f"  {r['filter']:<46s} {int(r['n']):>3d} "
              f"€{r['invested']:>10,.0f} €{r['net_eur']:>+10,.2f} "
              f"€{r['spy_net_eur']:>+10,.2f} €{r['alpha_eur']:>+10,.2f} "
              f"{r['roi']:>+7.2%} {r['alpha_roi']:>+6.2%} {r['win_rate']:>5.1%}"
              f"{marker}")

    # ---------------------------------------------------------------
    # 4. Cross-tab: SA Quant band × PM intent
    # ---------------------------------------------------------------
    print()
    print("4. CROSS-TAB — SA Quant band × PM intent → mean 1w return")
    print("-" * 100)
    m = merged.dropna(subset=["return_1w", "quant_score"]).copy()
    m["sa_band"] = pd.cut(
        m["quant_score"],
        bins=[0, 3.0, 3.5, 4.0, 5.001],
        labels=["<3.0 SA Sell/Hold", "3.0–3.5 SA Hold", "3.5–4.0 SA Buy", "4.0+ SA Strong Buy"],
    )
    pivot_mean = m.pivot_table(index="intent", columns="sa_band",
                               values="return_1w", aggfunc="mean", observed=True)
    pivot_n = m.pivot_table(index="intent", columns="sa_band",
                            values="return_1w", aggfunc="size", observed=True)
    print("  Mean 1w return (n in parens):")
    print()
    cols = list(pivot_mean.columns)
    intents_order = ["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]
    intents_seen = [i for i in intents_order if i in pivot_mean.index]
    print(f"  {'PM intent':<14s} " + "  ".join(f"{str(c):<22s}" for c in cols))
    for intent in intents_seen:
        cells = []
        for c in cols:
            mean = pivot_mean.loc[intent, c] if c in pivot_mean.columns else np.nan
            n = pivot_n.loc[intent, c] if c in pivot_n.columns else 0
            if pd.isna(mean):
                cells.append(f"{'—':<22s}")
            else:
                sign = "+" if mean >= 0 else ""
                cells.append(f"{sign}{mean*100:5.2f}% (n={int(n)})".ljust(22))
        print(f"  {intent:<14s} " + "  ".join(cells))

    # ---------------------------------------------------------------
    # Save artifacts
    # ---------------------------------------------------------------
    out_dir = REPO_ROOT / "docs" / "performance" / f"{datetime.now():%Y-%m-%d}-package" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not corr_df.empty:
        corr_df.to_csv(out_dir / "combined_signal_correlations.csv", index=False)
    if not ols_df.empty:
        ols_df.to_csv(out_dir / "combined_signal_ols.csv", index=False)
    filter_df.to_csv(out_dir / "combined_signal_filters.csv", index=False)
    if not pivot_mean.empty:
        pivot_mean.to_csv(out_dir / "combined_signal_crosstab_mean.csv")
        pivot_n.to_csv(out_dir / "combined_signal_crosstab_n.csv")

    print()
    print(f"Saved: {out_dir}/combined_signal_*.csv")


if __name__ == "__main__":
    main()
