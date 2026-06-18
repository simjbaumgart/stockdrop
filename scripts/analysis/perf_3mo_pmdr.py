"""3-month P&L analysis: PM & DR decisions, SP500-relative, std-dev + violin plots."""
import sqlite3, warnings
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import yfinance as yf

warnings.filterwarnings("ignore")
OUT = "analysis_output"
import os; os.makedirs(OUT, exist_ok=True)
sns.set_theme(style="whitegrid")

# ---- load positions joined to PM + DR verdicts ----
con = sqlite3.connect("subscribers.db")
df = pd.read_sql_query("""
SELECT sp.ticker, sp.status, sp.entry_date, substr(sp.exit_date,1,10) exit_date,
       sp.entry_price, sp.exit_price, sp.realized_pnl_pct, sp.unrealized_pnl_pct, sp.exit_reason,
       dp.recommendation pm_verdict, dp.deep_research_action dr_action,
       dp.deep_research_review_verdict dr_review
FROM desk_positions sp JOIN decision_points dp ON sp.decision_point_id=dp.id
ORDER BY sp.entry_date;""", con)
con.close()

# ---- SP500 tracker: real SPY closes for the window ----
spy = yf.download("SPY", start="2026-04-08", end="2026-06-16", progress=False, auto_adjust=True)["Close"]
spy = spy.squeeze()
spy.index = spy.index.strftime("%Y-%m-%d")
spy_map = spy.to_dict()

def spy_on(d):
    if d in spy_map: return spy_map[d]
    # nearest prior trading day
    prior = [k for k in spy_map if k <= str(d)]
    return spy_map[max(prior)] if prior else np.nan

df["entry_spy"] = df["entry_date"].map(spy_on)
df["exit_spy"]  = df["exit_date"].map(lambda d: spy_on(d) if pd.notna(d) else np.nan)

closed = df[df.status == "CLOSED"].copy()
closed["spy_ret"]  = (closed.exit_spy - closed.entry_spy) / closed.entry_spy * 100
closed["alpha"]    = closed.realized_pnl_pct - closed.spy_ret
closed["win"]      = closed.realized_pnl_pct > 0

# ---- period-level SP500 benchmark ----
period_spy_ret = (spy.iloc[-1] - spy.iloc[0]) / spy.iloc[0] * 100
print(f"\n# SP500 (SPY) over window {spy.index[0]}..{spy.index[-1]}: {period_spy_ret:+.2f}%  "
      f"({spy.iloc[0]:.2f} -> {spy.iloc[-1]:.2f})")

def block(g):
    return pd.Series({
        "n": len(g),
        "win_rate": 100*g.win.mean(),
        "avg_pnl": g.realized_pnl_pct.mean(),
        "std_pnl": g.realized_pnl_pct.std(),
        "sum_pnl": g.realized_pnl_pct.sum(),
        "avg_spy": g.spy_ret.mean(),
        "avg_alpha": g.alpha.mean(),
        "sharpe_like": g.realized_pnl_pct.mean()/g.realized_pnl_pct.std() if g.realized_pnl_pct.std() else np.nan,
    })

print("\n=== OVERALL (closed) ===")
print(block(closed).round(2).to_string())
print("\n=== BY PM VERDICT ===")
print(closed.groupby("pm_verdict").apply(block).round(2).to_string())
print("\n=== BY DR ACTION ===")
print(closed.groupby("dr_action").apply(block).round(2).to_string())

# save the combined table to csv
tbl = pd.concat([
    block(closed).to_frame("ALL").T,
    closed.groupby("pm_verdict").apply(block),
    closed.groupby("dr_action").apply(block).rename(index=lambda x: f"DR:{x}"),
])
tbl.round(2).to_csv(f"{OUT}/perf_table_3mo.csv")

