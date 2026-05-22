# Volatility Regime Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give StockDrop a numeric volatility-regime signal (VIX level + percentile, VIX term structure, optional CNN Fear & Greed, combined into a 0–1 regime score) and feed it into the gatekeeper, Market Sentiment Agent, Fund Manager, and Deep Research, so agents stop hallucinating VIX context from news prose.

**Architecture:** A new `VolatilityService` fetches VIX from FRED (`VIXCLS`, daily, gives clean percentile history), the VIX/VIX3M term structure from yfinance, and CNN Fear & Greed from CNN's unofficial endpoint (graceful failure). It exposes a single `get_regime(trend)` method that combines these into a regime dict with a 0–1 `regime_score`. `GatekeeperService.check_market_regime()` becomes the single producer of the unified regime dict: it computes the SPY/SMA200 trend (as today) then merges the volatility data. The regime dict is stored on `MarketState.volatility_regime` and injected into the sentiment, PM, and Deep Research prompts.

**Tech Stack:** Python 3.9, FRED API (existing `FredService`), `yfinance==0.2.66` (already a dependency), `requests` (already a dependency), `pytest` + `unittest.mock`. No new dependencies.

---

## File Structure

**Create:**
- `app/services/volatility_service.py` — `VolatilityService`: VIX context, term structure, Fear & Greed, regime scoring. Module-level singleton `volatility_service`.
- `tests/test_volatility_service.py` — unit tests for `VolatilityService` (FRED/yfinance/CNN all mocked).
- `tests/test_gatekeeper_regime.py` — tests for the gradient regime merge in `check_market_regime()`.

**Modify:**
- `app/services/fred_service.py` — add `fetch_series_history()` to pull a multi-observation history (needed for VIX percentile).
- `app/services/gatekeeper_service.py` — `check_market_regime()` merges volatility data and a `regime_score`.
- `app/models/market_state.py` — add `volatility_regime: Optional[dict]` field.
- `app/services/research_service.py` — populate `state.volatility_regime`; inject volatility block into the Market Sentiment Agent prompt and the Fund Manager prompt.
- `app/services/stock_service.py` — add `volatility_regime` to the Deep Research context package.
- `tests/test_fred_cache.py` — add tests for `fetch_series_history()`.

**Out of scope (per spec):** AAII / NAAIM / Investors Intelligence (weekly cadence too coarse), MOVE / SKEW (tail-risk hedging, not recovery forecasting). Do not add these.

---

## Design Reference (read before starting)

**Regime dict shape** — every task that produces or consumes the regime uses exactly these keys:

```python
{
    "regime": "BULL",            # legacy SPY/SMA200 verdict — kept for existing callers
    "details": "SPY Close ...",  # legacy human string — kept for existing callers
    "trend": "BULL",             # same as regime; BULL / BEAR / UNKNOWN
    "vix": 16.75,                # latest VIXCLS, float, or None
    "vix_date": "2026-05-21",    # str or None
    "vix_class": "NORMAL",       # COMPLACENT / NORMAL / ELEVATED / PANIC, or None
    "vix_pctile_5d": 80.0,       # float 0-100, or None
    "vix_pctile_20d": 65.0,      # float 0-100, or None
    "vix3m": 18.20,              # 3-month VIX from yfinance, float, or None
    "term_spread": -1.45,        # vix_spot - vix3m (yfinance spot, not FRED), float, or None
    "term_structure": "CONTANGO",# BACKWARDATION (spread > 0) / CONTANGO, or None
    "fear_greed": 42,            # CNN composite 0-100, int, or None
    "fear_greed_rating": "Fear", # CNN rating string, or None
    "regime_score": 0.48,        # 0-1, higher = more favorable for dip-buying
    "regime_label": "NEUTRAL",   # FAVORABLE (>=0.60) / NEUTRAL (>=0.40) / UNFAVORABLE
    "errors": [],                # list[str] of component fetch failures
    "summary": "VIX 16.75 (NORMAL), CONTANGO, trend BULL — regime NEUTRAL (0.48) for dip-buying.",
}
```

**Scoring rationale:** dip-buys mean-revert better when volatility is elevated (real panic → real reversion) and worse when VIX is low (slow grind → drops continue). Backwardation (VIX > VIX3M) is historically a strong mean-reversion signal. Trend (SPY vs SMA200) is the existing regime check. Weights and bands below are deliberate starting points — they live in named constants so they are easy to tune later.

---

### Task 1: `FredService.fetch_series_history()` — multi-observation fetch

**Files:**
- Modify: `app/services/fred_service.py` (add a method to `FredService`, after `_fetch_latest_observation`, around line 188)
- Test: `tests/test_fred_cache.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_fred_cache.py`:

```python
class TestFredSeriesHistory:
    def test_fetch_series_history_returns_tuples_newest_first(self, svc):
        body = {"observations": [
            {"value": "16.75", "date": "2026-05-21"},
            {"value": "17.10", "date": "2026-05-20"},
            {"value": ".", "date": "2026-05-19"},
        ]}
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status = MagicMock()
            mget.return_value.json = MagicMock(return_value=body)
            result = svc.fetch_series_history("VIXCLS", limit=3)
        assert result == [
            ("16.75", "2026-05-21"),
            ("17.10", "2026-05-20"),
            (".", "2026-05-19"),
        ]

    def test_fetch_series_history_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        s = FredService()
        assert s.fetch_series_history("VIXCLS") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fred_cache.py::TestFredSeriesHistory -v`
Expected: FAIL with `AttributeError: 'FredService' object has no attribute 'fetch_series_history'`

- [ ] **Step 3: Write minimal implementation**

In `app/services/fred_service.py`, add this method to `FredService` immediately after `_fetch_latest_observation` (before `_fetch_av_treasury_yield`):

