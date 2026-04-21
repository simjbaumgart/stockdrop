# Pipeline Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four reliability issues in the StockDrop pipeline: (1) TradingView silently fails for non-NASDAQ/OTC tickers on the analyst path, (2) deep-research JSON is corrupted by inline `[Source N]` markers, (3) FRED 500s leak into every cycle, (4) Drive quota errors spam logs.

**Architecture:** Four independent, mechanical changes to existing services. No new dependencies. Each fix is self-contained and shippable on its own. TradingView is the largest (new module + refactor of two call sites); the other three are small additions to existing files.

**Tech Stack:** Python 3.9, FastAPI, tradingview_ta, tradingview_screener, google-api-python-client, pytest, pytest-asyncio.

---

## Ordering rationale

Ship in this order (each task ends with a commit so you can stop after any one):

1. **Task 1 — TradingView exchange resolver.** Biggest silent-damage fix. Unifies the gatekeeper and analyst paths on one resolution function.
2. **Task 2 — Citation strip + prompt update.** Biggest blast-radius fix when it breaks (corrupts the JSON that gates every BUY).
3. **Task 3 — Drive circuit breaker.** Small noise reduction.
4. **Task 4 — FRED cache + fallback.** Variable-size investigation — start with a manual `curl` check before coding.

---

## File structure

**New files:**
- `app/services/tv_exchange_resolver.py` — Single source of truth for mapping a ticker to a TradingView `(exchange, screener)` pair. Pure function + tiny module-level cache. Has no other responsibilities.
- `tests/test_tv_exchange_resolver.py` — Unit tests for the resolver.
- `tests/test_citation_strip.py` — Unit tests for the citation-stripping helper.
- `tests/test_drive_circuit_breaker.py` — Unit tests for the Drive circuit breaker state machine.
- `tests/test_fred_cache.py` — Unit tests for the FRED in-memory cache + fallback behavior.

**Modified files:**
- `app/services/tradingview_service.py` — `get_technical_analysis` grows `exchange`/`screener` params; both `get_technical_analysis` and `get_technical_indicators` delegate to the new resolver when exchange is not supplied; on OTC failure they return a structured "TA unavailable" sentinel instead of an empty dict.
- `app/services/stock_service.py:522` — Pass `exchange` + `screener` through to `get_technical_analysis`.
- `scripts/reassess_positions.py:79` — Same: pass exchange through.
- `app/services/deep_research_service.py` — (a) add a module-level `_strip_citations` helper + call it inside `_parse_output` and `_parse_sell_reassessment_output` before every `json.loads`; (b) add one line to both prompts telling the model not to emit `[Source N]`.
- `app/services/drive_service.py` — Replace the single `_quota_exceeded` boolean with a consecutive-failure counter + a disabled-until timestamp persisted to disk.
- `app/services/fred_service.py` — Add a 24-hour in-memory per-series cache, serve stale on 500, fall back to Alpha Vantage `TREASURY_YIELD` for `DGS10`/`DGS2`.

---

## Task 1: TradingView exchange resolver

**Context:** `app/services/tradingview_service.py:344-377` (`get_technical_analysis`) hard-codes `exchange = "NASDAQ"`. Non-NASDAQ tickers (NYSE, AMEX, OTC pink-sheet ADRs like MBGYY, PAYP) silently return `{}`. Meanwhile `get_technical_indicators` at line 379 accepts an `exchange` arg and is already called by the gatekeeper with screener-supplied data — so the gatekeeper passes but the analyst path fails on the same ticker. We factor exchange resolution out so both paths share it.

**Files:**
- Create: `app/services/tv_exchange_resolver.py`
- Create: `tests/test_tv_exchange_resolver.py`
- Modify: `app/services/tradingview_service.py` (lines 344-377 `get_technical_analysis`, lines 379-433 `get_technical_indicators`)
- Modify: `app/services/stock_service.py:522`
- Modify: `scripts/reassess_positions.py:79`

### Step 1.1: Write the failing test for the resolver

- [ ] **Write `tests/test_tv_exchange_resolver.py`**

```python
import pytest
from unittest.mock import patch
from app.services.tv_exchange_resolver import (
    resolve_tv_exchange,
    clear_cache,
    TA_UNAVAILABLE_SENTINEL,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class TestResolveTvExchange:
    def test_nasdaq_ticker_from_probe(self):
        # AAPL should resolve to NASDAQ via the probe path
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            # NASDAQ probe succeeds
            probe.side_effect = lambda sym, ex: ex == "NASDAQ"
            result = resolve_tv_exchange("AAPL")
        assert result == ("NASDAQ", "america")

    def test_nyse_ticker_from_probe(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            # Only NYSE returns True
            probe.side_effect = lambda sym, ex: ex == "NYSE"
            result = resolve_tv_exchange("JPM")
        assert result == ("NYSE", "america")

    def test_otc_pink_sheet_ticker(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "OTC"
            result = resolve_tv_exchange("MBGYY")
        assert result == ("OTC", "america")

    def test_unresolvable_returns_none(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists", return_value=False):
            result = resolve_tv_exchange("XXXXX")
        assert result is None

    def test_cache_hit_skips_probe(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "NYSE"
            resolve_tv_exchange("JPM")
            call_count_first = probe.call_count
            # Second call should hit cache, not probe
            resolve_tv_exchange("JPM")
            assert probe.call_count == call_count_first

    def test_explicit_inputs_bypass_resolver(self):
        # If caller already knows the exchange/screener, resolver should
        # accept and return them without probing.
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            result = resolve_tv_exchange("AAPL", known_exchange="NASDAQ", known_screener="america")
            assert result == ("NASDAQ", "america")
            assert probe.call_count == 0


class TestSentinel:
    def test_sentinel_shape(self):
        # Callers use this shape to detect unavailable TA.
        assert TA_UNAVAILABLE_SENTINEL == {"ta_unavailable": True}
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_tv_exchange_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.tv_exchange_resolver'`

### Step 1.2: Implement the resolver

