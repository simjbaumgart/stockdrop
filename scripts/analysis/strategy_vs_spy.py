"""Strategy vs SPY on MATCHED holding windows (apples-to-apples).

Replaces the misleading 'mean P&L vs SPY full-period' chart: each closed trade
is compared to SPY's return over that trade's exact entry->exit window, so a
3-day hold is judged against 3 days of SPY, not 2 months of buy-and-hold.
"""
import sqlite3, warnings, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import yfinance as yf

warnings.filterwarnings("ignore")
OUT = "analysis_output"; os.makedirs(OUT, exist_ok=True)
sns.set_theme(style="whitegrid")

con = sqlite3.connect("subscribers.db")
df = pd.read_sql_query("""
SELECT sp.ticker, sp.entry_date, substr(sp.exit_date,1,10) exit_date,
       sp.realized_pnl_pct, dp.recommendation pm_verdict, dp.deep_research_action dr_action
FROM desk_positions sp JOIN decision_points dp ON sp.decision_point_id=dp.id
WHERE sp.realized_pnl_pct IS NOT NULL;""", con)
con.close()

spy = yf.download("SPY", start="2026-04-08", end="2026-06-16", progress=False, auto_adjust=True)["Close"]
spy = spy.squeeze(); spy.index = spy.index.strftime("%Y-%m-%d"); sm = spy.to_dict()
def spy_on(d):
    ks = [k for k in sm if k <= str(d)]
    return sm[max(ks)] if ks else np.nan
df["spy_ret"] = (df.exit_date.map(spy_on) - df.entry_date.map(spy_on)) / df.entry_date.map(spy_on) * 100
df["alpha"] = df.realized_pnl_pct - df.spy_ret

avg_hold = (pd.to_datetime(df.exit_date) - pd.to_datetime(df.entry_date)).dt.days.mean()

def stats(g):
    n = len(g)
    return dict(n=n,
                strat=g.realized_pnl_pct.mean(), strat_se=g.realized_pnl_pct.std()/np.sqrt(n),
                spy=g.spy_ret.mean(),            spy_se=g.spy_ret.std()/np.sqrt(n),
                alpha=g.alpha.mean(), beat=100*(g.alpha>0).mean())

def panel(ax, groups, title):
    labels = [g[0] for g in groups]
    S  = [g[1] for g in groups]
    x = np.arange(len(labels)); w = 0.38
    b1 = ax.bar(x-w/2, [s["strat"] for s in S], w, yerr=[s["strat_se"] for s in S],
                capsize=5, color="#2a6f97", label="Strategy", zorder=3,
                error_kw=dict(ecolor="#16364d", lw=1.4))
    b2 = ax.bar(x+w/2, [s["spy"] for s in S], w, yerr=[s["spy_se"] for s in S],
                capsize=5, color="#bcccdc", label="SPY (same window)", zorder=3,
                error_kw=dict(ecolor="#6b7a8c", lw=1.4))
    ax.axhline(0, color="#444", lw=1)
    for i, s in enumerate(S):
        top = max(s["strat"]+s["strat_se"], s["spy"]+s["spy_se"])
        col = "#1b7a3d" if s["alpha"] >= 0 else "#b3261e"
        ax.annotate(f"α {s['alpha']:+.2f} pts\nbeat SPY {s['beat']:.0f}%",
                    (i, top+0.12), ha="center", va="bottom", fontsize=9.5,
                    weight="bold", color=col)
        ax.text(i, -0.06, f"n={s['n']}", ha="center", va="top",
                transform=ax.get_xaxis_transform(), fontsize=8.5, color="#555")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_title(title, fontsize=13, weight="bold")
    ax.set_ylabel("Mean return per trade (%)  ·  bars = ±1 SE")
    ax.legend(loc="upper left", framealpha=.9)
    ax.margins(y=0.22)

allrow = ("ALL", stats(df))
pm = [allrow] + [(v, stats(df[df.pm_verdict==v])) for v in ["BUY","BUY_LIMIT"]]
dr = [allrow] + [(v, stats(df[df.dr_action==v])) for v in ["BUY","BUY_LIMIT"]]

fig, axes = plt.subplots(1, 2, figsize=(15, 6.5), sharey=True)
panel(axes[0], pm, "By PM verdict")
panel(axes[1], dr, "By DR action")
fig.suptitle(f"StockDrop — Strategy vs SPY on matched holding windows  "
             f"(40 closed trades, avg hold ≈{avg_hold:.0f} days, Apr 9 – May 14 2026)",
             fontsize=14, weight="bold")
fig.text(0.5, 0.005,
         "Each trade is benchmarked against SPY's return over its own entry→exit window — not full-period buy-and-hold. "
         "Positive α (green) = the desk beat SPY for the time it was actually exposed.",
         ha="center", fontsize=9.5, style="italic", color="#444")
fig.tight_layout(rect=[0,0.03,1,0.96])
fig.savefig(f"{OUT}/strategy_vs_spy_matched.png", dpi=140)
print("saved", f"{OUT}/strategy_vs_spy_matched.png")
print("\n=== matched-window summary ===")
for name, s in [("ALL",stats(df))]+[(f"PM:{v}",stats(df[df.pm_verdict==v])) for v in ["BUY","BUY_LIMIT"]]+\
                [(f"DR:{v}",stats(df[df.dr_action==v])) for v in ["BUY","BUY_LIMIT"]]:
    print(f"{name:12s} n={s['n']:2d}  strat {s['strat']:+.2f}  spy {s['spy']:+.2f}  "
          f"alpha {s['alpha']:+.2f}  beat {s['beat']:.0f}%")
