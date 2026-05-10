"""Portfolio simulation: filter by R/R threshold, hold for N trading days, compute P&L.

Now includes:
  • SPY paired benchmark per trade (€X invested in SPY over the same window).
  • R/R cutoff sweep — find the threshold that maximizes net P&L / ROI / alpha.
  • Investment-size sweep — show how per-trade capital changes the picture.

Usage:
    ./venv/bin/python scripts/analysis/portfolio_sim.py
    ./venv/bin/python scripts/analysis/portfolio_sim.py --rr-min 2.0 --horizon 2w
    ./venv/bin/python scripts/analysis/portfolio_sim.py --no-sweep    # skip optimization

Defaults match the user-requested simulation (PM R/R > 1.5, 1-week hold,
€750/trade, €6 round-trip cost).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402
from app.services.analytics.price_cache import get_bars  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("portfolio_sim")

HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}


def _spy_returns_by_date(
    cohort_dates: pd.Series,
    horizon_days: int,
) -> Dict[pd.Timestamp, Optional[float]]:
    """Look up SPY's return over `horizon_days` trading days starting at each date.

    Uses the existing parquet cache via price_cache.get_bars.
    """
    if cohort_dates.empty:
        return {}
    start = pd.Timestamp(cohort_dates.min())
    end = pd.Timestamp(cohort_dates.max()) + pd.Timedelta(days=horizon_days * 3 + 5)
    spy = get_bars("SPY", start=start, end=end)
    if spy is None or spy.empty:
        return {}
    spy = spy.sort_index()

    out: Dict[pd.Timestamp, Optional[float]] = {}
    for d in cohort_dates.dropna().unique():
        d = pd.Timestamp(d).normalize()
        forward = spy.loc[spy.index >= d]
        if forward.empty or len(forward) <= horizon_days:
            out[d] = None
            continue
        try:
            close_at = float(forward["Close"].iloc[0])
            close_after = float(forward["Close"].iloc[horizon_days])
        except Exception:
            out[d] = None
            continue
        if close_at <= 0:
            out[d] = None
        else:
            out[d] = (close_after - close_at) / close_at
    return out


def run_simulation(
    df: pd.DataFrame,
    rr_col: str,
    rr_min: float,
    horizon: str,
    investment: float,
    cost_in: float,
    cost_out: float,
    intent_only: bool = False,
    min_price: Optional[float] = None,
    spy_returns: Optional[Dict[pd.Timestamp, Optional[float]]] = None,
) -> Tuple[pd.DataFrame, Dict]:
    """Apply trade filter and compute per-row + aggregate P&L (with SPY benchmark)."""
    cost_total = cost_in + cost_out
    horizon_col = f"return_{horizon}"

    if rr_col not in df.columns:
        raise ValueError(f"R/R column {rr_col!r} not in cohort")
    if horizon_col not in df.columns:
        raise ValueError(f"Horizon column {horizon_col!r} not in cohort")

    sub = df[(df[rr_col] > rr_min) & df[horizon_col].notna()].copy()
    if intent_only:
        sub = sub[sub["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]
    if min_price is not None:
        sub = sub[sub["price_at_decision"] >= min_price]

    sub = sub.sort_values(rr_col, ascending=False).reset_index(drop=True)

    sub["investment_eur"] = investment
    sub["gross_pnl_eur"] = investment * sub[horizon_col]
    sub["cost_in_eur"] = cost_in
    sub["cost_out_eur"] = cost_out
    sub["round_trip_cost_eur"] = cost_total
    sub["net_pnl_eur"] = sub["gross_pnl_eur"] - cost_total
    sub["net_return_pct"] = sub["net_pnl_eur"] / investment

    # Paired SPY benchmark
    if spy_returns is not None and not sub.empty:
        sub["spy_return"] = sub["decision_date"].map(
            lambda d: spy_returns.get(pd.Timestamp(d).normalize())
        )
        sub["spy_pnl_eur"] = investment * sub["spy_return"]
        # SPY benchmark assumes the same round-trip cost (a fair like-for-like)
        sub["spy_net_pnl_eur"] = sub["spy_pnl_eur"] - cost_total
        sub["alpha_pnl_eur"] = sub["net_pnl_eur"] - sub["spy_net_pnl_eur"]
    else:
        sub["spy_return"] = np.nan
        sub["spy_pnl_eur"] = np.nan
        sub["spy_net_pnl_eur"] = np.nan
        sub["alpha_pnl_eur"] = np.nan

    n = len(sub)
    total_invested = n * investment
    total_gross = float(sub["gross_pnl_eur"].sum()) if n else 0.0
    total_cost = float(sub["round_trip_cost_eur"].sum()) if n else 0.0
    total_net = float(sub["net_pnl_eur"].sum()) if n else 0.0
    win_gross = int((sub["gross_pnl_eur"] > 0).sum()) if n else 0
    win_net = int((sub["net_pnl_eur"] > 0).sum()) if n else 0

    # SPY aggregate (skipping rows with no SPY data)
    spy_valid = sub.dropna(subset=["spy_pnl_eur"]) if "spy_pnl_eur" in sub else pd.DataFrame()
    n_spy = len(spy_valid)
    total_spy_gross = float(spy_valid["spy_pnl_eur"].sum()) if n_spy else 0.0
    total_spy_net = float(spy_valid["spy_net_pnl_eur"].sum()) if n_spy else 0.0
    total_alpha = float(spy_valid["alpha_pnl_eur"].sum()) if n_spy else 0.0

    agg = {
        "n_trades": n,
        "total_invested_eur": total_invested,
        "total_gross_pnl_eur": total_gross,
        "total_cost_eur": total_cost,
        "total_net_pnl_eur": total_net,
        "final_value_eur": total_invested + total_net,
        "roi": (total_net / total_invested) if total_invested else 0.0,
        "win_rate_gross": (win_gross / n) if n else 0.0,
        "win_rate_net": (win_net / n) if n else 0.0,
        "mean_net_return_pct": float(sub["net_return_pct"].mean()) if n else 0.0,
        "median_net_return_pct": float(sub["net_return_pct"].median()) if n else 0.0,
        # SPY benchmark
        "n_spy_paired": n_spy,
        "total_spy_gross_pnl_eur": total_spy_gross,
        "total_spy_net_pnl_eur": total_spy_net,
        "spy_roi": (total_spy_net / (n_spy * investment)) if n_spy else 0.0,
        "total_alpha_eur": total_alpha,
        "alpha_roi": (total_alpha / (n_spy * investment)) if n_spy else 0.0,
    }
    return sub, agg


def _print_simulation(label: str, df: pd.DataFrame, agg: Dict,
                      horizon_col: str, rr_col: str) -> None:
    print("=" * 110)
    print(f"  {label}")
    print("=" * 110)
    if df.empty:
        print("  No trades match the filter.")
        return
    cols = ["symbol", "decision_date", "intent", "recommendation", rr_col,
            "price_at_decision", horizon_col, "spy_return",
            "net_pnl_eur", "spy_net_pnl_eur", "alpha_pnl_eur"]
    cols = [c for c in cols if c in df.columns]
    disp = df[cols].copy()
    disp[horizon_col] = disp[horizon_col].apply(lambda v: f"{v * 100:+.2f}%")
    if "spy_return" in disp.columns:
        disp["spy_return"] = disp["spy_return"].apply(
            lambda v: "" if pd.isna(v) else f"{v * 100:+.2f}%"
        )
    for c in ("net_pnl_eur", "spy_net_pnl_eur", "alpha_pnl_eur"):
        if c in disp.columns:
            disp[c] = disp[c].apply(lambda v: "" if pd.isna(v) else f"€{v:+.2f}")
    disp["price_at_decision"] = disp["price_at_decision"].apply(
        lambda v: f"${v:.4f}" if v < 1 else f"${v:.2f}"
    )
    rename = {
        "symbol": "symbol", "decision_date": "date",
        rr_col: "R/R", horizon_col: "return", "price_at_decision": "entry",
        "spy_return": "spy_ret", "net_pnl_eur": "net €",
        "spy_net_pnl_eur": "spy net €", "alpha_pnl_eur": "alpha €",
    }
    disp.columns = [rename.get(c, c) for c in disp.columns]
    print(disp.to_string(index=False))
    print()
    print(f"  Number of trades:               {agg['n_trades']}")
    print(f"  Total capital deployed:         €{agg['total_invested_eur']:>14,.2f}")
    print(f"  Strategy NET P&L:               €{agg['total_net_pnl_eur']:>+14,.2f}    "
          f"(ROI {agg['roi']:+.2%})")
    print(f"  SPY NET P&L (same dates+sizes): €{agg['total_spy_net_pnl_eur']:>+14,.2f}    "
          f"(ROI {agg['spy_roi']:+.2%})")
    print(f"  Strategy ALPHA vs SPY:          €{agg['total_alpha_eur']:>+14,.2f}    "
          f"({agg['alpha_roi']:+.2%})")
    print(f"  Win rate (net):                 {agg['win_rate_net']:.1%}")
    print(f"  Mean / median net return:       {agg['mean_net_return_pct']:+.2%} / "
          f"{agg['median_net_return_pct']:+.2%}")


def sweep_rr_cutoff(
    df: pd.DataFrame, rr_col: str, horizon: str,
    investment: float, cost_in: float, cost_out: float,
    spy_returns: Dict, thresholds: List[float],
) -> pd.DataFrame:
    rows = []
    for t in thresholds:
        _, agg = run_simulation(
            df, rr_col=rr_col, rr_min=t, horizon=horizon,
            investment=investment, cost_in=cost_in, cost_out=cost_out,
            spy_returns=spy_returns,
        )
        rows.append({
            "rr_threshold": t,
            "n_trades": agg["n_trades"],
            "total_invested_eur": agg["total_invested_eur"],
            "strategy_net_eur": agg["total_net_pnl_eur"],
            "spy_net_eur": agg["total_spy_net_pnl_eur"],
            "alpha_eur": agg["total_alpha_eur"],
            "roi": agg["roi"],
            "spy_roi": agg["spy_roi"],
            "alpha_roi": agg["alpha_roi"],
            "win_rate_net": agg["win_rate_net"],
        })
    return pd.DataFrame(rows)


def per_verdict_breakdown(
    df: pd.DataFrame, rr_col: str, rr_min: float, horizon: str,
    investment: float, cost_in: float, cost_out: float,
    spy_returns: Dict,
) -> pd.DataFrame:
    """Decompose the strategy's P&L by intent within the chosen R/R band."""
    rows = []
    for intent in ("ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"):
        sub = df[df["intent"] == intent]
        _, agg = run_simulation(
            sub, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
            investment=investment, cost_in=cost_in, cost_out=cost_out,
            spy_returns=spy_returns,
        )
        rows.append({
            "intent": intent,
            "n_trades": agg["n_trades"],
            "total_invested_eur": agg["total_invested_eur"],
            "strategy_net_eur": agg["total_net_pnl_eur"],
            "spy_net_eur": agg["total_spy_net_pnl_eur"],
            "alpha_eur": agg["total_alpha_eur"],
            "roi": agg["roi"],
            "alpha_roi": agg["alpha_roi"],
            "win_rate_net": agg["win_rate_net"],
        })
    # Append a TOTAL row
    _, total_agg = run_simulation(
        df, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
        investment=investment, cost_in=cost_in, cost_out=cost_out,
        spy_returns=spy_returns,
    )
    rows.append({
        "intent": "TOTAL",
        "n_trades": total_agg["n_trades"],
        "total_invested_eur": total_agg["total_invested_eur"],
        "strategy_net_eur": total_agg["total_net_pnl_eur"],
        "spy_net_eur": total_agg["total_spy_net_pnl_eur"],
        "alpha_eur": total_agg["total_alpha_eur"],
        "roi": total_agg["roi"],
        "alpha_roi": total_agg["alpha_roi"],
        "win_rate_net": total_agg["win_rate_net"],
    })
    return pd.DataFrame(rows)