- [ ] **Create `app/services/tv_exchange_resolver.py`**

```python
"""
Single source of truth for TradingView exchange/screener resolution.

Both the gatekeeper path (get_technical_indicators) and the analyst path
(get_technical_analysis) must agree on where a ticker trades. Having one
function here means they cannot diverge silently — which was the MBGYY bug
where the gatekeeper correctly used OTC but the analyst hardcoded NASDAQ
and returned {}.
"""

from __future__ import annotations

from typing import Optional, Tuple
from tradingview_ta import TA_Handler, Interval

_EXCHANGE_CACHE: dict[str, Tuple[str, str]] = {}

TA_UNAVAILABLE_SENTINEL = {"ta_unavailable": True}

# Probe order matters: most tickers are NASDAQ, then NYSE, then OTC.
_PROBE_ORDER = ("NASDAQ", "NYSE", "AMEX", "OTC")
_US_SCREENER = "america"


def clear_cache() -> None:
    _EXCHANGE_CACHE.clear()


def _tv_symbol_exists(symbol: str, exchange: str) -> bool:
    """
    Returns True if tradingview_ta can resolve (symbol, exchange) for
    the US screener at the daily interval. We treat any exception as
    'not found'.
    """
    try:
        handler = TA_Handler(
            symbol=symbol,
            screener=_US_SCREENER,
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY,
        )
        handler.get_analysis()
        return True
    except Exception:
        return False


def resolve_tv_exchange(
    symbol: str,
    known_exchange: Optional[str] = None,
    known_screener: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """
    Map a ticker to a (exchange, screener) pair TradingView will accept.

    - If the caller already knows both (e.g. the screener passed them in
      cached_indicators), return them verbatim.
    - Otherwise consult the cache, then probe NASDAQ → NYSE → AMEX → OTC.
    - On failure to resolve, return None. Callers should treat None as
      "TA unavailable" rather than substituting a default exchange.
    """
    if known_exchange and known_screener:
        return (known_exchange.upper(), known_screener.lower())

    if symbol in _EXCHANGE_CACHE:
        return _EXCHANGE_CACHE[symbol]

    for candidate in _PROBE_ORDER:
        if _tv_symbol_exists(symbol, candidate):
            resolved = (candidate, _US_SCREENER)
            _EXCHANGE_CACHE[symbol] = resolved
            return resolved

    return None
```

- [ ] **Run test to verify it passes**

Run: `pytest tests/test_tv_exchange_resolver.py -v`
Expected: PASS (all 7 tests)

- [ ] **Commit**

```bash
git add app/services/tv_exchange_resolver.py tests/test_tv_exchange_resolver.py
git commit -m "feat(tv): add shared TradingView exchange resolver

Single source of truth for ticker → (exchange, screener) mapping.
Fixes divergence between gatekeeper path (which honored screener
exchange) and analyst path (which hard-coded NASDAQ), which caused
OTC ADRs like MBGYY to pass the gate then return empty TA."
```

### Step 1.3: Wire resolver into `get_technical_indicators`

- [ ] **Write the failing test** — add to `tests/test_tv_exchange_resolver.py`:

```python
class TestGetTechnicalIndicatorsDelegates:
    """Verify tradingview_service uses the resolver when exchange is missing."""

    def test_indicators_resolves_missing_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NYSE", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.return_value.indicators = {
                    "close": 10.0, "SMA200": 9.0, "RSI": 50.0,
                    "BB.lower": 8.0, "BB.upper": 12.0, "volume": 100,
                }
                tradingview_service.get_technical_indicators("JPM")
                res.assert_called_once_with(
                    "JPM", known_exchange=None, known_screener=None,
                )
                # Handler should use the resolved exchange
                _, kwargs = handler_cls.call_args
                assert kwargs["exchange"] == "NYSE"

    def test_indicators_skips_resolver_when_exchange_supplied(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NASDAQ", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.return_value.indicators = {
                    "close": 10.0, "SMA200": 9.0, "RSI": 50.0,
                    "BB.lower": 8.0, "BB.upper": 12.0, "volume": 100,
                }
                tradingview_service.get_technical_indicators(
                    "AAPL", exchange="NASDAQ", screener="america",
                )
                # Caller supplied both — resolver still called with known_* set,
                # but resolver will short-circuit.
                res.assert_called_once_with(
                    "AAPL", known_exchange="NASDAQ", known_screener="america",
                )
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_tv_exchange_resolver.py::TestGetTechnicalIndicatorsDelegates -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_tv_exchange' from 'app.services.tradingview_service'` or similar.

- [ ] **Modify `app/services/tradingview_service.py:379-433`** — replace the body of `get_technical_indicators`:

```python
    def get_technical_indicators(
        self,
        symbol: str,
        region: str = "US",
        exchange: str = None,
        screener: str = None,
    ) -> Dict:
        """
        Fetches specific technical indicators (SMA200, RSI, BB, Volume) for Gatekeeper.
        If exchange/screener are provided, they are used directly. Otherwise,
        resolve via resolve_tv_exchange. Returns {} if unresolvable or TA fails.
        """
        # Index/ETF overrides (AMEX/ARCA)
        if symbol in ["SPY", "XLK", "XLF", "XLV", "XLY", "XLP",
                      "XLE", "XLI", "XLC", "XLU", "XLB", "XLRE"]:
            exchange = "AMEX"
            screener = "america"

        resolved = resolve_tv_exchange(
            symbol,
            known_exchange=exchange,
            known_screener=screener,
        )
        if resolved is None:
            print(f"TV: could not resolve exchange for {symbol}")
            return {}
        exchange, screener = resolved

        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_1_DAY,
            )

            import time
            max_retries = 3
            for i in range(max_retries):
                try:
                    analysis = handler.get_analysis()
                    break
                except Exception as e:
                    if "429" in str(e) and i < max_retries - 1:
                        wait_time = (i + 1) * 2
                        print(f"429 Limit hit for {symbol}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    raise

            if analysis:
                inds = analysis.indicators
                return {
                    "close": inds.get("close", 0.0),
                    "sma200": inds.get("SMA200", 0.0),
                    "rsi": inds.get("RSI", 50.0),
                    "bb_lower": inds.get("BB.lower", 0.0),
                    "bb_upper": inds.get("BB.upper", 0.0),
                    "volume": inds.get("volume", 0),
                }
        except Exception as e:
            print(f"Error fetching indicators for {symbol}: {e}")

        return {}
```

