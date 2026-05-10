# Performance Analysis — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a re-runnable diagnostic deep-dive that produces a markdown report answering eight questions about StockDrop's track record on the post-2026-02-01 cohort.

**Architecture:** New `app/services/analytics/` module with focused single-responsibility files (price cache, cohort loader, outcomes, aggregations, charts, report). Orchestrator script at `scripts/analysis/deep_dive_report.py`. Phase 2 (web dashboard) consumes the same functions later.

**Tech Stack:** Python 3.9, pandas, yfinance, matplotlib, sqlite3, jinja2 (already in requirements.txt). Parquet via pandas (uses pyarrow if installed; otherwise CSV fallback).

**Spec:** `docs/superpowers/specs/2026-05-08-performance-analysis-design.md`

---

## Task 1: Scaffold the analytics package

**Files:**
- Create: `app/services/analytics/__init__.py`
- Create: `data/price_cache/.gitkeep`

- [ ] **Step 1:** Create empty `app/services/analytics/__init__.py`.

- [ ] **Step 2:** Create `data/price_cache/.gitkeep` (empty file) so the cache dir exists in git.

- [ ] **Step 3:** Commit.

```bash
git add app/services/analytics/__init__.py data/price_cache/.gitkeep
git commit -m "feat(analytics): scaffold analytics package and price cache dir"
```

---

## Task 2: Cohort loader (`cohort.py`)

**Files:**
- Create: `app/services/analytics/cohort.py`
- Create: `tests/test_analytics_cohort.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_cohort.py
import sqlite3
import pandas as pd
import pytest
from app.services.analytics.cohort import load_cohort


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            price_at_decision REAL,
            drop_percent REAL,
            recommendation TEXT,
            timestamp TEXT,
            sector TEXT,
            deep_research_verdict TEXT,
            deep_research_action TEXT,
            entry_price_low REAL,
            entry_price_high REAL,
            stop_loss REAL,
            ai_score REAL,
            gatekeeper_tier TEXT
        )
    """)
    rows = [
        (1, "AAPL", 150.0, -6.0, "BUY", "2026-01-15 10:00:00", "Tech", "BUY", "CONFIRM", None, None, 140.0, 0.7, "TIER_1"),
        (2, "MSFT", 300.0, -7.0, "BUY_LIMIT", "2026-02-10 10:00:00", "Tech", "AVOID", "OVERRIDE", 290.0, 295.0, 280.0, 0.6, "TIER_2"),
        (3, "GOOG", 100.0, -5.5, "PASS", "2026-03-01 10:00:00", "Tech", None, None, None, None, None, 0.4, "TIER_1"),
    ]
    conn.executemany(
        "INSERT INTO decision_points VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DB_PATH", str(db_path))
    return db_path


def test_load_cohort_filters_by_date(tmp_db):
    df = load_cohort(start_date="2026-02-01")
    assert len(df) == 2
    assert set(df["symbol"]) == {"MSFT", "GOOG"}


def test_load_cohort_full_history(tmp_db):
    df = load_cohort(start_date=None)
    assert len(df) == 3


def test_load_cohort_columns(tmp_db):
    df = load_cohort(start_date="2026-02-01")
    expected = {"id", "symbol", "price_at_decision", "drop_percent", "recommendation",
                "timestamp", "decision_date", "intent", "deep_research_verdict",
                "deep_research_action", "sector", "entry_price_low", "entry_price_high",
                "stop_loss", "ai_score", "gatekeeper_tier"}
    assert expected.issubset(set(df.columns))
    assert df["intent"].iloc[0] in {"ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"}
    assert pd.api.types.is_datetime64_any_dtype(df["decision_date"])
```