```python
    def fetch_series_history(self, series_id: str, limit: int = 30) -> list:
        """Fetch up to `limit` most-recent observations for a FRED series.

        Returns a list of (value_str, date_str) tuples, newest first. FRED's
        missing-value marker "." is passed through unchanged — the caller
        filters non-numeric values. Returns [] when no API key is configured.
        Raises on a persistent HTTP failure (caller decides the fallback).
        """
        if not self.api_key:
            logger.warning("FRED_API_KEY not found. Cannot fetch series history.")
            return []
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        response = requests.get(self.BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        observations = response.json().get("observations", [])
        return [(o.get("value", "."), o.get("date", "")) for o in observations]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fred_cache.py::TestFredSeriesHistory -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/fred_service.py tests/test_fred_cache.py
git commit -m "feat(fred): add fetch_series_history for multi-observation pulls"
```

---

### Task 2: `VolatilityService.get_vix_context()` — VIX level + percentile

**Files:**
- Create: `app/services/volatility_service.py`
- Test: `tests/test_volatility_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_volatility_service.py`:

```python
from unittest.mock import patch

import pytest

from app.services.volatility_service import VolatilityService, classify_vix


def _history(values):
    """Build a FRED-style (value, date) list, newest first."""
    return [(str(v), f"2026-05-{21 - i:02d}") for i, v in enumerate(values)]


class TestClassifyVix:
    @pytest.mark.parametrize("level,expected", [
        (12.0, "COMPLACENT"),
        (17.0, "NORMAL"),
        (24.0, "ELEVATED"),
        (35.0, "PANIC"),
    ])
    def test_classify_vix_bands(self, level, expected):
        assert classify_vix(level) == expected


class TestGetVixContext:
    def test_returns_latest_level_class_and_percentiles(self):
        # newest value 18.0 sits above 15 of 20 trailing values
        values = [18.0] + [10.0] * 15 + [22.0] * 4
        svc = VolatilityService()
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   return_value=_history(values)):
            ctx = svc.get_vix_context()
        assert ctx["vix"] == 18.0
        assert ctx["vix_date"] == "2026-05-21"
        assert ctx["vix_class"] == "NORMAL"
        assert ctx["vix_pctile_20d"] == 75.0  # 15 of 20 below 18.0
        assert "error" not in ctx

    def test_skips_fred_missing_marker(self):
        svc = VolatilityService()
        history = [(".", "2026-05-21"), ("16.5", "2026-05-20")]
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   return_value=history):
            ctx = svc.get_vix_context()
        assert ctx["vix"] == 16.5
        assert ctx["vix_date"] == "2026-05-20"

    def test_fetch_failure_returns_error_dict(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.fred_service.fetch_series_history",
                   side_effect=RuntimeError("FRED down")):
            ctx = svc.get_vix_context()
        assert ctx["vix"] is None
        assert "FRED down" in ctx["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volatility_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.volatility_service'`

- [ ] **Step 3: Write minimal implementation**

Create `app/services/volatility_service.py`:

```python
import datetime
import logging
from typing import Any, Dict, List, Optional

import requests
import yfinance as yf

from app.services.fred_service import fred_service

logger = logging.getLogger(__name__)

# VIX level bands (CBOE convention).
VIX_COMPLACENT = 15.0
VIX_ELEVATED = 20.0
VIX_PANIC = 30.0


def classify_vix(level: float) -> str:
    """Map a VIX level to a regime band."""
    if level < VIX_COMPLACENT:
        return "COMPLACENT"
    if level < VIX_ELEVATED:
        return "NORMAL"
    if level < VIX_PANIC:
        return "ELEVATED"
    return "PANIC"


def _percentile_rank(window: List[float], value: float) -> float:
    """Percent of `window` values strictly below `value` (0-100)."""
    if not window:
        return 0.0
    below = sum(1 for v in window if v < value)
    return 100.0 * below / len(window)


class VolatilityService:
    def get_vix_context(self) -> Dict[str, Any]:
        """Latest VIX level + 5/20-day percentile from FRED VIXCLS."""
        try:
            history = fred_service.fetch_series_history("VIXCLS", limit=30)
        except Exception as e:
            logger.warning(f"VIX history fetch failed: {e}")
            return {"vix": None, "error": str(e)}

        series: List[float] = []
        latest_date: Optional[str] = None
        for value, date in history:  # newest first
            try:
                series.append(float(value))
            except (TypeError, ValueError):
                continue  # FRED uses "." for holidays / missing days
            if latest_date is None:
                latest_date = date

        if not series:
            return {"vix": None, "error": "no numeric VIXCLS observations"}

        latest = series[0]
        return {
            "vix": round(latest, 2),
            "vix_date": latest_date,
            "vix_class": classify_vix(latest),
            "vix_pctile_5d": round(_percentile_rank(series[:5], latest), 1),
            "vix_pctile_20d": round(_percentile_rank(series[:20], latest), 1),
        }


volatility_service = VolatilityService()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_volatility_service.py -v`
Expected: PASS (6 tests: 4 parametrized classify + 3 context, minus naming — expect all green)

- [ ] **Step 5: Commit**

```bash
git add app/services/volatility_service.py tests/test_volatility_service.py
git commit -m "feat(volatility): add VolatilityService.get_vix_context via FRED VIXCLS"
```

---

### Task 3: `VolatilityService.get_term_structure()` — VIX/VIX3M spread

**Files:**
- Modify: `app/services/volatility_service.py`
- Test: `tests/test_volatility_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_volatility_service.py`:

```python
import pandas as pd


def _yf_close_frame(vix_series, vix3m_series, dates):
    """Build a yfinance-style multi-ticker download frame."""
    cols = pd.MultiIndex.from_tuples([("Close", "^VIX"), ("Close", "^VIX3M")])
    return pd.DataFrame(
        {("Close", "^VIX"): vix_series, ("Close", "^VIX3M"): vix3m_series},
        index=pd.to_datetime(dates),
        columns=cols,
    )


class TestGetTermStructure:
    def test_contango_when_vix_below_vix3m(self):
        svc = VolatilityService()
        frame = _yf_close_frame([16.0, 16.75], [18.0, 18.20],
                                ["2026-05-20", "2026-05-21"])
        with patch("app.services.volatility_service.yf.download", return_value=frame):
            ts = svc.get_term_structure()
        assert ts["vix_spot"] == 16.75
        assert ts["vix3m"] == 18.20
        assert ts["term_spread"] == -1.45
        assert ts["term_structure"] == "CONTANGO"

    def test_backwardation_when_vix_above_vix3m(self):
        svc = VolatilityService()
        frame = _yf_close_frame([30.0, 32.0], [28.0, 29.0],
                                ["2026-05-20", "2026-05-21"])
        with patch("app.services.volatility_service.yf.download", return_value=frame):
            ts = svc.get_term_structure()
        assert ts["term_spread"] == 3.0
        assert ts["term_structure"] == "BACKWARDATION"

    def test_fetch_failure_returns_error_dict(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.yf.download",
                   side_effect=RuntimeError("yahoo down")):
            ts = svc.get_term_structure()
        assert ts["term_spread"] is None
        assert "yahoo down" in ts["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volatility_service.py::TestGetTermStructure -v`
Expected: FAIL with `AttributeError: 'VolatilityService' object has no attribute 'get_term_structure'`

- [ ] **Step 3: Write minimal implementation**

In `app/services/volatility_service.py`, add this method to `VolatilityService` after `get_vix_context`:

```python
    def get_term_structure(self) -> Dict[str, Any]:
        """VIX vs 3-month VIX (^VIX, ^VIX3M) spread from yfinance.

        spread > 0 (backwardation) is historically a strong mean-reversion
        signal — directly relevant to the dip-recovery thesis.
        """
        try:
            data = yf.download(["^VIX", "^VIX3M"], period="5d", progress=False)
            closes = data["Close"].dropna()
            vix = float(closes["^VIX"].iloc[-1])
            vix3m = float(closes["^VIX3M"].iloc[-1])
        except Exception as e:
            logger.warning(f"VIX term structure fetch failed: {e}")
            return {"term_spread": None, "error": str(e)}

        spread = vix - vix3m
        return {
            "vix_spot": round(vix, 2),
            "vix3m": round(vix3m, 2),
            "term_spread": round(spread, 2),
            "term_structure": "BACKWARDATION" if spread > 0 else "CONTANGO",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_volatility_service.py::TestGetTermStructure -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/volatility_service.py tests/test_volatility_service.py
git commit -m "feat(volatility): add VIX term-structure spread via yfinance"
```

---

### Task 4: `VolatilityService.get_fear_greed()` — CNN composite (graceful)

**Files:**
- Modify: `app/services/volatility_service.py`
- Test: `tests/test_volatility_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_volatility_service.py`:

```python
from unittest.mock import MagicMock


class TestGetFearGreed:
    def test_parses_score_and_rating(self):
        svc = VolatilityService()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "fear_and_greed": {"score": 41.6, "rating": "Fear"}
        })
        with patch("app.services.volatility_service.requests.get", return_value=resp):
            fg = svc.get_fear_greed()
        assert fg["fear_greed"] == 42  # rounded
        assert fg["fear_greed_rating"] == "Fear"

    def test_failure_is_non_fatal_returns_none(self):
        svc = VolatilityService()
        with patch("app.services.volatility_service.requests.get",
                   side_effect=RuntimeError("cnn 418")):
            fg = svc.get_fear_greed()
        assert fg["fear_greed"] is None
        assert fg["fear_greed_rating"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volatility_service.py::TestGetFearGreed -v`
Expected: FAIL with `AttributeError: 'VolatilityService' object has no attribute 'get_fear_greed'`

- [ ] **Step 3: Write minimal implementation**

In `app/services/volatility_service.py`, add the module constants near the VIX bands (top of file, after `VIX_PANIC`):

```python
# CNN Fear & Greed — unofficial endpoint, must fail gracefully.
_CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_CNN_HEADERS = {"User-Agent": "Mozilla/5.0 (StockDrop volatility probe)"}
```

Then add this method to `VolatilityService` after `get_term_structure`:

```python
    def get_fear_greed(self) -> Dict[str, Any]:
        """CNN Fear & Greed composite (0-100). Unofficial endpoint —
        every failure path returns None rather than raising.
        """
        try:
            r = requests.get(_CNN_URL, headers=_CNN_HEADERS, timeout=10)
            r.raise_for_status()
            fg = r.json().get("fear_and_greed", {})
            score = fg.get("score")
            return {
                "fear_greed": round(float(score)) if score is not None else None,
                "fear_greed_rating": fg.get("rating"),
            }
        except Exception as e:
            logger.warning(f"CNN Fear & Greed fetch failed (non-fatal): {e}")
            return {"fear_greed": None, "fear_greed_rating": None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_volatility_service.py::TestGetFearGreed -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/volatility_service.py tests/test_volatility_service.py
git commit -m "feat(volatility): add CNN Fear & Greed with graceful failure"
```

---

### Task 5: `score_regime()` + `get_regime()` — combined 0–1 regime

**Files:**
- Modify: `app/services/volatility_service.py`
- Test: `tests/test_volatility_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_volatility_service.py`:

```python
class TestScoreRegime:
    def test_bull_elevated_backwardation_scores_favorable(self):
        # trend 0.65, vix ELEVATED 0.85, term spread +2 -> 1.0
        score = VolatilityService.score_regime("BULL", "ELEVATED", 2.0)
        assert score == round(0.40 * 0.65 + 0.35 * 0.85 + 0.25 * 1.0, 3)
        assert score >= 0.60

    def test_bear_complacent_contango_scores_unfavorable(self):
        # trend 0.35, vix COMPLACENT 0.30, term spread -3 -> clamped 0.0
        score = VolatilityService.score_regime("BEAR", "COMPLACENT", -3.0)
        assert score == round(0.40 * 0.35 + 0.35 * 0.30 + 0.25 * 0.0, 3)
        assert score < 0.40

    def test_unknown_trend_and_missing_spread_use_neutral_defaults(self):
        score = VolatilityService.score_regime("UNKNOWN", "NORMAL", None)
        assert score == round(0.40 * 0.50 + 0.35 * 0.50 + 0.25 * 0.50, 3)


class TestGetRegime:
    def _patch_all(self, vix_ctx, term_ctx, fg_ctx):
        return [
            patch.object(VolatilityService, "get_vix_context", return_value=vix_ctx),
            patch.object(VolatilityService, "get_term_structure", return_value=term_ctx),
            patch.object(VolatilityService, "get_fear_greed", return_value=fg_ctx),
        ]

    def test_assembles_full_regime_dict(self):
        svc = VolatilityService()
        patches = self._patch_all(
            {"vix": 16.75, "vix_date": "2026-05-21", "vix_class": "NORMAL",
             "vix_pctile_5d": 80.0, "vix_pctile_20d": 65.0},
            {"vix3m": 18.20, "term_spread": -1.45, "term_structure": "CONTANGO"},
            {"fear_greed": 42, "fear_greed_rating": "Fear"},
        )
        for p in patches:
            p.start()
        try:
            regime = svc.get_regime(trend="BULL")
        finally:
            for p in patches:
                p.stop()
        assert regime["vix"] == 16.75
        assert regime["term_structure"] == "CONTANGO"
        assert regime["fear_greed"] == 42
        assert regime["trend"] == "BULL"
        assert 0.0 <= regime["regime_score"] <= 1.0
        assert regime["regime_label"] in ("FAVORABLE", "NEUTRAL", "UNFAVORABLE")
        assert "VIX 16.75" in regime["summary"]
        assert regime["errors"] == []

    def test_collects_component_errors(self):
        svc = VolatilityService()
        patches = self._patch_all(
            {"vix": None, "error": "FRED down"},
            {"term_spread": None, "error": "yahoo down"},
            {"fear_greed": None, "fear_greed_rating": None},
        )
        for p in patches:
            p.start()
        try:
            regime = svc.get_regime(trend="UNKNOWN")
        finally:
            for p in patches:
                p.stop()
        assert any("FRED down" in e for e in regime["errors"])
        assert any("yahoo down" in e for e in regime["errors"])
        # vix_class falls back to NORMAL so scoring still produces a number
        assert regime["regime_score"] is not None

    def test_caches_within_ttl_for_same_trend(self):
        svc = VolatilityService()
        with patch.object(VolatilityService, "get_vix_context",
                          return_value={"vix": 16.0, "vix_class": "NORMAL"}) as m, \
             patch.object(VolatilityService, "get_term_structure",
                          return_value={"term_spread": 0.0, "term_structure": "CONTANGO"}), \
             patch.object(VolatilityService, "get_fear_greed",
                          return_value={"fear_greed": None, "fear_greed_rating": None}):
            svc.get_regime(trend="BULL")
            svc.get_regime(trend="BULL")
            assert m.call_count == 1  # second call served from cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volatility_service.py::TestScoreRegime tests/test_volatility_service.py::TestGetRegime -v`
Expected: FAIL with `AttributeError: ... has no attribute 'score_regime'`

- [ ] **Step 3: Write minimal implementation**

In `app/services/volatility_service.py`, add a constant near the VIX bands:

```python
# Favorability of each VIX class for dip-buy mean reversion (0-1).
# Low VIX = slow grind, drops continue. Elevated VIX = real panic, real reversion.
_VIX_FAVORABILITY = {
    "COMPLACENT": 0.30,
    "NORMAL": 0.50,
    "ELEVATED": 0.85,
    "PANIC": 0.70,  # extreme panic: still favorable but outcome variance rises
}
```

Add a module-level helper after `_percentile_rank`:

```python
def _format_summary(r: Dict[str, Any]) -> str:
    vix = r.get("vix")
    vix_txt = f"VIX {vix} ({r.get('vix_class')})" if vix is not None else "VIX unavailable"
    term_txt = r.get("term_structure") or "term structure unavailable"
    fg = r.get("fear_greed")
    fg_txt = (f", Fear&Greed {fg} ({r.get('fear_greed_rating')})"
              if fg is not None else "")
    return (f"{vix_txt}, {term_txt}, trend {r.get('trend')}{fg_txt} — "
            f"regime {r.get('regime_label')} ({r.get('regime_score')}) for dip-buying.")
```

Add `import` already present (`datetime` is imported). Add these two methods to `VolatilityService` after `get_fear_greed`, and add `__init__`:

