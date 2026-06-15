"""Re-analyze full performance dataset against live prices through today.

Combines data/subscribers.db (Dec 2025 - March 2026) with the live
subscribers.db (April 2026) and recomputes Max ROI vs SPY baseline using
yfinance, through whatever 'today' is.
"""

import os
import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

DBS = ["data/subscribers.db", "subscribers.db"]
START_DATE = "2026-01-15"
OUTPUT = "reports/full_2026_readout.md"


def load_decisions():
    frames = []
    for db in DBS:
        if not os.path.exists(db):
            continue
        conn = sqlite3.connect(db)
        q = f"""
            SELECT id, symbol, price_at_decision,
                   recommendation,
                   IFNULL(deep_research_verdict, 'None') AS deep_research_verdict,
                   timestamp,
                   entry_price_low, entry_price_high
            FROM decision_points
            WHERE timestamp >= '{START_DATE} 00:00:00'
              AND recommendation != 'PENDING'
        """
        df = pd.read_sql_query(q, conn)
        df["source_db"] = db
        frames.append(df)
        conn.close()
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["symbol", "timestamp"], keep="last")
    return combined


def fetch_perf(symbol, ts, original_price, sp500_history, entry_price_high=None):
    try:
        start_date = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        history = yf.Ticker(symbol).history(start=start_date, end=end_date)
        if history.empty:
            return None

        max_price = history["High"].max()
        min_price = history["Low"].min()
        current_price = history["Close"].iloc[-1]

        max_roi = (max_price - original_price) / original_price * 100
        current_roi = (current_price - original_price) / original_price * 100
        max_drawdown = (min_price - original_price) / original_price * 100

        sp500_max_roi = None
        if not sp500_history.empty:
            tz = sp500_history.index.tzinfo
            start_dt = pd.to_datetime(start_date)
            if tz is not None:
                start_dt = start_dt.tz_localize(tz)
            sp500_slice = sp500_history.loc[start_dt:]
            if not sp500_slice.empty:
                sp_start = sp500_slice["Close"].iloc[0]
                sp_max = sp500_slice["High"].max()
                sp500_max_roi = (sp_max - sp_start) / sp_start * 100

        is_triggered = False
        limit_max_roi = None
        miss_distance = None
        has_limit = pd.notnull(entry_price_high)
        if has_limit:
            if history["Low"].min() <= entry_price_high:
                is_triggered = True
                limit_max_roi = (max_price - entry_price_high) / entry_price_high * 100
            else:
                miss_distance = (history["Low"].min() - entry_price_high) / entry_price_high * 100

        return {
            "max_roi": max_roi,
            "current_roi": current_roi,
            "max_drawdown": max_drawdown,
            "is_triggered": is_triggered,
            "limit_max_roi": limit_max_roi,
            "miss_distance": miss_distance,
            "has_limit": has_limit,
            "sp500_max_roi": sp500_max_roi,
        }
    except Exception as e:
        print(f"  err {symbol}: {e}")
        return None


