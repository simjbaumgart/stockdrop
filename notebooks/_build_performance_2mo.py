"""
Generator for notebooks/performance_2mo.ipynb.

Run: `python notebooks/_build_performance_2mo.py`

Keeping the cell sources in plain Python (rather than editing JSON directly)
makes the notebook diffable and re-generatable. The .ipynb is the artifact;
this script is the source of truth.
"""

import json
from pathlib import Path

CELLS = []


def md(src: str):
    CELLS.append({
        "cell_type": "markdown",
        "metadata": {},
        "source": src.strip("\n").splitlines(keepends=True),
    })


def code(src: str):
    CELLS.append({
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": src.strip("\n").splitlines(keepends=True),
    })


# ============================================================================
# Title
# ============================================================================
md("""
# StockDrop — 2-Month Performance Analysis

Window: decisions made in the last 60 days (configurable below).
Goal: extract maximum insight from the recent recommendation sample using
non-parametric statistics + distribution-aware visualization.

**Sections**
0. Sample overview
1. Headline performance per intent
2. Statistical tests
3. Return distributions
4. Time evolution
5. Deep Research signal
6. Subgroup breakdowns
7. Calibration
8. Risk metrics (MFE / MAE / drawdown)
9. Takeaways
""")

# ============================================================================
# Imports & config
# ============================================================================
code("""
import sqlite3
import warnings
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import yfinance as yf
from scipy import stats

warnings.filterwarnings("ignore", category=FutureWarning)
sns.set_theme(style="whitegrid", context="notebook")
plt.rcParams["figure.dpi"] = 110

# --- Config ---------------------------------------------------------------
REPO_ROOT = Path.cwd() if Path.cwd().name == "Stock-Tracker" else Path.cwd().parent
DB_PATH = REPO_ROOT / "subscribers.db"
WINDOW_DAYS = 60
TODAY = pd.Timestamp("2026-05-24")  # frozen for reproducibility; change to pd.Timestamp.today().normalize() for live runs
CUTOFF = TODAY - pd.Timedelta(days=WINDOW_DAYS)
HORIZONS = [1, 3, 7, 14, 30]  # trading-day horizons after entry
BENCHMARK = "SPY"

print(f"DB: {DB_PATH}  (exists: {DB_PATH.exists()})")
print(f"Window: {CUTOFF.date()}  →  {TODAY.date()}  ({WINDOW_DAYS} days)")
""")

# ============================================================================
# Load decisions
# ============================================================================
md("## Data prep")

code("""
import sys
sys.path.insert(0, str(REPO_ROOT))
from app.services.performance_service import normalize_to_intent

con = sqlite3.connect(DB_PATH)
decisions_raw = pd.read_sql_query(
    \"\"\"
    SELECT id, symbol, timestamp, price_at_decision, drop_percent,
           recommendation, status, sector, drop_type,
           gatekeeper_tier, ai_score,
           deep_research_action, deep_research_conviction, deep_research_score,
           deep_research_entry_low, deep_research_entry_high,
           deep_research_stop_loss, deep_research_tp1, deep_research_tp2,
           entry_price_low, entry_price_high, stop_loss, take_profit_1, take_profit_2
    FROM decision_points
    WHERE timestamp >= ?
    ORDER BY timestamp
    \"\"\",
    con,
    params=[CUTOFF.isoformat()],
)
con.close()

# Filter out obvious test data (synthetic tickers used in unit tests)
decisions_raw = decisions_raw[
    ~decisions_raw["symbol"].str.startswith(("TEST_", "T8_"), na=False)
].reset_index(drop=True)

decisions_raw["timestamp"] = pd.to_datetime(decisions_raw["timestamp"])
decisions_raw["entry_date"] = decisions_raw["timestamp"].dt.normalize()
decisions_raw["intent"] = decisions_raw["recommendation"].fillna("").apply(normalize_to_intent)
decisions_raw["dr_intent"] = decisions_raw["deep_research_action"].fillna("").apply(normalize_to_intent)

print(f"Loaded {len(decisions_raw)} decisions.")
print(decisions_raw["intent"].value_counts().to_string())
""")

# ============================================================================
# Batch fetch prices
# ============================================================================
code("""
# One batched yfinance call covers every ticker + benchmark for the window.
# We fetch a couple of days before the cutoff so we always have an entry price.
tickers = sorted(decisions_raw["symbol"].dropna().unique().tolist())
fetch_list = tickers + [BENCHMARK]

print(f"Fetching {len(fetch_list)} tickers from yfinance ...")
prices = yf.download(
    fetch_list,
    start=(CUTOFF - pd.Timedelta(days=10)).date(),
    end=(TODAY + pd.Timedelta(days=1)).date(),
    progress=False,
    auto_adjust=False,
    group_by="ticker",
    threads=True,
)
print(f"yfinance returned shape={prices.shape}")
""")

