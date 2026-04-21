# Price/Volume Liquidity Pre-Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a price and volume liquidity pre-filter to the Gatekeeper so sub-$5 OTC penny stocks (PBMRF, PPERF, BKRKF, PINXF, CEBCF) are rejected before consuming pipeline resources.

**Architecture:** Introduce a new `check_liquidity_filter(price)` method on `GatekeeperService` that enforces a $5 minimum price. Call it inside `check_technical_filters` as the very first gate, before the Bollinger %B check. Separately, raise the average-volume threshold in `stock_service._is_actively_traded` from 50,000 to 100,000 shares to catch the "priced above $5 but illiquid" case. Thresholds live as module-level constants so they can be tuned without code archaeology.

**Tech Stack:** Python 3.9, pytest, pytest-asyncio, existing `GatekeeperService` in `app/services/gatekeeper_service.py`, existing `StockService._is_actively_traded` in `app/services/stock_service.py`.

---

## Background context for the engineer

The pipeline today looks like this (`app/services/stock_service.py` ~line 454 onward):

```
screener hit (>5% drop)
  -> _is_actively_traded (volume >= 50k)
    -> gatekeeper_service.check_technical_filters (Bollinger %B < 0.50)
      -> Phase 1 sensor council, Phase 2 debate, PM, Deep Research
```

Recent runs show sub-$1 OTC stocks (PBMRF at $0.003, BKRKF at $0.17, PPERF at $0.26, CEBCF at $0.38, PINXF at $0.75) passing the gatekeeper because a penny-stock's Bollinger Band math still says "dipped," and the 50k volume floor is too low for US OTC tickers. Each one burns ~3-5 minutes and multiple Gemini calls per run, then gets rejected at the PM stage anyway.

Fix: block them at the earliest possible point.

## File Structure

- **Modify:** `app/services/gatekeeper_service.py` — add `MIN_PRICE_USD` constant and `check_liquidity_filter` method; call it first inside `check_technical_filters`.
- **Modify:** `app/services/stock_service.py` — raise the volume threshold constant in `_is_actively_traded` from 50_000 to 100_000 (both the fast-path and the yfinance fallback), and extract to a module-level constant.
- **Create:** `tests/test_gatekeeper_liquidity.py` — new test file covering the liquidity filter in isolation and integrated with `check_technical_filters`.

No files are being split; both are small and focused. Constants are added inline at the top of each service file to match existing project style (the codebase has no central `constants.py`).

---

## Task 1: Liquidity filter constants + new method in GatekeeperService

**Files:**
- Modify: `app/services/gatekeeper_service.py:1-12` (add constants near imports)
- Modify: `app/services/gatekeeper_service.py` (add `check_liquidity_filter` method on the class)
- Test: `tests/test_gatekeeper_liquidity.py`

- [ ] **Step 1: Create the failing test file**

Create `tests/test_gatekeeper_liquidity.py` with these tests for the standalone filter:

```python
import pytest
from app.services.gatekeeper_service import GatekeeperService, MIN_PRICE_USD


@pytest.fixture
def gatekeeper():
    return GatekeeperService()


def test_liquidity_filter_rejects_penny_stock(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=0.003)
    assert is_ok is False
    assert "price" in reason.lower()
    assert "0.00" in reason or "0.003" in reason


def test_liquidity_filter_rejects_just_under_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=4.99)
    assert is_ok is False
    assert "4.99" in reason


def test_liquidity_filter_accepts_at_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=MIN_PRICE_USD)
    assert is_ok is True


def test_liquidity_filter_accepts_above_threshold(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=42.0)
    assert is_ok is True


def test_liquidity_filter_rejects_zero_or_missing_price(gatekeeper):
    is_ok, reason = gatekeeper.check_liquidity_filter(price=0.0)
    assert is_ok is False


def test_min_price_constant_is_five_dollars():
    assert MIN_PRICE_USD == 5.0
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: ImportError / AttributeError because `MIN_PRICE_USD` and `check_liquidity_filter` don't exist yet.

- [ ] **Step 3: Add the constant and method to `GatekeeperService`**

Edit `app/services/gatekeeper_service.py`. Add the constant at the top of the module, right after the imports block (below line 5, before `class GatekeeperService`):

```python
# Minimum share price (USD) for a ticker to be considered tradeable.
# Filters out OTC penny stocks (e.g. PBMRF $0.003, BKRKF $0.17, PPERF $0.26,
# CEBCF $0.38, PINXF $0.75) whose Bollinger math still flags them as "dipped"
# but which have no realistic liquidity, wide spreads, and poor LLM coverage.
MIN_PRICE_USD = 5.0
```

Then add this method to the `GatekeeperService` class (insert it right before `check_market_regime`, so the pre-filters group together):

```python
    def check_liquidity_filter(self, price: float) -> Tuple[bool, str]:
        """
        Pre-filter: reject sub-$5 tickers before any expensive analysis.
        Returns (is_valid, reason_string).
        """
        if price is None or price <= 0:
            return False, f"Price missing or non-positive ({price})"
        if price < MIN_PRICE_USD:
            return False, f"Price ${price:.2f} < ${MIN_PRICE_USD:.2f} minimum (penny-stock filter)"
        return True, f"Price ${price:.2f} >= ${MIN_PRICE_USD:.2f} minimum"
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_liquidity.py
git commit -m "feat(gatekeeper): add \$5 minimum price liquidity pre-filter"
```

---

## Task 2: Wire the liquidity filter into `check_technical_filters`

**Files:**
- Modify: `app/services/gatekeeper_service.py:57-109` (inside `check_technical_filters`, before the Bollinger block)
- Test: `tests/test_gatekeeper_liquidity.py` (add integration tests)

- [ ] **Step 1: Add failing integration tests**

Append to `tests/test_gatekeeper_liquidity.py`:

```python
def test_check_technical_filters_rejects_sub_5_penny_stock_via_cached(gatekeeper):
    # PBMRF-like: price way under $5, but Bollinger math would otherwise pass
    cached = {
        "close": 0.003,
        "bb_lower": 0.001,
        "bb_upper": 0.010,
        "volume": 1_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="PBMRF", cached_indicators=cached
    )
    assert is_valid is False
    assert "liquidity_status" in reasons
    assert "Price" in reasons["liquidity_status"]
    # Ensure we short-circuited: no Bollinger verdict should be produced
    assert "bb_status" not in reasons