def main():
    print("Loading decisions from both databases...")
    df = load_decisions()
    print(f"  {len(df)} decisions across {df['source_db'].nunique()} dbs (>= {START_DATE}, no PENDING)")

    print("Fetching SPY baseline...")
    sp500_history = yf.Ticker("^GSPC").history(start=START_DATE)

    print("Fetching per-ticker history...")
    rows = []
    for i, r in df.iterrows():
        if i % 25 == 0:
            print(f"  {i}/{len(df)}")
        perf = fetch_perf(
            r["symbol"], r["timestamp"], r["price_at_decision"],
            sp500_history, r["entry_price_high"],
        )
        if perf:
            rows.append({
                "symbol": r["symbol"],
                "recommendation": r["recommendation"],
                "deep_research_verdict": r["deep_research_verdict"],
                "timestamp_dt": pd.to_datetime(r["timestamp"]),
                **perf,
            })

    p = pd.DataFrame(rows)
    if p.empty:
        print("No data.")
        return

    p["is_win"] = p["max_roi"] >= 10.0

    grouped = p.groupby(["recommendation", "deep_research_verdict"]).agg(
        count=("symbol", "count"),
        avg_max_roi=("max_roi", "mean"),
        avg_current_roi=("current_roi", "mean"),
        win_rate=("is_win", lambda x: x.sum() / len(x) * 100),
        has_limit_count=("has_limit", "sum"),
        trigger_count=("is_triggered", "sum"),
        limit_avg_max_roi=("limit_max_roi", lambda x: x.mean(skipna=True)),
        avg_sp500_max_roi=("sp500_max_roi", lambda x: x.mean(skipna=True)),
        avg_timestamp=("timestamp_dt", "mean"),
    ).reset_index()
    grouped["trigger_rate"] = grouped.apply(
        lambda r: r["trigger_count"] / r["has_limit_count"] * 100 if r["has_limit_count"] > 0 else 0, axis=1,
    )
    grouped = grouped[grouped["count"] >= 1].sort_values("avg_max_roi", ascending=False)

    limit_df = p[p["has_limit"]]
    triggered_df = limit_df[limit_df["is_triggered"]]
    missed_df = limit_df[~limit_df["is_triggered"]]
    overall_trigger_rate = len(triggered_df) / len(limit_df) * 100 if len(limit_df) else 0

    lines = [
        f"# Full 2026 Trading Performance Readout (as of {datetime.now().strftime('%Y-%m-%d')})",
        "",
        f"Combined dataset from `data/subscribers.db` and `subscribers.db` covering "
        f"`timestamp >= {START_DATE}` (no PENDING). Live prices through today via yfinance.",
        "",
        "## Cohort Performance",
        "",
        "| Recommendation | DR Verdict | N | Avg Date | Std Avg Max ROI | SP500 Avg Max ROI | Trigger Rate | Limit Fill Avg Max ROI | Win Rate (>10%) | Current ROI |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in grouped.iterrows():
        limit_roi = f"{r['limit_avg_max_roi']:.2f}%" if pd.notnull(r["limit_avg_max_roi"]) else "N/A"
        sp_roi = f"{r['avg_sp500_max_roi']:.2f}%" if pd.notnull(r["avg_sp500_max_roi"]) else "N/A"
        avg_date = r["avg_timestamp"].strftime("%b %d") if pd.notnull(r["avg_timestamp"]) else "N/A"
        lines.append(
            f"| {r['recommendation']} | {r['deep_research_verdict']} | {int(r['count'])} | {avg_date} | "
            f"{r['avg_max_roi']:.2f}% | {sp_roi} | {r['trigger_rate']:.1f}% | {limit_roi} | "
            f"{r['win_rate']:.1f}% | {r['avg_current_roi']:.2f}% |"
        )

    lines += [
        "",
        "## Overall Stats",
        "",
        f"- Trades analyzed: **{len(p)}**",
        f"- Overall system win rate (>10% peak): **{(p['is_win'].sum() / len(p) * 100):.1f}%**",
        f"- Trades with established entry limit: **{len(limit_df)}**",
        f"- True limit-order trigger rate: **{overall_trigger_rate:.1f}%**",
    ]
    if len(triggered_df):
        trig_win = (triggered_df["limit_max_roi"] >= 10.0).sum() / len(triggered_df) * 100
        base_win = (triggered_df["max_roi"] >= 10.0).sum() / len(triggered_df) * 100
        lines.append(
            f"- For the {len(triggered_df)} triggered limits, the better fill price "
            f"raised win rate from {base_win:.1f}% to **{trig_win:.1f}%** ({trig_win - base_win:+.1f}%)."
        )
    if len(missed_df):
        lines.append(
            f"- For the {len(missed_df)} missed limits, the price came within "
            f"**{missed_df['miss_distance'].mean():.2f}%** of the target on average; "
            f"those tickers' avg peak ROI from the decision price was "
            f"**{missed_df['max_roi'].mean():.2f}%**."
        )

    os.makedirs("reports", exist_ok=True)
    with open(OUTPUT, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