code("""
def get_ohlc(ticker):
    \"\"\"Return per-ticker (Close, High, Low) DataFrame, or None if missing.\"\"\"
    try:
        sub = prices[ticker][["Close", "High", "Low"]].dropna(how="all")
        if sub.empty:
            return None
        sub.index = pd.to_datetime(sub.index).tz_localize(None).normalize()
        return sub
    except (KeyError, AttributeError):
        return None


spy = get_ohlc(BENCHMARK)
assert spy is not None, "SPY benchmark missing; check yfinance connection."


def nth_trading_day(series_index, entry_date, n):
    \"\"\"Return the date that is n trading days after entry, or last available if past horizon.\"\"\"
    future = series_index[series_index >= entry_date]
    if len(future) == 0:
        return None
    if n >= len(future):
        return future[-1]  # not enough history yet → use most recent
    return future[n]


def first_close_on_or_after(ohlc, date):
    \"\"\"Entry price = first available close on or after the decision date.\"\"\"
    future = ohlc.loc[ohlc.index >= date]
    if future.empty:
        return None, None
    return future.index[0], float(future["Close"].iloc[0])
""")

code("""
records = []
missing_tickers = []

for _, row in decisions_raw.iterrows():
    sym = row["symbol"]
    entry_date = row["entry_date"]
    ohlc = get_ohlc(sym)
    if ohlc is None:
        missing_tickers.append(sym)
        continue

    actual_entry, entry_px = first_close_on_or_after(ohlc, entry_date)
    if entry_px is None or entry_px <= 0:
        missing_tickers.append(sym)
        continue

    spy_entry_date, spy_entry_px = first_close_on_or_after(spy, entry_date)
    if spy_entry_px is None or spy_entry_px <= 0:
        continue

    rec = {"id": int(row["id"]), "symbol": sym, "entry_date": actual_entry,
           "entry_price": entry_px, "spy_entry_price": spy_entry_px}

    # Returns at each horizon (trading days after entry)
    for h in HORIZONS:
        d = nth_trading_day(ohlc.index, actual_entry, h)
        spy_d = nth_trading_day(spy.index, spy_entry_date, h)
        if d is None or spy_d is None:
            rec[f"ret_d{h}"] = np.nan
            rec[f"alpha_d{h}"] = np.nan
            rec[f"matured_d{h}"] = False
            continue
        close = float(ohlc.loc[d, "Close"])
        spy_close = float(spy.loc[spy_d, "Close"])
        r = close / entry_px - 1.0
        s = spy_close / spy_entry_px - 1.0
        rec[f"ret_d{h}"] = r
        rec[f"alpha_d{h}"] = r - s
        # "Matured" = enough calendar time has elapsed since entry for this horizon
        rec[f"matured_d{h}"] = (TODAY - actual_entry).days >= h

    # "Current" return = latest close vs entry
    latest_close = float(ohlc["Close"].iloc[-1])
    latest_spy = float(spy["Close"].iloc[-1])
    rec["ret_current"] = latest_close / entry_px - 1.0
    rec["alpha_current"] = rec["ret_current"] - (latest_spy / spy_entry_px - 1.0)
    rec["days_held"] = (ohlc.index[-1] - actual_entry).days

    # MFE / MAE from daily highs/lows over a 30-trading-day window
    window = ohlc.loc[(ohlc.index >= actual_entry) & (ohlc.index <= actual_entry + pd.Timedelta(days=45))]
    if len(window) >= 2:
        rec["mfe"] = float(window["High"].max()) / entry_px - 1.0
        rec["mae"] = float(window["Low"].min()) / entry_px - 1.0
        # Max drawdown from running peak of Close
        peak = window["Close"].cummax()
        dd = window["Close"] / peak - 1.0
        rec["max_drawdown"] = float(dd.min())
    else:
        rec["mfe"] = rec["mae"] = rec["max_drawdown"] = np.nan

    records.append(rec)

returns_df = pd.DataFrame(records)
df = decisions_raw.merge(returns_df, on=["id", "symbol"], how="left", suffixes=("", "_y"))

print(f"Decisions with price data: {df['entry_price'].notna().sum()} / {len(df)}")
print(f"Missing/failed tickers: {len(set(missing_tickers))} unique")
""")

# ============================================================================
# Section 0
# ============================================================================
md("## 0. Sample overview")

code("""
print(f"Total decisions in window: {len(df)}")
print(f"With usable price data:    {df['entry_price'].notna().sum()}")
print(f"Date range:  {df['entry_date'].min().date()}  →  {df['entry_date'].max().date()}")
print()
print("By intent:")
print(df["intent"].value_counts().to_string())
print()
print("By raw recommendation:")
print(df["recommendation"].value_counts().to_string())
print()
print("By status:")
print(df["status"].value_counts().to_string())
""")

code("""
fig, axes = plt.subplots(1, 3, figsize=(16, 4))

df["intent"].value_counts().plot(kind="bar", ax=axes[0], color="steelblue")
axes[0].set_title("Intent distribution")
axes[0].set_ylabel("count")
axes[0].tick_params(axis="x", rotation=30)

df["entry_date"].dt.date.value_counts().sort_index().plot(ax=axes[1], color="darkorange")
axes[1].set_title("Decisions per day")
axes[1].set_ylabel("count")

# Tracking coverage check (we expect 0 rows for this window)
con = sqlite3.connect(DB_PATH)
tc = pd.read_sql_query(
    "SELECT decision_id, COUNT(*) AS n FROM decision_tracking GROUP BY decision_id",
    con,
)
con.close()
covered = df["id"].isin(tc["decision_id"]).sum()
axes[2].bar(["no tracking", "tracked"], [len(df) - covered, covered], color=["lightcoral", "seagreen"])
axes[2].set_title(f"decision_tracking coverage  ({covered}/{len(df)})")

plt.tight_layout()
plt.show()
""")