def test_check_technical_filters_accepts_above_5_with_dip(gatekeeper):
    cached = {
        "close": 42.00,
        "bb_lower": 40.00,
        "bb_upper": 60.00,
        "volume": 2_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="AAPL", cached_indicators=cached
    )
    # %B = (42-40)/(60-40) = 0.10 -> qualifies
    assert is_valid is True
    assert "liquidity_status" in reasons
    assert "bb_status" in reasons


def test_check_technical_filters_rejects_above_5_when_not_dipped(gatekeeper):
    cached = {
        "close": 50.00,
        "bb_lower": 40.00,
        "bb_upper": 60.00,
        "volume": 2_000_000,
    }
    is_valid, reasons = gatekeeper.check_technical_filters(
        symbol="MSFT", cached_indicators=cached
    )
    # %B = 0.5, which is NOT < 0.50 -> reject
    assert is_valid is False
    assert "liquidity_status" in reasons  # liquidity passed
    assert "bb_status" in reasons  # but BB rejected
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `pytest tests/test_gatekeeper_liquidity.py -v -k check_technical_filters`
Expected: FAIL — the penny-stock test will currently pass Bollinger math and return `is_valid=True`, and `liquidity_status` will be missing from `reasons`.

- [ ] **Step 3: Modify `check_technical_filters` to run the liquidity gate first**

Edit `app/services/gatekeeper_service.py`. Locate the block starting at line 71 (`reasons = {}`) inside `check_technical_filters`. Replace the existing body from `reasons = {}` through the end of the `%B` filter with the version below so the liquidity gate runs before the Bollinger check and short-circuits on failure:

```python
            reasons = {}

            # Extract
            price = indicators.get('close', 0.0)
            bb_lower = indicators.get('bb_lower', 0.0)
            bb_upper = indicators.get('bb_upper', 0.0)
            volume = indicators.get('volume', 0)

            # --- Pre-filter: Liquidity (minimum share price) ---
            liquidity_ok, liquidity_reason = self.check_liquidity_filter(price)
            reasons['liquidity_status'] = liquidity_reason
            reasons['price'] = price
            if not liquidity_ok:
                # Short-circuit before Bollinger; save the downstream pipeline cost.
                reasons['lower_bb'] = bb_lower
                return False, reasons

            # --- Filter: Bollinger Band %B (Dip) ---
            # %B = (Price - Lower) / (Upper - Lower)
            if bb_upper != bb_lower:
                curr_pct_b = (price - bb_lower) / (bb_upper - bb_lower)
            else:
                curr_pct_b = 0.5  # Default if squeezed/error

            is_valid = False

            # Logic: IF %B < 0.50: VALID
            if curr_pct_b < 0.50:
                is_valid = True
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) < 0.50 (Dip)"
            else:
                reasons['bb_status'] = f"%B ({curr_pct_b:.2f}) >= 0.50 (Not Dip Enough)"

            # --- Filter: Volume Anomaly (Optional) ---
            # We don't have Avg Volume easily from TA.
            # reasons['volume'] = volume

            # Add raw values for debugging/logging
            reasons['lower_bb'] = bb_lower
            reasons['bb_pct_b'] = curr_pct_b

            return is_valid, reasons
```