- [ ] **Add the import at the top of `app/services/tradingview_service.py`** — right after line 4 (`import concurrent.futures`):

```python
from app.services.tv_exchange_resolver import resolve_tv_exchange, TA_UNAVAILABLE_SENTINEL
```

- [ ] **Run test to verify it passes**

Run: `pytest tests/test_tv_exchange_resolver.py::TestGetTechnicalIndicatorsDelegates -v`
Expected: PASS (2 tests)

### Step 1.4: Wire resolver into `get_technical_analysis` + return OTC sentinel

- [ ] **Write the failing test** — add to `tests/test_tv_exchange_resolver.py`:

```python
class TestGetTechnicalAnalysisDelegates:
    def test_analysis_resolves_missing_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("OTC", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.side_effect = Exception("no data for OTC")
                result = tradingview_service.get_technical_analysis("MBGYY")
                # OTC probe succeeded but TA call failed → structured sentinel
                assert result == {"ta_unavailable": True}

    def test_analysis_accepts_known_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NYSE", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                mock_analysis = handler.get_analysis.return_value
                mock_analysis.summary = {"RECOMMENDATION": "BUY"}
                mock_analysis.oscillators = {}
                mock_analysis.moving_averages = {}
                mock_analysis.indicators = {"close": 10.0}
                result = tradingview_service.get_technical_analysis(
                    "JPM", exchange="NYSE", screener="america",
                )
                assert result["summary"]["RECOMMENDATION"] == "BUY"
                res.assert_called_once_with(
                    "JPM", known_exchange="NYSE", known_screener="america",
                )

    def test_analysis_unresolvable_returns_empty(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange", return_value=None):
            result = tradingview_service.get_technical_analysis("XXXXX")
            assert result == {}
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_tv_exchange_resolver.py::TestGetTechnicalAnalysisDelegates -v`
Expected: FAIL — existing `get_technical_analysis` has no `exchange` param and does not call the resolver.

- [ ] **Modify `app/services/tradingview_service.py:344-377`** — replace `get_technical_analysis`:

```python
    def get_technical_analysis(
        self,
        symbol: str,
        region: str = "US",
        exchange: str = None,
        screener: str = None,
    ) -> Dict:
        """
        Fetches technical analysis summary for a symbol.

        Returns:
          - A dict with summary/oscillators/moving_averages/indicators on success.
          - {'ta_unavailable': True} if the exchange resolves but TA itself
            fails (common for OTC pinks — tradingview-ta coverage is incomplete).
            Downstream agents should weight technicals at zero, not bearish.
          - {} if the exchange cannot be resolved at all.
        """
        resolved = resolve_tv_exchange(
            symbol,
            known_exchange=exchange,
            known_screener=screener,
        )
        if resolved is None:
            print(f"TV TA: could not resolve exchange for {symbol}")
            return {}
        exchange, screener = resolved

        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=screener,
                exchange=exchange,
                interval=Interval.INTERVAL_1_DAY,
            )
            analysis = handler.get_analysis()

            if analysis:
                return {
                    "summary": analysis.summary,
                    "oscillators": analysis.oscillators,
                    "moving_averages": analysis.moving_averages,
                    "indicators": analysis.indicators,
                }
        except Exception as e:
            print(f"Error fetching TA for {symbol} on {exchange}: {e}")
            # Resolver said the symbol exists on this exchange, but TA data
            # wasn't retrievable. Signal unavailability rather than absence.
            return dict(TA_UNAVAILABLE_SENTINEL)

        return {}
```

- [ ] **Run test to verify it passes**

Run: `pytest tests/test_tv_exchange_resolver.py::TestGetTechnicalAnalysisDelegates -v`
Expected: PASS (3 tests)

### Step 1.5: Pass exchange through from stock_service

- [ ] **Modify `app/services/stock_service.py:522`** — change the call from:

```python
                    technical_analysis = tradingview_service.get_technical_analysis(symbol, region=stock.get("region", "US"))
```

to:

```python
                    technical_analysis = tradingview_service.get_technical_analysis(
                        symbol,
                        region=stock.get("region", "US"),
                        exchange=exchange,
                        screener=stock.get("screener"),
                    )
```

(`exchange` is already in scope — it was assigned at line 457.)

- [ ] **Modify `scripts/reassess_positions.py:79`** — change:

```python
        ta = tradingview_service.get_technical_analysis(symbol, region=region)
```

to:

```python
        # Reuse indicators' resolver result by letting get_technical_analysis
        # probe. The cache in tv_exchange_resolver means the same ticker is
        # not re-probed on the TA call.
        ta = tradingview_service.get_technical_analysis(symbol, region=region)
```

(No code change in the second line — leave the call as-is; the cache in the resolver will handle it. This is a deliberate no-op to document the thinking. If you want to skip it, just leave `scripts/reassess_positions.py` untouched.)

- [ ] **Run the full test file**

Run: `pytest tests/test_tv_exchange_resolver.py tests/test_us_only.py -v`
Expected: PASS across both files. If `test_us_only.py` fails on the new signature, update the mocks there to accept the new kwargs (they currently assert `TA_Handler` was called with `exchange="NASDAQ"` — change to match however resolver probes).

- [ ] **Manually test against MBGYY (regression case)**

Run (from project root):
```bash
python -c "
from app.services.tradingview_service import tradingview_service
r = tradingview_service.get_technical_analysis('MBGYY')
print('MBGYY result:', r)
r2 = tradingview_service.get_technical_analysis('AAPL')
print('AAPL keys:', list(r2.keys()))
"
```
Expected:
- `MBGYY` returns either a real TA dict (if tradingview-ta has OTC data) or `{'ta_unavailable': True}`. NOT `{}`.
- `AAPL` returns `['summary', 'oscillators', 'moving_averages', 'indicators']`.

