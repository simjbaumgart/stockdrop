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


def fit_ols(merged: pd.DataFrame, intent_col: str = "intent",
            rr_col: str = "risk_reward_ratio") -> pd.DataFrame:
    """OLS regression of return_1w on every signal we have. Standardised
    coefficients so magnitudes are directly comparable.
    """
    cols_continuous = [rr_col, "drop_percent",
                       "Rank", "quant_score", "sa_score", "ws_score", "perf_6m"]
    sub = merged.dropna(subset=["return_1w"]).copy()
    # Build intent dummies (drop the largest group as reference category)
    if intent_col not in sub.columns:
        return pd.DataFrame()
    intent_dum = pd.get_dummies(sub[intent_col].astype(str), prefix=intent_col, drop_first=True)
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
    if n <= k + 5:  # need enough degrees of freedom
        return pd.DataFrame()
    beta, *_ = np.linalg.lstsq(Xmat, y, rcond=None)
    yhat = Xmat @ beta
    resid = y - yhat
    rss = float(resid @ resid)
    dof = n - k
    sigma2 = rss / dof
    try:
        XtX_inv = np.linalg.inv(Xmat.T @ Xmat)
    except np.linalg.LinAlgError:
        # near-singular (high collinearity / sparse intent dummies); use pseudo-inverse
        XtX_inv = np.linalg.pinv(Xmat.T @ Xmat)
    se = np.sqrt(np.diag(np.abs(sigma2 * XtX_inv)))
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
    parser.add_argument("--council", default="pm", choices=["pm", "dr"],
                        help="Which council's R/R + verdict to analyse "
                             "(pm = AI council intent + risk_reward_ratio, "
                             "dr = deep_research_verdict + deep_research_rr_ratio)")
    args = parser.parse_args()

    sa_path = Path(args.sa_file)
    if not sa_path.exists():
        logger.error("SA file not found: %s", sa_path); sys.exit(1)

    logger.info("Loading cohort + SA + SPY...")
    merged, ds = load_combined(args.start, sa_path)
    spy = spy_returns_at(merged["decision_date"], horizon_days=5)
    cost_total = args.cost_in + args.cost_out
    inv = args.investment

    # Council switch: PM (default) or DR
    if args.council == "pm":
        intent_col = "intent"
        rr_col = "risk_reward_ratio"
        BUY_INTENTS = ["ENTER_NOW", "ENTER_LIMIT"]
        council_label = "PM (AI council)"
    else:
        intent_col = "deep_research_verdict"
        rr_col = "deep_research_rr_ratio"
        BUY_INTENTS = ["BUY", "BUY_LIMIT"]
        council_label = "DR (Deep Research)"
    print(f"COUNCIL: {council_label}  | intent={intent_col}, R/R={rr_col}, "
          f"BUY values={BUY_INTENTS}")

    has_sa = merged["Rank"].notna()
    has_ret = merged["return_1w"].notna()
    overlap = merged[has_sa & has_ret]
    print("=" * 100)
    print(f"COMBINED SIGNAL ANALYSIS — cohort × SA × SPY  [{council_label}]")
    print(f"Cohort with SA + return_1w: {len(overlap)} rows")
    print(f"  {intent_col} breakdown: {dict(overlap[intent_col].dropna().value_counts())}")
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
        (rr_col,             f"{args.council.upper()} R/R"),
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
    res = fit_ols(merged, intent_col=intent_col, rr_col=rr_col)
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

    council_short = args.council.upper()
    is_buy = merged[intent_col].isin(BUY_INTENTS) if intent_col in merged.columns else pd.Series(False, index=merged.index)
    rr_series = merged[rr_col] if rr_col in merged.columns else pd.Series(np.nan, index=merged.index)

    filters = [
        ("ALL cohort with return_1w",
            merged["return_1w"].notna()),
        (f"{council_short} BUY-only (no R/R or SA filter)",
            is_buy),
        (f"{council_short} BUY + R/R > 1.5",
            is_buy & (rr_series > 1.5)),
        (f"{council_short} BUY + R/R > 2.0",
            is_buy & (rr_series > 2.0)),
        (f"{council_short} BUY + WS rating ≥ 4.0",
            is_buy & (merged["ws_score"] >= 4.0)),
        (f"{council_short} BUY + WS rating ≥ 4.5",
            is_buy & (merged["ws_score"] >= 4.5)),
        (f"{council_short} BUY + R/R > 1.5 + WS ≥ 4.0",
            is_buy & (rr_series > 1.5) & (merged["ws_score"] >= 4.0)),
        (f"{council_short} BUY + R/R > 1.5 + WS ≥ 4.5",
            is_buy & (rr_series > 1.5) & (merged["ws_score"] >= 4.5)),
        (f"{council_short} BUY + R/R > 2.0 + WS ≥ 4.0",
            is_buy & (rr_series > 2.0) & (merged["ws_score"] >= 4.0)),
        (f"{council_short} BUY + R/R > 1.5 + SA Analyst ≥ 4.0",
            is_buy & (rr_series > 1.5) & (merged["sa_score"] >= 4.0)),
        (f"{council_short} BUY + R/R > 1.5 + 6M perf > 0",
            is_buy & (rr_series > 1.5) & (merged["perf_6m"] > 0)),
        (f"{council_short} BUY + R/R > 1.5 + WS ≥ 4.0 + 6M > 0",
            is_buy & (rr_series > 1.5) & (merged["ws_score"] >= 4.0) & (merged["perf_6m"] > 0)),
        (f"CONTRARIAN: {council_short} BUY + SA Quant < 3.0",
            is_buy & (merged["quant_score"] < 3.0)),
        (f"CONSENSUS: {council_short} BUY + SA Quant ≥ 4.5",
            is_buy & (merged["quant_score"] >= 4.5)),
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
    print(f"4. CROSS-TAB — SA Quant band × {council_short} verdict → mean 1w return")
    print("-" * 100)
    m = merged.dropna(subset=["return_1w", "quant_score", intent_col]).copy()
    m["sa_band"] = pd.cut(
        m["quant_score"],
        bins=[0, 3.0, 3.5, 4.0, 5.001],
        labels=["<3.0 SA Sell/Hold", "3.0–3.5 SA Hold", "3.5–4.0 SA Buy", "4.0+ SA Strong Buy"],
    )
    pivot_mean = m.pivot_table(index=intent_col, columns="sa_band",
                               values="return_1w", aggfunc="mean", observed=True)
    pivot_n = m.pivot_table(index=intent_col, columns="sa_band",
                            values="return_1w", aggfunc="size", observed=True)
    print("  Mean 1w return (n in parens):")
    print()
    cols = list(pivot_mean.columns)
    if args.council == "pm":
        intents_order = ["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]
    else:
        intents_order = ["BUY", "BUY_LIMIT", "AVOID", "WATCH", "HOLD"]
    intents_seen = [i for i in intents_order if i in pivot_mean.index]
    print(f"  {council_short + ' verdict':<14s} " + "  ".join(f"{str(c):<22s}" for c in cols))
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
    # 5. Fine-grained SA Quant band breakdown (does <3.0 bring little return?)
    # ---------------------------------------------------------------
    print()
    print("5. SA QUANT BAND — finer split (1w return for the WHOLE cohort, irrespective of council intent)")
    print("-" * 100)
    fine = merged.dropna(subset=["return_1w", "quant_score"]).copy()
    fine["fine_band"] = pd.cut(
        fine["quant_score"],
        bins=[0, 2.0, 2.5, 3.0, 3.25, 3.5, 4.0, 4.5, 5.001],
        labels=["<2.0", "2.0–2.5", "2.5–3.0", "3.0–3.25", "3.25–3.5",
                "3.5–4.0", "4.0–4.5", "4.5+"],
    )
    fine_agg = fine.groupby("fine_band", observed=True).agg(
        n=("return_1w", "size"),
        win_rate=("return_1w", lambda x: (x > 0).mean()),
        mean_return=("return_1w", "mean"),
        median_return=("return_1w", "median"),
    ).reset_index()
    print(f"  {'SA Quant band':<14s} {'n':>4s} {'win%':>6s} {'mean':>9s} {'median':>9s}")
    for _, r in fine_agg.iterrows():
        print(f"  {str(r['fine_band']):<14s} {int(r['n']):>4d} {r['win_rate']:>5.1%} "
              f"{r['mean_return']*100:>+7.2f}% {r['median_return']*100:>+7.2f}%")

    # Same split but for BUY-only cohort
    fine_buy = fine[fine[intent_col].isin(BUY_INTENTS)].copy()
    if not fine_buy.empty:
        print()
        print(f"   Same split but {council_short} BUY-only:")
        fine_agg_buy = fine_buy.groupby("fine_band", observed=True).agg(
            n=("return_1w", "size"),
            win_rate=("return_1w", lambda x: (x > 0).mean()),
            mean_return=("return_1w", "mean"),
            median_return=("return_1w", "median"),
        ).reset_index()
        print(f"  {'SA Quant band':<14s} {'n':>4s} {'win%':>6s} {'mean':>9s} {'median':>9s}")
        for _, r in fine_agg_buy.iterrows():
            print(f"  {str(r['fine_band']):<14s} {int(r['n']):>4d} {r['win_rate']:>5.1%} "
                  f"{r['mean_return']*100:>+7.2f}% {r['median_return']*100:>+7.2f}%")

    # ---------------------------------------------------------------
    # Save artifacts
    # ---------------------------------------------------------------
    out_dir = REPO_ROOT / "docs" / "performance" / f"{datetime.now():%Y-%m-%d}-package" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.council}"
    if not corr_df.empty:
        corr_df.to_csv(out_dir / f"combined_signal_correlations{suffix}.csv", index=False)
    if not ols_df.empty:
        ols_df.to_csv(out_dir / f"combined_signal_ols{suffix}.csv", index=False)
    filter_df.to_csv(out_dir / f"combined_signal_filters{suffix}.csv", index=False)
    if not pivot_mean.empty:
        pivot_mean.to_csv(out_dir / f"combined_signal_crosstab_mean{suffix}.csv")
        pivot_n.to_csv(out_dir / f"combined_signal_crosstab_n{suffix}.csv")
    if not fine_agg.empty:
        fine_agg.to_csv(out_dir / f"sa_quant_fine_bands{suffix}.csv", index=False)
    if not fine_buy.empty and not fine_agg_buy.empty:
        fine_agg_buy.to_csv(out_dir / f"sa_quant_fine_bands{suffix}_buy_only.csv", index=False)

    print()
    print(f"Saved: {out_dir}/combined_signal_*.csv")


if __name__ == "__main__":
    main()