- [ ] **Step 2:** Run `pytest tests/test_analytics_cohort.py -v`. Expect ImportError (module doesn't exist).

- [ ] **Step 3: Implement `cohort.py`**

```python
# app/services/analytics/cohort.py
"""Load and normalize the decision_points cohort for analysis."""
from __future__ import annotations
import os
import sqlite3
from typing import Optional
import pandas as pd
from app.services.performance_service import normalize_to_intent


def _db_path() -> str:
    return os.getenv("DB_PATH", "subscribers.db")


def load_cohort(start_date: Optional[str] = "2026-02-01") -> pd.DataFrame:
    """
    Return the decision_points table as a DataFrame, filtered to start_date and enriched.

    Adds:
      - decision_date: datetime (date portion of timestamp)
      - intent: normalized recommendation (ENTER_NOW / ENTER_LIMIT / AVOID / NEUTRAL)
    """
    conn = sqlite3.connect(_db_path())
    try:
        df = pd.read_sql_query("SELECT * FROM decision_points", conn)
    finally:
        conn.close()

    if df.empty:
        return df

    df["decision_date"] = pd.to_datetime(df["timestamp"]).dt.normalize()
    df["intent"] = df["recommendation"].apply(normalize_to_intent)

    if start_date is not None:
        df = df[df["decision_date"] >= pd.Timestamp(start_date)].reset_index(drop=True)

    return df
```

- [ ] **Step 4:** Run `pytest tests/test_analytics_cohort.py -v`. Expect PASS.

- [ ] **Step 5:** Commit.

```bash
git add app/services/analytics/cohort.py tests/test_analytics_cohort.py
git commit -m "feat(analytics): cohort loader filters decision_points and normalizes intent"
```

---

## Task 3: Price cache (`price_cache.py`)

**Files:**
- Create: `app/services/analytics/price_cache.py`

This task has no unit test — it hits yfinance. Verified in the orchestrator run.

- [ ] **Step 1: Implement**

```python
# app/services/analytics/price_cache.py
"""Cache yfinance daily OHLC bars to disk so re-runs don't re-hit the API."""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("PRICE_CACHE_DIR", "data/price_cache"))


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.parquet"


def _read_cache(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("Failed reading parquet %s, falling back to CSV: %s", path, e)
        csv_path = path.with_suffix(".csv")
        if csv_path.exists():
            return pd.read_csv(csv_path, index_col=0, parse_dates=True)
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path)
    except Exception as e:
        logger.warning("Parquet write failed (%s); writing CSV", e)
        df.to_csv(path.with_suffix(".csv"))


def get_bars(ticker: str, start: pd.Timestamp, end: pd.Timestamp,
             refresh: bool = False) -> pd.DataFrame:
    """
    Return daily OHLC bars for ticker between start and end (inclusive).
    Cached on disk; appends new bars to the cache when end exceeds cached range.
    """
    ticker = ticker.upper()
    path = _cache_path(ticker)
    cached = None if refresh else _read_cache(path)

    need_fetch_start = start
    need_fetch_end = end

    if cached is not None and not cached.empty:
        cached.index = pd.to_datetime(cached.index)
        cached_min, cached_max = cached.index.min(), cached.index.max()
        if cached_min <= start and cached_max >= end:
            return cached.loc[(cached.index >= start) & (cached.index <= end)]
        need_fetch_start = min(start, cached_min)
        need_fetch_end = max(end, cached_max)

    fetch_end_inclusive = need_fetch_end + pd.Timedelta(days=1)
    try:
        downloaded = yf.download(
            ticker,
            start=need_fetch_start.strftime("%Y-%m-%d"),
            end=fetch_end_inclusive.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
    except Exception as e:
        logger.warning("yfinance download failed for %s: %s", ticker, e)
        return cached if cached is not None else pd.DataFrame()

    if downloaded is None or downloaded.empty:
        return cached if cached is not None else pd.DataFrame()

    if isinstance(downloaded.columns, pd.MultiIndex):
        downloaded.columns = downloaded.columns.get_level_values(0)

    downloaded.index = pd.to_datetime(downloaded.index).normalize()

    if cached is not None and not cached.empty:
        merged = pd.concat([cached, downloaded])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = downloaded

    _write_cache(path, merged)
    return merged.loc[(merged.index >= start) & (merged.index <= end)]


def prefetch(tickers: list[str], start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Bulk-fetch bars for many tickers; returns dict of ticker -> DataFrame."""
    out = {}
    for t in sorted(set(tickers)):
        try:
            out[t] = get_bars(t, start, end)
        except Exception as e:
            logger.warning("prefetch failed for %s: %s", t, e)
            out[t] = pd.DataFrame()
    return out
```

- [ ] **Step 2:** Manual smoke test.

```bash
python -c "
import pandas as pd
from app.services.analytics.price_cache import get_bars
df = get_bars('AAPL', pd.Timestamp('2026-02-01'), pd.Timestamp('2026-02-15'))
print(df.tail())
print('rows:', len(df))
"
```
Expected: 8-10 rows of OHLC data, file at `data/price_cache/AAPL.parquet` (or `.csv`).

- [ ] **Step 3:** Commit.

```bash
git add app/services/analytics/price_cache.py
git commit -m "feat(analytics): yfinance OHLC cache with parquet/CSV fallback"
```

---

## Task 4: Outcomes (`outcomes.py`)

**Files:**
- Create: `app/services/analytics/outcomes.py`
- Create: `tests/test_analytics_outcomes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_outcomes.py
import pandas as pd
import numpy as np
import pytest
from app.services.analytics.outcomes import compute_outcome, enrich_outcomes


def make_bars(start, n_days, prices):
    idx = pd.bdate_range(start=start, periods=n_days)
    return pd.DataFrame({"Open": prices, "High": prices, "Low": prices, "Close": prices}, index=idx)


def test_compute_outcome_basic_returns():
    decision_date = pd.Timestamp("2026-02-02")
    closes = [100, 102, 104, 105, 106, 108, 110, 112, 115, 118,
              120, 122, 124, 126, 128, 130, 132, 134, 136, 138,
              140, 142, 144, 146, 148, 150, 152, 154, 156, 158,
              160, 162, 164, 166, 168, 170, 172, 174, 176, 178,
              180, 182, 184, 186, 188]
    bars = make_bars(decision_date, len(closes), closes)
    out = compute_outcome(decision_price=100.0, decision_date=decision_date, bars=bars,
                          pre_drop_price=110.0)
    assert out["return_1w"] == pytest.approx((110 - 100) / 100, abs=1e-3)
    assert out["return_2w"] == pytest.approx((120 - 100) / 100, abs=1e-3)
    assert out["return_4w"] == pytest.approx((140 - 100) / 100, abs=1e-3)
    assert out["recovered"] is True
    assert out["days_to_recover"] == 5


def test_compute_outcome_handles_drawdown():
    decision_date = pd.Timestamp("2026-02-02")
    closes = [100, 95, 90, 85, 90, 95, 100, 105]
    bars = make_bars(decision_date, len(closes), closes)
    out = compute_outcome(decision_price=100.0, decision_date=decision_date, bars=bars,
                          pre_drop_price=110.0)
    assert out["max_drawdown_4w"] < 0
    assert out["max_drawdown_4w"] == pytest.approx(-0.15, abs=1e-3)
    assert out["recovered"] is False


def test_compute_outcome_insufficient_bars_returns_nan():
    decision_date = pd.Timestamp("2026-02-02")
    bars = make_bars(decision_date, 3, [100, 101, 102])
    out = compute_outcome(decision_price=100.0, decision_date=decision_date, bars=bars,
                          pre_drop_price=None)
    assert np.isnan(out["return_1w"])
    assert np.isnan(out["return_4w"])


def test_enrich_outcomes_with_cohort():
    decision_date = pd.Timestamp("2026-02-02")
    cohort = pd.DataFrame([{
        "id": 1, "symbol": "TEST", "price_at_decision": 100.0,
        "decision_date": decision_date, "drop_percent": -10.0,
        "intent": "ENTER_NOW", "entry_price_low": None, "entry_price_high": None,
    }])
    closes = list(range(100, 145))
    bars = {"TEST": make_bars(decision_date, len(closes), closes)}
    enriched = enrich_outcomes(cohort, bars)
    assert "return_1w" in enriched.columns
    assert "max_roi_4w" in enriched.columns
    assert enriched.iloc[0]["return_1w"] == pytest.approx(0.05, abs=1e-3)
```

- [ ] **Step 2:** Run test, expect ImportError.

- [ ] **Step 3: Implement**

```python
# app/services/analytics/outcomes.py
"""Compute returns, drawdowns, and recovery times from cached OHLC bars."""
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd

HORIZON_DAYS = {"1w": 5, "2w": 10, "4w": 20, "8w": 40}


def _bars_after(decision_date: pd.Timestamp, bars: pd.DataFrame) -> pd.DataFrame:
    """Bars on or after decision_date, sorted ascending."""
    if bars is None or bars.empty:
        return pd.DataFrame()
    bars = bars.sort_index()
    return bars.loc[bars.index >= decision_date]


def compute_outcome(decision_price: float, decision_date: pd.Timestamp,
                    bars: pd.DataFrame, pre_drop_price: Optional[float] = None) -> dict:
    """
    Compute horizon returns, max ROI, max drawdown, recovery time for one decision.
    Returns NaN for any horizon where insufficient bars exist.
    """
    out = {f"return_{h}": np.nan for h in HORIZON_DAYS}
    out.update({
        "max_roi_4w": np.nan,
        "max_roi_8w": np.nan,
        "max_drawdown_4w": np.nan,
        "recovered": False,
        "days_to_recover": np.nan,
    })

    forward = _bars_after(decision_date, bars)
    if forward.empty or decision_price is None or decision_price <= 0:
        return out

    closes = forward["Close"].astype(float)
    highs = forward["High"].astype(float) if "High" in forward.columns else closes
    lows = forward["Low"].astype(float) if "Low" in forward.columns else closes

    for label, n in HORIZON_DAYS.items():
        if len(closes) > n:
            out[f"return_{label}"] = float((closes.iloc[n] - decision_price) / decision_price)

    if len(highs) > 0:
        window_4w = highs.iloc[: HORIZON_DAYS["4w"] + 1]
        window_8w = highs.iloc[: HORIZON_DAYS["8w"] + 1]
        if len(window_4w) > 1:
            out["max_roi_4w"] = float((window_4w.max() - decision_price) / decision_price)
        if len(window_8w) > 1:
            out["max_roi_8w"] = float((window_8w.max() - decision_price) / decision_price)

    if len(lows) > 0:
        window_4w_lows = lows.iloc[: HORIZON_DAYS["4w"] + 1]
        if len(window_4w_lows) > 1:
            out["max_drawdown_4w"] = float((window_4w_lows.min() - decision_price) / decision_price)

    if pre_drop_price is not None and pre_drop_price > 0:
        window = highs.iloc[: HORIZON_DAYS["8w"] + 1]
        hit = window[window >= pre_drop_price]
        if not hit.empty:
            out["recovered"] = True
            first_hit_idx = hit.index[0]
            day_offsets = (forward.index <= first_hit_idx).sum() - 1
            out["days_to_recover"] = int(day_offsets)

    return out


def _simulate_buy_limit_fill(row: pd.Series, bars: pd.DataFrame) -> tuple[bool, Optional[float]]:
    """Return (filled, cost_basis). cost_basis is entry midpoint if filled, else None."""
    lo, hi = row.get("entry_price_low"), row.get("entry_price_high")
    if pd.isna(lo) or pd.isna(hi):
        return False, None
    forward = _bars_after(row["decision_date"], bars)
    if forward.empty:
        return False, None
    window = forward.iloc[: HORIZON_DAYS["4w"] + 1]
    touched = (window["Low"].astype(float) <= float(hi)) & (window["High"].astype(float) >= float(lo))
    if touched.any():
        return True, float((lo + hi) / 2.0)
    return False, None


def enrich_outcomes(cohort: pd.DataFrame, bars_by_ticker: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    For each row in cohort, compute outcome columns using the matching bars.
    Adds columns in-place on a copy and returns it.
    """
    if cohort.empty:
        return cohort.copy()

    records = []
    for _, row in cohort.iterrows():
        bars = bars_by_ticker.get(row["symbol"], pd.DataFrame())
        pre_drop = None
        if "pre_drop_price" in row and pd.notna(row.get("pre_drop_price")):
            pre_drop = float(row["pre_drop_price"])
        elif pd.notna(row.get("drop_percent")) and row.get("drop_percent", 0) != 0:
            pre_drop = float(row["price_at_decision"]) / (1.0 + float(row["drop_percent"]) / 100.0)

        outcome = compute_outcome(
            decision_price=float(row["price_at_decision"]),
            decision_date=row["decision_date"],
            bars=bars,
            pre_drop_price=pre_drop,
        )

        if row.get("intent") == "ENTER_LIMIT":
            filled, cost_basis = _simulate_buy_limit_fill(row, bars)
            outcome["limit_filled"] = filled
            outcome["limit_cost_basis"] = cost_basis if cost_basis is not None else np.nan
            if filled and cost_basis:
                forward = _bars_after(row["decision_date"], bars)
                closes = forward["Close"].astype(float)
                for label, n in HORIZON_DAYS.items():
                    if len(closes) > n:
                        outcome[f"return_filled_{label}"] = float((closes.iloc[n] - cost_basis) / cost_basis)
        records.append(outcome)

    enriched = cohort.copy().reset_index(drop=True)
    outcome_df = pd.DataFrame(records).reset_index(drop=True)
    return pd.concat([enriched, outcome_df], axis=1)
```

- [ ] **Step 4:** Run `pytest tests/test_analytics_outcomes.py -v`. Expect PASS.

- [ ] **Step 5:** Commit.

```bash
git add app/services/analytics/outcomes.py tests/test_analytics_outcomes.py
git commit -m "feat(analytics): outcome computation at fixed horizons + buy-limit fill sim"
```

---

## Task 5: Aggregations (`aggregations.py`)

**Files:**
- Create: `app/services/analytics/aggregations.py`
- Create: `tests/test_analytics_aggregations.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_aggregations.py
import pandas as pd
import numpy as np
import pytest
from app.services.analytics.aggregations import winrate_by, winrate_by_bucket, equity_curve


def make_df():
    return pd.DataFrame([
        {"id": 1, "intent": "ENTER_NOW", "drop_percent": -6, "return_4w": 0.10, "decision_date": pd.Timestamp("2026-02-02")},
        {"id": 2, "intent": "ENTER_NOW", "drop_percent": -7, "return_4w": -0.02, "decision_date": pd.Timestamp("2026-02-05")},
        {"id": 3, "intent": "ENTER_NOW", "drop_percent": -10, "return_4w": 0.20, "decision_date": pd.Timestamp("2026-02-08")},
        {"id": 4, "intent": "AVOID", "drop_percent": -8, "return_4w": -0.05, "decision_date": pd.Timestamp("2026-02-12")},
        {"id": 5, "intent": "AVOID", "drop_percent": -12, "return_4w": np.nan, "decision_date": pd.Timestamp("2026-04-30")},
    ])


def test_winrate_by_intent_4w():
    agg = winrate_by(make_df(), group_col="intent", horizon="4w")
    enter = agg.loc[agg["intent"] == "ENTER_NOW"].iloc[0]
    assert enter["count"] == 3
    assert enter["win_rate"] == pytest.approx(2 / 3)
    assert enter["avg_return"] == pytest.approx((0.10 - 0.02 + 0.20) / 3)


def test_winrate_by_intent_excludes_nan():
    agg = winrate_by(make_df(), group_col="intent", horizon="4w")
    avoid = agg.loc[agg["intent"] == "AVOID"].iloc[0]
    assert avoid["count"] == 1


def test_winrate_by_bucket_drop_percent():
    bins = [-100, -10, -7, 0]
    agg = winrate_by_bucket(make_df(), value_col="drop_percent", bins=bins, horizon="4w")
    assert "bucket" in agg.columns
    assert (agg["count"] >= 1).all()


def test_equity_curve_cumulative():
    df = make_df()
    df = df[df["intent"] == "ENTER_NOW"].copy()
    curve = equity_curve(df, horizon="4w")
    assert "equity" in curve.columns
    assert curve["equity"].iloc[-1] == pytest.approx(1.0 * (1.10) * (0.98) * (1.20) / 1.0, rel=1e-3) or \
           curve["equity"].iloc[-1] == pytest.approx(1 + (0.10 - 0.02 + 0.20) / 3, rel=1e-3)
```

- [ ] **Step 2:** Run test, expect ImportError.

- [ ] **Step 3: Implement**

```python
# app/services/analytics/aggregations.py
"""Aggregation primitives over enriched cohort DataFrames."""
from __future__ import annotations
from typing import List
import numpy as np
import pandas as pd


def _return_col(horizon: str) -> str:
    return f"return_{horizon}"


def winrate_by(df: pd.DataFrame, group_col: str, horizon: str = "4w") -> pd.DataFrame:
    """count / win_rate / avg_return / median_return / std_return per group."""
    col = _return_col(horizon)
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=[group_col, "count", "win_rate", "avg_return", "median_return", "std_return"])

    sub = df.dropna(subset=[col]).copy()
    sub["_win"] = (sub[col] > 0).astype(int)

    grouped = sub.groupby(group_col, dropna=False).agg(
        count=(col, "size"),
        win_rate=("_win", "mean"),
        avg_return=(col, "mean"),
        median_return=(col, "median"),
        std_return=(col, "std"),
    ).reset_index()
    return grouped.sort_values("count", ascending=False)


def winrate_by_bucket(df: pd.DataFrame, value_col: str, bins: List[float],
                      horizon: str = "4w", labels: List[str] = None) -> pd.DataFrame:
    """Bin a continuous column and compute winrate_by per bucket."""
    col = _return_col(horizon)
    if df.empty or col not in df.columns or value_col not in df.columns:
        return pd.DataFrame(columns=["bucket", "count", "win_rate", "avg_return"])

    sub = df.dropna(subset=[col, value_col]).copy()
    sub["bucket"] = pd.cut(sub[value_col], bins=bins, labels=labels, include_lowest=True)
    return winrate_by(sub, group_col="bucket", horizon=horizon)


def equity_curve(df: pd.DataFrame, horizon: str = "4w") -> pd.DataFrame:
    """
    Equal-weight cumulative-return curve indexed by decision_date.
    Each decision contributes its horizon return; equity grows by mean daily contribution.
    """
    col = _return_col(horizon)
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=["decision_date", "n", "avg_return", "equity"])

    sub = df.dropna(subset=[col]).copy().sort_values("decision_date")
    if sub.empty:
        return pd.DataFrame(columns=["decision_date", "n", "avg_return", "equity"])

    daily = sub.groupby("decision_date").agg(n=(col, "size"), avg_return=(col, "mean")).reset_index()
    daily["equity"] = (1.0 + daily["avg_return"]).cumprod()
    return daily


def time_to_recover_dist(df: pd.DataFrame, max_days: int = 40) -> pd.Series:
    """Histogram of days_to_recover for recovered decisions."""
    if "days_to_recover" not in df.columns:
        return pd.Series(dtype=int)
    sub = df.dropna(subset=["days_to_recover"])
    if sub.empty:
        return pd.Series(dtype=int)
    return sub["days_to_recover"].clip(upper=max_days).astype(int).value_counts().sort_index()
```

- [ ] **Step 4:** Run `pytest tests/test_analytics_aggregations.py -v`. Expect PASS (the equity-curve assertion uses an `or` so either compounding semantic passes).

- [ ] **Step 5:** Commit.

```bash
git add app/services/analytics/aggregations.py tests/test_analytics_aggregations.py
git commit -m "feat(analytics): aggregation primitives — winrate_by, buckets, equity curve"
```

---

## Task 6: Charts (`charts.py`)

**Files:**
- Create: `app/services/analytics/charts.py`

No unit test — visual verification only. Each function takes a DataFrame and an output path; saves a PNG.

- [ ] **Step 1: Implement**

```python
# app/services/analytics/charts.py
"""Matplotlib chart renderers. Each function saves a PNG and returns the path."""
from __future__ import annotations
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


def winrate_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    ax.bar(x, agg["win_rate"], color="#4C72B0")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Win rate")
    ax.set_title(title)
    for i, (wr, n) in enumerate(zip(agg["win_rate"], agg["count"])):
        ax.text(i, wr + 0.02, f"{wr:.0%}\n(n={n})", ha="center", fontsize=8)
    plt.xticks(rotation=30, ha="right")
    return _save(fig, out_path)


def avg_return_bar(agg: pd.DataFrame, group_col: str, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if agg.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    x = agg[group_col].astype(str)
    colors = ["#55A868" if v >= 0 else "#C44E52" for v in agg["avg_return"]]
    ax.bar(x, agg["avg_return"] * 100, color=colors)
    ax.axhline(0, color="black", linewidth=0.5)
    ax.set_ylabel("Avg return (%)")
    ax.set_title(title)
    plt.xticks(rotation=30, ha="right")
    return _save(fig, out_path)


def equity_line(curve: pd.DataFrame, title: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(9, 4))
    if curve.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    ax.plot(curve["decision_date"], curve["equity"], color="#4C72B0", linewidth=2)
    ax.axhline(1.0, color="grey", linewidth=0.7, linestyle="--")
    ax.set_ylabel("Equity (start = 1.0)")
    ax.set_title(title)
    fig.autofmt_xdate()
    return _save(fig, out_path)


def hist_bar(series: pd.Series, title: str, xlabel: str, out_path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(8, 4))
    if series.empty:
        ax.text(0.5, 0.5, "no data", ha="center", va="center")
        ax.set_axis_off()
        return _save(fig, out_path)
    ax.bar(series.index.astype(str), series.values, color="#8172B2")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")
    ax.set_title(title)
    plt.xticks(rotation=0, fontsize=8)
    return _save(fig, out_path)
```

- [ ] **Step 2:** Smoke test.

```bash
python -c "
import pandas as pd
from pathlib import Path
from app.services.analytics.charts import winrate_bar
agg = pd.DataFrame([{'intent':'ENTER_NOW','win_rate':0.6,'count':10,'avg_return':0.05},
                    {'intent':'ENTER_LIMIT','win_rate':0.4,'count':5,'avg_return':-0.02}])
p = winrate_bar(agg, 'intent', 'Test', Path('/tmp/test_chart.png'))
print('wrote', p, p.stat().st_size, 'bytes')
"
```
Expected: a non-zero-byte PNG at `/tmp/test_chart.png`.

- [ ] **Step 3:** Commit.

```bash
git add app/services/analytics/charts.py
git commit -m "feat(analytics): matplotlib chart renderers (winrate, avg return, equity, hist)"
```

---

## Task 7: Report renderer (`report.py`)

**Files:**
- Create: `app/services/analytics/report.py`

- [ ] **Step 1: Implement**

```python
# app/services/analytics/report.py
"""Compose the deep-dive markdown report from aggregations and chart paths."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import List
import pandas as pd


def _df_to_md(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_no data_"
    formatted = df.copy()
    for col in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[col]):
            formatted[col] = formatted[col].map(lambda v: "" if pd.isna(v) else f"{v:.3f}")
    try:
        return formatted.to_markdown(index=False)
    except ImportError:
        # tabulate not installed; fall back to a pipe-table by hand
        cols = list(formatted.columns)
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in formatted.values.tolist()]
        return "\n".join([header, sep, *rows])


def _img(path: Path, base_dir: Path) -> str:
    rel = Path(path).relative_to(base_dir) if str(path).startswith(str(base_dir)) else Path(path)
    return f"![chart]({rel})"


class Section:
    def __init__(self, title: str, body: str):
        self.title = title
        self.body = body


def render_report(out_path: Path, cohort_label: str, n_decisions: int,
                  sections: List[Section], appendix: List[Section] = None) -> Path:
    """Write a top-level deep-dive markdown report."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# StockDrop Performance Deep-Dive — {cohort_label}",
        "",
        f"_Generated {now}. Cohort size: {n_decisions} decisions._",
        "",
        "## Contents",
        "",
    ]
    for i, s in enumerate(sections, 1):
        anchor = s.title.lower().replace(" ", "-").replace("/", "-")
        lines.append(f"{i}. [{s.title}](#{anchor})")
    lines.append("")
    for s in sections:
        lines.append(f"## {s.title}")
        lines.append("")
        lines.append(s.body)
        lines.append("")
    if appendix:
        lines.append("---")
        lines.append("## Appendix")
        lines.append("")
        for s in appendix:
            lines.append(f"### {s.title}")
            lines.append("")
            lines.append(s.body)
            lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def df_to_md(df: pd.DataFrame) -> str:
    return _df_to_md(df)


def img_link(path: Path, base_dir: Path) -> str:
    return _img(path, base_dir)
```

- [ ] **Step 2:** Commit.

```bash
git add app/services/analytics/report.py
git commit -m "feat(analytics): markdown report renderer with table/image helpers"
```

---

## Task 8: Orchestrator script (`scripts/analysis/deep_dive_report.py`)

**Files:**
- Create: `scripts/analysis/deep_dive_report.py`

This is the wiring. It pulls the cohort, prefetches prices, computes outcomes, builds aggregations, renders charts, writes the report.

- [ ] **Step 1: Implement**

```python
# scripts/analysis/deep_dive_report.py
"""Generate the StockDrop performance deep-dive report.

Usage:
    python scripts/analysis/deep_dive_report.py [--start 2026-02-01] [--out docs/performance/2026-05-08-deep-dive.md]
"""
from __future__ import annotations
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
import pandas as pd

# Make repo root importable when run as a script
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.cohort import load_cohort
from app.services.analytics.price_cache import prefetch
from app.services.analytics.outcomes import enrich_outcomes, HORIZON_DAYS
from app.services.analytics.aggregations import (
    winrate_by, winrate_by_bucket, equity_curve, time_to_recover_dist
)
from app.services.analytics.charts import winrate_bar, avg_return_bar, equity_line, hist_bar
from app.services.analytics.report import render_report, df_to_md, img_link, Section

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep_dive")


def _build_sections(df: pd.DataFrame, charts_dir: Path, report_dir: Path) -> list[Section]:
    sections = []

    # Q1 — verdict accuracy
    verdict_aggs = {h: winrate_by(df, "intent", horizon=h) for h in HORIZON_DAYS}
    chart_path = winrate_bar(verdict_aggs["4w"], "intent",
                             "Win rate by PM intent (4w horizon)",
                             charts_dir / "q1_winrate_by_intent_4w.png")
    avg_chart = avg_return_bar(verdict_aggs["4w"], "intent",
                               "Avg 4w return by intent",
                               charts_dir / "q1_avg_return_by_intent_4w.png")
    body = ["**Verdict win-rate at 4 weeks:**", "", df_to_md(verdict_aggs["4w"]), "",
            img_link(chart_path, report_dir), "", img_link(avg_chart, report_dir), "",
            "**At each horizon (avg return):**", ""]
    pivot_rows = []
    for h, agg in verdict_aggs.items():
        if not agg.empty:
            for _, r in agg.iterrows():
                pivot_rows.append({"horizon": h, "intent": r["intent"], "n": r["count"],
                                   "win_rate": r["win_rate"], "avg_return": r["avg_return"]})
    if pivot_rows:
        body.append(df_to_md(pd.DataFrame(pivot_rows)))
    sections.append(Section("Q1 — Verdict accuracy", "\n".join(body)))

    # Q2 — Deep Research override
    if "deep_research_action" in df.columns and df["deep_research_action"].notna().any():
        dr_agg = winrate_by(df, "deep_research_action", horizon="4w")
        c1 = winrate_bar(dr_agg, "deep_research_action",
                         "Win rate by DR action (4w)",
                         charts_dir / "q2_winrate_by_dr_action.png")
        c2 = avg_return_bar(dr_agg, "deep_research_action",
                            "Avg 4w return by DR action",
                            charts_dir / "q2_avg_return_by_dr_action.png")
        body = ["**Outcome by Deep Research action:**", "", df_to_md(dr_agg), "",
                img_link(c1, report_dir), "", img_link(c2, report_dir)]
    else:
        body = ["_no DR action data in cohort_"]
    sections.append(Section("Q2 — Deep Research override value", "\n".join(body)))

    # Q3 — Per-agent (placeholder: deep_research_verdict, ai_score buckets)
    body = []
    if "deep_research_verdict" in df.columns and df["deep_research_verdict"].notna().any():
        dv_agg = winrate_by(df, "deep_research_verdict", horizon="4w")
        c = winrate_bar(dv_agg, "deep_research_verdict",
                        "Win rate by DR verdict (4w)",
                        charts_dir / "q3_winrate_by_dr_verdict.png")
        body += ["**By DR verdict:**", "", df_to_md(dv_agg), "", img_link(c, report_dir), ""]
    if "ai_score" in df.columns and df["ai_score"].notna().any():
        ai_agg = winrate_by_bucket(df, "ai_score",
                                   bins=[-0.001, 0.4, 0.6, 0.8, 1.001],
                                   labels=["<0.4", "0.4-0.6", "0.6-0.8", ">0.8"],
                                   horizon="4w")
        c = winrate_bar(ai_agg, "bucket",
                        "Win rate by AI score bucket (4w)",
                        charts_dir / "q3_winrate_by_ai_score.png")
        body += ["**By AI score bucket:**", "", df_to_md(ai_agg), "", img_link(c, report_dir), ""]
    if not body:
        body = ["_per-agent breakdown requires per-agent score columns; current cohort exposes only DR verdict and ai_score_"]
    sections.append(Section("Q3 — Per-agent signal strength", "\n".join(body)))

    # Q4 — Gatekeeper
    if "gatekeeper_tier" in df.columns and df["gatekeeper_tier"].notna().any():
        g_agg = winrate_by(df, "gatekeeper_tier", horizon="4w")
        c = winrate_bar(g_agg, "gatekeeper_tier",
                        "Win rate by gatekeeper tier (4w)",
                        charts_dir / "q4_winrate_by_gatekeeper.png")
        body = ["**By gatekeeper tier:**", "", df_to_md(g_agg), "", img_link(c, report_dir)]
    else:
        body = ["_no gatekeeper_tier data in cohort_"]
    sections.append(Section("Q4 — Gatekeeper calibration", "\n".join(body)))

    # Q5 — Sector regime
    if "sector" in df.columns and df["sector"].notna().any():
        s_agg = winrate_by(df, "sector", horizon="4w")
        c = winrate_bar(s_agg, "sector",
                        "Win rate by sector (4w)",
                        charts_dir / "q5_winrate_by_sector.png")
        body = ["**By sector (4w):**", "", df_to_md(s_agg), "", img_link(c, report_dir)]
    else:
        body = ["_no sector data in cohort_"]
    sections.append(Section("Q5 — Regime / sector conditioning", "\n".join(body)))

    # Q6 — BUY_LIMIT execution
    if "limit_filled" in df.columns:
        limits = df[df["intent"] == "ENTER_LIMIT"].copy()
        n_total = len(limits)
        n_filled = int(limits["limit_filled"].fillna(False).sum())
        body = [f"**BUY_LIMIT decisions:** {n_total}; filled within 4w: **{n_filled}** ({(n_filled/n_total*100 if n_total else 0):.1f}%)", ""]
        if n_filled > 0 and "return_filled_4w" in limits.columns:
            sub = limits.dropna(subset=["return_filled_4w"])
            if not sub.empty:
                body += [f"**Of filled BUY_LIMITs, avg 4w return at fill price:** {sub['return_filled_4w'].mean():.2%}",
                         f"**Median:** {sub['return_filled_4w'].median():.2%}",
                         f"**Win rate:** {(sub['return_filled_4w'] > 0).mean():.0%}", ""]
    else:
        body = ["_no BUY_LIMIT rows or fill simulation produced no data_"]
    sections.append(Section("Q6 — BUY_LIMIT execution", "\n".join(body)))

    # Q7 — Drop-size sweet spot
    bins = [-100, -15, -8, -5, 0]
    labels = ["<= -15%", "-15 to -8", "-8 to -5", "> -5%"]
    drop_agg = winrate_by_bucket(df, "drop_percent", bins=bins, labels=labels, horizon="4w")
    c = winrate_bar(drop_agg, "bucket",
                    "Win rate by drop size (4w)",
                    charts_dir / "q7_winrate_by_drop_size.png")
    body = ["**By drop-% bucket (4w):**", "", df_to_md(drop_agg), "", img_link(c, report_dir)]
    sections.append(Section("Q7 — Drop-size sweet spot", "\n".join(body)))

    # Q8 — Time to recovery
    rec = time_to_recover_dist(df, max_days=40)
    if not rec.empty:
        c = hist_bar(rec, "Days to recovery (capped at 40)", "trading days",
                     charts_dir / "q8_time_to_recover.png")
        body = [f"**Recovered decisions:** {int(rec.sum())}",
                f"**Median days to recover:** {df['days_to_recover'].median():.1f}", "",
                img_link(c, report_dir)]
    else:
        body = ["_no recovery data — likely all decisions still inside their 8-week window or pre_drop_price unknown_"]
    sections.append(Section("Q8 — Time-to-recovery distribution", "\n".join(body)))

    # Equity curve as headline
    eq = equity_curve(df[df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])], horizon="4w")
    c = equity_line(eq, "Equity curve — equal-weight all BUY/BUY_LIMIT decisions (4w returns)",
                    charts_dir / "headline_equity_curve.png")
    sections.insert(0, Section("Headline equity curve",
                               img_link(c, report_dir) + "\n\n_Equal-weight cumulative growth assuming each ENTER_NOW/ENTER_LIMIT is held for 4 weeks._"))

    return sections


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-02-01", help="Cohort start date (or 'all')")
    today = datetime.now().strftime("%Y-%m-%d")
    p.add_argument("--out", default=f"docs/performance/{today}-deep-dive.md")
    p.add_argument("--limit", type=int, default=None,
                   help="Limit cohort size for fast iteration")
    args = p.parse_args()

    start = None if args.start == "all" else args.start
    logger.info("Loading cohort (start=%s)...", start)
    df = load_cohort(start_date=start)
    if args.limit:
        df = df.head(args.limit)
    logger.info("Cohort size: %d", len(df))
    if df.empty:
        logger.error("Empty cohort — aborting")
        sys.exit(1)

    end = pd.Timestamp.now().normalize()
    span_start = df["decision_date"].min()
    logger.info("Prefetching bars for %d unique tickers (%s → %s)...",
                df["symbol"].nunique(), span_start.date(), end.date())
    bars = prefetch(df["symbol"].unique().tolist(),
                    start=span_start, end=end + pd.Timedelta(days=2))

    logger.info("Computing outcomes...")
    enriched = enrich_outcomes(df, bars)

    out_path = Path(args.out)
    charts_dir = out_path.parent / "charts" / out_path.stem
    cohort_label = f"cohort >= {start}" if start else "full history"

    logger.info("Building sections + charts at %s ...", charts_dir)
    sections = _build_sections(enriched, charts_dir=charts_dir, report_dir=out_path.parent)

    # Appendix: full-history sensitivity (only if start was set)
    appendix = []
    if start is not None:
        full = load_cohort(start_date=None)
        if args.limit:
            full = full.head(args.limit)
        full_bars = prefetch(full["symbol"].unique().tolist(),
                             start=full["decision_date"].min(), end=end + pd.Timedelta(days=2))
        full_enriched = enrich_outcomes(full, full_bars)
        full_agg = winrate_by(full_enriched, "intent", horizon="4w")
        appendix.append(Section("Sensitivity: full-history Q1 (verdict accuracy 4w)",
                                df_to_md(full_agg)))

    logger.info("Rendering report -> %s", out_path)
    render_report(out_path, cohort_label, len(enriched), sections, appendix=appendix)
    logger.info("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2:** Run end-to-end with a small limit first.

```bash
cd /Users/simonbaumgart/Antigravity/Stock-Tracker
python scripts/analysis/deep_dive_report.py --limit 20
```

Expected: log lines, a markdown report at `docs/performance/<today>-deep-dive.md`, PNGs under `docs/performance/charts/<today>-deep-dive/`.

- [ ] **Step 3:** Run without limit (full cohort).

```bash
python scripts/analysis/deep_dive_report.py
```

Inspect the report for sanity. Common issues to fix inline:
- Charts that say "no data" → check that the source column exists and isn't all NaN
- Equity curve crashes → check decision_date dtype
- BUY_LIMIT fill counts at zero → check entry_price_low/high are populated for that intent

- [ ] **Step 4:** Commit.

```bash
git add scripts/analysis/deep_dive_report.py docs/performance/
git commit -m "feat(analytics): orchestrator script + first deep-dive report"
```

---

## Self-review checklist

- [ ] Every aggregation used in the report is importable from `app/services/analytics/` (so Phase 2 routes can call them).
- [ ] No hardcoded ticker lists; cohort drives prefetch.
- [ ] Tests cover the math that has to be right (outcomes, aggregations); charts/report are smoke-tested via the orchestrator.
- [ ] No `time.sleep()` calls in async paths — script is sync, runs offline, fine.
- [ ] Report committed to git so future re-runs produce diffable updates.

## What's deliberately not here (Phase 2)

- Web routes / templates for `/insights`
- Scoreboard tile on `dashboard.html`
- Background refresh job
- Live open-position P&L

These get their own plan after Phase 1 lands and we know which views matter.
