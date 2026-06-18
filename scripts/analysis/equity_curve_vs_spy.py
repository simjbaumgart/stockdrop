"""Equity curve: desk vs SPY, with time-in-market shading.

Explains the +11.7% 'SPY buy-and-hold' vs matched-window-alpha gap: SPY is
invested every day; the desk only has capital at risk during its short holds.
Daily mark-to-market per ticker (yfinance); equal-weight across concurrent
open positions; cash (0% return) on days with no open trade.
"""
import sqlite3, warnings, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import yfinance as yf

warnings.filterwarnings("ignore")
OUT = "analysis_output"; os.makedirs(OUT, exist_ok=True)
sns.set_theme(style="whitegrid")

con = sqlite3.connect("subscribers.db")
tr = pd.read_sql_query("""
SELECT sp.ticker, sp.entry_date, substr(sp.exit_date,1,10) exit_date,
       sp.entry_price, sp.exit_price, sp.realized_pnl_pct
FROM desk_positions sp
WHERE sp.realized_pnl_pct IS NOT NULL AND sp.exit_date IS NOT NULL;""", con)
con.close()

start, end = "2026-04-08", "2026-06-16"
tickers = sorted(tr.ticker.unique())
px = yf.download(tickers + ["SPY"], start=start, end=end, progress=False, auto_adjust=True)["Close"]
px.index = pd.to_datetime(px.index)
cal = px.index  # trading days

# build daily portfolio return: equal-weight across positions open that day
daily_ret = pd.Series(0.0, index=cal)
in_mkt = pd.Series(0, index=cal)
for d in cal:
    rets = []
    for t in tr.itertuples():
        ed, xd = pd.Timestamp(t.entry_date), pd.Timestamp(t.exit_date)
        if ed < d <= xd and t.ticker in px.columns:
            s = px[t.ticker]
            if d in s.index:
                prev = s.loc[:d].iloc[-2:] if len(s.loc[:d]) >= 2 else None
                if prev is not None and prev.iloc[0] > 0 and not np.isnan(prev.iloc[-1]):
                    rets.append(prev.iloc[-1]/prev.iloc[0] - 1)
    if rets:
        daily_ret[d] = np.mean(rets)
        in_mkt[d] = 1

strat_eq = 100 * (1 + daily_ret).cumprod()
spy_ret = px["SPY"].pct_change().fillna(0)
spy_eq = 100 * (1 + spy_ret).cumprod()

# clip to the active trading window (first entry .. last exit)
lo, hi = pd.Timestamp(tr.entry_date.min()), pd.Timestamp(tr.exit_date.max())
mask = (cal >= lo) & (cal <= hi)
cal_w = cal[mask]
strat_eq = 100 * strat_eq[mask] / strat_eq[mask].iloc[0]
spy_eq   = 100 * spy_eq[mask]   / spy_eq[mask].iloc[0]
in_mkt_w = in_mkt[mask]
pct_in = 100 * in_mkt_w.mean()

fig, ax = plt.subplots(figsize=(13, 6.8))
edge = (strat_eq.iloc[-1] - spy_eq.iloc[-1])
ax.plot(cal_w, spy_eq.values, color="#b3261e", lw=2.6, label=f"SPY buy & hold  →  {spy_eq.iloc[-1]-100:+.1f}%")
ax.plot(cal_w, strat_eq.values, color="#2a6f97", lw=2.8, label=f"Desk (equal-weight portfolio)  →  {strat_eq.iloc[-1]-100:+.1f}%")
ax.fill_between(cal_w, spy_eq.values, strat_eq.values,
                where=(strat_eq.values >= spy_eq.values), color="#2a6f97", alpha=0.10, zorder=1)
ax.axhline(100, color="#888", lw=1, ls=":")
ax.set_title("StockDrop — desk equity vs SPY buy-and-hold (Apr 9 – May 14 2026)",
             fontsize=14, weight="bold")
ax.set_ylabel("Growth of 100 (daily mark-to-market)")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
ax.legend(loc="upper left", fontsize=11, framealpha=.95)
ax.text(0.985, 0.05,
        f"Desk held ≥1 position on {pct_in:.0f}% of days (heavy overlap),\n"
        f"so this is a like-for-like, fully-invested comparison.\n"
        f"→ Desk {strat_eq.iloc[-1]-100:+.1f}% vs SPY {spy_eq.iloc[-1]-100:+.1f}%  =  {edge:+.1f} pts ahead.",
        transform=ax.transAxes, ha="right", va="bottom", fontsize=10,
        bbox=dict(boxstyle="round,pad=0.5", fc="#eef5ee", ec="#8fb98f"))
fig.tight_layout()
fig.savefig(f"{OUT}/equity_curve_vs_spy.png", dpi=140)
print("saved", f"{OUT}/equity_curve_vs_spy.png")
print(f"desk final {strat_eq.iloc[-1]-100:+.2f}%  | SPY {spy_eq.iloc[-1]-100:+.2f}%  | in-market {pct_in:.0f}% of days")