def sweep_drop_one_verdict(
    df: pd.DataFrame, rr_col: str, rr_min: float, horizon: str,
    investment: float, cost_in: float, cost_out: float,
    spy_returns: Dict,
) -> pd.DataFrame:
    """For each verdict G, run the strategy without G; compare to baseline + BUY-only."""
    intents = ("ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL")
    _, base_agg = run_simulation(
        df, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
        investment=investment, cost_in=cost_in, cost_out=cost_out,
        spy_returns=spy_returns,
    )
    base_alpha = base_agg["total_alpha_eur"]
    base_alpha_roi = base_agg["alpha_roi"]

    rows = [{
        "config": "ALL (baseline)",
        "n_trades": base_agg["n_trades"],
        "total_invested_eur": base_agg["total_invested_eur"],
        "strategy_net_eur": base_agg["total_net_pnl_eur"],
        "spy_net_eur": base_agg["total_spy_net_pnl_eur"],
        "alpha_eur": base_agg["total_alpha_eur"],
        "roi": base_agg["roi"],
        "alpha_roi": base_agg["alpha_roi"],
        "delta_alpha_eur": 0.0,
        "delta_alpha_pct": 0.0,
    }]
    for drop in intents:
        kept = df[df["intent"] != drop]
        _, agg = run_simulation(
            kept, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
            investment=investment, cost_in=cost_in, cost_out=cost_out,
            spy_returns=spy_returns,
        )
        rows.append({
            "config": f"Drop {drop}",
            "n_trades": agg["n_trades"],
            "total_invested_eur": agg["total_invested_eur"],
            "strategy_net_eur": agg["total_net_pnl_eur"],
            "spy_net_eur": agg["total_spy_net_pnl_eur"],
            "alpha_eur": agg["total_alpha_eur"],
            "roi": agg["roi"],
            "alpha_roi": agg["alpha_roi"],
            "delta_alpha_eur": agg["total_alpha_eur"] - base_alpha,
            "delta_alpha_pct": agg["alpha_roi"] - base_alpha_roi,
        })
    # BUY-only
    buys = df[df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]
    _, agg = run_simulation(
        buys, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
        investment=investment, cost_in=cost_in, cost_out=cost_out,
        spy_returns=spy_returns,
    )
    rows.append({
        "config": "Keep BUY only",
        "n_trades": agg["n_trades"],
        "total_invested_eur": agg["total_invested_eur"],
        "strategy_net_eur": agg["total_net_pnl_eur"],
        "spy_net_eur": agg["total_spy_net_pnl_eur"],
        "alpha_eur": agg["total_alpha_eur"],
        "roi": agg["roi"],
        "alpha_roi": agg["alpha_roi"],
        "delta_alpha_eur": agg["total_alpha_eur"] - base_alpha,
        "delta_alpha_pct": agg["alpha_roi"] - base_alpha_roi,
    })
    return pd.DataFrame(rows)


