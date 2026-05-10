"""Compare exit strategies on the BUY R/R > 1.5 cohort over a 5-trading-day hold.

Question: a hard TP=21% only fires on 2 of 18 trades. The other 16 sit through
the full 5-day window and exit at close — sometimes flat, sometimes negative.
What's a smarter exit rule that captures more upside while limiting downside?

Strategies tested
-----------------
1. BASELINE        — no TP/SL, hold to day-5 close (timeout). Reference point.
2. HARD TP/SL      — fixed TP and SL prices set at entry. (the previous optimum)
3. TRAILING STOP   — track running peak high; exit if today's low <= peak * (1 - trail_pct).
                     Optional initial catastrophic SL.
4. BREAKEVEN-TRAIL — initial SL at -X%; once price reaches +Y%, move SL to entry.
                     Then trail at the higher of {entry, peak * (1 - trail_pct)}.
5. TIME-DECAY      — if return at end of day N is below threshold, exit at that close.
                     Otherwise hold to day 5.
6. MULTI-TIER TP   — at TP1 exit half the position; the remainder runs to TP2 with a
                     fallback SL. Models scaling out.
7. ORACLE          — hindsight: exit at the maximum close achieved during the window.
                     Upper bound on what's theoretically possible.

For each strategy that has parameters we sweep over a small grid and report
the parameter set that maximizes total net P&L.

Defaults
--------
  • Filter: PM R/R > 1.5 AND intent in (ENTER_NOW, ENTER_LIMIT)
  • Hold ≤ 5 trading days, €750/trade, €6 round-trip cost
  • Conservative intraday rule: if a single bar's High and Low cross both an
    upper exit and a lower exit, assume the lower (downside) fires first.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("exit_strats")


# -----------------------------------------------------------------------------
# Per-trade simulators
# -----------------------------------------------------------------------------

def _slice_forward(decision_date: pd.Timestamp, bars: pd.DataFrame, max_days: int) -> Optional[pd.DataFrame]:
    if bars is None or bars.empty:
        return None
    bars = bars.sort_index()
    forward = bars.loc[bars.index >= pd.Timestamp(decision_date).normalize()]
    forward = forward.iloc[: max_days + 1]
    if len(forward) < 2:
        return None
    return forward


def sim_baseline(entry: float, decision_date, bars, max_days: int = 5) -> Optional[Dict]:
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    final_close = float(forward.iloc[last]["Close"])
    return {
        "exit_return": (final_close - entry) / entry,
        "exit_day": last, "exit_reason": "TIMEOUT", "exit_price": final_close,
    }


def sim_hard_tp_sl(entry, decision_date, bars, tp_pct, sl_pct, max_days=5):
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    tp_price = entry * (1 + tp_pct)
    sl_price = entry * (1 - sl_pct)
    last = min(max_days, len(forward) - 1)
    for i in range(1, last + 1):
        day = forward.iloc[i]
        high = float(day["High"]); low = float(day["Low"])
        if low <= sl_price:
            return {"exit_return": -sl_pct, "exit_day": i,
                    "exit_reason": "SL", "exit_price": sl_price}
        if high >= tp_price:
            return {"exit_return": tp_pct, "exit_day": i,
                    "exit_reason": "TP", "exit_price": tp_price}
    final = float(forward.iloc[last]["Close"])
    return {"exit_return": (final - entry) / entry, "exit_day": last,
            "exit_reason": "TIMEOUT", "exit_price": final}


def sim_trailing_stop(entry, decision_date, bars, trail_pct, initial_sl_pct, max_days=5):
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    peak = entry
    initial_sl = entry * (1 - initial_sl_pct)
    for i in range(1, last + 1):
        day = forward.iloc[i]
        high = float(day["High"]); low = float(day["Low"])
        # Effective stop is the more defensive of (initial SL, trailing SL from current peak)
        stop = initial_sl
        # Only consider trailing once peak has meaningfully exceeded entry (>0.1 pp)
        if peak > entry * 1.001:
            trail = peak * (1 - trail_pct)
            stop = max(stop, trail)
        if low <= stop:
            return {"exit_return": (stop - entry) / entry, "exit_day": i,
                    "exit_reason": "TRAIL_SL" if stop > initial_sl else "INITIAL_SL",
                    "exit_price": stop}
        peak = max(peak, high)
    final = float(forward.iloc[last]["Close"])
    return {"exit_return": (final - entry) / entry, "exit_day": last,
            "exit_reason": "TIMEOUT", "exit_price": final}


def sim_breakeven_trail(entry, decision_date, bars, trigger_pct, trail_pct, initial_sl_pct, max_days=5):
    """Initial SL at -X%. When intraday high reaches entry*(1+trigger_pct),
    permanently lift SL to max(entry, peak*(1-trail_pct))."""
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    peak = entry
    triggered = False
    initial_sl = entry * (1 - initial_sl_pct)
    for i in range(1, last + 1):
        day = forward.iloc[i]
        high = float(day["High"]); low = float(day["Low"])

        # Compute today's effective stop BEFORE updating peak
        stop = initial_sl
        if triggered:
            stop = max(stop, entry, peak * (1 - trail_pct))

        if low <= stop:
            return {"exit_return": (stop - entry) / entry, "exit_day": i,
                    "exit_reason": "BE_TRAIL" if triggered else "INITIAL_SL",
                    "exit_price": stop}

        # Did we trigger today? (use today's high)
        if not triggered and high >= entry * (1 + trigger_pct):
            triggered = True

        peak = max(peak, high)
    final = float(forward.iloc[last]["Close"])
    return {"exit_return": (final - entry) / entry, "exit_day": last,
            "exit_reason": "TIMEOUT", "exit_price": final}


def sim_time_decay(entry, decision_date, bars, check_day, min_progress_pct, max_days=5):
    """If close at end of `check_day` is below `min_progress_pct`, exit then.
    Otherwise hold to day-`max_days` close."""
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    if check_day >= last:
        # Just hold to last day
        final = float(forward.iloc[last]["Close"])
        return {"exit_return": (final - entry) / entry, "exit_day": last,
                "exit_reason": "TIMEOUT", "exit_price": final}
    cd_close = float(forward.iloc[check_day]["Close"])
    cd_return = (cd_close - entry) / entry
    if cd_return < min_progress_pct:
        return {"exit_return": cd_return, "exit_day": check_day,
                "exit_reason": "TIME_DECAY", "exit_price": cd_close}
    final = float(forward.iloc[last]["Close"])
    return {"exit_return": (final - entry) / entry, "exit_day": last,
            "exit_reason": "TIMEOUT", "exit_price": final}


def sim_multi_tier_tp(entry, decision_date, bars, tp1_pct, tp2_pct, sl_pct,
                      partial_pct: float = 0.5, max_days=5):
    """Exit `partial_pct` of the position at TP1; remainder targets TP2 with the
    initial SL fallback. Returns the size-weighted realized return.
    """
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    tp1_price = entry * (1 + tp1_pct)
    tp2_price = entry * (1 + tp2_pct)
    sl_price = entry * (1 - sl_pct)

    tp1_return = None  # return realized on the half exited at TP1 (or whole if SL fires before TP1)
    tp2_return = None  # return realized on the remainder
    tp1_day = None
    tp2_day = None

    for i in range(1, last + 1):
        day = forward.iloc[i]
        high = float(day["High"]); low = float(day["Low"])

        if low <= sl_price:
            # SL fires; if TP1 hasn't fired the WHOLE position stops out.
            if tp1_return is None:
                tp1_return = -sl_pct; tp1_day = i
            tp2_return = -sl_pct; tp2_day = i
            break

        if tp1_return is None and high >= tp1_price:
            tp1_return = tp1_pct; tp1_day = i
            # If today also reaches TP2 we let it ride one more day for clarity
        if tp1_return is not None and tp2_return is None and high >= tp2_price:
            tp2_return = tp2_pct; tp2_day = i
            break

    # Timeout fills any unfilled leg at the day-`last` close
    if tp1_return is None or tp2_return is None:
        final = float(forward.iloc[last]["Close"])
        ret_at_close = (final - entry) / entry
        if tp1_return is None:
            tp1_return = ret_at_close; tp1_day = last
        if tp2_return is None:
            tp2_return = ret_at_close; tp2_day = last

    weighted = tp1_return * partial_pct + tp2_return * (1 - partial_pct)
    return {"exit_return": weighted,
            "exit_day": max(tp1_day or 0, tp2_day or 0),
            "exit_reason": f"TP1={tp1_pct:.0%}|TP2={tp2_pct:.0%}",
            "exit_price": entry * (1 + weighted)}


def sim_oracle(entry, decision_date, bars, max_days=5):
    """Hindsight: exit at the max close achieved during the window."""
    forward = _slice_forward(decision_date, bars, max_days)
    if forward is None:
        return None
    last = min(max_days, len(forward) - 1)
    closes = forward["Close"].astype(float).iloc[1: last + 1]
    if closes.empty:
        return None
    best_day = int(np.argmax(closes.values)) + 1
    best_close = float(closes.max())
    return {"exit_return": (best_close - entry) / entry, "exit_day": best_day,
            "exit_reason": "ORACLE", "exit_price": best_close}


# -----------------------------------------------------------------------------
# Aggregate runner
# -----------------------------------------------------------------------------

def run_strategy(
    name: str,
    sim_fn,
    trades: pd.DataFrame,
    bars_by_ticker: Dict,
    investment: float,
    cost_total: float,
    max_days: int,
) -> Dict:
    rows = []
    for _, row in trades.iterrows():
        sym = str(row["symbol"]).upper()
        bars = bars_by_ticker.get(sym)
        out = sim_fn(float(row["price_at_decision"]), row["decision_date"],
                     bars, max_days=max_days)
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
    detail = pd.DataFrame(rows)
    n = len(detail)
    if n == 0:
        return {"name": name, "n": 0, "total_net_eur": 0, "roi": 0,
                "win_rate": 0, "mean_return": 0, "median_return": 0, "detail": detail}
    total_net = float(detail["net_eur"].sum())
    return {
        "name": name,
        "n": n,
        "total_invested_eur": n * investment,
        "total_net_eur": total_net,
        "roi": total_net / (n * investment),
        "win_rate": float((detail["net_eur"] > 0).mean()),
        "mean_return": float(detail["exit_return"].mean()),
        "median_return": float(detail["exit_return"].median()),
        "min_return": float(detail["exit_return"].min()),
        "max_return": float(detail["exit_return"].max()),
        "detail": detail,
    }


def _print_strategy_table(rows: List[Dict]) -> None:
    print(f"  {'strategy':<48s} {'n':>3s} {'invested':>11s} "
          f"{'net €':>11s} {'ROI':>8s} {'win%':>6s} {'mean':>8s} {'median':>8s}")
    print(f"  {'-' * 110}")
    for r in rows:
        print(f"  {r['name']:<48s} {r['n']:>3d} "
              f"€{r['total_invested_eur']:>10,.0f} "
              f"€{r['total_net_eur']:>+10,.2f} "
              f"{r['roi']:>+7.2%} {r['win_rate']:>5.1%} "
              f"{r['mean_return']*100:>+7.2f}% {r['median_return']*100:>+7.2f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01")
    parser.add_argument("--rr-col", default="risk_reward_ratio")
    parser.add_argument("--rr-min", type=float, default=1.5)
    parser.add_argument("--horizon", default="1w", choices=["1w", "2w", "4w"])
    parser.add_argument("--investment", type=float, default=750.0)
    parser.add_argument("--cost-in", type=float, default=3.0)
    parser.add_argument("--cost-out", type=float, default=3.0)
    args = parser.parse_args()

    logger.info("Loading cohort + bars...")
    ds = compute_dataset(start_date=args.start)
    df = ds["enriched"]
    bars_by_ticker = ds["bars"]
    horizon_days = {"1w": 5, "2w": 10, "4w": 20}[args.horizon]
    horizon_col = f"return_{args.horizon}"

    trades = df[
        (df[args.rr_col] > args.rr_min)
        & df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])
        & df[horizon_col].notna()
    ].copy()
    n_trades = len(trades)
    logger.info("Qualifying trades: %d", n_trades)
    if n_trades == 0:
        sys.exit(1)

    cost_total = args.cost_in + args.cost_out
    inv = args.investment

    # =========== Strategy comparison ===========
    print("=" * 110)
    print(f"  EXIT STRATEGY COMPARISON — {args.rr_col} > {args.rr_min}, BUY-only, "
          f"{horizon_days}d max hold, €{inv:.0f}/trade, €{cost_total:.0f} round-trip")
    print(f"  Cohort: n={n_trades}")
    print("=" * 110)

    results: List[Dict] = []

    # 1. BASELINE
    results.append(run_strategy(
        "BASELINE — no TP/SL, exit day-5 close",
        lambda e, dd, b, max_days: sim_baseline(e, dd, b, max_days),
        trades, bars_by_ticker, inv, cost_total, horizon_days,
    ))

    # 2. HARD TP/SL — best from prior optimizer (TP=21%, SL=9.5%)
    results.append(run_strategy(
        "HARD TP=21% SL=9.5% (prior optimum)",
        lambda e, dd, b, max_days: sim_hard_tp_sl(e, dd, b, 0.21, 0.095, max_days),
        trades, bars_by_ticker, inv, cost_total, horizon_days,
    ))

    # 3. TRAILING STOP — sweep trail %
    print("\n  --- TRAILING STOP (initial SL=9.5%) ---")
    trail_sweep = []
    for trail_pct in [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
        r = run_strategy(
            f"  trail={trail_pct:.1%}",
            lambda e, dd, b, max_days, tp=trail_pct: sim_trailing_stop(e, dd, b, tp, 0.095, max_days),
            trades, bars_by_ticker, inv, cost_total, horizon_days,
        )
        trail_sweep.append(r)
    trail_best = max(trail_sweep, key=lambda r: r["total_net_eur"])
    _print_strategy_table(trail_sweep)
    print(f"  → best trailing stop: {trail_best['name']}  net=€{trail_best['total_net_eur']:+.2f}")
    trail_best["name"] = f"TRAILING STOP — best ({trail_best['name'].strip()})"
    results.append(trail_best)

    # 4. BREAKEVEN-TRAIL — sweep trigger %
    print("\n  --- BREAKEVEN-TRAIL (initial SL=9.5%, trail=3% after trigger) ---")
    be_sweep = []
    for trigger_pct in [0.02, 0.03, 0.05, 0.08, 0.10, 0.15]:
        r = run_strategy(
            f"  trigger={trigger_pct:.1%}",
            lambda e, dd, b, max_days, tg=trigger_pct: sim_breakeven_trail(
                e, dd, b, tg, trail_pct=0.03, initial_sl_pct=0.095, max_days=max_days),
            trades, bars_by_ticker, inv, cost_total, horizon_days,
        )
        be_sweep.append(r)
    be_best = max(be_sweep, key=lambda r: r["total_net_eur"])
    _print_strategy_table(be_sweep)
    print(f"  → best breakeven-trail: {be_best['name']}  net=€{be_best['total_net_eur']:+.2f}")
    be_best["name"] = f"BREAKEVEN-TRAIL — best ({be_best['name'].strip()})"
    results.append(be_best)

    # 5. TIME-DECAY — sweep check_day × min_progress
    print("\n  --- TIME-DECAY (exit on day N if return < threshold) ---")
    td_sweep = []
    for check_day in (2, 3):
        for min_progress in (-0.02, 0.0, 0.01, 0.02, 0.03):
            r = run_strategy(
                f"  day={check_day} threshold={min_progress:+.1%}",
                lambda e, dd, b, max_days, cd=check_day, mp=min_progress: sim_time_decay(
                    e, dd, b, cd, mp, max_days),
                trades, bars_by_ticker, inv, cost_total, horizon_days,
            )
            td_sweep.append(r)
    td_best = max(td_sweep, key=lambda r: r["total_net_eur"])
    _print_strategy_table(td_sweep)
    print(f"  → best time-decay: {td_best['name']}  net=€{td_best['total_net_eur']:+.2f}")
    td_best["name"] = f"TIME-DECAY — best ({td_best['name'].strip()})"
    results.append(td_best)

    # 6. MULTI-TIER TP — sweep TP1 levels with TP2=21%, SL=9.5%
    print("\n  --- MULTI-TIER TP (TP2=21%, SL=9.5%, 50/50 partial) ---")
    mt_sweep = []
    for tp1 in (0.03, 0.04, 0.05, 0.07, 0.10):
        r = run_strategy(
            f"  TP1={tp1:.1%}",
            lambda e, dd, b, max_days, t1=tp1: sim_multi_tier_tp(
                e, dd, b, t1, tp2_pct=0.21, sl_pct=0.095, partial_pct=0.5, max_days=max_days),
            trades, bars_by_ticker, inv, cost_total, horizon_days,
        )
        mt_sweep.append(r)
    mt_best = max(mt_sweep, key=lambda r: r["total_net_eur"])
    _print_strategy_table(mt_sweep)
    print(f"  → best multi-tier: {mt_best['name']}  net=€{mt_best['total_net_eur']:+.2f}")
    mt_best["name"] = f"MULTI-TIER TP — best ({mt_best['name'].strip()})"
    results.append(mt_best)

    # 7. ORACLE upper bound
    results.append(run_strategy(
        "ORACLE (hindsight: exit at max close)",
        lambda e, dd, b, max_days: sim_oracle(e, dd, b, max_days),
        trades, bars_by_ticker, inv, cost_total, horizon_days,
    ))

    # =========== Final summary ===========
    print()
    print("=" * 110)
    print("  HEAD-TO-HEAD SUMMARY")
    print("=" * 110)
    _print_strategy_table(results)

    # Detail of the best non-oracle strategy
    non_oracle = [r for r in results if "ORACLE" not in r["name"]]
    winner = max(non_oracle, key=lambda r: r["total_net_eur"])
    print()
    print("=" * 110)
    print(f"  PER-TRADE LEDGER — {winner['name']}")
    print("=" * 110)
    detail = winner["detail"].copy()
    if not detail.empty:
        detail = detail.sort_values("net_eur", ascending=False)
        disp = detail.copy()
        disp["entry"] = disp["entry"].apply(lambda v: f"${v:.4f}" if v < 1 else f"${v:.2f}")
        disp["exit_return"] = disp["exit_return"].apply(lambda v: f"{v*100:+.2f}%")
        disp["gross_eur"] = disp["gross_eur"].apply(lambda v: f"€{v:+.2f}")
        disp["net_eur"] = disp["net_eur"].apply(lambda v: f"€{v:+.2f}")
        print(disp.to_string(index=False))

    # Save outputs
    out_dir = REPO_ROOT / "docs" / "performance" / f"{datetime.now():%Y-%m-%d}-package" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"exit_strategy_summary_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
    pd.DataFrame([
        {k: v for k, v in r.items() if k != "detail"} for r in results
    ]).to_csv(summary_path, index=False)
    print(f"\nSaved summary CSV to {summary_path}")

    # Per-trade ledger of every strategy
    all_details = []
    for r in results:
        if r["detail"] is not None and not r["detail"].empty:
            d = r["detail"].copy()
            d.insert(0, "strategy", r["name"])
            all_details.append(d)
    if all_details:
        all_path = out_dir / f"exit_strategy_per_trade_{args.rr_col}_above_{args.rr_min}_{args.horizon}.csv"
        pd.concat(all_details).to_csv(all_path, index=False)
        print(f"Saved per-trade ledger to {all_path}")


if __name__ == "__main__":
    main()