- [ ] **Step 4: Run the full gatekeeper test file to confirm it passes**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Run neighbouring test file that touches the gatekeeper to guard for regressions**

Run: `pytest tests/test_deduplication_logic.py -v`
Expected: PASS (or the same pass/fail state it had before this change — if a test was failing before, it should still be failing for the same reason).

- [ ] **Step 6: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_liquidity.py
git commit -m "feat(gatekeeper): short-circuit on liquidity filter before Bollinger"
```

---

## Task 3: Surface the rejection clearly in stock_service logs

**Files:**
- Modify: `app/services/stock_service.py:483-501` (the gatekeeper-rejected branch that prints reasons)

The current rejected-print logic expects `bb_status` as the primary reason. After Task 2 a liquidity rejection will have `liquidity_status` as the primary reason instead, but no `bb_status`. Make the log line prefer liquidity when that's what tripped.

- [ ] **Step 1: Read the current rejection-logging block**

Open `app/services/stock_service.py` and locate the block at lines 483-501 (the `if not is_valid:` branch printing "GATEKEEPER: {symbol} REJECTED.").

- [ ] **Step 2: Update the logging to prefer liquidity_status**

Replace the inner body of the `if not is_valid:` branch (lines ~484-501) with this version. The change: print `liquidity_status` first when present, then fall back to `bb_status`.

```python
                    if not is_valid:
                        print(f"GATEKEEPER: {symbol} REJECTED.")

                        # Primary Reason: liquidity takes priority over BB
                        if 'liquidity_status' in reasons and 'Price' in str(reasons.get('liquidity_status', '')) and '<' in str(reasons.get('liquidity_status', '')):
                            print(f"  [PRIMARY REASON] {reasons['liquidity_status']}")
                        elif 'bb_status' in reasons:
                            print(f"  [PRIMARY REASON] {reasons['bb_status']}")
                        elif 'liquidity_status' in reasons:
                            print(f"  [PRIMARY REASON] {reasons['liquidity_status']}")

                        # Context Data
                        print("  [CONTEXT]")
                        for key, value in reasons.items():
                            if key in ('bb_status', 'liquidity_status'):
                                continue
                            try:
                                val_to_print = f"{float(value):.2f}"
                            except (ValueError, TypeError):
                                val_to_print = value
                            print(f"    {key}: {val_to_print}")

                        # Optionally log this rejection to DB or file?
                        continue
```

- [ ] **Step 3: Sanity check — run the gatekeeper tests again**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all 9 tests still PASS (this task only touched stock_service logging, no gatekeeper logic changed).

- [ ] **Step 4: Commit**

```bash
git add app/services/stock_service.py
git commit -m "feat(stock_service): log liquidity rejection as primary gatekeeper reason"
```

---

## Task 4: Raise average-volume threshold from 50k to 100k

**Files:**
- Modify: `app/services/stock_service.py` (add `MIN_AVG_VOLUME` constant near top of file)
- Modify: `app/services/stock_service.py:765-802` (`_is_actively_traded` method — both the fast-path and yfinance fallback)
- Test: `tests/test_gatekeeper_liquidity.py` (add one assertion on the constant so it can't silently regress)

The user explicitly suggested 100k as the floor. This catches the case where a stock is priced above $5 but still essentially untradeable (wide spreads, no institutional coverage).

- [ ] **Step 1: Find existing module-level constants / imports at the top of `stock_service.py`**

Read `app/services/stock_service.py:1-30` to see where other constants live. Plan to insert `MIN_AVG_VOLUME` after the imports block and before the first class definition.

- [ ] **Step 2: Add a failing test for the constant**

Append to `tests/test_gatekeeper_liquidity.py`:

```python
def test_min_avg_volume_constant_is_100k():
    from app.services.stock_service import MIN_AVG_VOLUME
    assert MIN_AVG_VOLUME == 100_000