# ============================================================================
# Section 1
# ============================================================================
md("""
## 1. Headline performance per intent

Mean / median / win-rate per intent at each horizon, with bootstrap 95% CIs.

**Win rate** = share of decisions with `return > 0` at that horizon, using only
*matured* decisions (i.e. enough time has passed since entry).
""")

code("""
RNG = np.random.default_rng(42)


def bootstrap_ci(x, fn, n_boot=2000, alpha=0.05):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 3:
        return (np.nan, np.nan)
    idx = RNG.integers(0, len(x), size=(n_boot, len(x)))
    samples = np.array([fn(x[i]) for i in idx])
    return tuple(np.quantile(samples, [alpha / 2, 1 - alpha / 2]))


def summarize(group, col, matured_col=None):
    if matured_col is not None and matured_col in group.columns:
        mask = group[matured_col].fillna(False).astype(bool)
        x = group.loc[mask, col].dropna()
    else:
        x = group[col].dropna()
    if len(x) == 0:
        return dict(n=0, mean=np.nan, median=np.nan, std=np.nan, win_rate=np.nan,
                    mean_lo=np.nan, mean_hi=np.nan, wr_lo=np.nan, wr_hi=np.nan)
    mean_lo, mean_hi = bootstrap_ci(x.values, np.mean)
    wins = (x > 0).astype(float).values
    wr_lo, wr_hi = bootstrap_ci(wins, np.mean)
    return dict(
        n=len(x), mean=x.mean(), median=x.median(), std=x.std(),
        win_rate=(x > 0).mean(),
        mean_lo=mean_lo, mean_hi=mean_hi, wr_lo=wr_lo, wr_hi=wr_hi,
    )


rows = []
for horizon in ["d7", "d30", "current"]:
    col = f"ret_{horizon}"
    matured = f"matured_{horizon}" if horizon != "current" else None
    for intent, g in df.groupby("intent"):
        s = summarize(g, col, matured)
        rows.append({"horizon": horizon, "intent": intent, **s})

headline = pd.DataFrame(rows)
print("Returns by intent (bootstrap 95% CIs in brackets):")
for h in ["d7", "d30", "current"]:
    print(f"\\n--- {h} ---")
    sub = headline[headline["horizon"] == h].set_index("intent")
    for intent, r in sub.iterrows():
        if r["n"] == 0:
            continue
        print(f"  {intent:12s}  n={int(r['n']):3d}  "
              f"mean={r['mean']*100:+6.2f}%  [{r['mean_lo']*100:+6.2f}, {r['mean_hi']*100:+6.2f}]  "
              f"win_rate={r['win_rate']*100:5.1f}%  [{r['wr_lo']*100:5.1f}, {r['wr_hi']*100:5.1f}]")
""")

code("""
# SPY-relative alpha (mean) per intent at d30 and current
print("SPY-relative alpha (mean) by intent:")
for horizon in ["d7", "d30", "current"]:
    col = f"alpha_{horizon}"
    matured = f"matured_{horizon}" if horizon != "current" else None
    print(f"\\n--- {horizon} ---")
    for intent, g in df.groupby("intent"):
        x = g.loc[g[matured].fillna(False).astype(bool), col].dropna() if matured else g[col].dropna()
        if len(x) < 3:
            continue
        lo, hi = bootstrap_ci(x.values, np.mean)
        print(f"  {intent:12s}  n={len(x):3d}  alpha={x.mean()*100:+6.2f}%  [{lo*100:+6.2f}, {hi*100:+6.2f}]")
""")

# ============================================================================
# Section 2
# ============================================================================
md("""
## 2. Statistical tests

Non-parametric throughout. With this sample size and skewed return distributions,
parametric tests overstate confidence.

- **Mann-Whitney U**: does ENTER_NOW (and ENTER_LIMIT) outperform AVOID?
- **Sign test (binomial)**: are buy returns asymmetrically positive?
- **KS test**: are buy and avoid return distributions actually different shapes?
- **Cohen's d**: effect size with bootstrap CI.
""")

