"""TP/SL optimizer — find the take-profit and stop-loss thresholds that
maximize total profit on a filtered cohort over a fixed holding period.

Defaults match the user's question:
  • Filter: PM R/R > 1.5 AND intent in (ENTER_NOW, ENTER_LIMIT)
  • Holding period: 5 trading days (≈ 1 week)
  • Per-trade investment: €750
  • Round-trip commission: €6 (€3 in + €3 out)
  • TP grid: 1% .. 25% in 0.5% steps
  • SL grid: 1% .. 15% in 0.5% steps

Simulation rule per trade
-------------------------
For each trading day from day 1 .. day 5 (after the decision day):
  - If High of that day  >= entry × (1 + TP)  → TP fires (exit at TP price)
  - If Low  of that day  <= entry × (1 - SL)  → SL fires (exit at SL price)
  - If both happen on the same day → conservative: assume SL fires FIRST
                                     (downside often hits before upside in
                                      a bar — worst-case assumption)
  - If neither fires by end of day 5 → exit at close on day 5 (timeout)

Outputs
-------
  • Top 10 (TP, SL) combos by total net P&L.
  • The break-even TP grid: smallest TP at each SL where total net >= €0.
  • Heatmap PNG of total net P&L over the (TP × SL) grid.
  • CSV of every (TP, SL) combo: sweep_tp_sl_<filter>.csv

Usage
-----
  ./venv/bin/python scripts/analysis/tp_sl_optimizer.py
  ./venv/bin/python scripts/analysis/tp_sl_optimizer.py --rr-min 2.0
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tp_sl")


def simulate_one(
    decision_price: float,
    decision_date: pd.Timestamp,
    bars: pd.DataFrame,
    tp_pct: float,
    sl_pct: float,
    max_days: int = 5,
) -> Optional[Dict]:
    """Simulate one trade with TP/SL exits over `max_days` trading days.

    Returns dict with: exit_return (decimal), exit_day (int), exit_reason
    ("TP" | "SL" | "SL_FIRST" | "TIMEOUT"), exit_price.
    None if the bars are insufficient.
    """
    if bars is None or bars.empty or decision_price is None or decision_price <= 0:
        return None
    bars = bars.sort_index()
    # CRITICAL: slice to decision_date forward — different trades have
    # different decision dates, so we cannot just take the first N rows of
    # the whole ticker.
    forward = bars.loc[bars.index >= pd.Timestamp(decision_date).normalize()]
    forward = forward.iloc[: max_days + 1]
    if len(forward) < 2:
        return None

    tp_price = decision_price * (1.0 + tp_pct)
    sl_price = decision_price * (1.0 - sl_pct)

    last_idx = min(max_days, len(forward) - 1)
    for i in range(1, last_idx + 1):
        day = forward.iloc[i]
        high = float(day["High"])
        low = float(day["Low"])

        hit_tp = high >= tp_price
        hit_sl = low <= sl_price

        if hit_tp and hit_sl:
            return {
                "exit_return": -sl_pct,
                "exit_day": i,
                "exit_reason": "SL_FIRST",
                "exit_price": sl_price,
            }
        if hit_sl:
            return {
                "exit_return": -sl_pct,
                "exit_day": i,
                "exit_reason": "SL",
                "exit_price": sl_price,
            }
        if hit_tp:
            return {
                "exit_return": tp_pct,
                "exit_day": i,
                "exit_reason": "TP",
                "exit_price": tp_price,
            }

    # Neither hit — exit at close on the last day
    final_close = float(forward.iloc[last_idx]["Close"])
    return {
        "exit_return": (final_close - decision_price) / decision_price,
        "exit_day": last_idx,
        "exit_reason": "TIMEOUT",
        "exit_price": final_close,
    }


def aggregate(
    trades: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    tp_pct: float,
    sl_pct: float,
    investment: float,
    cost_total: float,
    max_days: int,
) -> Dict:
    n_tp = n_sl = n_timeout = 0
    wins = 0
    total_net = 0.0
    total_gross = 0.0
    n = 0
    for _, row in trades.iterrows():
        sym = str(row["symbol"]).upper()
        bars = bars_by_ticker.get(sym)
        out = simulate_one(
            float(row["price_at_decision"]),
            row["decision_date"],
            bars, tp_pct, sl_pct, max_days=max_days,
        )
        if out is None:
            continue
        n += 1
        gross = investment * out["exit_return"]
        net = gross - cost_total
        total_gross += gross
        total_net += net
        if net > 0:
            wins += 1
        if out["exit_reason"] == "TP":
            n_tp += 1
        elif out["exit_reason"] in ("SL", "SL_FIRST"):
            n_sl += 1
        else:
            n_timeout += 1
    return {
        "tp_pct": tp_pct, "sl_pct": sl_pct,
        "n": n, "n_tp": n_tp, "n_sl": n_sl, "n_timeout": n_timeout,
        "total_gross_eur": total_gross,
        "total_net_eur": total_net,
        "roi": total_net / (n * investment) if n else 0.0,
        "win_rate": wins / n if n else 0.0,
    }


def sweep(
    trades: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    tp_grid: List[float],
    sl_grid: List[float],
    investment: float,
    cost_total: float,
    max_days: int,
) -> pd.DataFrame:
    rows = []
    for tp in tp_grid:
        for sl in sl_grid:
            rows.append(aggregate(trades, bars_by_ticker, tp, sl,
                                  investment, cost_total, max_days))
    return pd.DataFrame(rows)


def per_trade_at(
    trades: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    tp_pct: float,
    sl_pct: float,
    investment: float,
    cost_total: float,
    max_days: int,
) -> pd.DataFrame:
    """Per-trade outcome at one specific (TP, SL) pair, for inspection."""
    rows = []
    for _, row in trades.iterrows():
        sym = str(row["symbol"]).upper()
        bars = bars_by_ticker.get(sym)
        out = simulate_one(
            float(row["price_at_decision"]),
            row["decision_date"],
            bars, tp_pct, sl_pct, max_days=max_days,
        )
        if out is None:
            continue
        gross = investment * out["exit_return"]
        net = gross - cost_total
        rows.append({
            "symbol": row["symbol"],
            "decision_date": row["decision_date"].strftime("%Y-%m-%d"),
            "intent": row["intent"],
            "rr": float(row["risk_reward_ratio"]),
            "entry": float(row["price_at_decision"]),
            "exit_reason": out["exit_reason"],
            "exit_day": out["exit_day"],
            "exit_return": out["exit_return"],
            "gross_eur": gross,
            "net_eur": net,
        })
    return pd.DataFrame(rows).sort_values("net_eur", ascending=False).reset_index(drop=True)


def render_heatmap(
    sweep_df: pd.DataFrame, out_path: Path, title: str,
) -> Path:
    """Heatmap: x=TP, y=SL, color=total_net_eur."""
    if sweep_df.empty:
        return out_path
    pivot = sweep_df.pivot(index="sl_pct", columns="tp_pct", values="total_net_eur")
    fig, ax = plt.subplots(figsize=(11, 5.5))
    pcm = ax.pcolormesh(
        pivot.columns.values * 100,
        pivot.index.values * 100,
        pivot.values,
        cmap="RdYlGn", shading="auto",
        vmin=-abs(pivot.values).max(), vmax=abs(pivot.values).max(),
    )
    fig.colorbar(pcm, ax=ax, label="Total NET P&L (€)")
    ax.set_xlabel("Take-profit (%)")
    ax.set_ylabel("Stop-loss (%)")
    ax.set_title(title)

    # Mark the maximum
    best_idx = sweep_df["total_net_eur"].idxmax()
    best = sweep_df.loc[best_idx]
    ax.plot(best["tp_pct"] * 100, best["sl_pct"] * 100,
            marker="*", markersize=16, color="white",
            markeredgecolor="black", markeredgewidth=1.2,
            label=f"max: TP={best['tp_pct']:.1%}, SL={best['sl_pct']:.1%}, "
                  f"€{best['total_net_eur']:+.0f}")
    ax.legend(loc="upper right", fontsize=9, frameon=True)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--rr-col", default="risk_reward_ratio")
    parser.add_argument("--rr-min", type=float, default=1.5)
    parser.add_argument("--horizon", default="1w", choices=["1w", "2w", "4w"])
    parser.add_argument("--investment", type=float, default=750.0)
    parser.add_argument("--cost-in", type=float, default=3.0)
    parser.add_argument("--cost-out", type=float, default=3.0)
    parser.add_argument("--tp-min", type=float, default=0.01)
    parser.add_argument("--tp-max", type=float, default=0.25)
    parser.add_argument("--tp-step", type=float, default=0.005)
    parser.add_argument("--sl-min", type=float, default=0.01)
    parser.add_argument("--sl-max", type=float, default=0.15)
    parser.add_argument("--sl-step", type=float, default=0.005)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    logger.info("Loading cohort + bars...")
    ds = compute_dataset(start_date=args.start)
    df = ds["enriched"]
    bars_by_ticker = ds["bars"]
    horizon_days = {"1w": 5, "2w": 10, "4w": 20}[args.horizon]

    # Filter to BUY-intent and the chosen R/R band, with completed return at the horizon
    horizon_col = f"return_{args.horizon}"
    trades = df[
        (df[args.rr_col] > args.rr_min)
        & df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])
        & df[horizon_col].notna()
    ].copy()
    logger.info("Qualifying trades after filter: %d", len(trades))
    if trades.empty:
        sys.exit(1)

    cost_total = args.cost_in + args.cost_out

    # 1) Baseline (no TP/SL — just hold for max_days and exit at close)
    baseline = aggregate(
        trades, bars_by_ticker, tp_pct=10.0, sl_pct=10.0,  # huge TP/SL = effectively no exit
        investment=args.investment, cost_total=cost_total, max_days=horizon_days,
    )
    # use absurdly high TP/SL to ensure no early exit
    baseline = aggregate(
        trades, bars_by_ticker, tp_pct=1000.0, sl_pct=1000.0,
        investment=args.investment, cost_total=cost_total, max_days=horizon_days,
    )

    print("=" * 100)
    print(f"  TP/SL optimizer — {args.rr_col} > {args.rr_min}, BUY-only "
          f"(ENTER_NOW + ENTER_LIMIT), hold ≤ {args.horizon} ({horizon_days} trading days)")
    print(f"  €{args.investment:.0f}/trade, €{cost_total:.0f} round-trip")
    print("=" * 100)
    print()
    print(f"  Baseline (no TP/SL, hold full {horizon_days} days, exit at close):")
    print(f"    n={baseline['n']}  total_net=€{baseline['total_net_eur']:+,.2f}  "
          f"ROI={baseline['roi']:+.2%}  win_rate={baseline['win_rate']:.1%}")
    print()

    # 2) Grid sweep
    tp_grid = np.round(np.arange(args.tp_min, args.tp_max + 1e-9, args.tp_step), 4).tolist()
    sl_grid = np.round(np.arange(args.sl_min, args.sl_max + 1e-9, args.sl_step), 4).tolist()
    logger.info("Sweeping TP grid (%d) × SL grid (%d) = %d combinations...",
                len(tp_grid), len(sl_grid), len(tp_grid) * len(sl_grid))
    sweep_df = sweep(
        trades, bars_by_ticker, tp_grid, sl_grid,
        investment=args.investment, cost_total=cost_total, max_days=horizon_days,
    )

    # Top 10 by net P&L
    print("=" * 100)
    print("  TOP 10 (TP, SL) COMBINATIONS BY TOTAL NET P&L")
    print("=" * 100)
    print(f"  {'rank':<5s} {'TP':>6s} {'SL':>6s} {'n':>3s} "
          f"{'TP fired':>9s} {'SL fired':>9s} {'timeout':>9s} "
          f"{'net €':>11s} {'ROI':>8s} {'win%':>6s}")
    for i, r in sweep_df.nlargest(10, "total_net_eur").reset_index(drop=True).iterrows():
        print(f"  {i+1:<5d} {r['tp_pct']:>5.1%} {r['sl_pct']:>5.1%} "
              f"{int(r['n']):>3d} {int(r['n_tp']):>9d} {int(r['n_sl']):>9d} "
              f"{int(r['n_timeout']):>9d} €{r['total_net_eur']:>+10,.2f} "
              f"{r['roi']:>+7.2%} {r['win_rate']:>5.1%}")

    # 3) Break-even TP per SL (smallest TP at each SL where net >= 0)
    print()
    print("=" * 100)
    print("  BREAK-EVEN MAP: smallest TP that yields total NET >= 0 at each SL")
    print("=" * 100)
    print(f"  {'SL':>6s} {'min TP for break-even':>26s} "
          f"{'net at that point €':>22s} {'n_tp':>5s} {'n_sl':>5s} {'n_timeout':>10s}")
    for sl_val in sl_grid:
        sl_rows = sweep_df[(sweep_df["sl_pct"] - sl_val).abs() < 1e-9]
        positive = sl_rows[sl_rows["total_net_eur"] >= 0].sort_values("tp_pct")
        if positive.empty:
            print(f"  {sl_val:>5.1%}  {'no TP yields >= 0':>26s}")
        else:
            r = positive.iloc[0]
            print(f"  {sl_val:>5.1%}  TP={r['tp_pct']:>22.1%}  "
                  f"€{r['total_net_eur']:>+19,.2f}  {int(r['n_tp']):>5d} "
                  f"{int(r['n_sl']):>5d} {int(r['n_timeout']):>10d}")

    # 4) Per-trade detail at the best combination
    best = sweep_df.loc[sweep_df["total_net_eur"].idxmax()]
    print()
    print("=" * 100)
    print(f"  PER-TRADE OUTCOMES AT OPTIMUM (TP={best['tp_pct']:.1%}, "
          f"SL={best['sl_pct']:.1%})")
    print("=" * 100)
    detail = per_trade_at(
        trades, bars_by_ticker,
        tp_pct=float(best["tp_pct"]), sl_pct=float(best["sl_pct"]),
        investment=args.investment, cost_total=cost_total, max_days=horizon_days,
    )
    if not detail.empty:
        disp = detail.copy()
        disp["entry"] = disp["entry"].apply(lambda v: f"${v:.4f}" if v < 1 else f"${v:.2f}")
        disp["exit_return"] = disp["exit_return"].apply(lambda v: f"{v * 100:+.2f}%")
        disp["gross_eur"] = disp["gross_eur"].apply(lambda v: f"€{v:+.2f}")
        disp["net_eur"] = disp["net_eur"].apply(lambda v: f"€{v:+.2f}")
        print(disp.to_string(index=False))

    # 5) Heatmap
    out_dir = Path(args.out_dir or
                   REPO_ROOT / "docs" / "performance"
                   / f"{datetime.now():%Y-%m-%d}-package" / "data")
    out_dir.mkdir(parents=True, exist_ok=True)
    heatmap_path = out_dir.parent / "charts" / f"29_tp_sl_heatmap_{args.rr_col}_above_{args.rr_min}_{args.horizon}.png"
    render_heatmap(
        sweep_df, heatmap_path,
        f"TP/SL grid — net P&L (BUY-only, R/R > {args.rr_min}, "
        f"{horizon_days} trading days, €{args.investment:.0f}/trade)",
    )
    sweep_path = out_dir / f"sweep_tp_sl_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    sweep_df.to_csv(sweep_path, index=False)
    detail_path = out_dir / f"tp_sl_optimum_trades_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    detail.to_csv(detail_path, index=False)

    print()
    print(f"Saved sweep CSV to {sweep_path}")
    print(f"Saved per-trade ledger to {detail_path}")
    print(f"Saved heatmap to {heatmap_path}")


if __name__ == "__main__":
    main()