- [ ] **Commit**

```bash
git add app/services/tradingview_service.py app/services/stock_service.py tests/test_tv_exchange_resolver.py
git commit -m "fix(tv): unify exchange resolution across analyst and gatekeeper paths

- get_technical_analysis now accepts exchange/screener and falls back to
  resolve_tv_exchange() — same function the gatekeeper already uses via
  get_technical_indicators.
- stock_service passes the screener-supplied exchange/screener through.
- On resolved-but-TA-unavailable (OTC coverage gap), return
  {'ta_unavailable': True} instead of {} so downstream agents can
  distinguish absent-data from bearish-data.

Fixes MBGYY-class bug where OTC ADRs passed the gate but silently
returned empty TA because the analyst path hard-coded NASDAQ."
```

---

## Task 2: Deep-research citation strip + prompt hardening

**Context:** `app/services/deep_research_service.py:1313` and line 1500 parse model output with `json.loads`. When the model emits inline `[Source N]` markers inside string values (e.g. `"signa [Source 1]ling"`), the markers pass JSON parsing but corrupt the readable string. We strip them defensively before parsing, and also tell the model not to emit them in the first place.

**Files:**
- Modify: `app/services/deep_research_service.py` — add helper, call it in both parsers, add one line to both prompts.
- Create: `tests/test_citation_strip.py`

### Step 2.1: Write the failing test

- [ ] **Create `tests/test_citation_strip.py`**

```python
import pytest
from app.services.deep_research_service import _strip_citations, _CITATION_STRIP_COUNTER


class TestStripCitations:
    def test_simple_trailing_marker(self):
        assert _strip_citations("great news [Source 1]") == "great news"

    def test_mid_word_marker_collapses_cleanly(self):
        # The bug: "signa [Source 1]ling" must become "signaling", not "signa ling"
        assert _strip_citations("signa [Source 1]ling") == "signaling"

    def test_multiple_markers(self):
        raw = "text [Source 1] more [Source 2] end"
        assert _strip_citations(raw) == "text more end"

    def test_no_markers_is_noop(self):
        raw = '{"action": "BUY", "reason": "clean"}'
        assert _strip_citations(raw) == raw

    def test_marker_with_multiple_digits(self):
        assert _strip_citations("x [Source 42] y") == "x y"

    def test_marker_with_internal_whitespace(self):
        assert _strip_citations("x [Source  3] y") == "x y"

    def test_counter_increments_only_on_change(self):
        before = _CITATION_STRIP_COUNTER["stripped"]
        _strip_citations("no markers here")
        assert _CITATION_STRIP_COUNTER["stripped"] == before
        _strip_citations("has [Source 1] marker")
        assert _CITATION_STRIP_COUNTER["stripped"] == before + 1

    def test_json_parseable_after_strip(self):
        import json
        raw = '{"action": "BUY", "reason": "strong setup [Source 1] confirmed"}'
        cleaned = _strip_citations(raw)
        parsed = json.loads(cleaned)
        assert parsed["reason"] == "strong setup confirmed"
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_citation_strip.py -v`
Expected: FAIL with `ImportError: cannot import name '_strip_citations'`.

### Step 2.2: Add the helper + counter to `deep_research_service.py`

- [ ] **Modify `app/services/deep_research_service.py`** — add near the top of the file, just after the existing imports (around line 10-30; place it at module level outside the class):

```python
import re

# Module-level counter so we can tell whether the prompt-side fix is sticking.
# If this keeps incrementing in production, the prompt hardening isn't working
# and the strip is papering over the problem.
_CITATION_STRIP_COUNTER = {"stripped": 0}

# Match " [Source N] " with surrounding whitespace collapsed so that
# "signa [Source 1]ling" → "signaling" rather than "signa ling".
_CITATION_RE = re.compile(r"\s*\[Source\s*\d+\]\s*")


def _strip_citations(raw: str) -> str:
    """
    Remove inline [Source N] markers from deep-research JSON text.

    The model sometimes emits these despite instructions; they don't break
    json.loads but they corrupt the human-readable string values.
    """
    if "[Source" not in raw:
        return raw
    cleaned = _CITATION_RE.sub("", raw)
    if cleaned != raw:
        _CITATION_STRIP_COUNTER["stripped"] += 1
    return cleaned
```

- [ ] **Run the helper test to verify it passes**

Run: `pytest tests/test_citation_strip.py -v`
Expected: PASS (8 tests)

### Step 2.3: Call `_strip_citations` in both parsers before `json.loads`

- [ ] **Modify `app/services/deep_research_service.py:_parse_output`** (around line 1469-1512). Inside the `for output in reversed(outputs):` loop, after the markdown-fence stripping (right before the first `json.loads`), insert the citation strip.

Find this block (around lines 1487-1500):

```python
                # Cleaning
                text = text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                     text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

                # Try explicit JSON parsing first
                import json
                try:
                    return json.loads(text)
```

Change to:

```python
                # Cleaning
                text = text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                     text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                text = _strip_citations(text)

                # Try explicit JSON parsing first
                import json
                try:
                    return json.loads(text)
```

And the regex-fallback a few lines later (around line 1507):

```python
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    try:
                        return json.loads(json_match.group(0))
```

Change to:

```python
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    try:
                        return json.loads(_strip_citations(json_match.group(0)))
```

- [ ] **Modify `_parse_sell_reassessment_output`** at lines 1302-1321 similarly. Find:

```python
                text = text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                if not best_text:
                    best_text = text
                try:
                    parsed = json.loads(text)
```

Change to:

```python
                text = text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                text = _strip_citations(text)
                if not best_text:
                    best_text = text
                try:
                    parsed = json.loads(text)
```

And the regex-fallback a few lines below (line 1320-1321):

```python
                    m = re.search(r"\{.*\}", text, re.DOTALL)
                    if m:
                        try:
                            return json.loads(m.group(0))
```