code("""
def mannwhitney(a, b):
    a = np.asarray(a, dtype=float); a = a[~np.isnan(a)]
    b = np.asarray(b, dtype=float); b = b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    u, p = stats.mannwhitneyu(a, b, alternative="greater")
    return {"n_a": len(a), "n_b": len(b), "median_a": np.median(a),
            "median_b": np.median(b), "U": u, "p_one_sided": p}


def sign_test(x, null=0.0):
    x = np.asarray(x, dtype=float); x = x[~np.isnan(x)]
    if len(x) < 3:
        return None
    wins = int((x > null).sum())
    n = len(x)
    p = stats.binomtest(wins, n, p=0.5, alternative="greater").pvalue
    return {"n": n, "wins": wins, "win_rate": wins / n, "p_one_sided": p}


def ks_test(a, b):
    a = np.asarray(a, dtype=float); a = a[~np.isnan(a)]
    b = np.asarray(b, dtype=float); b = b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    stat, p = stats.ks_2samp(a, b)
    return {"D": stat, "p": p}


def cohens_d(a, b):
    a = np.asarray(a, dtype=float); a = a[~np.isnan(a)]
    b = np.asarray(b, dtype=float); b = b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    pooled = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
    d = (a.mean() - b.mean()) / pooled if pooled > 0 else np.nan
    # Bootstrap CI on d
    def _d(idx_a, idx_b):
        aa, bb = a[idx_a], b[idx_b]
        p = np.sqrt(((len(aa)-1)*aa.var(ddof=1) + (len(bb)-1)*bb.var(ddof=1)) / (len(aa)+len(bb)-2))
        return (aa.mean() - bb.mean()) / p if p > 0 else np.nan
    boots = []
    for _ in range(1000):
        ia = RNG.integers(0, len(a), size=len(a))
        ib = RNG.integers(0, len(b), size=len(b))
        boots.append(_d(ia, ib))
    return {"d": d, "ci_lo": np.nanquantile(boots, 0.025), "ci_hi": np.nanquantile(boots, 0.975)}


for horizon in ["d30", "current"]:
    col = f"ret_{horizon}"
    matured = f"matured_{horizon}" if horizon != "current" else None
    base = df[df[matured].fillna(False).astype(bool)] if matured else df

    buy = base.loc[base["intent"] == "ENTER_NOW", col].dropna().values
    lim = base.loc[base["intent"] == "ENTER_LIMIT", col].dropna().values
    avoid = base.loc[base["intent"] == "AVOID", col].dropna().values

    print(f"\\n========== HORIZON: {horizon} ==========")
    print(f"  n  ENTER_NOW={len(buy)}  ENTER_LIMIT={len(lim)}  AVOID={len(avoid)}")

    for name, arr in [("ENTER_NOW", buy), ("ENTER_LIMIT", lim)]:
        if len(arr) < 3 or len(avoid) < 3:
            continue
        mw = mannwhitney(arr, avoid)
        ks = ks_test(arr, avoid)
        d = cohens_d(arr, avoid)
        print(f"\\n  {name} vs AVOID:")
        print(f"    Mann-Whitney U p (greater): {mw['p_one_sided']:.4f}  (medians: {mw['median_a']*100:+.2f}% vs {mw['median_b']*100:+.2f}%)")
        print(f"    KS test:                    D={ks['D']:.3f}  p={ks['p']:.4f}")
        if d is not None:
            print(f"    Cohen's d:                  {d['d']:+.3f}  [{d['ci_lo']:+.3f}, {d['ci_hi']:+.3f}]")

    for name, arr in [("ENTER_NOW return > 0", buy), ("ENTER_LIMIT return > 0", lim),
                      ("AVOID return < 0", -avoid if len(avoid) else avoid)]:
        st = sign_test(arr)
        if st:
            print(f"\\n  Sign test {name}:  wins={st['wins']}/{st['n']}  ({st['win_rate']*100:.1f}%)  p={st['p_one_sided']:.4f}")
""")

# ============================================================================
# Section 3
# ============================================================================
md("""
## 3. Return distributions

Violin + strip plots. Each point is one decision. Watch for:
- whether ENTER_NOW / ENTER_LIMIT distributions sit above AVOID
- fat tails (skew driven by a few outsized winners or losers)
""")

code("""
fig, axes = plt.subplots(1, 3, figsize=(18, 5), sharey=False)
order = ["ENTER_NOW", "ENTER_LIMIT", "NEUTRAL", "AVOID"]

for ax, horizon in zip(axes, ["d7", "d30", "current"]):
    col = f"ret_{horizon}"
    matured = f"matured_{horizon}" if horizon != "current" else None
    sub = df[df[matured].fillna(False).astype(bool)] if matured else df
    sub = sub[sub["intent"].isin(order)].copy()
    sub[col] = sub[col] * 100  # percent for readability
    sns.violinplot(data=sub, x="intent", y=col, order=order, ax=ax,
                   inner=None, cut=0, palette="Set2")
    sns.stripplot(data=sub, x="intent", y=col, order=order, ax=ax,
                  color="black", size=2.5, alpha=0.5, jitter=0.25)
    ax.axhline(0, color="grey", linewidth=0.8)
    ax.set_title(f"Returns @ {horizon}")
    ax.set_ylabel("return (%)")
    ax.set_xlabel("")

plt.tight_layout()
plt.show()
""")

# ============================================================================
# Section 4
# ============================================================================
md("""
## 4. Time evolution

Two views:

**Left.** Mean return as a function of holding day, by intent. Truncated
where matured-sample size drops below 5.

**Right.** Equal-weight portfolio: hold each decision for up to 30 trading
days from its entry, average the per-decision returns at each holding day,
overlay against the average SPY return over the same windows.
""")