# =====================================================================
# PLOT 1 — "complicated" std-dev figure: mean ± std by group, with
#          per-trade scatter, win-rate annotation, and SPY reference.
# =====================================================================
fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
for ax, key, title in [(axes[0], "pm_verdict", "PM verdict"),
                       (axes[1], "dr_action", "DR action")]:
    grp = closed.groupby(key)
    cats = list(grp.groups.keys())
    means = [grp.get_group(c).realized_pnl_pct.mean() for c in cats]
    stds  = [grp.get_group(c).realized_pnl_pct.std()  for c in cats]
    x = np.arange(len(cats))
    # per-trade jittered scatter
    for i, c in enumerate(cats):
        v = grp.get_group(c).realized_pnl_pct.values
        jit = np.random.default_rng(7).normal(0, 0.06, len(v))
        ax.scatter(np.full(len(v), i)+jit, v, color="#7f8fb0", s=34, alpha=.55, zorder=2)
    bars = ax.bar(x, means, yerr=stds, capsize=8, color="#3b6ea5", alpha=.85,
                  error_kw=dict(ecolor="#13335c", lw=2), zorder=3)
    ax.axhline(0, color="#555", lw=1)
    ax.axhline(period_spy_ret, color="#d1495b", ls="--", lw=1.8,
               label=f"SPY period {period_spy_ret:+.1f}%")
    for i, c in enumerate(cats):
        sub = grp.get_group(c)
        ax.text(i, means[i]+stds[i]+0.5, f"μ={means[i]:.1f}\nσ={stds[i]:.1f}\nn={len(sub)}\nwin {100*sub.win.mean():.0f}%",
                ha="center", va="bottom", fontsize=9, weight="bold")
    ax.set_xticks(x); ax.set_xticklabels(cats)
    ax.set_title(f"Mean P&L ± 1σ by {title}", fontsize=13, weight="bold")
    ax.set_ylabel("Realized P&L per trade (%)")
    ax.legend(loc="lower right")
fig.suptitle("StockDrop — Return dispersion by decision layer (closed trades, Apr 9 – May 14 2026)",
             fontsize=14, weight="bold")
fig.tight_layout(rect=[0,0,1,0.96])
fig.savefig(f"{OUT}/stddev_by_decision.png", dpi=140)
print(f"\nsaved {OUT}/stddev_by_decision.png")

# =====================================================================
# PLOT 2 — violin plot of P&L distributions (overall, PM, DR) + SPY line
# =====================================================================
fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
pal = "Set2"
sns.violinplot(data=closed, x="pm_verdict", y="realized_pnl_pct", ax=axes[0],
               hue="pm_verdict", palette=pal, inner="box", cut=0, legend=False)
sns.stripplot(data=closed, x="pm_verdict", y="realized_pnl_pct", ax=axes[0],
              color="#22303f", size=4, alpha=.6)
axes[0].set_title("P&L distribution by PM verdict", weight="bold")
sns.violinplot(data=closed, x="dr_action", y="realized_pnl_pct", ax=axes[1],
               hue="dr_action", palette=pal, inner="box", cut=0, legend=False)
sns.stripplot(data=closed, x="dr_action", y="realized_pnl_pct", ax=axes[1],
              color="#22303f", size=4, alpha=.6)
axes[1].set_title("P&L distribution by DR action", weight="bold")
for ax in axes:
    ax.axhline(0, color="#555", lw=1)
    ax.axhline(period_spy_ret, color="#d1495b", ls="--", lw=1.8,
               label=f"SPY {period_spy_ret:+.1f}%")
    ax.set_ylabel("Realized P&L per trade (%)"); ax.set_xlabel("")
    ax.legend(loc="lower right")
fig.suptitle("StockDrop — P&L distribution shape by decision layer", fontsize=14, weight="bold")
fig.tight_layout(rect=[0,0,1,0.95])
fig.savefig(f"{OUT}/violin_by_decision.png", dpi=140)
print(f"saved {OUT}/violin_by_decision.png")

# ---- strategy vs SPY equity curve (sequential, equal-weight) ----
print("\n=== relative-to-SP500 summary ===")
print(f"strategy avg/trade {closed.realized_pnl_pct.mean():+.2f}%  |  "
      f"SPY avg over same holds {closed.spy_ret.mean():+.2f}%  |  "
      f"avg alpha {closed.alpha.mean():+.2f}%")
print(f"strategy win vs SPY (beat benchmark): {100*(closed.alpha>0).mean():.0f}% of trades")
