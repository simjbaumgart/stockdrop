"""Portfolio simulation: filter by R/R threshold, hold for N trading days, compute P&L.

Usage:
    ./venv/bin/python scripts/analysis/portfolio_sim.py
    ./venv/bin/python scripts/analysis/portfolio_sim.py --rr-min 1.5 --horizon 1w \
        --investment 750 --cost-in 3 --cost-out 3
    ./venv/bin/python scripts/analysis/portfolio_sim.py --rr-col deep_research_rr_ratio \
        --rr-min 2.0 --horizon 2w
    ./venv/bin/python scripts/analysis/portfolio_sim.py --intent-only --no-penny

Defaults match the user-requested simulation (PM R/R > 1.5, 1-week hold,
€750/trade, €6 round-trip cost). Output is a per-trade ledger CSV plus
aggregate stats printed to stdout.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("portfolio_sim")


def run_simulation(
    df: pd.DataFrame,
    rr_col: str,
    rr_min: float,
    horizon: str,
    investment: float,
    cost_in: float,
    cost_out: float,
    intent_only: bool,
    min_price: float | None,
) -> tuple[pd.DataFrame, dict]:
    """Apply the trade filter and compute per-row + aggregate P&L.

    Returns (per_trade_df, aggregate_stats_dict).
    """
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

    n = len(sub)
    total_invested = n * investment
    total_gross = float(sub["gross_pnl_eur"].sum()) if n else 0.0
    total_cost = float(sub["round_trip_cost_eur"].sum()) if n else 0.0
    total_net = float(sub["net_pnl_eur"].sum()) if n else 0.0
    win_gross = int((sub["gross_pnl_eur"] > 0).sum()) if n else 0
    win_net = int((sub["net_pnl_eur"] > 0).sum()) if n else 0

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
    }
    return sub, agg


def _print_simulation(label: str, df: pd.DataFrame, agg: dict, horizon_col: str,
                      rr_col: str, top_n: int = 5) -> None:
    print("=" * 96)
    print(f"  {label}")
    print("=" * 96)
    if df.empty:
        print("  No trades match the filter.")
        return
    cols = ["symbol", "decision_date", "intent", "recommendation", rr_col,
            "price_at_decision", horizon_col, "gross_pnl_eur",
            "round_trip_cost_eur", "net_pnl_eur"]
    disp = df[cols].copy()
    disp[horizon_col] = disp[horizon_col].apply(lambda v: f"{v * 100:+.2f}%")
    disp["gross_pnl_eur"] = disp["gross_pnl_eur"].apply(lambda v: f"€{v:+.2f}")
    disp["net_pnl_eur"] = disp["net_pnl_eur"].apply(lambda v: f"€{v:+.2f}")
    disp["round_trip_cost_eur"] = disp["round_trip_cost_eur"].apply(lambda v: f"€{v:.2f}")
    disp["price_at_decision"] = disp["price_at_decision"].apply(
        lambda v: f"${v:.4f}" if v < 1 else f"${v:.2f}"
    )
    disp.columns = ["symbol", "date", "intent", "rec", "R/R",
                    "entry", "return", "gross", "cost", "net"]
    print(disp.to_string(index=False))
    print()
    print(f"  Number of trades:         {agg['n_trades']}")
    print(f"  Total capital deployed:   €{agg['total_invested_eur']:>12,.2f}")
    print(f"  Total gross P&L:          €{agg['total_gross_pnl_eur']:>+12,.2f}")
    print(f"  Total trading costs:      €{agg['total_cost_eur']:>+12,.2f}")
    print(f"  Total NET P&L:            €{agg['total_net_pnl_eur']:>+12,.2f}")
    print(f"  Final portfolio value:    €{agg['final_value_eur']:>12,.2f}    "
          f"({agg['roi']:+.2%} ROI)")
    print(f"  Win rate (gross | net):   {agg['win_rate_gross']:.1%} | {agg['win_rate_net']:.1%}")
    print(f"  Mean / median net return: {agg['mean_net_return_pct']:+.2%} / "
          f"{agg['median_net_return_pct']:+.2%}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01", help="Cohort start date")
    parser.add_argument("--rr-col", default="risk_reward_ratio",
                        choices=["risk_reward_ratio", "deep_research_rr_ratio"],
                        help="R/R column to filter on")
    parser.add_argument("--rr-min", type=float, default=1.5,
                        help="Minimum R/R to include the trade")
    parser.add_argument("--horizon", default="1w", choices=["1w", "2w", "4w", "8w"],
                        help="Holding period")
    parser.add_argument("--investment", type=float, default=750.0,
                        help="Capital per trade (currency units)")
    parser.add_argument("--cost-in", type=float, default=3.0,
                        help="Fill commission per trade")
    parser.add_argument("--cost-out", type=float, default=3.0,
                        help="Termination commission per trade")
    parser.add_argument("--intent-only", action="store_true",
                        help="Only count ENTER_NOW + ENTER_LIMIT verdicts")
    parser.add_argument("--no-penny", action="store_true",
                        help="Exclude stocks priced < $1 at decision")
    parser.add_argument("--out", default=None, help="Output CSV path")
    args = parser.parse_args()

    logger.info("Loading cohort (start=%s)...", args.start)
    ds = compute_dataset(start_date=args.start)
    df = ds["enriched"]
    logger.info("Cohort size: %d", len(df))

    horizon_col = f"return_{args.horizon}"
    main_df, main_agg = run_simulation(
        df,
        rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
        investment=args.investment, cost_in=args.cost_in, cost_out=args.cost_out,
        intent_only=args.intent_only, min_price=1.0 if args.no_penny else None,
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

    # Always show two sensitivities so the user sees outlier impact
    if not args.no_penny and not args.intent_only:
        no_penny_df, no_penny_agg = run_simulation(
            df, rr_col=args.rr_col, rr_min=args.rr_min, horizon=args.horizon,
            investment=args.investment, cost_in=args.cost_in, cost_out=args.cost_out,
            intent_only=False, min_price=1.0,
        )
        print()
        _print_simulation(
            "SENSITIVITY: same filter but excluding penny stocks (price < $1)",
            no_penny_df, no_penny_agg, horizon_col, args.rr_col,
        )

    out = args.out or (
        REPO_ROOT
        / "docs"
        / "performance"
        / f"{datetime.now():%Y-%m-%d}-package"
        / "data"
        / f"portfolio_sim_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    main_df.to_csv(out, index=False)
    print()
    print(f"Saved per-trade ledger to {out}")


if __name__ == "__main__":
    main()