code("""
# Compute per-decision daily returns from entry, for up to MAX_HOLD trading days.
MAX_HOLD = 30

def per_day_returns(row):
    ohlc = get_ohlc(row["symbol"])
    if ohlc is None or pd.isna(row["entry_price"]):
        return None
    future = ohlc.loc[ohlc.index >= row["entry_date"]].head(MAX_HOLD + 1)
    if future.empty:
        return None
    rets = (future["Close"].values / row["entry_price"]) - 1.0
    return rets


def spy_per_day_returns(row):
    sd, sp = first_close_on_or_after(spy, row["entry_date"])
    if sp is None:
        return None
    future = spy.loc[spy.index >= sd].head(MAX_HOLD + 1)
    return (future["Close"].values / sp) - 1.0


curves = {}
for intent in ["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]:
    sub = df[df["intent"] == intent]
    series_list = [per_day_returns(r) for _, r in sub.iterrows()]
    series_list = [s for s in series_list if s is not None and len(s) >= 2]
    if not series_list:
        continue
    # Stack into (n_decisions, max_hold+1), pad with NaN
    arr = np.full((len(series_list), MAX_HOLD + 1), np.nan)
    for i, s in enumerate(series_list):
        arr[i, :len(s)] = s
    curves[intent] = arr

# Same for SPY-aligned baselines, averaged across the same decision dates
all_decisions = [r for _, r in df.iterrows()]
spy_series = [spy_per_day_returns(r) for r in all_decisions]
spy_arr = np.full((len(spy_series), MAX_HOLD + 1), np.nan)
for i, s in enumerate(spy_series):
    if s is not None:
        spy_arr[i, :len(s)] = s

fig, axes = plt.subplots(1, 2, figsize=(16, 5))

# --- Left: mean curve per intent, with CI ---
days = np.arange(MAX_HOLD + 1)
colors = {"ENTER_NOW": "C2", "ENTER_LIMIT": "C0", "AVOID": "C3", "NEUTRAL": "C7"}
for intent, arr in curves.items():
    n_per_day = (~np.isnan(arr)).sum(axis=0)
    mean = np.nanmean(arr, axis=0) * 100
    se = (np.nanstd(arr, axis=0) / np.sqrt(np.maximum(n_per_day, 1))) * 100
    mask = n_per_day >= 5
    axes[0].plot(days[mask], mean[mask], label=f"{intent}  (n_max={int(arr.shape[0])})",
                 color=colors.get(intent, "k"))
    axes[0].fill_between(days[mask], (mean - 1.96 * se)[mask], (mean + 1.96 * se)[mask],
                         color=colors.get(intent, "k"), alpha=0.15)
axes[0].axhline(0, color="grey", linewidth=0.8)
axes[0].set_xlabel("trading days since entry")
axes[0].set_ylabel("mean return (%)")
axes[0].set_title("Mean return vs holding day, by intent")
axes[0].legend()

# --- Right: equal-weight BUY portfolio vs SPY ---
buy_mask = df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"]).values
buy_arrs = []
for intent in ["ENTER_NOW", "ENTER_LIMIT"]:
    if intent in curves:
        buy_arrs.append(curves[intent])
if buy_arrs:
    buy_combined = np.vstack(buy_arrs)
    buy_mean = np.nanmean(buy_combined, axis=0) * 100
    spy_mean_buys = np.nanmean(spy_arr[buy_mask], axis=0) * 100
    n_buy = (~np.isnan(buy_combined)).sum(axis=0)
    mask = n_buy >= 5
    axes[1].plot(days[mask], buy_mean[mask], label=f"Equal-weight BUY portfolio  (n={buy_combined.shape[0]})", color="C2")
    axes[1].plot(days[mask], spy_mean_buys[mask], label="SPY (same entry dates)", color="C1", linestyle="--")
    axes[1].fill_between(days[mask], buy_mean[mask], spy_mean_buys[mask],
                         where=buy_mean[mask] > spy_mean_buys[mask], color="C2", alpha=0.2, label="BUY > SPY")
    axes[1].fill_between(days[mask], buy_mean[mask], spy_mean_buys[mask],
                         where=buy_mean[mask] < spy_mean_buys[mask], color="C3", alpha=0.2, label="BUY < SPY")
axes[1].axhline(0, color="grey", linewidth=0.8)
axes[1].set_xlabel("trading days since entry")
axes[1].set_ylabel("mean return (%)")
axes[1].set_title("BUY portfolio vs SPY (entry-date-matched)")
axes[1].legend()

plt.tight_layout()
plt.show()
""")

# ============================================================================
# Section 5
# ============================================================================
md("""
## 5. Deep Research signal

Only ~89 decisions in this window have DR fields populated. We answer:

- **Conviction calibration**: does DR-rated HIGH conviction actually beat MODERATE/LOW?
- **Override resolution**: when DR's action differs from PM's recommendation, who's right?
- **Entry-zone hit rate**: of BUY_LIMIT calls, how often does the price actually visit
  `[deep_research_entry_low, deep_research_entry_high]` within 30 days?
""")

code("""
dr = df[df["deep_research_conviction"].notna() & (df["deep_research_conviction"] != "")].copy()
print(f"Decisions with DR conviction: {len(dr)}")
print(dr["deep_research_conviction"].value_counts().to_string())
""")

code("""
# Conviction vs realized return (Spearman)
conv_map = {"LOW": 1, "MODERATE": 2, "HIGH": 3}
dr["conv_rank"] = dr["deep_research_conviction"].map(conv_map)

for horizon in ["d30", "current"]:
    col = f"ret_{horizon}"
    sub = dr[[col, "conv_rank", "deep_research_conviction"]].dropna()
    if len(sub) < 5:
        print(f"\\n{horizon}: insufficient sample (n={len(sub)})")
        continue
    rho, p = stats.spearmanr(sub["conv_rank"], sub[col])
    print(f"\\n{horizon}: Spearman conviction↔return  rho={rho:+.3f}  p={p:.4f}  (n={len(sub)})")
    print(sub.groupby("deep_research_conviction")[col].agg(["count", "mean", "median"]))
""")