def sweep_investment(
    df: pd.DataFrame, rr_col: str, rr_min: float, horizon: str,
    cost_in: float, cost_out: float, spy_returns: Dict,
    investments: List[float],
) -> pd.DataFrame:
    rows = []
    for inv in investments:
        _, agg = run_simulation(
            df, rr_col=rr_col, rr_min=rr_min, horizon=horizon,
            investment=inv, cost_in=cost_in, cost_out=cost_out,
            spy_returns=spy_returns,
        )
        rows.append({
            "per_trade_eur": inv,
            "n_trades": agg["n_trades"],
            "total_invested_eur": agg["total_invested_eur"],
            "strategy_net_eur": agg["total_net_pnl_eur"],
            "spy_net_eur": agg["total_spy_net_pnl_eur"],
            "alpha_eur": agg["total_alpha_eur"],
            "roi": agg["roi"],
            "spy_roi": agg["spy_roi"],
            "alpha_roi": agg["alpha_roi"],
            "cost_drag_pct": (cost_in + cost_out) / inv,
        })
    return pd.DataFrame(rows)


def _print_rr_sweep(df: pd.DataFrame, label: str) -> None:
    print("=" * 110)
    print(f"  {label}")
    print("=" * 110)
    print(f"{'R/R >':<7s} {'n':>4s} {'invested':>11s} {'strat €':>11s} "
          f"{'spy €':>11s} {'alpha €':>11s} {'ROI':>7s} {'spy ROI':>8s} "
          f"{'alpha':>7s} {'win%':>6s}")
    print("-" * 110)
    best_alpha_idx = df["alpha_eur"].idxmax() if not df.empty else None
    best_roi_idx = df["roi"].idxmax() if not df.empty else None
    for i, r in df.iterrows():
        marker = ""
        if i == best_alpha_idx:
            marker += "  ◀ MAX α"
        if i == best_roi_idx and i != best_alpha_idx:
            marker += "  ◀ MAX ROI"
        elif i == best_roi_idx and i == best_alpha_idx:
            marker += " (and ROI)"
        print(f"{r['rr_threshold']:<7.2f} {int(r['n_trades']):>4d} "
              f"€{r['total_invested_eur']:>10,.0f} "
              f"€{r['strategy_net_eur']:>+10,.2f} "
              f"€{r['spy_net_eur']:>+10,.2f} "
              f"€{r['alpha_eur']:>+10,.2f} "
              f"{r['roi']:>+7.2%} {r['spy_roi']:>+7.2%} {r['alpha_roi']:>+7.2%} "
              f"{r['win_rate_net']:>5.1%}{marker}")


