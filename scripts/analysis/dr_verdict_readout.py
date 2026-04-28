"""Focused performance readout: Deep Research verdicts and their outcomes.

Combines data/subscribers.db (Dec 2025 - March 2026) and subscribers.db
(April 2026), filters to decisions that actually got a DR verdict, fetches
live prices via yfinance, and reports cohort performance + a clean chart.
"""

import os
import sqlite3
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import yfinance as yf

DBS = ["data/subscribers.db", "subscribers.db"]
START_DATE = "2026-01-15"
OUTPUT_MD = "reports/dr_verdict_readout.md"
OUTPUT_PNG = "docs/images/dr_verdict_distribution.png"
OUTPUT_BAR = "docs/images/dr_verdict_avg_roi.png"
ROI_CLIP = 300.0  # cap absurd outliers (corporate-action artifacts) for plotting


def load_dr_decisions():
    frames = []
    for db in DBS:
        if not os.path.exists(db):
            continue
        conn = sqlite3.connect(db)
        q = f"""
            SELECT id, symbol, price_at_decision, recommendation,
                   deep_research_verdict, timestamp,
                   entry_price_low, entry_price_high
            FROM decision_points
            WHERE timestamp >= '{START_DATE} 00:00:00'
              AND deep_research_verdict IS NOT NULL
              AND deep_research_verdict != ''
              AND deep_research_verdict != 'None'
              AND price_at_decision > 0
        """
        df = pd.read_sql_query(q, conn)
        frames.append(df)
        conn.close()
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=["symbol", "timestamp"], keep="last")


def fetch_perf(symbol, ts, original_price, sp500_history, entry_price_high=None):
    try:
        start = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        h = yf.Ticker(symbol).history(start=start, end=end)
        if h.empty:
            return None

        max_p, min_p = h["High"].max(), h["Low"].min()
        cur_p = h["Close"].iloc[-1]
        max_roi = (max_p - original_price) / original_price * 100
        cur_roi = (cur_p - original_price) / original_price * 100
        max_dd = (min_p - original_price) / original_price * 100

        sp_max = None
        if not sp500_history.empty:
            tz = sp500_history.index.tzinfo
            sd = pd.to_datetime(start)
            if tz is not None:
                sd = sd.tz_localize(tz)
            sl = sp500_history.loc[sd:]
            if not sl.empty:
                sp_max = (sl["High"].max() - sl["Close"].iloc[0]) / sl["Close"].iloc[0] * 100

        is_trig = False
        lim_max = None
        has_lim = pd.notnull(entry_price_high) and entry_price_high > 0
        if has_lim and h["Low"].min() <= entry_price_high:
            is_trig = True
            lim_max = (max_p - entry_price_high) / entry_price_high * 100

        return {
            "max_roi": max_roi, "current_roi": cur_roi, "max_drawdown": max_dd,
            "is_triggered": is_trig, "limit_max_roi": lim_max,
            "has_limit": has_lim, "sp500_max_roi": sp_max,
        }
    except Exception as e:
        print(f"  err {symbol}: {e}")
        return None


# canonical ordering, best -> worst (per system intent)
VERDICT_ORDER = [
    "STRONG_BUY", "SPECULATIVE_BUY", "BUY", "BUY_LIMIT",
    "WAIT_FOR_STABILIZATION", "WATCH", "AVOID", "HARD_AVOID",
]