```

- [ ] **Step 3: Run the test to confirm it fails**

Run: `pytest tests/test_gatekeeper_liquidity.py::test_min_avg_volume_constant_is_100k -v`
Expected: ImportError — `MIN_AVG_VOLUME` not defined.

- [ ] **Step 4: Add the constant and update `_is_actively_traded`**

In `app/services/stock_service.py`, add the constant after the imports block (before the first class definition):

```python
# Minimum average daily volume (shares) for a ticker to be considered tradeable.
# Paired with the gatekeeper's $5 price floor to catch above-$5 tickers that
# still have no realistic liquidity.
MIN_AVG_VOLUME = 100_000
```

Then edit the two hard-coded `50000` / `50k` references inside `_is_actively_traded` (around lines 771 and 795). The replacement body of the method should read:

```python
    def _is_actively_traded(self, symbol: str, region: str = "US", volume: float = 0, exchange: str = "", name: str = "") -> bool:
        """
        Checks if the stock is actively traded to avoid illiquid tickers.
        Criteria: Avg volume > MIN_AVG_VOLUME over last 5 days.
        """
        # 1. Faster Check: Use volume from Screener if available
        if volume > MIN_AVG_VOLUME:
            return True

        # 2. Fallback: Check yfinance (historical volume)
        try:
            # Suffix mapping for yfinance
            yf_symbol = self._resolve_yfinance_ticker(symbol, region, exchange, name)

            # Use yfinance for quick volume check with shared session
            ticker = yf.Ticker(yf_symbol)
            hist = ticker.history(period="5d")

            if hist.empty:
                print(f"  > [Active Check] No history found for {yf_symbol}. Assuming inactive (or suffix mismatch).")
                # If mapped symbol failed, maybe try original?
                if yf_symbol != symbol:
                    print(f"  > [Active Check] Retrying with original symbol {symbol}...")
                    hist = yf.Ticker(symbol).history(period="5d")
                    if hist.empty:
                        return False
                else:
                    return False

            avg_vol = hist['Volume'].mean()
            if avg_vol < MIN_AVG_VOLUME:
                print(f"  > [Active Check] {yf_symbol} Volume Low ({int(avg_vol)} < {MIN_AVG_VOLUME:,}). Skipping.")
                return False

            return True
        except Exception as e:
            print(f"  > [Active Check] Error checking {symbol}: {e}. Skipping to be safe.")
            return False
```

- [ ] **Step 5: Run the constant test and the gatekeeper suite to confirm they pass**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all 10 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/stock_service.py tests/test_gatekeeper_liquidity.py
git commit -m "feat(stock_service): raise min avg volume from 50k to 100k"
```

---

## Task 5: Manual smoke check against known offenders

**Files:** none modified in this task — verification only.

- [ ] **Step 1: Run an end-to-end import sanity check**

Run: `python -c "from app.services.gatekeeper_service import gatekeeper_service, MIN_PRICE_USD; from app.services.stock_service import MIN_AVG_VOLUME; print('price:', MIN_PRICE_USD, 'volume:', MIN_AVG_VOLUME)"`
Expected: `price: 5.0 volume: 100000`

- [ ] **Step 2: Directly exercise the gatekeeper on each known-offender price**

Run: `python -c "from app.services.gatekeeper_service import gatekeeper_service as g; [print(t, g.check_liquidity_filter(p)) for t, p in [('PBMRF', 0.003), ('BKRKF', 0.17), ('PPERF', 0.26), ('CEBCF', 0.38), ('PINXF', 0.75), ('HEALTHY', 42.0)]]"`

Expected: each of PBMRF, BKRKF, PPERF, CEBCF, PINXF returns `(False, "Price $... < $5.00 minimum (penny-stock filter)")`. HEALTHY at $42 returns `(True, ...)`.

- [ ] **Step 3: Run the whole new test file one more time**

Run: `pytest tests/test_gatekeeper_liquidity.py -v`
Expected: all 10 tests PASS, zero failures.

- [ ] **Step 4: Done — no commit needed (no file changes in this task)**

If any step failed, stop and debug. Do not proceed to a PR with a failing smoke check.

---

## Self-review notes

- **Spec coverage:** The user asked for (a) $5 minimum price — Tasks 1+2 (gatekeeper constant + short-circuit inside `check_technical_filters`); (b) 100k minimum average daily volume — Task 4 (raised threshold in `_is_actively_traded`); (c) code to live in the gatekeeper or `stock_service` right before the Bollinger check — Task 2 places it inside `check_technical_filters` before the Bollinger block; (d) cheap and simple — no new dependencies, thresholds are constants.
- **Ordering note:** `_is_actively_traded` is called *before* the gatekeeper in `stock_service.py`, so penny-stock tickers that fail the liquidity gate in the gatekeeper will still have already passed `_is_actively_traded`. That is fine — the point of Task 2 is to short-circuit before the expensive Bollinger + sensor council work, not before the cheap active-trading check. Saving the pipeline cost (Gemini calls) is what matters.
- **Not in scope:** Changing the gatekeeper to also short-circuit on volume (would require avg-volume data the gatekeeper doesn't currently fetch — current-day `volume` from TradingView TA isn't a reliable average). The `_is_actively_traded` bump to 100k handles that requirement at its existing call site.