Change to:

```python
                    m = re.search(r"\{.*\}", text, re.DOTALL)
                    if m:
                        try:
                            return json.loads(_strip_citations(m.group(0)))
```

### Step 2.4: Add the prompt instruction

- [ ] **Modify `app/services/deep_research_service.py:_construct_prompt`** — add one line inside the `OUTPUT FORMAT:` section around line 1084. Find:

```python
OUTPUT FORMAT:
Your output must be valid JSON. All price fields must be numbers. All percentage fields must be numbers.
```

Change to:

```python
OUTPUT FORMAT:
Your output must be valid JSON. All price fields must be numbers. All percentage fields must be numbers.
Do NOT include inline source markers like [Source 1], [Source 2], etc. in any string value. Your search grounding is recorded separately by the API; do not repeat citation markers inside JSON fields.
```

- [ ] **Modify `_construct_sell_reassessment_prompt`** — find the matching `OUTPUT FORMAT` section (within the function defined at line 1127; grep for `OUTPUT FORMAT` if needed) and add the same instruction. If the sell prompt doesn't have an explicit `OUTPUT FORMAT` line, add the no-citations line at the end of the prompt body before the closing `"""`.

### Step 2.5: Write integration-style test and run

- [ ] **Add one more test to `tests/test_citation_strip.py`**

```python
class TestParserStripsCitations:
    def test_parse_output_handles_citation_markers(self):
        from unittest.mock import patch
        from app.services.deep_research_service import DeepResearchService
        svc = DeepResearchService.__new__(DeepResearchService)  # skip __init__
        poll = {
            "outputs": [
                {"text": '{"action": "BUY", "reason": "clean [Source 1] setup"}'},
            ]
        }
        # Prevent repair path from kicking in
        with patch.object(svc, "_repair_json_using_flash", return_value=None):
            result = svc._parse_output(poll, schema_type="individual")
        assert result is not None
        assert result["reason"] == "clean setup"
        assert result["action"] == "BUY"
```

- [ ] **Run tests**

Run: `pytest tests/test_citation_strip.py -v`
Expected: PASS (9 tests)

- [ ] **Commit**

```bash
git add app/services/deep_research_service.py tests/test_citation_strip.py
git commit -m "fix(deep-research): strip [Source N] markers before JSON parse

Two-layer fix:
1. Prompt now explicitly forbids inline citation markers in JSON values.
2. Defensive _strip_citations() runs before every json.loads so the
   prompt slipping does not corrupt recommendation strings.

The regex swallows surrounding whitespace so 'signa [Source 1]ling'
collapses to 'signaling' rather than 'signa ling'. A module-level
counter tracks how often the strip fires — if the prompt change is
holding, it should trend toward zero."
```

---

## Task 3: Drive quota circuit breaker

**Context:** `app/services/drive_service.py` already has a `_quota_exceeded` flag set on first error (lines 16, 112, 170). Problem: (a) it's session-only, so a process restart starts retrying immediately, and (b) it flips on the first transient error rather than after a sustained failure. We replace it with a 3-strikes-in-24h breaker persisted to disk.

**Files:**
- Modify: `app/services/drive_service.py`
- Create: `tests/test_drive_circuit_breaker.py`

### Step 3.1: Write the failing test

- [ ] **Create `tests/test_drive_circuit_breaker.py`**

```python
import datetime
import pytest
from unittest.mock import patch, MagicMock
from app.services.drive_service import GoogleDriveService


@pytest.fixture
def svc(tmp_path):
    """Construct a GoogleDriveService instance without real credentials."""
    s = GoogleDriveService.__new__(GoogleDriveService)
    s.creds = None
    s.sheets_service = MagicMock()
    s.drive_service = MagicMock()
    s._breaker_state_path = str(tmp_path / "drive_breaker.json")
    s._consecutive_quota_failures = 0
    s._disabled_until = None
    return s


class TestCircuitBreaker:
    def test_single_quota_error_does_not_disable(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("storageQuotaExceeded: quota exceeded")
        )
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 1
        assert svc._disabled_until is None

    def test_three_quota_errors_trip_breaker(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = (
            Exception("storageQuotaExceeded")
        )
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        for _ in range(3):
            svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 3
        assert svc._disabled_until is not None
        assert svc._disabled_until > datetime.datetime.utcnow()

    def test_successful_upload_resets_counter(self, svc):
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = [
            Exception("storageQuotaExceeded"),
            Exception("storageQuotaExceeded"),
            {"updates": {"updatedCells": 1}},
        ]
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.upload_data({"AAPL": {"price": 1.0}})
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 2
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 0

    def test_disabled_window_short_circuits(self, svc):
        svc._disabled_until = datetime.datetime.utcnow() + datetime.timedelta(hours=1)
        svc._get_or_create_spreadsheet = MagicMock()
        svc.upload_data({"AAPL": {"price": 1.0}})
        # Should have returned early — get_or_create should not have been called
        svc._get_or_create_spreadsheet.assert_not_called()

    def test_expired_disabled_window_resets(self, svc):
        svc._disabled_until = datetime.datetime.utcnow() - datetime.timedelta(hours=1)
        svc._consecutive_quota_failures = 3
        svc._get_or_create_spreadsheet = MagicMock(return_value="sid")
        svc.sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {
            "updates": {"updatedCells": 1}
        }
        svc.upload_data({"AAPL": {"price": 1.0}})
        assert svc._consecutive_quota_failures == 0
        assert svc._disabled_until is None


class TestPersistence:
    def test_state_persists_across_instances(self, svc, tmp_path):
        svc._consecutive_quota_failures = 3
        svc._disabled_until = datetime.datetime.utcnow() + datetime.timedelta(hours=12)
        svc._save_breaker_state()

        s2 = GoogleDriveService.__new__(GoogleDriveService)
        s2._breaker_state_path = svc._breaker_state_path
        s2._load_breaker_state()
        assert s2._consecutive_quota_failures == 3
        assert s2._disabled_until is not None
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_drive_circuit_breaker.py -v`
Expected: FAIL — `_breaker_state_path`, `_consecutive_quota_failures`, `_disabled_until`, `_save_breaker_state`, `_load_breaker_state` don't exist yet.