```python
    CACHE_TTL = datetime.timedelta(hours=1)

    def __init__(self):
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[datetime.datetime] = None
        self._cache_trend: Optional[str] = None

    @staticmethod
    def score_regime(trend: str, vix_class: str,
                     term_spread: Optional[float]) -> float:
        """Combine trend, VIX class, and term structure into a 0-1 score.
        Higher = more favorable for dip-buying.
        """
        trend_component = {"BULL": 0.65, "BEAR": 0.35}.get(trend, 0.50)
        vix_component = _VIX_FAVORABILITY.get(vix_class, 0.50)
        if term_spread is None:
            term_component = 0.50
        else:
            # spread 0 -> 0.5, +2 -> 1.0, -2 -> 0.0
            term_component = min(1.0, max(0.0, 0.5 + term_spread / 4.0))
        score = (0.40 * trend_component
                 + 0.35 * vix_component
                 + 0.25 * term_component)
        return round(score, 3)

    def get_regime(self, trend: str = "UNKNOWN") -> Dict[str, Any]:
        """Assemble the unified volatility-regime dict (see PLAN design ref).
        Cached for CACHE_TTL per trend value.
        """
        now = datetime.datetime.utcnow()
        if (self._cache is not None and self._cache_time is not None
                and self._cache_trend == trend
                and now - self._cache_time < self.CACHE_TTL):
            return self._cache

        errors: List[str] = []
        vix_ctx = self.get_vix_context()
        if vix_ctx.get("error"):
            errors.append(f"vix: {vix_ctx['error']}")
        term_ctx = self.get_term_structure()
        if term_ctx.get("error"):
            errors.append(f"term: {term_ctx['error']}")
        fg_ctx = self.get_fear_greed()

        vix_class = vix_ctx.get("vix_class") or "NORMAL"
        term_spread = term_ctx.get("term_spread")
        score = self.score_regime(trend, vix_class, term_spread)
        if score >= 0.60:
            label = "FAVORABLE"
        elif score >= 0.40:
            label = "NEUTRAL"
        else:
            label = "UNFAVORABLE"

        regime = {
            "trend": trend,
            "vix": vix_ctx.get("vix"),
            "vix_date": vix_ctx.get("vix_date"),
            "vix_class": vix_ctx.get("vix_class"),
            "vix_pctile_5d": vix_ctx.get("vix_pctile_5d"),
            "vix_pctile_20d": vix_ctx.get("vix_pctile_20d"),
            "vix3m": term_ctx.get("vix3m"),
            "term_spread": term_spread,
            "term_structure": term_ctx.get("term_structure"),
            "fear_greed": fg_ctx.get("fear_greed"),
            "fear_greed_rating": fg_ctx.get("fear_greed_rating"),
            "regime_score": score,
            "regime_label": label,
            "errors": errors,
        }
        regime["summary"] = _format_summary(regime)

        self._cache = regime
        self._cache_time = now
        self._cache_trend = trend
        return regime
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_volatility_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add app/services/volatility_service.py tests/test_volatility_service.py
git commit -m "feat(volatility): add score_regime + get_regime composite"
```

---

### Task 6: Merge volatility into `GatekeeperService.check_market_regime()`

**Files:**
- Modify: `app/services/gatekeeper_service.py:63-102`
- Test: `tests/test_gatekeeper_regime.py`

`check_market_regime()` becomes the single producer of the unified regime dict. It keeps the legacy `regime` and `details` keys (used by `stock_service.py:378`) and merges the volatility fields.

- [ ] **Step 1: Write the failing test**

Create `tests/test_gatekeeper_regime.py`:

```python
from unittest.mock import patch

from app.services.gatekeeper_service import GatekeeperService


_FAKE_REGIME = {
    "trend": "BULL",
    "vix": 16.75,
    "vix_class": "NORMAL",
    "term_structure": "CONTANGO",
    "term_spread": -1.45,
    "fear_greed": 42,
    "fear_greed_rating": "Fear",
    "regime_score": 0.48,
    "regime_label": "NEUTRAL",
    "errors": [],
    "summary": "VIX 16.75 (NORMAL), CONTANGO, trend BULL — regime NEUTRAL (0.48).",
}


class TestCheckMarketRegime:
    def test_merges_volatility_and_keeps_legacy_keys(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators",
                   return_value={"close": 500.0, "sma200": 480.0}), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            result = gk.check_market_regime()
        # legacy keys preserved
        assert result["regime"] == "BULL"
        assert "above" in result["details"]
        # volatility merged in
        assert result["vix"] == 16.75
        assert result["regime_score"] == 0.48
        assert result["regime_label"] == "NEUTRAL"
        # trend passed through to volatility_service
        mreg.assert_called_once_with(trend="BULL")

    def test_bear_trend_passed_to_volatility(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators",
                   return_value={"close": 460.0, "sma200": 480.0}), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            gk.check_market_regime()
        mreg.assert_called_once_with(trend="BEAR")

    def test_unknown_trend_still_attaches_volatility(self):
        gk = GatekeeperService()
        with patch("app.services.gatekeeper_service.tradingview_service"
                   ".get_technical_indicators", return_value=None), \
             patch("app.services.gatekeeper_service.volatility_service.get_regime",
                   return_value=dict(_FAKE_REGIME)) as mreg:
            result = gk.check_market_regime()
        assert result["regime"] == "UNKNOWN"
        assert result["vix"] == 16.75
        mreg.assert_called_once_with(trend="UNKNOWN")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gatekeeper_regime.py -v`
Expected: FAIL — `test_merges_volatility_and_keeps_legacy_keys` fails on `KeyError: 'vix'` (volatility not merged); the import patch target `volatility_service` does not yet exist.

- [ ] **Step 3: Write minimal implementation**

In `app/services/gatekeeper_service.py`, add the import after the existing `tradingview_service` import (line 6):

```python
from app.services.volatility_service import volatility_service
```

Replace the `check_market_regime` method (lines 63-102) with this version. It computes the trend string up front, then makes a single merge point at the end so every exit path attaches volatility:

```python
    def check_market_regime(self) -> Dict[str, str]:
        """
        Checks the global market regime (Rising Tide Rule) and merges the
        volatility-regime signal (VIX, term structure, Fear & Greed, score).

        Returns a dict with legacy keys 'regime' ('BULL'/'BEAR'/'UNKNOWN') and
        'details', plus the volatility fields documented in
        PLAN_volatility_regime_signal.md.
        """
        # Simple cache to avoid fetching SPY every time
        if self.regime_cache and self.regime_cache_time and \
           datetime.now() - self.regime_cache_time < self.cache_duration:
            return self.regime_cache

        regime = "UNKNOWN"
        details = "No data"
        try:
            indicators = tradingview_service.get_technical_indicators(
                self.benchmark_symbol, region="US")

            if not indicators:
                print(f"Error: Could not fetch indicators for {self.benchmark_symbol}")
                details = "No data"
            else:
                current_close = indicators.get("close", 0.0)
                current_sma = indicators.get("sma200", 0.0)
                if current_close == 0.0 or current_sma == 0.0:
                    details = "Missing Price or SMA data"
                else:
                    regime = "BULL" if current_close > current_sma else "BEAR"
                    details = (
                        f"{self.benchmark_symbol} Close ({current_close:.2f}) "
                        f"{'above' if regime == 'BULL' else 'below'} "
                        f"200 SMA ({current_sma:.2f})"
                    )
        except Exception as e:
            print(f"Error checking market regime: {e}")
            details = str(e)

        result = {"regime": regime, "details": details}
        try:
            result.update(volatility_service.get_regime(trend=regime))
        except Exception as e:
            print(f"Error merging volatility regime: {e}")
        # get_regime sets 'trend' == regime; keep legacy 'regime' authoritative.
        result["regime"] = regime
        result["details"] = details

        self.regime_cache = result
        self.regime_cache_time = datetime.now()
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gatekeeper_regime.py tests/test_gatekeeper_liquidity.py tests/test_gatekeeper_nan.py -v`
Expected: PASS (new regime tests + existing gatekeeper tests still green)

- [ ] **Step 5: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_regime.py
git commit -m "feat(gatekeeper): merge volatility regime into check_market_regime"
```

---

### Task 7: Add `volatility_regime` to `MarketState` and populate it

**Files:**
- Modify: `app/models/market_state.py`
- Modify: `app/services/research_service.py:17-21` (import) and `:242-247` (state init)
- Test: `tests/test_volatility_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_volatility_service.py`:

```python
class TestMarketStateField:
    def test_market_state_accepts_volatility_regime(self):
        from app.models.market_state import MarketState
        st = MarketState(ticker="AAPL", date="2026-05-22",
                         volatility_regime={"regime_score": 0.5})
        assert st.volatility_regime == {"regime_score": 0.5}

    def test_market_state_volatility_regime_defaults_none(self):
        from app.models.market_state import MarketState
        st = MarketState(ticker="AAPL", date="2026-05-22")
        assert st.volatility_regime is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_volatility_service.py::TestMarketStateField -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'volatility_regime'`

- [ ] **Step 3: Write minimal implementation**

In `app/models/market_state.py`, add the field after `earnings_facts` (line 15):

```python
    volatility_regime: Optional[dict] = None