def _print_per_verdict(df: pd.DataFrame, label: str) -> None:
    print("=" * 96)
    print(f"  {label}")
    print("=" * 96)
    print(f"  {'verdict':<14s} {'n':>3s} {'invested':>10s} {'strat €':>11s} "
          f"{'spy €':>11s} {'alpha €':>11s} {'ROI':>7s} {'alpha %':>8s} {'win%':>6s}")
    print(f"  {'-' * 90}")
    for _, r in df.iterrows():
        print(f"  {r['intent']:<14s} {int(r['n_trades']):>3d} "
              f"€{r['total_invested_eur']:>9,.0f} "
              f"€{r['strategy_net_eur']:>+10,.2f} €{r['spy_net_eur']:>+10,.2f} "
              f"€{r['alpha_eur']:>+10,.2f} {r['roi']:>+7.2%} {r['alpha_roi']:>+8.2%} "
              f"{r['win_rate_net']:>5.1%}")


def _print_drop_one(df: pd.DataFrame, label: str) -> None:
    print("=" * 110)
    print(f"  {label}")
    print("=" * 110)
    print(f"  {'config':<22s} {'n':>3s} {'invested':>10s} {'strat €':>11s} "
          f"{'spy €':>11s} {'alpha €':>11s} {'ROI':>7s} {'alpha %':>8s} "
          f"{'Δalpha€':>9s} {'Δalpha%':>9s}")
    print(f"  {'-' * 106}")
    for _, r in df.iterrows():
        delta_eur = f"{r['delta_alpha_eur']:+9.2f}" if r["config"] != "ALL (baseline)" else "       —"
        delta_pct = f"{r['delta_alpha_pct']:+8.2%}" if r["config"] != "ALL (baseline)" else "       —"
        print(f"  {r['config']:<22s} {int(r['n_trades']):>3d} "
              f"€{r['total_invested_eur']:>9,.0f} "
              f"€{r['strategy_net_eur']:>+10,.2f} €{r['spy_net_eur']:>+10,.2f} "
              f"€{r['alpha_eur']:>+10,.2f} {r['roi']:>+7.2%} {r['alpha_roi']:>+8.2%} "
              f"{delta_eur} {delta_pct}")


