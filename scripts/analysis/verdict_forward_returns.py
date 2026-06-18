"""Forward-return predictive analysis across ALL PM verdicts (incl. AVOID/WATCH).

Measures the 5-trading-day forward return from each decision's anchor close.
A 'correct' AVOID/WATCH should underperform what the system BUYs.
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
HORIZON = 5  # trading days

con = sqlite3.connect("subscribers.db")
dp = pd.read_sql_query("""
SELECT symbol, substr(timestamp,1,10) d, price_at_decision px, recommendation verdict
FROM decision_points
WHERE recommendation IN ('BUY','BUY_LIMIT','WATCH','AVOID') AND price_at_decision>0
ORDER BY timestamp;""", con)
con.close()

# de-dupe: one verdict per symbol+day (keep first)
dp = dp.drop_duplicates(["symbol","d"])
tickers = sorted(dp.symbol.unique())
print(f"{len(dp)} decisions, {len(tickers)} unique tickers")

px = yf.download(tickers, start="2026-04-08", end="2026-06-19",
                 progress=False, auto_adjust=True)["Close"]
if isinstance(px, pd.Series):  # single ticker edge case
    px = px.to_frame()
px.index = px.index.strftime("%Y-%m-%d")
trade_days = list(px.index)

def fwd_return(sym, d):
    if sym not in px.columns: return np.nan
    s = px[sym].dropna()
    days = [x for x in s.index if x >= d]
    if len(days) <= HORIZON: return np.nan
    anchor = s[days[0]]
    fwd = s[days[HORIZON]]
    if anchor <= 0: return np.nan
    return (fwd/anchor - 1)*100

dp["fwd5"] = [fwd_return(r.symbol, r.d) for r in dp.itertuples()]
ev = dp.dropna(subset=["fwd5"]).copy()
# winsorize obvious data-glitch outliers (splits/bad tickers): |5d move|>30% on a
# large-cap is almost always an artifact. Clip for robust means; medians unaffected.
CLIP = 30
ev["fwd5w"] = ev.fwd5.clip(-CLIP, CLIP)
n_clip = int((ev.fwd5.abs() > CLIP).sum())
print(f"{len(ev)}/{len(dp)} decisions have a full +{HORIZON}d window; "
      f"{n_clip} outliers winsorized at ±{CLIP}%\n")

order = ["BUY","BUY_LIMIT","WATCH","AVOID"]
def block(g):
    return pd.Series({
        "n": len(g),
        "pct_up": 100*(g.fwd5>0).mean(),
        "median_fwd5": g.fwd5.median(),
        "wins_mean_fwd5": g.fwd5w.mean(),
        "std_w": g.fwd5w.std(),
    })
tbl = ev.groupby("verdict").apply(block).reindex(order)
print("=== +5d forward return by PM verdict ===")
print(tbl.round(2).to_string())
tbl.round(2).to_csv(f"{OUT}/verdict_forward_returns.csv")

# spread: do BUYs beat AVOIDs? (medians = robust)
buys = ev[ev.verdict.isin(["BUY","BUY_LIMIT"])]
avoids = ev[ev.verdict=="AVOID"]
print(f"\nBUY+BUY_LIMIT median +{buys.fwd5.median():.2f}%  vs  AVOID median {avoids.fwd5.median():+.2f}%  "
      f"=> median separation {buys.fwd5.median()-avoids.fwd5.median():+.2f} pts")

# ---- violin: forward-return distribution across ALL four verdicts (winsorized) ----
fig, ax = plt.subplots(figsize=(12, 6.5))
sns.violinplot(data=ev, x="verdict", y="fwd5w", order=order, hue="verdict",
               palette="Set2", inner="box", cut=0, legend=False, ax=ax)
sns.stripplot(data=ev, x="verdict", y="fwd5w", order=order, color="#22303f",
              size=2.5, alpha=.35, ax=ax)
meds = ev.groupby("verdict").fwd5.median().reindex(order)
ax.scatter(range(len(order)), meds.values, color="#c0392b", marker="D", s=70, zorder=5, label="median")
for i,v in enumerate(order):
    sub = ev[ev.verdict==v]
    ax.text(i, 27, f"n={len(sub)}\nmed={sub.fwd5.median():.2f}%\nup {100*(sub.fwd5>0).mean():.0f}%",
            ha="center", va="top", fontsize=9, weight="bold")
ax.set_ylim(-30, 30)
ax.axhline(0, color="#555", lw=1)
ax.set_title("StockDrop — +5-day forward return by PM verdict (winsorized ±30%; does the screen separate winners from avoids?)",
             fontsize=12, weight="bold")
ax.set_ylabel("Forward return, decision close → +5 trading days (%)"); ax.set_xlabel("")
ax.legend(loc="lower right")
fig.tight_layout()
fig.savefig(f"{OUT}/forward_return_all_verdicts.png", dpi=140)
print(f"\nsaved {OUT}/forward_return_all_verdicts.png")