### Step 3.2: Implement the breaker

- [ ] **Modify `app/services/drive_service.py`** — replace the entire file body with:

```python
import os
import json
import datetime
import pathlib
from google.oauth2 import service_account
from googleapiclient.discovery import build


class GoogleDriveService:
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.file',
    ]
    SERVICE_ACCOUNT_FILE = 'service_account.json'
    SPREADSHEET_NAME = 'Stock Tracker Data'
    FOLDER_ID = '1tSvhvXdF_mCX1MbPEngDfByH6E2TdHFy'

    # Breaker tuning
    QUOTA_FAILURES_TO_TRIP = 3
    DISABLED_DURATION = datetime.timedelta(hours=24)

    BREAKER_STATE_FILE = '.drive_breaker_state.json'

    def __init__(self):
        self.creds = None
        self.sheets_service = None
        self.drive_service = None
        self._breaker_state_path = self.BREAKER_STATE_FILE
        self._consecutive_quota_failures = 0
        self._disabled_until: datetime.datetime | None = None
        self._load_breaker_state()
        self._authenticate()

    def _load_breaker_state(self):
        try:
            if os.path.exists(self._breaker_state_path):
                with open(self._breaker_state_path) as f:
                    state = json.load(f)
                self._consecutive_quota_failures = int(state.get("consecutive_quota_failures", 0))
                dis = state.get("disabled_until")
                self._disabled_until = datetime.datetime.fromisoformat(dis) if dis else None
        except Exception as e:
            print(f"[Google Drive] Could not load breaker state: {e}")

    def _save_breaker_state(self):
        try:
            state = {
                "consecutive_quota_failures": self._consecutive_quota_failures,
                "disabled_until": self._disabled_until.isoformat() if self._disabled_until else None,
            }
            with open(self._breaker_state_path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"[Google Drive] Could not save breaker state: {e}")

    def _authenticate(self):
        if os.path.exists(self.SERVICE_ACCOUNT_FILE):
            try:
                self.creds = service_account.Credentials.from_service_account_file(
                    self.SERVICE_ACCOUNT_FILE, scopes=self.SCOPES)
                self.sheets_service = build('sheets', 'v4', credentials=self.creds)
                self.drive_service = build('drive', 'v3', credentials=self.creds)
                print("Authenticated with Google Drive/Sheets.")
            except Exception as e:
                print(f"Error authenticating with Google Drive: {e}")
        else:
            print(f"Service account file {self.SERVICE_ACCOUNT_FILE} not found. Drive upload disabled.")

    def _breaker_tripped(self) -> bool:
        """True if we should short-circuit; side-effect: clears stale disabled_until."""
        if self._disabled_until is None:
            return False
        if datetime.datetime.utcnow() >= self._disabled_until:
            # Window expired — reset and try again.
            self._consecutive_quota_failures = 0
            self._disabled_until = None
            self._save_breaker_state()
            return False
        return True

    def _record_quota_failure(self):
        self._consecutive_quota_failures += 1
        if self._consecutive_quota_failures >= self.QUOTA_FAILURES_TO_TRIP:
            self._disabled_until = datetime.datetime.utcnow() + self.DISABLED_DURATION
            print(
                f"[Google Drive] Circuit breaker tripped after "
                f"{self._consecutive_quota_failures} consecutive quota errors. "
                f"Disabled until {self._disabled_until.isoformat()}."
            )
        self._save_breaker_state()

    def _record_success(self):
        if self._consecutive_quota_failures:
            self._consecutive_quota_failures = 0
            self._save_breaker_state()

    def _get_or_create_spreadsheet(self):
        if not self.drive_service:
            return None
        query = (
            f"name = '{self.SPREADSHEET_NAME}' and '{self.FOLDER_ID}' in parents "
            f"and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        )
        results = self.drive_service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']
        file_metadata = {
            'name': self.SPREADSHEET_NAME,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [self.FOLDER_ID],
        }
        try:
            file = self.drive_service.files().create(body=file_metadata, fields='id').execute()
            print(f"Created new spreadsheet: {self.SPREADSHEET_NAME} ({file.get('id')})")
            return file.get('id')
        except Exception as e:
            print(f"Error creating spreadsheet: {e}")
            return None

    def upload_data(self, data_dict):
        if self._breaker_tripped():
            return
        if not self.sheets_service:
            return
        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sorted_keys = sorted(data_dict.keys())
        values = [timestamp] + [data_dict.get(k, {}).get('price', 0.0) for k in sorted_keys]
        body = {'values': [values]}
        try:
            self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            self._record_success()
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._record_quota_failure()
            else:
                print(f"Error appending data to sheet: {e}")

    def save_decision(self, decision_data: dict):
        if self._breaker_tripped():
            return
        if not self.sheets_service:
            return
        spreadsheet_id = self._get_or_create_spreadsheet()
        if not spreadsheet_id:
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        values = [
            timestamp,
            decision_data.get("symbol", ""),
            decision_data.get("company_name", ""),
            decision_data.get("price", 0.0),
            decision_data.get("change_percent", 0.0),
            decision_data.get("recommendation", ""),
            decision_data.get("reasoning", ""),
            decision_data.get("pe_ratio", 0.0),
            decision_data.get("market_cap", 0.0),
            decision_data.get("sector", ""),
            decision_data.get("region", ""),
        ]
        body = {'values': [values]}
        try:
            self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body).execute()
            self._record_success()
        except Exception as e:
            if "storageQuotaExceeded" in str(e):
                self._record_quota_failure()
            else:
                print(f"Error saving decision to Drive: {e}")


drive_service = GoogleDriveService()
```

- [ ] **Run test to verify it passes**

Run: `pytest tests/test_drive_circuit_breaker.py -v`
Expected: PASS (6 tests)

- [ ] **Add `.drive_breaker_state.json` to `.gitignore`**

