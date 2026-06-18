"""Recovery trajectory by PM verdict: cumulative return path over 10 trading
days (2 weeks) after each recommendation. Anchored at the decision-day close."""
import sqlite3, warnings, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import yfinance as yf

warnings.filterwarnings("ignore")
OUT = "analysis_output"; os.makedirs(OUT, exist_ok=True)
sns.set_theme(style="whitegrid")
H = 10  # trading days (~2 weeks)

con = sqlite3.connect("subscribers.db")
dp = pd.read_sql_query("""
SELECT symbol, substr(timestamp,1,10) d, price_at_decision px, recommendation verdict
FROM decision_points
WHERE recommendation IN ('BUY','BUY_LIMIT','WATCH','AVOID') AND price_at_decision>0;""", con)
con.close()
dp = dp.drop_duplicates(["symbol","d"])
tickers = sorted(dp.symbol.unique())

px = yf.download(tickers, start="2026-04-08", end="2026-07-03",
                 progress=False, auto_adjust=True)["Close"]
if isinstance(px, pd.Series): px = px.to_frame()
px.index = px.index.strftime("%Y-%m-%d")

def path(sym, d):
    """cumulative % return from day0 close to day t, t=0..H. None if incomplete."""
    if sym not in px.columns: return None
    s = px[sym].dropna()
    days = [x for x in s.index if x >= d]
    if len(days) <= H: return None
    anchor = s[days[0]]
    if anchor <= 0: return None
    p = np.array([(s[days[t]]/anchor - 1)*100 for t in range(H+1)])
    if np.any(np.abs(p) > 60):   # split / bad-ticker artifact
        return None
    return p

rows, vlabel = [], []
for r in dp.itertuples():
    p = path(r.symbol, r.d)
    if p is not None:
        rows.append(p); vlabel.append(r.verdict)
paths = pd.DataFrame(rows, columns=[f"d{t}" for t in range(H+1)])
paths["verdict"] = vlabel
print(f"{len(paths)} decisions with a full {H}-day path")
print(paths.groupby("verdict").size().to_string())

order = ["BUY","BUY_LIMIT","WATCH","AVOID"]
colors = {"BUY":"#2a9d8f","BUY_LIMIT":"#457b9d","WATCH":"#e9c46a","AVOID":"#e76f51"}
x = np.arange(H+1)

fig, axes = plt.subplots(1, 2, figsize=(16, 6.8), sharey=True)
# LEFT: median path + IQR band per verdict
ax = axes[0]
for v in order:
    g = paths[paths.verdict==v][[f"d{t}" for t in range(H+1)]]
    med = g.median().values
    q1, q3 = g.quantile(.25).values, g.quantile(.75).values
    ax.plot(x, med, color=colors[v], lw=2.6, marker="o", ms=4,
            label=f"{v} (n={len(g)}, +{med[-1]:.1f}% @2wk)")
    ax.fill_between(x, q1, q3, color=colors[v], alpha=.12)
ax.axhline(0, color="#444", lw=1)
ax.set_title("Median recovery path (band = IQR)", weight="bold", fontsize=13)
ax.set_xlabel("Trading days after recommendation"); ax.set_ylabel("Cumulative return from decision close (%)")
ax.set_xticks(x); ax.legend(loc="upper left", fontsize=9)

# RIGHT: mean path (cleaner comparison of central tendency)
ax = axes[1]
for v in order:
    g = paths[paths.verdict==v][[f"d{t}" for t in range(H+1)]]
    mean = g.mean().values
    se = g.std().values/np.sqrt(len(g))
    ax.plot(x, mean, color=colors[v], lw=2.6, marker="s", ms=4,
            label=f"{v} (+{mean[-1]:.1f}% @2wk)")
    ax.fill_between(x, mean-se, mean+se, color=colors[v], alpha=.12)
ax.axhline(0, color="#444", lw=1)
ax.set_title("Mean recovery path (band = ±1 SE)", weight="bold", fontsize=13)
ax.set_xlabel("Trading days after recommendation")
ax.set_xticks(x); ax.legend(loc="upper left", fontsize=9)

fig.suptitle("StockDrop — post-recommendation recovery over 2 weeks, by PM verdict (real prices, ±60% artifacts dropped)",
             fontsize=14, weight="bold")
fig.tight_layout(rect=[0,0,1,0.95])
fig.savefig(f"{OUT}/recovery_curves_2wk.png", dpi=140)
print(f"\nsaved {OUT}/recovery_curves_2wk.png")

# print the day-by-day median table
med_tbl = paths.groupby("verdict")[[f"d{t}" for t in range(H+1)]].median().reindex(order)
print("\n=== median cumulative return by day ===")
print(med_tbl.round(2).to_string())
med_tbl.round(3).to_csv(f"{OUT}/recovery_curves_2wk.csv")