code("""
# PM-vs-DR override resolution: when they disagree, whose intent does the return validate?
dr_disagree = dr[(dr["intent"] != dr["dr_intent"]) & dr["dr_intent"].isin(["ENTER_NOW", "ENTER_LIMIT", "AVOID"])].copy()
print(f"PM-vs-DR disagreements: {len(dr_disagree)}")

if len(dr_disagree) >= 3:
    def who_won(row):
        r = row["ret_current"]
        if pd.isna(r):
            return None
        pm_bullish = row["intent"] in ("ENTER_NOW", "ENTER_LIMIT")
        dr_bullish = row["dr_intent"] in ("ENTER_NOW", "ENTER_LIMIT")
        actually_up = r > 0
        if pm_bullish == dr_bullish:
            return None
        if pm_bullish and actually_up: return "PM"
        if dr_bullish and actually_up: return "DR"
        if not pm_bullish and not actually_up: return "PM"
        if not dr_bullish and not actually_up: return "DR"
        return None

    dr_disagree["winner"] = dr_disagree.apply(who_won, axis=1)
    print(dr_disagree["winner"].value_counts(dropna=False).to_string())
    print()
    print(dr_disagree[["symbol", "recommendation", "deep_research_action", "ret_current", "winner"]].head(15).to_string(index=False))
""")

code("""
# Entry-zone hit rate: BUY_LIMIT calls with DR entry range — did price reach the zone within 30d?
buy_limit = df[(df["intent"] == "ENTER_LIMIT") &
               df["deep_research_entry_low"].notna() &
               df["deep_research_entry_high"].notna()].copy()
print(f"BUY_LIMIT calls with DR entry zone: {len(buy_limit)}")

hits = 0
checked = 0
for _, row in buy_limit.iterrows():
    ohlc = get_ohlc(row["symbol"])
    if ohlc is None: continue
    win = ohlc.loc[(ohlc.index >= row["entry_date"]) &
                   (ohlc.index <= row["entry_date"] + pd.Timedelta(days=45))]
    if win.empty: continue
    checked += 1
    low = float(row["deep_research_entry_low"])
    high = float(row["deep_research_entry_high"])
    if (win["Low"] <= high).any() and (win["High"] >= low).any():
        hits += 1

if checked > 0:
    print(f"Entry-zone touched within ~30 trading days: {hits}/{checked}  ({hits/checked*100:.1f}%)")
""")

# ============================================================================
# Section 6
# ============================================================================
md("""
## 6. Subgroup breakdowns

Which features of a setup are predictive? Sector is too sparse (n=8) and is
dropped. We test:

- `drop_type` (e.g. earnings, news, technical)
- `gatekeeper_tier` (DEEP_DIP / STANDARD_DIP / SHALLOW_DIP)
- Drop magnitude (quartiled)
""")

code("""
def report_subgroup(field, horizon="ret_d30"):
    matured = "matured_d30" if horizon == "ret_d30" else None
    sub = df[df[matured].fillna(False).astype(bool)] if matured else df
    sub = sub[sub[field].notna() & (sub[field] != "")]
    if len(sub) < 5:
        print(f"  {field}: insufficient sample")
        return
    print(f"\\n--- {field}  (n={len(sub)})  ---")
    g = sub.groupby(field)[horizon].agg(["count", "mean", "median",
                                          lambda x: (x > 0).mean()])
    g.columns = ["n", "mean", "median", "win_rate"]
    g = g.sort_values("mean", ascending=False)
    for name, row in g.iterrows():
        print(f"  {str(name)[:24]:24s}  n={int(row['n']):3d}  "
              f"mean={row['mean']*100:+6.2f}%  median={row['median']*100:+6.2f}%  "
              f"win={row['win_rate']*100:5.1f}%")


for field in ["drop_type", "gatekeeper_tier"]:
    report_subgroup(field, "ret_d30")
""")

code("""
# Drop magnitude quartiles
sub = df[df["matured_d30"].fillna(False).astype(bool) & df["drop_percent"].notna()].copy()
sub["drop_q"] = pd.qcut(sub["drop_percent"], q=4, duplicates="drop")
print("\\n--- Drop magnitude quartiles vs d30 return ---")
g = sub.groupby("drop_q")["ret_d30"].agg(["count", "mean", "median",
                                           lambda x: (x > 0).mean()])
g.columns = ["n", "mean", "median", "win_rate"]
for name, row in g.iterrows():
    print(f"  {str(name):28s}  n={int(row['n']):3d}  "
          f"mean={row['mean']*100:+6.2f}%  median={row['median']*100:+6.2f}%  "
          f"win={row['win_rate']*100:5.1f}%")
""")

code("""
# Visualize: boxplot of d30 return by gatekeeper tier
fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))

sub_gk = df[df["matured_d30"].fillna(False).astype(bool) & df["gatekeeper_tier"].notna() & (df["gatekeeper_tier"] != "")].copy()
sub_gk["ret_d30_pct"] = sub_gk["ret_d30"] * 100
sns.boxplot(data=sub_gk, x="gatekeeper_tier", y="ret_d30_pct",
            order=["SHALLOW_DIP", "STANDARD_DIP", "DEEP_DIP"], ax=axes[0])
axes[0].axhline(0, color="grey", linewidth=0.8)
axes[0].set_title("d30 return by gatekeeper tier")
axes[0].set_ylabel("d30 return (%)")

sub_dt = df[df["matured_d30"].fillna(False).astype(bool) & df["drop_type"].notna() & (df["drop_type"] != "")].copy()
sub_dt["ret_d30_pct"] = sub_dt["ret_d30"] * 100
top_types = sub_dt["drop_type"].value_counts().head(6).index.tolist()
sub_dt = sub_dt[sub_dt["drop_type"].isin(top_types)]
sns.boxplot(data=sub_dt, x="drop_type", y="ret_d30_pct", ax=axes[1])
axes[1].axhline(0, color="grey", linewidth=0.8)
axes[1].tick_params(axis="x", rotation=30)
axes[1].set_title("d30 return by drop type (top 6)")
axes[1].set_ylabel("d30 return (%)")

plt.tight_layout()
plt.show()
""")