def main():
    print("Loading DR-verdicted decisions...")
    df = load_dr_decisions()
    print(f"  {len(df)} decisions with a DR verdict (>= {START_DATE})")
    print(f"  verdict counts:\n{df['deep_research_verdict'].value_counts()}")

    print("Fetching SPY baseline...")
    sp = yf.Ticker("^GSPC").history(start=START_DATE)

    print("Fetching per-ticker history...")
    rows = []
    for i, r in df.iterrows():
        if i % 25 == 0:
            print(f"  {i}/{len(df)}")
        perf = fetch_perf(r["symbol"], r["timestamp"], r["price_at_decision"],
                          sp, r["entry_price_high"])
        if perf:
            rows.append({
                "symbol": r["symbol"],
                "deep_research_verdict": r["deep_research_verdict"],
                "timestamp_dt": pd.to_datetime(r["timestamp"]),
                **perf,
            })

    p = pd.DataFrame(rows)
    if p.empty:
        print("No data.")
        return

    # drop extreme outliers from cohort means (corporate-action artifacts)
    n_outliers = (p["max_roi"].abs() > ROI_CLIP).sum()
    if n_outliers:
        print(f"  filtering {n_outliers} extreme outlier(s) (|ROI| > {ROI_CLIP}%)")
    p = p[p["max_roi"].abs() <= ROI_CLIP].copy()

    p["is_win"] = p["max_roi"] >= 10.0
    p["is_loss"] = p["current_roi"] <= -10.0

    grouped = p.groupby("deep_research_verdict").agg(
        n=("symbol", "count"),
        avg_max_roi=("max_roi", "mean"),
        median_max_roi=("max_roi", "median"),
        avg_current_roi=("current_roi", "mean"),
        avg_drawdown=("max_drawdown", "mean"),
        win_rate=("is_win", lambda x: x.sum() / len(x) * 100),
        loss_rate=("is_loss", lambda x: x.sum() / len(x) * 100),
        avg_sp500_max=("sp500_max_roi", lambda x: x.mean(skipna=True)),
        avg_date=("timestamp_dt", "mean"),
    ).reset_index()

    # order by canonical, then any unknowns at the end
    grouped["_order"] = grouped["deep_research_verdict"].apply(
        lambda v: VERDICT_ORDER.index(v) if v in VERDICT_ORDER else 999
    )
    grouped = grouped.sort_values("_order").drop(columns=["_order"])

    # ---- chart 1: distribution per verdict (clipped, ordered) ----
    plt.figure(figsize=(12, 6))
    order_present = [v for v in VERDICT_ORDER if v in p["deep_research_verdict"].unique()]
    order_present += [v for v in p["deep_research_verdict"].unique() if v not in VERDICT_ORDER]
    sns.boxplot(data=p, x="max_roi", y="deep_research_verdict",
                order=order_present, color="#4C72B0", fliersize=2)
    plt.axvline(x=10, color="r", linestyle="--", label="+10% Win Threshold")
    plt.axvline(x=0, color="grey", linestyle=":", linewidth=0.8)
    plt.title("Peak ROI Distribution by Deep Research Verdict (Jan 15 → today)")
    plt.xlabel("Peak ROI (%) — outliers > 300% removed")
    plt.ylabel("Deep Research Verdict")
    plt.legend(loc="lower right")
    plt.tight_layout()
    os.makedirs(os.path.dirname(OUTPUT_PNG), exist_ok=True)
    plt.savefig(OUTPUT_PNG, dpi=120)
    plt.close()
    print(f"saved {OUTPUT_PNG}")

    # ---- chart 2: avg max ROI per verdict vs SPY ----
    plt.figure(figsize=(12, 6))
    melt = grouped.melt(
        id_vars="deep_research_verdict",
        value_vars=["avg_max_roi", "avg_sp500_max"],
        var_name="Metric", value_name="ROI",
    )
    melt["Metric"] = melt["Metric"].replace({
        "avg_max_roi": "Avg Peak ROI (stock)",
        "avg_sp500_max": "Avg Peak ROI (SPY, same window)",
    })
    sns.barplot(data=melt, x="ROI", y="deep_research_verdict",
                hue="Metric", order=order_present)
    plt.title("Avg Peak ROI by Deep Research Verdict vs SPY")
    plt.xlabel("Average Peak ROI (%)")
    plt.ylabel("Deep Research Verdict")
    plt.tight_layout()
    plt.savefig(OUTPUT_BAR, dpi=120)
    plt.close()
    print(f"saved {OUTPUT_BAR}")

    # ---- markdown report ----
    lines = [
        f"# Deep Research Verdict Readout (as of {datetime.now().strftime('%Y-%m-%d')})",
        "",
        f"Combined dataset from `data/subscribers.db` and `subscribers.db`, "
        f"filtered to decisions that received a Deep Research verdict "
        f"(`timestamp >= {START_DATE}`). Live prices through today via yfinance. "
        f"Outliers with |peak ROI| > {ROI_CLIP:.0f}% (corporate-action artifacts) "
        f"removed from cohort aggregates.",
        "",
        f"**Total DR-verdicted decisions analyzed: {len(p)}**",
        "",
        "## Cohort Performance",
        "",
        "| DR Verdict | N | Avg Date | Avg Peak ROI | Median Peak ROI | SPY Peak ROI (same windows) | Win Rate (>10%) | Loss Rate (<-10% current) | Avg Drawdown |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in grouped.iterrows():
        sp_str = f"{r['avg_sp500_max']:.2f}%" if pd.notnull(r["avg_sp500_max"]) else "N/A"
        avg_d = r["avg_date"].strftime("%b %d") if pd.notnull(r["avg_date"]) else "N/A"
        lines.append(
            f"| **{r['deep_research_verdict']}** | {int(r['n'])} | {avg_d} | "
            f"{r['avg_max_roi']:.2f}% | {r['median_max_roi']:.2f}% | "
            f"{sp_str} | {r['win_rate']:.1f}% | {r['loss_rate']:.1f}% | "
            f"{r['avg_drawdown']:.2f}% |"
        )

    overall_win = p["is_win"].sum() / len(p) * 100
    spy_avg = p["sp500_max_roi"].mean(skipna=True)
    lines += [
        "",
        "## Overall (DR-verdicted trades only)",
        "",
        f"- Trades analyzed: **{len(p)}**",
        f"- Overall win rate (>10% peak): **{overall_win:.1f}%**",
        f"- Average peak ROI across all DR-verdicted trades: **{p['max_roi'].mean():.2f}%**",
        f"- SPY average peak ROI over the same windows: **{spy_avg:.2f}%**",
        "",
        "## Charts",
        "",
        "![Distribution by DR Verdict](../docs/images/dr_verdict_distribution.png)",
        "",
        "![Avg ROI by DR Verdict vs SPY](../docs/images/dr_verdict_avg_roi.png)",
    ]
    os.makedirs(os.path.dirname(OUTPUT_MD), exist_ok=True)
    with open(OUTPUT_MD, "w") as f:
        f.write("\n".join(lines))
    print(f"saved {OUTPUT_MD}")


if __name__ == "__main__":
    main()
