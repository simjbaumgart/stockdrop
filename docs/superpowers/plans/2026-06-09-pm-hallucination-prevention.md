# PM Hallucination Prevention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Portfolio Manager from building theses on hallucinated numbers by injecting two pieces of ground truth into its prompt — a dividend block (with an "ex-date has passed" rule) and a "no canonical EPS → no specific figures" rule.

**Architecture:** Both fixes are prevention, not detection: they shape what enters the PM's context rather than auditing what comes out. Fix A is a pure prompt edit to the existing missing-earnings branch of `_create_fund_manager_prompt`. Fix B adds a new `DividendService.get_dividend_facts()` (yfinance), threads its output through `raw_data → MarketState.dividend_facts`, and renders a `DIVIDEND_FACTS` block in the same PM prompt. We deliberately do NOT build the free-text claim validator — that is a follow-up only if hallucinations persist after these land.

**Tech Stack:** Python 3.9, yfinance, pytest. No new dependencies.

---

## Background: the two errors this kills

1. **BAP dividend-capture hallucination** — the PM built a buy thesis around a June 12 dividend payout whose ex-date (May 18) had already passed. A buyer "today" was never entitled to it.
2. **BAP EPS hallucination** — Finnhub had no earnings data (common for ADRs), so the prompt said "facts unavailable" and the PM grabbed a PEN-denominated `$25.90` EPS from a news article as if it were USD ground truth.