```

In `app/services/research_service.py`, extend the existing `gatekeeper_service` import block (lines 17-21) to also import the singleton:

```python
from app.services.gatekeeper_service import (
    TIER_DEEP_DIP,
    TIER_STANDARD_DIP,
    TIER_SHALLOW_DIP,
    gatekeeper_service,
)
```

In `app/services/research_service.py`, update the `MarketState(...)` construction (lines 242-247) to populate the field:

```python
        state = MarketState(
            ticker=ticker,
            date=datetime.now().strftime("%Y-%m-%d"),
            gatekeeper_tier=raw_data.get("gatekeeper_tier"),
            earnings_facts=raw_data.get("earnings_facts"),
            volatility_regime=gatekeeper_service.check_market_regime(),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_volatility_service.py::TestMarketStateField -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/models/market_state.py app/services/research_service.py tests/test_volatility_service.py
git commit -m "feat(state): carry volatility_regime on MarketState"
```

---

### Task 8: Inject volatility block into the Market Sentiment Agent prompt

**Files:**
- Modify: `app/services/research_service.py:1788-1849` (`_create_market_sentiment_prompt`)
- Test: `tests/test_market_sentiment_agent.py`

The Market Sentiment Agent currently invents VIX context from news prose. Inject the numeric regime as ground truth.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_market_sentiment_agent.py` (a test class that builds the prompt and asserts on its text — patch out any heavy init the same way existing tests in this file do; if the file already constructs a `ResearchService`, reuse that fixture):

```python
class TestSentimentPromptVolatility:
    def _regime(self):
        return {
            "vix": 16.75, "vix_class": "NORMAL", "vix_pctile_20d": 65.0,
            "term_structure": "CONTANGO", "term_spread": -1.45,
            "fear_greed": 42, "fear_greed_rating": "Fear",
            "regime_score": 0.48, "regime_label": "NEUTRAL",
            "summary": "VIX 16.75 (NORMAL), CONTANGO, trend BULL — regime NEUTRAL (0.48).",
        }

    def test_volatility_block_present_when_regime_set(self):
        from app.models.market_state import MarketState
        from app.services.research_service import ResearchService
        svc = ResearchService()
        state = MarketState(ticker="AAPL", date="2026-05-22",
                            volatility_regime=self._regime())
        prompt = svc._create_market_sentiment_prompt(state, {})
        assert "VOLATILITY REGIME" in prompt
        assert "16.75" in prompt
        assert "CONTANGO" in prompt
        assert "NEUTRAL" in prompt

    def test_no_volatility_block_when_regime_missing(self):
        from app.models.market_state import MarketState
        from app.services.research_service import ResearchService
        svc = ResearchService()
        state = MarketState(ticker="AAPL", date="2026-05-22")
        prompt = svc._create_market_sentiment_prompt(state, {})
        assert "VOLATILITY REGIME" not in prompt
```

If `ResearchService()` cannot be constructed without API keys, patch its `__init__` the way other tests in `tests/test_market_sentiment_agent.py` already do, then call `_create_market_sentiment_prompt` on the bare instance.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_market_sentiment_agent.py::TestSentimentPromptVolatility -v`
Expected: FAIL with `AssertionError: assert 'VOLATILITY REGIME' in prompt`

- [ ] **Step 3: Write minimal implementation**

In `app/services/research_service.py`, inside `_create_market_sentiment_prompt`, after the `market_news_str` block is finalized (just before the `return f"""` on line 1807), add:

```python
        vol = getattr(state, "volatility_regime", None) or {}
        vol_block = ""
        if vol.get("regime_score") is not None:
            vol_block = (
                "\n        VOLATILITY REGIME (numeric ground truth — do NOT "
                "contradict this with news prose; cite these exact numbers):\n"
                f"        - VIX: {vol.get('vix')} ({vol.get('vix_class')}), "
                f"20-day percentile {vol.get('vix_pctile_20d')}%\n"
                f"        - VIX term structure: {vol.get('term_structure')} "
                f"(VIX - VIX3M spread {vol.get('term_spread')})\n"
                f"        - CNN Fear & Greed: {vol.get('fear_greed')} "
                f"({vol.get('fear_greed_rating')})\n"
                f"        - Regime: {vol.get('regime_label')} "
                f"(score {vol.get('regime_score')} of 1.0)\n"
                f"        {vol.get('summary')}\n"
            )
```

Then add `{vol_block}` to the prompt's `CONTEXT:` section. Change:

```python
        CONTEXT:
        - Date: {state.date}
        - Focus: TODAY and YESTERDAY only.
        {market_news_str}
```

to:

```python
        CONTEXT:
        - Date: {state.date}
        - Focus: TODAY and YESTERDAY only.
        {vol_block}{market_news_str}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_market_sentiment_agent.py -v`
Expected: PASS (new tests + existing sentiment tests still green)

- [ ] **Step 5: Commit**

```bash
git add app/services/research_service.py tests/test_market_sentiment_agent.py
git commit -m "feat(sentiment): inject numeric volatility regime into agent prompt"
```

---

### Task 9: Inject volatility block into the Fund Manager prompt

**Files:**
- Modify: `app/services/research_service.py:1298-1348` (`_create_fund_manager_prompt`)
- Test: `tests/test_fund_manager_prompt.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_fund_manager_prompt.py`:

```python
from app.models.market_state import MarketState
from app.services.research_service import ResearchService


def _regime():
    return {
        "vix": 24.0, "vix_class": "ELEVATED", "vix_pctile_20d": 90.0,
        "term_structure": "BACKWARDATION", "term_spread": 1.2,
        "fear_greed": 22, "fear_greed_rating": "Extreme Fear",
        "regime_score": 0.71, "regime_label": "FAVORABLE",
        "summary": "VIX 24.0 (ELEVATED), BACKWARDATION, trend BULL — "
                   "regime FAVORABLE (0.71) for dip-buying.",
    }


class TestFundManagerPromptVolatility:
    def test_volatility_block_present_when_regime_set(self):
        svc = ResearchService()
        state = MarketState(ticker="AAPL", date="2026-05-22",
                            volatility_regime=_regime())
        prompt = svc._create_fund_manager_prompt(state, [], [], "-6.0%")
        assert "VOLATILITY REGIME" in prompt
        assert "24.0" in prompt
        assert "BACKWARDATION" in prompt
        assert "FAVORABLE" in prompt

    def test_no_volatility_block_when_regime_missing(self):
        svc = ResearchService()
        state = MarketState(ticker="AAPL", date="2026-05-22")
        prompt = svc._create_fund_manager_prompt(state, [], [], "-6.0%")
        assert "VOLATILITY REGIME" not in prompt
```

If `ResearchService()` needs API keys, patch its `__init__` the same way `tests/test_market_sentiment_agent.py` does.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fund_manager_prompt.py -v`
Expected: FAIL with `AssertionError: assert 'VOLATILITY REGIME' in prompt`

- [ ] **Step 3: Write minimal implementation**

In `app/services/research_service.py`, inside `_create_fund_manager_prompt`, after the `tier_line` block (after line 1315, before the `ef = getattr(...)` line), add:

```python
        vol = getattr(state, "volatility_regime", None) or {}
        if vol.get("regime_score") is not None:
            vol_block = (
                "\nVOLATILITY REGIME (numeric ground truth — dip-buys "
                "mean-revert better when volatility is elevated and the term "
                "structure is in backwardation):\n"
                f"- VIX: {vol.get('vix')} ({vol.get('vix_class')}), "
                f"20-day percentile {vol.get('vix_pctile_20d')}%\n"
                f"- Term structure: {vol.get('term_structure')} "
                f"(VIX - VIX3M spread {vol.get('term_spread')})\n"
                f"- CNN Fear & Greed: {vol.get('fear_greed')} "
                f"({vol.get('fear_greed_rating')})\n"
                f"- Regime: {vol.get('regime_label')} "
                f"(score {vol.get('regime_score')} of 1.0) — higher favors "
                "dip-buying. Weigh this against the bull/bear cases; a "
                "FAVORABLE regime is a tailwind, UNFAVORABLE a headwind.\n"
            )
        else:
            vol_block = ""
```

Then add `{vol_block}` to the prompt's `DECISION CONTEXT:` section. Change:

```python
DECISION CONTEXT:
- Stock: {state.ticker}
- Drop: {drop_str} today
- This is a "Buy the Dip" evaluation. We are looking for oversold large-cap stocks with recovery potential.
- The investor holds positions until recovery (weeks to months), not day-trading.
- Gatekeeper Tier: {tier_line}
```

to:

```python
DECISION CONTEXT:
- Stock: {state.ticker}
- Drop: {drop_str} today
- This is a "Buy the Dip" evaluation. We are looking for oversold large-cap stocks with recovery potential.
- The investor holds positions until recovery (weeks to months), not day-trading.
- Gatekeeper Tier: {tier_line}
{vol_block}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fund_manager_prompt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/research_service.py tests/test_fund_manager_prompt.py
git commit -m "feat(pm): inject numeric volatility regime into Fund Manager prompt"
```

---

### Task 10: Add `volatility_regime` to the Deep Research context

**Files:**
- Modify: `app/services/stock_service.py:687-725` (`_build_deep_research_context`)
- Test: `tests/test_deep_research_context.py` (create)

`stock_service` already imports `gatekeeper_service` (used at line 376), so no new import is needed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research_context.py`:

```python
from unittest.mock import patch

from app.services.stock_service import StockService


_FAKE_REGIME = {
    "regime": "BULL", "vix": 16.75, "vix_class": "NORMAL",
    "term_structure": "CONTANGO", "regime_score": 0.48,
    "regime_label": "NEUTRAL", "summary": "...",
}


class TestDeepResearchContextVolatility:
    def test_context_includes_volatility_regime(self):
        svc = StockService()
        with patch("app.services.stock_service.gatekeeper_service"
                   ".check_market_regime", return_value=_FAKE_REGIME):
            ctx = svc._build_deep_research_context(
                report_data={"recommendation": "BUY"},
                raw_data={"change_percent": -6.0},
            )
        assert ctx["volatility_regime"] == _FAKE_REGIME
        assert ctx["volatility_regime"]["regime_score"] == 0.48
```

If `StockService()` cannot be constructed without side effects, patch its `__init__` to a no-op for the test (mirror the pattern used by other `tests/test_*` files that exercise `StockService` helpers), then call `_build_deep_research_context` on the bare instance.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_deep_research_context.py -v`
Expected: FAIL with `KeyError: 'volatility_regime'`

- [ ] **Step 3: Write minimal implementation**

In `app/services/stock_service.py`, inside `_build_deep_research_context`, add one entry to the returned dict (after the `"data_depth"` line, before the closing `}`):

```python
            # Evidence quality
            "data_depth": report_data.get("data_depth", {}),
            # Volatility regime (VIX, term structure, Fear & Greed, score) —
            # cached in gatekeeper_service so this is cheap.
            "volatility_regime": gatekeeper_service.check_market_regime(),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_deep_research_context.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add app/services/stock_service.py tests/test_deep_research_context.py
git commit -m "feat(deep-research): include volatility regime in DR context"
```

---

### Task 11: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -q`
Expected: PASS — no regressions. Pay attention to `tests/test_fred_cache.py`, `tests/test_gatekeeper_liquidity.py`, `tests/test_gatekeeper_nan.py`, and `tests/test_market_sentiment_agent.py`, since Tasks 1, 6, and 8 touched those modules.

- [ ] **Step 2: Smoke-check imports**

Run: `python -c "from app.services.volatility_service import volatility_service; from app.services.gatekeeper_service import gatekeeper_service; print(gatekeeper_service.check_market_regime().get('regime_score'))"`
Expected: prints a float between 0.0 and 1.0 (or `None` only if every external source is unreachable from the dev environment — in which case `errors` will be populated).

- [ ] **Step 3: Commit (only if Step 1/2 surfaced a fix)**

If no fixes were needed, skip this commit.

---

## Self-Review

**Spec coverage:**
- "Add VIX via FRED VIXCLS" → Tasks 1–2 (`fetch_series_history` + `get_vix_context`). ✓
- "current level + 5/20-day percentile" → Task 2 (`vix_pctile_5d`, `vix_pctile_20d`). ✓
- "Add VIX term structure (VIX − VIX3M) via yfinance" → Task 3. ✓
- "Use VIX as the gradient in a new regime check" (backlog #9) → Tasks 5–6 (`score_regime` + gatekeeper merge). ✓
- "Optionally add CNN Fear & Greed ... try/except graceful failure" → Task 4. ✓
- "Inject ... into the Market Sentiment Agent prompt, the PM prompt, and Deep Research context" → Tasks 8, 9, 10. ✓
- "Skip AAII, NAAIM, Investors Intelligence / Skip MOVE and SKEW" → explicitly out of scope, no tasks. ✓

**Type consistency:** The regime dict keys in the Design Reference match every producer (`get_regime`, `check_market_regime`) and consumer (sentiment prompt, PM prompt, DR context, tests). `score_regime` is a `@staticmethod` and is called as `VolatilityService.score_regime(...)` in tests and `self.score_regime(...)` in `get_regime` — consistent. `get_regime(trend=...)` is called with the `trend=` keyword in `check_market_regime` and asserted with `assert_called_once_with(trend=...)` in Task 6 tests — consistent.

**Placeholder scan:** No TBD/TODO/"handle edge cases" steps; every code step shows complete code.

**Note on `ResearchService` / `StockService` construction in prompt tests (Tasks 8–10):** the plan assumes these classes can be instantiated or `__init__`-patched in tests. The implementer should follow whatever pattern `tests/test_market_sentiment_agent.py` already uses (that file already builds prompts), and apply the same pattern to the two new prompt-test files. This is the one place the exact fixture shape depends on existing test conventions not fully visible in this plan.
