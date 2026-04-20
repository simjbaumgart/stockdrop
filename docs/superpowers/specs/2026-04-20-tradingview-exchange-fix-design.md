# TradingView `get_technical_analysis` Exchange Fix — Design

**Date:** 2026-04-20
**Status:** Approved, ready for implementation plan

## Problem

`TradingViewService.get_technical_analysis(symbol, region="US")` (in `app/services/tradingview_service.py:344-377`) hardcodes `exchange="NASDAQ"` and `screener="america"` inside the method body. Any symbol that does not trade on NASDAQ — OTC ADRs like MBGYY (Mercedes-Benz), non-US tickers, AMEX-listed ETFs — causes `TA_Handler` to raise, the `except` returns `{}`, and the downstream analysis runs with zero technical data.

Evidence from session v0.8.2-34: MBGYY and PAYP both had TA failures from this exact cause. Every non-NASDAQ stock that makes it past the gatekeeper is degraded.

The sibling method on the same class, `get_technical_indicators` (line 379), already handles this correctly — it accepts `exchange` and `screener` parameters, defaults them when absent, and overrides known AMEX-listed ETFs (SPY, XLK, XLF, …). The gatekeeper has been using `get_technical_indicators` all along, which is why the report observed "the gatekeeper's Bollinger Band check worked fine."

The sole caller of `get_technical_analysis` — `stock_service.py:522` — already has `exchange`, `region`, and `screener` available in local scope (pulled from the `stock` dict earlier in the same loop). It just doesn't pass them.

## Goal

Bring `get_technical_analysis` to parity with `get_technical_indicators`:
- Accept `exchange` and `screener` params.
- Apply the same defaults and ETF overrides.
- Update the caller to pass through the values it already has.

Non-goals (explicit YAGNI):
- Automatic retry across multiple exchanges on failure.
- Region-to-screener auto-mapping (`"JP"` → `"japan"`, etc.) — no current caller needs it.
- Changing the error-handling contract (still returns `{}` on failure).

## Architecture

### `app/services/tradingview_service.py`

1. **Extract a shared helper** `_resolve_exchange_and_screener(symbol, exchange, screener) -> Tuple[str, str]` on `TradingViewService`. Encapsulates:
   - Default `screener` to `"america"` when `None` or empty.
   - Default `exchange` to `"NASDAQ"` when `None` or empty.
   - Override for known AMEX-listed ETFs: `SPY, XLK, XLF, XLV, XLY, XLP, XLE, XLI, XLC, XLU, XLB, XLRE` → `"AMEX"`.
   - Returns `(exchange, screener)`.

2. **Refactor `get_technical_indicators`** (line 379) to use the helper. Today it has the override logic inline at lines 384-394; move it into `_resolve_exchange_and_screener` and call it from the top of the try block.

3. **Change `get_technical_analysis` signature and body:**
   - From: `def get_technical_analysis(self, symbol: str, region: str = "US") -> Dict:`
   - To: `def get_technical_analysis(self, symbol: str, region: str = "US", exchange: Optional[str] = None, screener: Optional[str] = None) -> Dict:`
   - Inside the method: call `_resolve_exchange_and_screener` to fill in defaults and apply overrides. Use the resulting values when constructing `TA_Handler`.
   - Delete the three-line dead comment at lines 373-375 (`# Fallback: Try without specific exchange if possible or different exchange?`) — we are explicitly not implementing that, and the comment is now misleading.

### `app/services/stock_service.py`

At line 522, change the single call:

```python
technical_analysis = tradingview_service.get_technical_analysis(
    symbol,
    region=stock.get("region", "US"),
    exchange=exchange,
    screener=stock.get("screener"),
)
```

`exchange` is already in local scope from line 452 (`exchange = stock.get("exchange")`). `stock.get("screener")` mirrors the value the gatekeeper call on lines 475-481 already uses.

## Data flow

1. Screener produces `stock` dict containing `region`, `exchange`, `screener`.
2. `stock_service.py` loop extracts those and passes them to both the gatekeeper and (now) `get_technical_analysis`.
3. `get_technical_analysis` resolves `None` values via the shared helper.
4. `TA_Handler` receives the correct `exchange` and `screener` for the symbol's actual listing venue.
5. Non-NASDAQ stocks (MBGYY on OTC, PAYP on its correct venue, BEIGF/UMGNF on OTC) now return populated technical analysis dicts instead of `{}`.

## Error handling

Unchanged. The existing `try/except` around `handler.get_analysis()` stays. On any exception, the method still prints an error and returns `{}`. Downstream code at `stock_service.py` already tolerates an empty dict (that was the degraded path we're fixing, and it remains available as a fallback when the API genuinely fails).

## Testing

New file: `tests/test_tradingview_exchange.py`. Primarily integration tests — per CLAUDE.md ("Integration tests should hit real APIs where feasible"), and because the core bug is about what parameters reach the external API; mocks can't catch that class of bug. A single pure-unit test covers the new helper's deterministic logic (defaults + overrides) without needing the network.

Tests use the `integration` pytest mark so a developer can skip them with `pytest -m "not integration"` in tight inner loops. We will register the mark in the appropriate project config (`pytest.ini`, `pyproject.toml`, or `conftest.py`) — the plan will check which exists and pick the right one, creating `pytest.ini` if none.

1. **`test_get_technical_analysis_nasdaq_smoke`** — `get_technical_analysis("AAPL", region="US", exchange="NASDAQ", screener="america")`. Asserts the returned dict has keys `"summary"`, `"oscillators"`, `"moving_averages"`, `"indicators"` and each is non-empty (truthy).

2. **`test_get_technical_analysis_otc_mbgyy`** — calls `get_technical_analysis("MBGYY", region="US", exchange="OTC", screener="america")`. Asserts non-empty dict. This is the regression test for the actual reported bug — it fails on pre-fix code. `MBGYY` is chosen because it is the exact ticker the production session report flagged. If MBGYY is delisted or renamed at implementation time, the plan will substitute another well-known OTC ADR (e.g. `DELHY`).

3. **`test_get_technical_analysis_etf_override`** — calls `get_technical_analysis("SPY", region="US", exchange="NASDAQ", screener="america")` with the wrong exchange on purpose. Asserts non-empty dict, because `_resolve_exchange_and_screener` should coerce `SPY` to `AMEX`.

4. **`test_resolve_exchange_and_screener_defaults`** — pure unit test for the helper: `None, None` → `("NASDAQ", "america")`; `"OTC", None` → `("OTC", "america")`; `"SPY", "NASDAQ"` → `("AMEX", "america")`. Covers the override and default-fill logic deterministically without an API call.

## Acceptance criteria

- Running the live pipeline against a fresh screener result that includes a non-NASDAQ candidate produces a populated `technical_analysis` dict in that stock's analysis (empirically verified by reading the decision_points DB row or log output).
- All four tests pass (three live integration, one pure unit).
- `pytest -m "not integration"` runs only the unit test and completes without errors.
- No change to `_format_citations`, `gatekeeper_service`, or any other module outside the two files listed.