A third type (KB's correctly-converted "revenue crossed $1B") is exactly why we are NOT building a naive numeric validator — it would false-positive on legitimate currency conversions.

## File Structure

- **Create:** `app/services/dividend_service.py` — `DividendService.get_dividend_facts(symbol)` returns a canonical dividend dict from yfinance, or `None`. Module-level singleton `dividend_service`, mirroring `finnhub_service`.
- **Create:** `tests/test_dividend_service.py` — unit tests for `get_dividend_facts` (mock `yf.Ticker`).
- **Modify:** `app/models/market_state.py` — add `dividend_facts: Optional[dict] = None`.
- **Modify:** `app/services/stock_service.py:1640-1684` — fetch dividend facts next to earnings facts; add `"dividend_facts"` to `raw_data`.
- **Modify:** `app/services/research_service.py:327-334` — extract `dividend_facts` into `MarketState`.
- **Modify:** `app/services/research_service.py:1514-1533` — Fix B (EPS else-branch rule) + Fix A (dividend block render).
- **Modify:** `tests/test_pm_prompt_earnings_block.py` — update the two missing-earnings assertions for the new rule text.
- **Create:** `tests/test_pm_prompt_dividend_block.py` — tests for the dividend block (past ex-date, future ex-date, absent).

**Build order:** Fix B first (pure prompt edit, no plumbing). Then Fix A (service → plumbing → prompt). Each task is independently testable and committed.

---

## Task 1: Fix B — "no canonical EPS → no specific figures" rule

When `earnings_facts` is missing/None, the prompt currently says "facts unavailable", which leaves the PM free to cite any EPS it finds in news text. Replace the bland fallback with an explicit prohibition on citing exact financial figures.

**Files:**
- Modify: `app/services/research_service.py:1514-1515`
- Modify: `tests/test_pm_prompt_earnings_block.py:60-69`

- [ ] **Step 1: Update the two existing tests to assert the new rule (make them fail first)**

In `tests/test_pm_prompt_earnings_block.py`, replace the two missing-earnings test functions (lines 60-69) with:

```python
def test_prompt_with_no_earnings_facts_forbids_specific_figures():
    state = _make_state(None)
    out = _build_prompt(state)
    assert "no canonical earnings data" in out
    assert "MUST NOT cite any specific" in out
    assert "QUALITATIVELY" in out


def test_prompt_with_missing_reported_eps_forbids_specific_figures():
    state = _make_state({"reported_eps": None})
    out = _build_prompt(state)
    assert "MUST NOT cite any specific" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_pm_prompt_earnings_block.py -v`
Expected: the two renamed tests FAIL with `AssertionError` (the new strings are not yet in the prompt). The other three tests still PASS.

- [ ] **Step 3: Rewrite the missing-earnings branch in the prompt builder**

In `app/services/research_service.py`, replace the `else` branch at lines 1514-1515:

```python
        else:
            earnings_block = "\nEARNINGS_FACTS: (no recent reported quarter available — drop is not earnings-driven, or facts unavailable)"
```

with:

```python
        else:
            earnings_block = (
                "\nEARNINGS_FACTS: (no canonical earnings data available from Finnhub — "
                "common for ADRs and foreign listings).\n"
                "RULE: Because no verified EPS figure exists, you MUST NOT cite any specific "
                "EPS number, revenue figure, or surprise percentage in your reasoning or "
                "key_factors. News articles may report figures in a foreign currency (e.g. PEN, "
                "KRW) or quote stale/unverified consensus numbers. You may describe earnings only "
                "QUALITATIVELY (e.g. 'beat expectations per press reports', 'reported a revenue "
                "decline'). Do not invent or repeat exact financial figures."
            )
```

- [ ] **Step 4: Run the full earnings-block test file to verify all pass**

Run: `pytest tests/test_pm_prompt_earnings_block.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/research_service.py tests/test_pm_prompt_earnings_block.py
git commit -m "feat(pm): forbid specific EPS figures when no canonical earnings exist"
```

---

## Task 2: Fix A.1 — DividendService.get_dividend_facts

A new service that fetches the canonical ex-dividend date, pay date, and per-share amount from yfinance, returning a structured dict or `None`. Mirrors `finnhub_service.get_earnings_facts`'s contract (None on any failure; ISO date strings for clean rendering and JSON-safety).

**Files:**
- Create: `app/services/dividend_service.py`
- Test: `tests/test_dividend_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dividend_service.py`:

```python
"""Unit tests for DividendService.get_dividend_facts (yfinance mocked)."""
import datetime
from unittest.mock import MagicMock, patch

from app.services.dividend_service import DividendService


def _ticker_with(calendar, info):
    t = MagicMock()
    t.calendar = calendar
    t.info = info
    return t


def test_returns_iso_dates_and_amount():
    cal = {
        "Ex-Dividend Date": datetime.date(2026, 5, 18),
        "Dividend Date": datetime.date(2026, 6, 12),
    }
    info = {"lastDividendValue": 1.23}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, info)):
        out = DividendService().get_dividend_facts("BAP")
    assert out["ex_dividend_date"] == "2026-05-18"
    assert out["pay_date"] == "2026-06-12"
    assert out["amount"] == 1.23
    assert out["source"] == "yfinance"
    assert "fetched_at" in out


def test_returns_none_when_no_ex_dividend_date():
    cal = {"Ex-Dividend Date": None, "Dividend Date": datetime.date(2026, 6, 12)}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, {})):
        assert DividendService().get_dividend_facts("XYZ") is None


def test_amount_none_when_info_missing():
    cal = {"Ex-Dividend Date": datetime.date(2026, 5, 18), "Dividend Date": None}
    with patch("app.services.dividend_service.yf.Ticker", return_value=_ticker_with(cal, {})):
        out = DividendService().get_dividend_facts("XYZ")
    assert out["ex_dividend_date"] == "2026-05-18"
    assert out["pay_date"] is None
    assert out["amount"] is None


def test_returns_none_on_exception():
    with patch("app.services.dividend_service.yf.Ticker", side_effect=RuntimeError("boom")):
        assert DividendService().get_dividend_facts("XYZ") is None


def test_returns_none_when_calendar_not_a_dict():
    # Older yfinance versions returned a DataFrame; treat anything non-dict as no data.
    t = MagicMock()
    t.calendar = ["not", "a", "dict"]
    with patch("app.services.dividend_service.yf.Ticker", return_value=t):
        assert DividendService().get_dividend_facts("XYZ") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_dividend_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.dividend_service'`.

- [ ] **Step 3: Implement the service**

Create `app/services/dividend_service.py`:

```python
"""Fetch canonical dividend facts (ex-date, pay date, amount) from yfinance.

The PM uses these as ground truth so it cannot build a dividend-capture thesis
around a payout whose ex-date has already passed. Returns None on any failure,
mirroring finnhub_service.get_earnings_facts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import yfinance as yf


def _to_iso(val) -> Optional[str]:
    """Normalize a date-like value to an ISO 'YYYY-MM-DD' string, or None."""
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        try:
            return val.isoformat()[:10]
        except Exception:
            return None
    return str(val)[:10] or None


class DividendService:
    def get_dividend_facts(self, symbol: str) -> Optional[dict]:
        """Return the upcoming/most-recent dividend facts as a structured dict,
        or None if no ex-dividend date is available.

        Returned shape:
            {
                "ex_dividend_date": "YYYY-MM-DD",
                "pay_date": "YYYY-MM-DD" | None,
                "amount": float | None,        # per-share, last known
                "source": "yfinance",
                "fetched_at": "<ISO 8601 UTC>",
            }
        """
        try:
            ticker = yf.Ticker(symbol)
            calendar = getattr(ticker, "calendar", None)
        except Exception as e:
            print(f"[DividendService] yf.Ticker failed for {symbol}: {e}")
            return None

        if not isinstance(calendar, dict):
            return None

        ex_iso = _to_iso(calendar.get("Ex-Dividend Date"))
        if not ex_iso:
            return None
        pay_iso = _to_iso(calendar.get("Dividend Date"))

        amount = None
        try:
            info = getattr(ticker, "info", None) or {}
            raw_amount = info.get("lastDividendValue")
            if raw_amount is not None:
                amount = float(raw_amount)
        except Exception:
            amount = None

        return {
            "ex_dividend_date": ex_iso,
            "pay_date": pay_iso,
            "amount": amount,
            "source": "yfinance",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


dividend_service = DividendService()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_dividend_service.py -v`
Expected: all five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/dividend_service.py tests/test_dividend_service.py
git commit -m "feat(dividend): add DividendService.get_dividend_facts via yfinance"
```

---

## Task 3: Fix A.2 — thread dividend_facts through to MarketState

Carry the dividend dict from the fetch site into the PM's state, exactly as `earnings_facts` is carried today.

**Files:**
- Modify: `app/models/market_state.py:15`
- Modify: `app/services/stock_service.py:1640-1684`
- Modify: `app/services/research_service.py:327-334`

- [ ] **Step 1: Add the field to MarketState**

In `app/models/market_state.py`, after the `earnings_facts` line (line 15):

```python
    earnings_facts: Optional[dict] = None
```

add:

```python
    dividend_facts: Optional[dict] = None
```

- [ ] **Step 2: Fetch dividend facts in stock_service next to earnings facts**

In `app/services/stock_service.py`, immediately after the earnings-facts try/except block (ends line 1645), add:

```python
        # Pre-fetch dividend facts so the PM has the ex-dividend date as ground
        # truth and cannot build a "buy to capture the dividend" thesis around a
        # payout whose ex-date has already passed.
        try:
            from app.services.dividend_service import dividend_service
            dividend_facts = dividend_service.get_dividend_facts(symbol)
        except Exception as e:
            print(f"[Dividend Facts] Failed to fetch for {symbol}: {e}")
            dividend_facts = None
```

- [ ] **Step 3: Add dividend_facts to the raw_data dict**

In `app/services/stock_service.py`, in the `raw_data` dict (after the `"earnings_facts": earnings_facts,` line, currently line 1681):

```python
            "earnings_facts": earnings_facts,
            "dividend_facts": dividend_facts,
```

- [ ] **Step 4: Extract dividend_facts into MarketState**

In `app/services/research_service.py`, in the `MarketState(...)` constructor (lines 327-334), after the `earnings_facts=raw_data.get("earnings_facts"),` line (line 331):

```python
            earnings_facts=raw_data.get("earnings_facts"),
            dividend_facts=raw_data.get("dividend_facts"),
```

- [ ] **Step 5: Verify nothing is broken (no behavior change yet)**

Run: `pytest tests/test_pm_prompt_earnings_block.py tests/test_dividend_service.py -v`
Expected: all PASS. (`_make_state` does not set `dividend_facts`, so it defaults to `None` — the dataclass field default makes this safe.)

- [ ] **Step 6: Commit**

```bash
git add app/models/market_state.py app/services/stock_service.py app/services/research_service.py
git commit -m "feat(pm): thread dividend_facts through raw_data into MarketState"
```

---

## Task 4: Fix A.3 — render the DIVIDEND_FACTS block in the PM prompt

Build a `DIVIDEND_FACTS` block and inject it after `EARNINGS_FACTS`. The block states the ex-date, pay date, and amount, and — critically — declares any dividend-capture argument INVALID when today is on or after the ex-date. ISO date strings compare lexically in chronological order, so the past-ex check is a plain string comparison.

**Files:**
- Modify: `app/services/research_service.py:1495-1533`
- Test: `tests/test_pm_prompt_dividend_block.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pm_prompt_dividend_block.py`:

```python
"""The PM prompt's DIVIDEND_FACTS block: ground truth + ex-date capture rule."""
from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _make_state(dividend_facts, date="2026-06-09"):
    state = MarketState(ticker="BAP", date=date)
    state.reports = {
        "technical": "stub", "news": "stub", "market_sentiment": "stub",
        "competitive": "stub", "seeking_alpha": "stub",
        "bull": "stub", "bear": "stub", "risk": "stub",
    }
    state.earnings_facts = None
    state.dividend_facts = dividend_facts
    state.gatekeeper_tier = None
    return state


def _build_prompt(state):
    rs = ResearchService.__new__(ResearchService)
    return rs._create_fund_manager_prompt(state, [], [], "-7%")


def test_past_ex_date_marks_capture_invalid():
    # Today 2026-06-09 is AFTER the ex-date 2026-05-18 (the BAP case).
    state = _make_state({
        "ex_dividend_date": "2026-05-18", "pay_date": "2026-06-12",
        "amount": 1.23, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "2026-05-18" in out
    assert "INVALID" in out
    assert "PAST THE EX-DIVIDEND DATE" in out


def test_future_ex_date_marks_capture_valid():
    state = _make_state({
        "ex_dividend_date": "2026-06-20", "pay_date": "2026-07-01",
        "amount": 1.23, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "2026-06-20" in out
    assert "INVALID" not in out
    assert "would be entitled" in out


def test_no_dividend_facts_omits_block():
    state = _make_state(None)
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" not in out


def test_amount_unknown_renders_gracefully():
    state = _make_state({
        "ex_dividend_date": "2026-05-18", "pay_date": None,
        "amount": None, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "unknown" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_pm_prompt_dividend_block.py -v`
Expected: the four tests FAIL — `test_no_dividend_facts_omits_block` passes incidentally (the string isn't present yet), the other three FAIL on missing strings. (If all four "pass" because the assertions for absence pass trivially, re-check: the three positive tests must fail.)

- [ ] **Step 3: Build the dividend_block before the return statement**

In `app/services/research_service.py`, immediately after the earnings-block `if/else` (after line 1515, before the `return f"""` on line 1517), insert:

```python
        df = getattr(state, "dividend_facts", None) or {}
        if df and df.get("ex_dividend_date"):
            ex_date = df["ex_dividend_date"]
            today = state.date
            pay_str = df.get("pay_date") or "unknown"
            amount_str = (
                f"${df['amount']:.2f}" if df.get("amount") is not None else "unknown"
            )
            # ISO 'YYYY-MM-DD' strings compare lexically in chronological order.
            # A buyer is only entitled to the dividend if they buy BEFORE the
            # ex-date, so today >= ex_date means any capture thesis is invalid.
            if today >= ex_date:
                capture_rule = (
                    f"TODAY ({today}) IS PAST THE EX-DIVIDEND DATE ({ex_date}). "
                    "Any 'buy now to capture the dividend' argument is INVALID — a "
                    "buyer today is NOT entitled to this dividend. Reject any "
                    "dividend-capture thesis in the bull case."
                )
            else:
                capture_rule = (
                    f"Today ({today}) is before the ex-dividend date ({ex_date}); a "
                    "buyer before the ex-date would be entitled to this dividend."
                )
            dividend_block = (
                "\nDIVIDEND_FACTS (canonical, from Yahoo Finance — ground truth for "
                "any dividend reasoning):\n"
                f"- Ex-dividend date: {ex_date}\n"
                f"- Pay date: {pay_str}\n"
                f"- Amount per share: {amount_str}\n"
                f"- {capture_rule}\n"
            )
        else:
            dividend_block = ""
```

- [ ] **Step 4: Inject the block into the prompt f-string**

In the same function, in the f-string template, find the line (currently line 1533):

```python
{earnings_block}
```

and change it to:

```python
{earnings_block}
{dividend_block}
```

- [ ] **Step 5: Run the dividend-block tests to verify they pass**

Run: `pytest tests/test_pm_prompt_dividend_block.py -v`
Expected: all four tests PASS.

- [ ] **Step 6: Run the full PM-prompt test surface for regressions**

Run: `pytest tests/test_pm_prompt_earnings_block.py tests/test_pm_prompt_dividend_block.py tests/test_fund_manager_prompt.py tests/test_dividend_service.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/research_service.py tests/test_pm_prompt_dividend_block.py
git commit -m "feat(pm): inject DIVIDEND_FACTS block with ex-date capture rule"
```

---

## Self-Review notes

- **Spec coverage:** Fix B = Task 1. Fix A = Tasks 2 (service) + 3 (plumbing) + 4 (prompt render). The deliberately-excluded claim validator is documented as a non-goal in the Architecture section.
- **Type consistency:** `get_dividend_facts` returns keys `ex_dividend_date`/`pay_date`/`amount`/`source`/`fetched_at`; Task 4's renderer reads exactly those keys; Task 4's tests construct exactly those keys. `MarketState.dividend_facts` (Task 3) is the attribute Task 4 reads via `getattr(state, "dividend_facts", None)`.
- **Existing-test impact:** Task 1 intentionally rewrites two assertions in `test_pm_prompt_earnings_block.py` because the fallback text changes; Step 2 of Task 1 confirms they fail before the prompt edit (true TDD).
- **Date-comparison safety:** both `state.date` (`datetime.now().strftime("%Y-%m-%d")`) and `ex_dividend_date` (`_to_iso(...)[:10]`) are zero-padded ISO strings, so `today >= ex_date` is a correct chronological comparison without parsing.
```