# ============================================================================
# Section 7
# ============================================================================
md("""
## 7. Calibration

Treat the recommendation as a binary classifier (ENTER_NOW + ENTER_LIMIT = predicted
positive; AVOID = predicted negative). Compute Brier score and a reliability
diagram bucketed by conviction tier.
""")

code("""
cal = df[df["matured_d30"].fillna(False).astype(bool) & df["ret_d30"].notna()].copy()
cal["pred_pos"] = cal["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"]).astype(int)
cal["actual_pos"] = (cal["ret_d30"] > 0).astype(int)

# Excluding NEUTRAL/PASS for a cleaner binary picture
cal_bin = cal[cal["intent"].isin(["ENTER_NOW", "ENTER_LIMIT", "AVOID"])].copy()
brier = ((cal_bin["pred_pos"] - cal_bin["actual_pos"]) ** 2).mean()
acc = (cal_bin["pred_pos"] == cal_bin["actual_pos"]).mean()
print(f"Binary classifier @ d30 (BUY-anything = positive, AVOID = negative)")
print(f"  n = {len(cal_bin)}")
print(f"  Accuracy = {acc*100:.1f}%")
print(f"  Brier score = {brier:.4f}  (lower is better; 0.25 = random)")

# Confusion matrix
print("\\nConfusion matrix:")
ct = pd.crosstab(cal_bin["intent"], cal_bin["actual_pos"], margins=True)
print(ct.to_string())
""")

code("""
# Reliability diagram by DR conviction
cal_dr = cal[cal["deep_research_conviction"].isin(["LOW", "MODERATE", "HIGH"]) &
             cal["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])].copy()

if len(cal_dr) >= 5:
    g = cal_dr.groupby("deep_research_conviction")["actual_pos"].agg(["count", "mean"])
    g = g.reindex(["LOW", "MODERATE", "HIGH"]).dropna()
    print("Reliability by DR conviction (BUY-side only):")
    for name, row in g.iterrows():
        print(f"  {name:10s}  n={int(row['count']):3d}  realized win rate = {row['mean']*100:.1f}%")

    fig, ax = plt.subplots(figsize=(6, 5))
    x = np.arange(len(g))
    ax.bar(x, g["mean"] * 100, color=["lightblue", "steelblue", "darkblue"])
    ax.axhline(50, color="grey", linestyle="--", label="50% (coin flip)")
    ax.set_xticks(x); ax.set_xticklabels(g.index)
    ax.set_ylabel("realized win rate @ d30 (%)")
    ax.set_title(f"DR conviction calibration  (n={len(cal_dr)})")
    for i, (idx, row) in enumerate(g.iterrows()):
        ax.text(i, row["mean"] * 100 + 1, f"n={int(row['count'])}", ha="center")
    ax.legend()
    plt.tight_layout()
    plt.show()
else:
    print(f"Insufficient sample for reliability diagram (n={len(cal_dr)})")
""")

# ============================================================================
# Section 8
# ============================================================================
md("""
## 8. Risk metrics (MFE / MAE / drawdown)

For each decision we computed, over a 30-trading-day window:

- **MFE** (max favorable excursion): how high did the price get above entry?
- **MAE** (max adverse excursion): how low did it get below entry?
- **Max drawdown**: largest peak-to-trough drop in closes.

These tell us about the *shape* of trades, not just the endpoint return.
""")

code("""
risk = df[df["mfe"].notna() & df["mae"].notna()].copy()
print(f"Risk-metric sample: n={len(risk)}")

for intent in ["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"]:
    sub = risk[risk["intent"] == intent]
    if len(sub) < 3:
        continue
    print(f"\\n{intent}  (n={len(sub)})")
    print(f"  MFE        median {sub['mfe'].median()*100:+6.2f}%   mean {sub['mfe'].mean()*100:+6.2f}%   p75 {sub['mfe'].quantile(0.75)*100:+6.2f}%")
    print(f"  MAE        median {sub['mae'].median()*100:+6.2f}%   mean {sub['mae'].mean()*100:+6.2f}%   p25 {sub['mae'].quantile(0.25)*100:+6.2f}%")
    print(f"  Drawdown   median {sub['max_drawdown'].median()*100:+6.2f}%   p25 {sub['max_drawdown'].quantile(0.25)*100:+6.2f}%")
    # Sharpe-like at d30
    rets = sub["ret_d30"].dropna()
    if len(rets) >= 3 and rets.std() > 0:
        print(f"  Sharpe-like (mean/std d30):  {rets.mean()/rets.std():+.3f}")
""")

code("""
fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
order = ["ENTER_NOW", "ENTER_LIMIT", "NEUTRAL", "AVOID"]
sub = risk[risk["intent"].isin(order)].copy()
for col, ax, label in [
    ("mfe", axes[0], "MFE (%)"),
    ("mae", axes[1], "MAE (%)"),
    ("max_drawdown", axes[2], "Max drawdown (%)"),
]:
    sub[f"{col}_pct"] = sub[col] * 100
    sns.boxplot(data=sub, x="intent", y=f"{col}_pct", order=order, ax=ax, palette="Set2")
    ax.axhline(0, color="grey", linewidth=0.8)
    ax.set_title(label)
    ax.set_ylabel(label)
    ax.set_xlabel("")

plt.tight_layout()
plt.show()
""")