def _print_investment_sweep(df: pd.DataFrame, label: str) -> None:
    print("=" * 110)
    print(f"  {label}")
    print("=" * 110)
    print(f"{'€/trade':<10s} {'n':>4s} {'invested':>11s} {'strat €':>11s} "
          f"{'spy €':>11s} {'alpha €':>11s} {'ROI':>7s} {'spy ROI':>8s} "
          f"{'alpha':>7s} {'cost drag':>10s}")
    print("-" * 110)
    for _, r in df.iterrows():
        print(f"€{int(r['per_trade_eur']):<9d} {int(r['n_trades']):>4d} "
              f"€{r['total_invested_eur']:>10,.0f} "
              f"€{r['strategy_net_eur']:>+10,.2f} "
              f"€{r['spy_net_eur']:>+10,.2f} "
              f"€{r['alpha_eur']:>+10,.2f} "
              f"{r['roi']:>+7.2%} {r['spy_roi']:>+7.2%} {r['alpha_roi']:>+7.2%} "
              f"{r['cost_drag_pct']:>9.2%}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--rr-col", default="risk_reward_ratio",
                        choices=["risk_reward_ratio", "deep_research_rr_ratio"])
    parser.add_argument("--rr-min", type=float, default=1.5)
    parser.add_argument("--horizon", default="1w", choices=["1w", "2w", "4w", "8w"])
    parser.add_argument("--investment", type=float, default=750.0)
    parser.add_argument("--cost-in", type=float, default=3.0)
    parser.add_argument("--cost-out", type=float, default=3.0)
    parser.add_argument("--intent-only", action="store_true")
    parser.add_argument("--no-penny", action="store_true")
    parser.add_argument("--no-sweep", action="store_true",
                        help="Skip the R/R + investment sweeps")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    logger.info("Loading cohort (start=%s)...", args.start)
    ds = compute_dataset(start_date=args.start)
    df = ds["enriched"]
    logger.info("Cohort size: %d", len(df))

    horizon_col = f"return_{args.horizon}"

    # Pre-fetch SPY returns for every decision date in the cohort
    horizon_days = HORIZON_DAYS[args.horizon]
    logger.info("Fetching SPY benchmark for %d unique dates...",
                df["decision_date"].nunique())
    spy_returns = _spy_returns_by_date(df["decision_date"], horizon_days)

    main_df, main_agg = run_simulation(
        df,
        rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
        investment=args.investment, cost_in=args.cost_in, cost_out=args.cost_out,
        intent_only=args.intent_only,
        min_price=1.0 if args.no_penny else None,
        spy_returns=spy_returns,
    )
    label = (
        f"PORTFOLIO SIM — {args.rr_col} > {args.rr_min}, hold {args.horizon}, "
        f"€{args.investment:.0f}/trade, €{args.cost_in + args.cost_out:.0f} round-trip"
    )
    if args.intent_only:
        label += "  (BUY-only)"
    if args.no_penny:
        label += "  (no penny stocks)"
    _print_simulation(label, main_df, main_agg, horizon_col, args.rr_col)

    out = args.out or (
        REPO_ROOT / "docs" / "performance"
        / f"{datetime.now():%Y-%m-%d}-package" / "data"
        / f"portfolio_sim_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    )
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    main_df.to_csv(out, index=False)
    print(f"\nSaved per-trade ledger to {out}")

    if args.no_sweep:
        return

    # --- Per-verdict contribution at the chosen R/R cutoff ---
    print()
    pv_df = per_verdict_breakdown(
        df, rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
        investment=args.investment, cost_in=args.cost_in, cost_out=args.cost_out,
        spy_returns=spy_returns,
    )
    _print_per_verdict(
        pv_df,
        f"PER-VERDICT CONTRIBUTION — {args.rr_col} > {args.rr_min}, "
        f"hold {args.horizon}, €{args.investment:.0f}/trade",
    )
    pv_path = out.parent / f"per_verdict_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    pv_df.to_csv(pv_path, index=False)
    print(f"\nSaved per-verdict CSV to {pv_path}")

    # --- Drop-one-verdict sensitivity ---
    print()
    drop_df = sweep_drop_one_verdict(
        df, rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
        investment=args.investment, cost_in=args.cost_in, cost_out=args.cost_out,
        spy_returns=spy_returns,
    )
    _print_drop_one(
        drop_df,
        f"DROP-ONE-VERDICT — {args.rr_col} > {args.rr_min}, "
        f"hold {args.horizon}, €{args.investment:.0f}/trade",
    )
    drop_path = out.parent / f"drop_one_verdict_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    drop_df.to_csv(drop_path, index=False)
    print(f"\nSaved drop-one-verdict CSV to {drop_path}")

    # --- R/R cutoff sweep ---
    print()
    rr_grid = [round(0.5 + 0.25 * i, 2) for i in range(0, 19)]   # 0.50 ... 5.00
    rr_sweep = sweep_rr_cutoff(
        df, rr_col=args.rr_col, horizon=args.horizon,
        investment=args.investment,
        cost_in=args.cost_in, cost_out=args.cost_out,
        spy_returns=spy_returns, thresholds=rr_grid,
    )
    _print_rr_sweep(
        rr_sweep,
        f"R/R CUTOFF SWEEP — {args.rr_col}, hold {args.horizon}, "
        f"€{args.investment:.0f}/trade",
    )
    sweep_path = out.parent / f"sweep_rr_{args.rr_col}_{args.horizon}_inv{int(args.investment)}.csv"
    rr_sweep.to_csv(sweep_path, index=False)
    print(f"\nSaved R/R sweep to {sweep_path}")

    # Same R/R sweep but restricted to BUY verdicts (ENTER_NOW + ENTER_LIMIT)
    print()
    buys_only = df[df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]
    rr_sweep_buy = sweep_rr_cutoff(
        buys_only, rr_col=args.rr_col, horizon=args.horizon,
        investment=args.investment,
        cost_in=args.cost_in, cost_out=args.cost_out,
        spy_returns=spy_returns, thresholds=rr_grid,
    )
    _print_rr_sweep(
        rr_sweep_buy,
        f"R/R CUTOFF SWEEP — BUY ONLY (ENTER_NOW + ENTER_LIMIT), "
        f"hold {args.horizon}, €{args.investment:.0f}/trade",
    )
    sweep_buy_path = out.parent / f"sweep_rr_buy_only_{args.rr_col}_{args.horizon}_inv{int(args.investment)}.csv"
    rr_sweep_buy.to_csv(sweep_buy_path, index=False)
    print(f"\nSaved BUY-only R/R sweep to {sweep_buy_path}")

    # --- Investment-size sweep at the user-chosen R/R threshold ---
    print()
    inv_grid = [100, 250, 500, 750, 1000, 2500, 5000, 10000]
    inv_sweep = sweep_investment(
        df, rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
        cost_in=args.cost_in, cost_out=args.cost_out,
        spy_returns=spy_returns, investments=inv_grid,
    )
    _print_investment_sweep(
        inv_sweep,
        f"INVESTMENT-SIZE SWEEP — {args.rr_col} > {args.rr_min}, hold {args.horizon}, "
        f"€{args.cost_in + args.cost_out:.0f} round-trip",
    )
    inv_path = out.parent / f"sweep_investment_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    inv_sweep.to_csv(inv_path, index=False)
    print(f"\nSaved investment sweep to {inv_path}")


if __name__ == "__main__":
    main()