Modify `.gitignore` — append the line:
```
.drive_breaker_state.json
```

- [ ] **Commit**

```bash
git add app/services/drive_service.py tests/test_drive_circuit_breaker.py .gitignore
git commit -m "fix(drive): replace session flag with 3-strike 24h circuit breaker

Previously the _quota_exceeded flag tripped on the first transient error
and reset on every process restart. Now:
- Three consecutive storageQuotaExceeded errors trip the breaker.
- Disabled state persists to .drive_breaker_state.json for 24h.
- Any successful upload resets the counter.
- Expired disabled windows self-clear on the next upload attempt.

Reports continue to land in SQLite and data/council_reports/ — losing
Drive sync is acceptable; flooding logs with one error per cycle is not."
```

---

## Task 4: FRED cache + Alpha Vantage fallback

**Context:** `app/services/fred_service.py:62` calls the FRED API once per indicator per macro fetch. When FRED returns 500 on `GDP`, `DGS10`, `DGS2` simultaneously, the PM prompt loses macro context entirely. We (a) cache each series for 24 hours in memory, (b) serve stale data with a flag on 500, (c) fall back to Alpha Vantage `TREASURY_YIELD` for treasury yields only.

**Start with a manual check** — before coding, confirm whether this is a FRED-side issue or a client-side issue:

```bash
curl -sS "https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key=$FRED_API_KEY&file_type=json&sort_order=desc&limit=1"
```
- If returns a JSON observation: bug is in `fred_service.py`. Proceed with the plan below.
- If returns 500/403: this is a FRED-side outage or key issue. The plan below still helps (cache + fallback) but also check `echo $FRED_API_KEY | cut -c1-6` to confirm the key loads.

**Files:**
- Modify: `app/services/fred_service.py`
- Create: `tests/test_fred_cache.py`

### Step 4.1: Write the failing test

- [ ] **Create `tests/test_fred_cache.py`**

```python
import datetime
import pytest
from unittest.mock import patch, MagicMock
from app.services.fred_service import FredService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "test_key")
    s = FredService()
    s._cache.clear()
    return s


def _obs(value="4.25", date="2026-04-20"):
    return {"observations": [{"value": value, "date": date}]}


class TestFredCache:
    def test_fresh_fetch_caches(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status = MagicMock()
            mget.return_value.json = MagicMock(return_value=_obs("4.25"))
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.25"
            assert "DGS10" in svc._cache

    def test_cache_hit_skips_http(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.30",
            "date": "2026-04-20",
            "fetched_at": datetime.datetime.utcnow(),
            "stale": False,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.30"
            mget.assert_not_called()

    def test_500_serves_stale(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.10",
            "date": "2026-04-19",
            "fetched_at": datetime.datetime.utcnow() - datetime.timedelta(hours=25),
            "stale": False,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            v, d = svc._fetch_latest_observation("DGS10")
            assert v == "4.10"  # stale served
            assert svc._cache["DGS10"]["stale"] is True

    def test_500_no_cache_falls_back_to_alpha_vantage_for_yields(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            with patch.object(svc, "_fetch_av_treasury_yield", return_value=("4.27", "2026-04-20")) as fav:
                v, d = svc._fetch_latest_observation("DGS10")
                assert v == "4.27"
                fav.assert_called_once_with("10year")

    def test_500_no_cache_no_fallback_for_non_yield_series(self, svc):
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            v, d = svc._fetch_latest_observation("GDP")
            assert v == "N/A"
            assert d == "N/A"

    def test_staleness_flag_in_get_macro_data(self, svc):
        svc._cache["DGS10"] = {
            "value": "4.10", "date": "2026-04-19",
            "fetched_at": datetime.datetime.utcnow() - datetime.timedelta(hours=25),
            "stale": True,
        }
        with patch("app.services.fred_service.requests.get") as mget:
            mget.return_value.raise_for_status.side_effect = Exception("500")
            data = svc.get_macro_data()
            assert data["10Y Treasury Yield"].get("stale") is True
```

- [ ] **Run test to verify it fails**

Run: `pytest tests/test_fred_cache.py -v`
Expected: FAIL — `_cache`, `_fetch_av_treasury_yield` don't exist.

### Step 4.2: Implement cache + fallback

- [ ] **Modify `app/services/fred_service.py`** — replace the file:

```python
import os
import datetime
import logging
import requests
from typing import Dict, Any, Optional, Tuple

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class FredService:
    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
    AV_URL = "https://www.alphavantage.co/query"

    CACHE_TTL = datetime.timedelta(hours=24)

    # Map FRED treasury series IDs to Alpha Vantage maturity params.
    _AV_YIELD_MAP = {
        "DGS10": "10year",
        "DGS2": "2year",
    }

    def __init__(self):
        self.api_key = os.getenv("FRED_API_KEY")
        self.av_api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get_macro_data(self) -> Dict[str, Any]:
        if not self.api_key:
            logger.warning("FRED_API_KEY not found. Returning empty macro data.")
            return {}

        indicators = {
            "Unemployment Rate": "UNRATE",
            "CPI (Inflation)": "CPIAUCSL",
            "Fed Funds Rate": "FEDFUNDS",
            "GDP": "GDP",
            "10Y Treasury Yield": "DGS10",
            "2Y Treasury Yield": "DGS2",
        }

        data: Dict[str, Any] = {}
        for name, series_id in indicators.items():
            try:
                value, date = self._fetch_latest_observation(series_id)
                entry: Dict[str, Any] = {"value": value, "date": date}
                cached = self._cache.get(series_id)
                if cached and cached.get("stale"):
                    entry["stale"] = True
                data[name] = entry
            except Exception as e:
                logger.error(f"Error fetching {name} ({series_id}): {e}")
                data[name] = {"value": "N/A", "date": "N/A"}

        try:
            ten_y = float(data["10Y Treasury Yield"]["value"])
            two_y = float(data["2Y Treasury Yield"]["value"])
            data["10Y-2Y Spread"] = {
                "value": f"{ten_y - two_y:.2f}",
                "date": data["10Y Treasury Yield"]["date"],
            }
        except (ValueError, KeyError):
            data["10Y-2Y Spread"] = {"value": "N/A", "date": "N/A"}

        return data

    def _fetch_latest_observation(self, series_id: str) -> Tuple[str, str]:
        # Cache hit within TTL
        cached = self._cache.get(series_id)
        now = datetime.datetime.utcnow()
        if cached and not cached.get("stale"):
            age = now - cached["fetched_at"]
            if age < self.CACHE_TTL:
                return cached["value"], cached["date"]

        # Fresh fetch
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            json_data = response.json()
            observations = json_data.get("observations", [])
            if observations:
                latest = observations[0]
                value = latest.get("value", "N/A")
                date = latest.get("date", "N/A")
                self._cache[series_id] = {
                    "value": value,
                    "date": date,
                    "fetched_at": now,
                    "stale": False,
                }
                return value, date
            return "N/A", "N/A"

        except Exception as e:
            logger.warning(f"FRED fetch failed for {series_id}: {e}")
            # Serve stale if we have any cached value
            if cached:
                cached["stale"] = True
                self._cache[series_id] = cached
                logger.info(f"Serving stale value for {series_id} (age: {now - cached['fetched_at']})")
                return cached["value"], cached["date"]
            # Last resort: Alpha Vantage for treasury yields
            if series_id in self._AV_YIELD_MAP and self.av_api_key:
                av = self._fetch_av_treasury_yield(self._AV_YIELD_MAP[series_id])
                if av:
                    value, date = av
                    self._cache[series_id] = {
                        "value": value,
                        "date": date,
                        "fetched_at": now,
                        "stale": False,
                    }
                    return value, date
            return "N/A", "N/A"

    def _fetch_av_treasury_yield(self, maturity: str) -> Optional[Tuple[str, str]]:
        try:
            params = {
                "function": "TREASURY_YIELD",
                "interval": "daily",
                "maturity": maturity,
                "apikey": self.av_api_key,
            }
            r = requests.get(self.AV_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return data[0]["value"], data[0]["date"]
        except Exception as e:
            logger.warning(f"Alpha Vantage treasury fallback failed for {maturity}: {e}")
        return None


fred_service = FredService()
```

- [ ] **Run test to verify it passes**

Run: `pytest tests/test_fred_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Manually verify live fetch still works**

Run (from project root with `.env` loaded):
```bash
python -c "
from app.services.fred_service import fred_service
data = fred_service.get_macro_data()
for k, v in data.items():
    print(f'{k}: {v}')
"
```
Expected: Real values for each indicator. If any series returns `N/A`, check whether your `FRED_API_KEY` is loaded (`echo $FRED_API_KEY | head -c 6`).

- [ ] **Commit**

```bash
git add app/services/fred_service.py tests/test_fred_cache.py
git commit -m "fix(fred): 24h cache + stale-on-failure + Alpha Vantage yield fallback

FRED 500s on GDP/DGS10/DGS2 were wiping macro context from the PM
prompt. Now:
- Each series is cached for 24h (matches FRED's natural update cadence
  for DGS10/DGS2; GDP is quarterly so even more oversized TTL is fine).
- On fetch failure with cache present, serve stale and flag the entry.
- On fetch failure with no cache for DGS10/DGS2, fall back to Alpha
  Vantage TREASURY_YIELD (we already hold that key).
- Non-yield series (UNRATE, CPI, FEDFUNDS, GDP) still return N/A on
  failure — no equivalent free source, and these are monthly+ anyway."
```

---

## Self-review checklist (run this before handing off)

After all four tasks are complete, verify:

- [ ] `pytest tests/test_tv_exchange_resolver.py tests/test_citation_strip.py tests/test_drive_circuit_breaker.py tests/test_fred_cache.py -v` — all pass.
- [ ] `grep -n 'exchange = "NASDAQ"' app/services/tradingview_service.py` — should show no matches (the hardcoded default is gone).
- [ ] `grep -n '_quota_exceeded' app/services/drive_service.py` — should show no matches (replaced by `_disabled_until`).
- [ ] MBGYY smoke test returns either real TA data or `{"ta_unavailable": True}`, not `{}`.
- [ ] No new dependencies added to `requirements.txt`.
- [ ] `.drive_breaker_state.json` is gitignored.

## Spec coverage check

| User proposal | Task |
|---|---|
| TradingView: factor resolver out of `get_technical_analysis`; share with gatekeeper path | 1.1–1.2 |
| TradingView: `_yahoo_to_tv_exchange` mapping + probe fallback (NASDAQ→NYSE→OTC) | 1.2 (probe only — Yahoo mapping deferred; probing covers the cases) |
| TradingView: OTC "TA unavailable" structured signal instead of empty dict | 1.4 |
| Deep research: prompt update forbidding `[Source N]` | 2.4 |
| Deep research: defensive strip before `json.loads` with whitespace-swallowing regex | 2.1–2.3 |
| Deep research: counter logging when strip changes the string | 2.2 (`_CITATION_STRIP_COUNTER`) |
| FRED: manual curl check to isolate client vs server | 4 intro |
| FRED: 24h cache per series | 4.2 |
| FRED: serve stale on 500 with staleness flag | 4.2 |
| FRED: Alpha Vantage `TREASURY_YIELD` fallback for yields | 4.2 |
| Drive: 3-strike circuit breaker, 24h disable, single warning | 3.2 |

**One deviation from spec:** the user suggested using `yahoo_ticker_resolver.resolve()` first to map Yahoo exchange → TradingView exchange. I deferred that because (a) the current resolver is just a pass-through for US exchanges and does not actually return exchange info (`app/services/yahoo_ticker_resolver.py:34` just returns the symbol unchanged), and (b) probing NASDAQ→NYSE→AMEX→OTC covers all the real cases with ≤4 HTTP calls, cached. If the probe approach turns out to be too slow in practice, follow-up work can add Yahoo-based prefiltering.