# ============================================================================
# Section 9
# ============================================================================
md("""
## 9. Takeaways

Auto-generated summary. Treat as a starting point, not the final word —
re-read the sections above before drawing strong conclusions.
""")

code("""
takeaways = []

# --- Headline edge ---
matured = df[df["matured_d30"].fillna(False).astype(bool)]
mean_buy_now = matured.loc[matured["intent"] == "ENTER_NOW", "ret_d30"].mean()
mean_buy_lim = matured.loc[matured["intent"] == "ENTER_LIMIT", "ret_d30"].mean()
mean_avoid = matured.loc[matured["intent"] == "AVOID", "ret_d30"].mean()
n_now = matured["intent"].eq("ENTER_NOW").sum()
n_lim = matured["intent"].eq("ENTER_LIMIT").sum()
n_avoid = matured["intent"].eq("AVOID").sum()

if pd.notna(mean_buy_now) and pd.notna(mean_avoid):
    takeaways.append(
        f"ENTER_NOW d30 mean = {mean_buy_now*100:+.2f}% (n={n_now}); "
        f"AVOID d30 mean = {mean_avoid*100:+.2f}% (n={n_avoid}); "
        f"spread = {(mean_buy_now - mean_avoid)*100:+.2f} pp"
    )
if pd.notna(mean_buy_lim):
    takeaways.append(
        f"ENTER_LIMIT d30 mean = {mean_buy_lim*100:+.2f}% (n={n_lim})"
    )

# --- Alpha vs SPY ---
alpha_now = matured.loc[matured["intent"] == "ENTER_NOW", "alpha_d30"].mean()
alpha_lim = matured.loc[matured["intent"] == "ENTER_LIMIT", "alpha_d30"].mean()
if pd.notna(alpha_now):
    takeaways.append(f"ENTER_NOW alpha vs SPY @ d30 = {alpha_now*100:+.2f} pp")
if pd.notna(alpha_lim):
    takeaways.append(f"ENTER_LIMIT alpha vs SPY @ d30 = {alpha_lim*100:+.2f} pp")

# --- Significance ---
buy_all = matured[matured["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]["ret_d30"].dropna().values
avoid_all = matured[matured["intent"] == "AVOID"]["ret_d30"].dropna().values
if len(buy_all) >= 5 and len(avoid_all) >= 5:
    _, p_mw = stats.mannwhitneyu(buy_all, avoid_all, alternative="greater")
    takeaways.append(
        f"BUY-anything vs AVOID, one-sided Mann-Whitney p = {p_mw:.4f}  "
        f"({'significant at 0.05' if p_mw < 0.05 else 'NOT significant at 0.05'})"
    )

# --- DR conviction signal ---
dr_sub = df[df["deep_research_conviction"].isin(["LOW", "MODERATE", "HIGH"]) & df["ret_d30"].notna()]
if len(dr_sub) >= 5:
    conv_map = {"LOW": 1, "MODERATE": 2, "HIGH": 3}
    rho, p = stats.spearmanr(dr_sub["deep_research_conviction"].map(conv_map),
                              dr_sub["ret_d30"])
    takeaways.append(
        f"DR conviction ↔ d30 return Spearman rho = {rho:+.3f} (p={p:.3f}, n={len(dr_sub)})"
    )

# --- Risk shape ---
buy_mfe = risk[risk["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]["mfe"].median()
buy_mae = risk[risk["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]["mae"].median()
if pd.notna(buy_mfe) and pd.notna(buy_mae):
    takeaways.append(
        f"BUY-side median MFE={buy_mfe*100:+.2f}%, median MAE={buy_mae*100:+.2f}%  "
        f"(asymmetry: {'+' if buy_mfe + buy_mae > 0 else '-'}{abs(buy_mfe+buy_mae)*100:.2f} pp)"
    )

# --- Best subgroup ---
gk = df[df["matured_d30"].fillna(False).astype(bool) & df["gatekeeper_tier"].notna() & (df["gatekeeper_tier"] != "")]
if len(gk) >= 10:
    gk_means = gk.groupby("gatekeeper_tier")["ret_d30"].mean().sort_values(ascending=False)
    takeaways.append(
        f"Best gatekeeper tier by d30 mean: {gk_means.index[0]} ({gk_means.iloc[0]*100:+.2f}%); "
        f"worst: {gk_means.index[-1]} ({gk_means.iloc[-1]*100:+.2f}%)"
    )

# --- Data quality caveat ---
takeaways.append(
    f"Caveat: decision_tracking is empty for this window — all price data from yfinance; "
    f"MFE/MAE from daily OHLC, not intraday."
)

print("=" * 70)
print("TAKEAWAYS")
print("=" * 70)
for i, t in enumerate(takeaways, 1):
    print(f"\\n{i}. {t}")
""")


# ============================================================================
# Build the notebook
# ============================================================================
nb = {
    "cells": CELLS,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out_path = Path(__file__).parent / "performance_2mo.ipynb"
out_path.write_text(json.dumps(nb, indent=1))
print(f"Wrote {out_path}  ({len(CELLS)} cells)")
