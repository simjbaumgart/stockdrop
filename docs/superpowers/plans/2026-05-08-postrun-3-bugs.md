# Pipeline Post-Run High-Impact Fixes Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five high-impact bugs surfaced in the latest pipeline run: (1) stale R/R after stop-guard widens, (2) `time.sleep(60)` blocking the screener worker on Alpha Vantage rate-limit, (3) batch-comparison "winner" naming candidates that deep research already overrode to AVOID, (4) PM trusting LLM-summarized EPS consensus from news articles (TOST $0.20 vs $0.27 mistake), (5) "Owned" status set by the council before deep research completes — UI and APP got marked Owned, then DR overrode them to AVOID with no state change.

**Architecture:**
1. Recompute `downside_risk_percent` and `risk_reward_ratio` in `research_service.py` immediately after `widen_stop_if_too_tight()` mutates the stop, in a small pure helper. Move the PM-decision log line so it prints the post-guard values. Adjust the PM prompt to require `stop = max(2*ATR_below_entry, technical_support)` so the guard fires less often.
2. Replace the 60-second blocking retry in `alpha_vantage_service.get_company_news` with a fail-fast: log the rate-limit, return `[]`, and let the next 20-minute scanner cycle retry. Optionally shorten the Seeking Alpha retry to 1 second (it's in a thread, but still adds latency).
3. Tighten the candidate-selection query in `database.get_unbatched_candidates_by_date` and `get_distinct_dates_with_unbatched_candidates` to exclude rows where deep research overrode to AVOID. Surface the deep-research review_verdict + action into the batch prompt as a hard constraint, and short-circuit the batch entirely when fewer than 2 valid candidates remain.
4. Pre-fetch structured earnings facts (reported EPS, consensus EPS, surprise %) from Finnhub's `/stock/earnings` endpoint *before* the PM runs, inject them as a labeled block in the PM prompt with explicit "use these numbers, not what news articles said" framing, and persist them to `decision_points`. Then add a deterministic post-PM consistency check that flags `EARNINGS_NARRATIVE_INCONSISTENT` when the PM's reasoning narrates "beat"/"miss" in a way that disagrees with `sign(surprise_pct)`, and downgrade the verdict by one tier.
5. Introduce an intermediate `Pending DR Review` status. When the council emits BUY or BUY_LIMIT with R/R > 1.0, set status to `Pending DR Review` instead of `Owned`. Lower the BUY_LIMIT deep-research trigger threshold from 1.25 → 1.0 so the gate cannot strand rows. When deep research completes, atomically transition the row to `Owned` (DR action ∈ {BUY, BUY_LIMIT}) or `Not Owned` (DR action ∈ {AVOID, WATCH, HOLD} or review_verdict = OVERRIDDEN). DR already overwrites entry/stop columns via `_apply_trading_level_overrides`, so downstream code reading entry zones automatically gets the post-DR values.

**Tech Stack:** Python 3.9, FastAPI, SQLite, pytest, requests.

---

## File Structure

**Modify:**
- `app/services/research_service.py` — recompute R/R after stop-guard; move log line; tweak PM prompt language; inject `EARNINGS_FACTS` block; call narrative-consistency check post-PM
- `app/utils/stop_loss_guard.py` — add `recompute_risk_metrics()` pure helper alongside the existing guard
- `app/services/alpha_vantage_service.py` — fail-fast on rate-limit (no `time.sleep(60)`)
- `app/services/seeking_alpha_service.py` — drop the retry sleep to 1s and document why (optional within Task 4)
- `app/database.py` — exclude DR-AVOID/OVERRIDDEN from batch candidate queries; add `earnings_*` columns; add `finalize_position_status_after_dr()` helper
- `app/services/deep_research_service.py` — short-circuit empty batches; pass DR verdict into the batch prompt; call `finalize_position_status_after_dr()` after `_apply_trading_level_overrides`; lower BUY_LIMIT trigger threshold to R/R > 1.0
- `app/services/finnhub_service.py` — add `get_earnings_facts(symbol)` returning the latest reported quarter's structured EPS facts
- `app/services/stock_service.py` — fetch `earnings_facts` before research runs; pass into `raw_data`; persist to DB; replace immediate `Owned`/`Not Owned` assignment with `Pending DR Review` for BUY/BUY_LIMIT with R/R > 1.0

**Create:**
- `tests/test_recompute_risk_metrics.py` — unit tests for the new helper
- `tests/test_alpha_vantage_rate_limit.py` — verify no `time.sleep` on 429
- `tests/test_batch_candidate_filter.py` — verify DR-overridden rows are excluded
- `tests/test_research_service_stop_guard_recompute.py` — integration-style test that the returned dict has post-guard R/R
- `app/utils/earnings_consistency.py` — pure helper that compares PM reasoning narrative to `sign(surprise_pct)` and returns a downgrade decision
- `tests/test_earnings_consistency.py` — unit tests for the consistency check + tier downgrade
- `tests/test_finnhub_earnings_facts.py` — unit tests for `get_earnings_facts`
- `tests/test_pending_dr_status.py` — unit tests for the `Pending DR Review` → `Owned`/`Not Owned` state machine

---

## Task 1: Add `recompute_risk_metrics` helper

**Why:** `research_service.py:421` logs and `research_service.py:508-509` returns `downside_risk_percent` / `risk_reward_ratio` from the PM's JSON. Then `app/utils/stop_loss_guard.widen_stop_if_too_tight` may widen the stop. Today the dict that goes to the DB still carries the PM's original (now-wrong) downside %. We need a pure helper to recompute both fields from a (entry_low, stop_loss, upside_percent) triple so it's testable in isolation.

**Files:**
- Modify: `app/utils/stop_loss_guard.py`
- Test: `tests/test_recompute_risk_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_recompute_risk_metrics.py
"""Recompute downside_risk_percent and risk_reward_ratio from
post-guard stop_loss values."""
import math
import pytest

from app.utils.stop_loss_guard import recompute_risk_metrics


def test_recompute_basic():
    # entry 100, stop 90 -> downside 10%, upside 20% -> R/R 2.0
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=90.0, upside_percent=20.0)
    assert out["downside_risk_percent"] == 10.0
    assert out["risk_reward_ratio"] == 2.0


def test_recompute_widens_after_guard_lowers_rr():
    # Mirrors the EXPE case from the deep-research log: entry 227, stop 201.9, upside 10%
    # downside ~= (227-201.9)/227 * 100 = 11.05% -> R/R ~= 0.9
    out = recompute_risk_metrics(entry_low=227.0, stop_loss=201.9, upside_percent=10.0)
    assert out["downside_risk_percent"] == pytest.approx(11.06, abs=0.05)
    assert out["risk_reward_ratio"] == pytest.approx(0.9, abs=0.05)


def test_recompute_returns_none_when_inputs_missing():
    out = recompute_risk_metrics(entry_low=None, stop_loss=90.0, upside_percent=20.0)
    assert out["downside_risk_percent"] is None
    assert out["risk_reward_ratio"] is None


def test_recompute_returns_none_when_stop_above_entry():
    # Defensive: invalid stop above entry should not produce a negative downside
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=105.0, upside_percent=20.0)
    assert out["downside_risk_percent"] is None
    assert out["risk_reward_ratio"] is None


def test_recompute_zero_downside_yields_none_rr():
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=100.0, upside_percent=20.0)
    assert out["downside_risk_percent"] == 0.0
    assert out["risk_reward_ratio"] is None  # division by zero
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recompute_risk_metrics.py -v`
Expected: FAIL with "cannot import name 'recompute_risk_metrics'"

- [ ] **Step 3: Implement helper**

Append to `app/utils/stop_loss_guard.py`:

```python
from typing import Dict, Optional


def recompute_risk_metrics(
    *,
    entry_low: Optional[float],
    stop_loss: Optional[float],
    upside_percent: Optional[float],
) -> Dict[str, Optional[float]]:
    """Recompute downside_risk_percent and risk_reward_ratio from a
    (entry_low, stop_loss) pair after the stop-guard may have widened the stop.

    Returns a dict with keys 'downside_risk_percent' (rounded to 2dp) and
    'risk_reward_ratio' (rounded to 1dp). Either may be None if inputs are
    missing or invalid (stop >= entry, missing values, etc.).
    """
    if entry_low is None or stop_loss is None or entry_low <= 0:
        return {"downside_risk_percent": None, "risk_reward_ratio": None}
    if stop_loss > entry_low:
        return {"downside_risk_percent": None, "risk_reward_ratio": None}

    downside = round((entry_low - stop_loss) / entry_low * 100.0, 2)
    if downside <= 0 or upside_percent is None:
        return {"downside_risk_percent": downside, "risk_reward_ratio": None}

    rr = round(float(upside_percent) / downside, 1)
    return {"downside_risk_percent": downside, "risk_reward_ratio": rr}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_recompute_risk_metrics.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/utils/stop_loss_guard.py tests/test_recompute_risk_metrics.py
git commit -m "feat(stop-guard): add recompute_risk_metrics helper for post-guard R/R"
```

---

## Task 2: Wire `recompute_risk_metrics` into `research_service.analyze_stock`

**Why:** Right now `research_service.py:421` prints the PM's pre-guard R/R, and `research_service.py:508-509` returns the pre-guard R/R into the dict written to DB and shown on the dashboard. Wire the helper in immediately after the stop-guard runs (research_service.py:486-492), then move the decision log so it reflects the post-guard numbers. Persist the new fields back into `final_decision` so the return dict at line 494+ is consistent.

**Files:**
- Modify: `app/services/research_service.py:415-492`
- Test: `tests/test_research_service_stop_guard_recompute.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_research_service_stop_guard_recompute.py
"""When the deterministic stop-guard widens the stop, the response dict
must carry the recomputed downside_risk_percent and risk_reward_ratio so
that downstream DB writes and dashboard rows aren't stale."""
from unittest.mock import patch, MagicMock

import pytest

from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _stub_state(ticker="EXPE"):
    state = MarketState(ticker=ticker, date="2026-05-08")
    state.reports = {
        "technical": "stub", "news": "stub", "market_sentiment": "stub",
        "competitive": "stub", "seeking_alpha": "stub",
        "bull": "stub", "bear": "stub", "risk": "stub",
    }
    state.agent_calls = 0
    return state


def test_response_dict_uses_post_guard_risk_metrics():
    # Simulate the EXPE case from the run: PM returns stop=222 (too tight),
    # the guard widens to 201.9; upside=10%. The response dict must show R/R≈0.9
    # not the PM's original 1.8.
    rs = ResearchService.__new__(ResearchService)  # bypass __init__ deps
    rs.api_key = "stub"
    pm_decision = {
        "action": "BUY", "conviction": "MODERATE", "drop_type": "OVERREACTION",
        "entry_price_low": 227.0, "entry_price_high": 230.0,
        "stop_loss": 222.0,
        "take_profit_1": 250.0, "take_profit_2": 270.0,
        "upside_percent": 10.0, "downside_risk_percent": 2.2,
        "risk_reward_ratio": 4.5,
        "reason": "stub", "key_factors": [],
    }

    raw_data = {"indicators": {"close": 227.0, "atr": 12.55, "sma50": 240.0, "sma200": 250.0}}

    with patch.object(rs, "_run_council_phase1", return_value=None), \
         patch.object(rs, "_run_bull_bear_perspectives", return_value=None), \
         patch.object(rs, "_run_risk_council_and_decision", return_value=pm_decision), \
         patch.object(rs, "_format_full_report", return_value="report"), \
         patch("app.services.research_service.QualityControlService") as qc:
        qc.validate_council_reports.side_effect = lambda r, t: r
        qc.validate_reports.side_effect = lambda r, t, names: r
        rs._count_real_phase1_reports = lambda r: (5, [])
        out = rs.analyze_stock(_stub_state(), drop_percent=-7.0, raw_data=raw_data)

    # Stop should have been widened by the guard
    assert out["stop_loss"] < 222.0
    # R/R must be recomputed against the new stop, not the PM's stale 4.5
    assert out["risk_reward_ratio"] is not None
    assert out["risk_reward_ratio"] < 2.0
    # And the downside should reflect the new (wider) stop
    assert out["downside_risk_percent"] > 2.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_research_service_stop_guard_recompute.py -v`
Expected: FAIL — `out["risk_reward_ratio"]` will still be `4.5`.

- [ ] **Step 3: Modify `analyze_stock` to recompute and reorder the log**

In `app/services/research_service.py`, change the block at roughly lines 470-492 from:

```python
        # Deterministic stop-loss guardrail: widen if PM placed it too tight.
        from app.utils.stop_loss_guard import widen_stop_if_too_tight
        _tv_inds = raw_data.get("indicators", {})
        _entry_low = final_decision.get("entry_price_low")
        # Fallback to current close if entry_price_low is missing or looks like a pct
        if _entry_low is None or (isinstance(_entry_low, (int, float)) and _entry_low < 0):
            _entry_low = _tv_inds.get("close")
        if _entry_low is not None:
            _guard = widen_stop_if_too_tight(
                stop_loss=final_decision.get("stop_loss"),
                entry_low=float(_entry_low),
                atr=float(_tv_inds.get("atr") or 0.0),
                sma_50=_tv_inds.get("sma50"),
                sma_200=_tv_inds.get("sma200"),
                bb_lower=_tv_inds.get("bb_lower"),
            )
            if _guard.adjusted:
                logger.info(
                    "[PM stop-guard] %s: widened stop %.2f -> %.2f (%s)",
                    state.ticker, final_decision["stop_loss"], _guard.stop_loss, _guard.reason,
                )
                final_decision["stop_loss"] = _guard.stop_loss
                final_decision["stop_loss_guard_reason"] = _guard.reason
```

to:

```python
        # Deterministic stop-loss guardrail: widen if PM placed it too tight,
        # then recompute R/R so the print line + DB row + dashboard reflect
        # the new (wider) stop instead of the PM's stale numbers.
        from app.utils.stop_loss_guard import widen_stop_if_too_tight, recompute_risk_metrics
        _tv_inds = raw_data.get("indicators", {})
        _entry_low = final_decision.get("entry_price_low")
        if _entry_low is None or (isinstance(_entry_low, (int, float)) and _entry_low < 0):
            _entry_low = _tv_inds.get("close")
        if _entry_low is not None:
            _guard = widen_stop_if_too_tight(
                stop_loss=final_decision.get("stop_loss"),
                entry_low=float(_entry_low),
                atr=float(_tv_inds.get("atr") or 0.0),
                sma_50=_tv_inds.get("sma50"),
                sma_200=_tv_inds.get("sma200"),
                bb_lower=_tv_inds.get("bb_lower"),
            )
            if _guard.adjusted:
                logger.info(
                    "[PM stop-guard] %s: widened stop %.2f -> %.2f (%s)",
                    state.ticker, final_decision["stop_loss"], _guard.stop_loss, _guard.reason,
                )
                final_decision["stop_loss"] = _guard.stop_loss
                final_decision["stop_loss_guard_reason"] = _guard.reason

            # Recompute downside / R/R against (possibly-new) stop_loss so the
            # value the user sees matches the value the user takes risk on.
            _metrics = recompute_risk_metrics(
                entry_low=float(_entry_low),
                stop_loss=final_decision.get("stop_loss"),
                upside_percent=final_decision.get("upside_percent"),
            )
            if _metrics["downside_risk_percent"] is not None:
                final_decision["downside_risk_percent"] = _metrics["downside_risk_percent"]
            if _metrics["risk_reward_ratio"] is not None:
                final_decision["risk_reward_ratio"] = _metrics["risk_reward_ratio"]
```

- [ ] **Step 4: Move the decision log so it prints the post-guard values**

Cut the block at `research_service.py:415-431` (the `print("\n" + "="*50)` ... `print("="*50 + "\n")` block) and paste it _after_ the recompute block from Step 3, keeping the exact same lines. The print at line 421 must read the values _after_ recompute_risk_metrics has run.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_research_service_stop_guard_recompute.py tests/test_recompute_risk_metrics.py tests/test_stop_loss_guard.py -v
```
Expected: all pass.

- [ ] **Step 6: Smoke-test the print path manually**

Run: `python -c "from app.services.research_service import ResearchService; print('import OK')"`
Expected: `import OK` with no exception.

- [ ] **Step 7: Commit**

```bash
git add app/services/research_service.py tests/test_research_service_stop_guard_recompute.py
git commit -m "fix(pm): recompute R/R after stop-guard widens, before log + DB write"
```

---

## Task 3: Tighten PM stop-loss prompt so guard fires less often

**Why:** The prompt at `research_service.py:1080-1140` currently lets the PM under-shoot stops (every batch in the recent log triggered the guard). Adding a hard floor in the prompt — `stop_loss = min(entry_low - 2 * ATR, nearest support below entry)` — addresses the root cause instead of relying on the guard. This is intentionally conservative; the guard remains as a safety net.

**Files:**
- Modify: `app/services/research_service.py` (the section that builds the PM JSON spec / fund-manager prompt)

- [ ] **Step 1: Locate the PM prompt section**

Run: `grep -n "downside_risk_percent\|stop_loss\|2\\*ATR\|2\\.0\\*ATR" app/services/research_service.py | head -30`
Expected: lines around 1080-1140 contain the JSON schema description.

- [ ] **Step 2: Read lines 1075-1145 to capture the exact wording**

Read: `app/services/research_service.py` lines 1075-1145.

- [ ] **Step 3: Update the `stop_loss` field description**

Replace the existing one-line description of `stop_loss` in the PM prompt (it likely says something like `"stop_loss: technical support level"`) with this exact phrasing:

```
- **stop_loss**: REQUIRED. Place at the *farther* (lower) of:
    (a) entry_price_low - 2.0 * ATR  (use TradingView ATR provided above)
    (b) nearest technical support below entry_price_low (prior swing low,
        SMA_50, or SMA_200 — whichever is below entry).
  Never place the stop closer than 1.5 * ATR below entry_price_low. Stops
  tighter than this floor will be programmatically widened.
```

- [ ] **Step 4: Verify import / syntax**

Run: `python -c "from app.services.research_service import ResearchService"`
Expected: no SyntaxError.

- [ ] **Step 5: Run the existing research-service tests**

Run: `pytest tests/ -k "research_service or stop_guard" -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py
git commit -m "fix(pm-prompt): require stop = max(2*ATR, nearest support below entry)"
```

---

## Task 4: Replace `time.sleep(60)` rate-limit retry in Alpha Vantage with fail-fast

**Why:** `app/services/alpha_vantage_service.py:69` blocks the calling thread (the screener worker) for a full minute on a 429. The screener runs every 20 minutes anyway — there is no value in stalling the current cycle to retry; better to skip AV for this ticker and let the next cycle pick it up. Same logic for the implicit "blocking the worker thread" issue at the top of the bug report.

**Files:**
- Modify: `app/services/alpha_vantage_service.py:63-72`
- Test: `tests/test_alpha_vantage_rate_limit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alpha_vantage_rate_limit.py
"""Alpha Vantage 429 must NOT call time.sleep — it should log and return [].
The screener worker can retry on the next 20-minute cycle."""
from unittest.mock import patch, MagicMock

import pytest

from app.services.alpha_vantage_service import AlphaVantageService


def _make_429_response(text="rate limit exceeded"):
    resp = MagicMock()
    resp.status_code = 429
    resp.text = text
    resp.json.return_value = {}
    return resp


def test_get_company_news_returns_empty_on_429_without_sleeping():
    av = AlphaVantageService()
    av.api_key = "stub"
    fake_resp = _make_429_response()

    with patch("app.services.alpha_vantage_service.requests.get", return_value=fake_resp) as req, \
         patch("app.services.alpha_vantage_service.time.sleep") as sleep_spy:
        result = av.get_company_news("AAPL", start_date="2026-05-01", end_date="2026-05-08")

    assert result == []
    sleep_spy.assert_not_called()
    # Must NOT have retried the request
    assert req.call_count == 1


def test_get_company_news_returns_empty_on_rate_limit_text_in_200():
    """Alpha Vantage sometimes returns 200 with body text 'rate limit'."""
    av = AlphaVantageService()
    av.api_key = "stub"
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "Information: Our standard API rate limit is 25 requests per day"
    resp.json.return_value = {"Information": "rate limit"}

    with patch("app.services.alpha_vantage_service.requests.get", return_value=resp) as req, \
         patch("app.services.alpha_vantage_service.time.sleep") as sleep_spy:
        result = av.get_company_news("AAPL", start_date="2026-05-01", end_date="2026-05-08")

    assert result == []
    sleep_spy.assert_not_called()
    assert req.call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_alpha_vantage_rate_limit.py -v`
Expected: FAIL — current code calls `time.sleep(60)` and retries.

- [ ] **Step 3: Modify the rate-limit branch**

In `app/services/alpha_vantage_service.py`, replace lines 63-72 (the `try: response = requests.get(...)` ... `data = response.json()` block) with:

```python
        try:
            response = requests.get(self.BASE_URL, params=params)

            # Rate limit: return empty and let the next 20-minute scanner
            # cycle retry. Do NOT block the caller for 60s — the screener
            # worker thread is shared and a long sleep starves later tickers.
            if response.status_code == 429 or "rate limit" in response.text.lower():
                print(f"Alpha Vantage Rate Limit Hit for {symbol}. Skipping for this cycle.")
                return []

            data = response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_alpha_vantage_rate_limit.py -v`
Expected: 2 passed.

- [ ] **Step 5: Verify nothing else regressed**

Run: `pytest tests/ -k "alpha_vantage" -v`
Expected: existing AV tests still pass (or skip if API-keyed).

- [ ] **Step 6: Commit**

```bash
git add app/services/alpha_vantage_service.py tests/test_alpha_vantage_rate_limit.py
git commit -m "fix(alpha-vantage): fail-fast on 429 instead of blocking 60s"
```

---

## Task 5: Exclude DR-overridden candidates from batch comparison

**Why:** `app/database.py:611-639` (`get_unbatched_candidates_by_date`) and `:672-697` (`get_distinct_dates_with_unbatched_candidates`) only filter on `deep_research_verdict NOT LIKE 'UNKNOWN%'` etc. and on the original PM `recommendation`. They do not exclude rows where DR's `review_verdict='OVERRIDDEN'` or `deep_research_action='AVOID'`. Result: the run named EMBJ and APP — both DR-overridden to AVOID — as 🏆 batch winners. Add a filter on the post-DR action.

**Files:**
- Modify: `app/database.py:611-697`
- Test: `tests/test_batch_candidate_filter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_batch_candidate_filter.py
"""Batch-comparison candidate selection must exclude rows where deep
research overrode the PM verdict to AVOID."""
import os
import sqlite3
import tempfile
from contextlib import contextmanager

import pytest

import app.database as db


@contextmanager
def _temp_db(monkeypatch_env):
    """Spin up a throwaway sqlite file with the production schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch_env.setenv("DB_PATH", path)
    # database.py reads DB_NAME at import; patch it directly too
    original = db.DB_NAME
    db.DB_NAME = path
    try:
        db.init_db()
        yield path
    finally:
        db.DB_NAME = original
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def _insert_row(path, *, symbol, recommendation, dr_verdict, dr_review_verdict, dr_action, ts="2026-05-08 10:00:00"):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO decision_points (symbol, timestamp, recommendation,
            deep_research_verdict, deep_research_review_verdict,
            deep_research_action, deep_research_score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, ts, recommendation, dr_verdict, dr_review_verdict, dr_action, 75),
    )
    conn.commit()
    conn.close()


def test_overridden_avoid_excluded_from_unbatched(monkeypatch):
    with _temp_db(monkeypatch) as path:
        # Eligible: PM=BUY, DR confirmed
        _insert_row(path, symbol="GOOD1", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="CONFIRMED", dr_action="BUY")
        # Eligible: PM=BUY, DR upgraded
        _insert_row(path, symbol="GOOD2", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="UPGRADED", dr_action="BUY")
        # NOT eligible: PM=BUY but DR overrode to AVOID (the EMBJ/APP case)
        _insert_row(path, symbol="OVERRIDE_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="OVERRIDDEN", dr_action="AVOID")
        # NOT eligible: DR action AVOID even without OVERRIDDEN flag
        _insert_row(path, symbol="DR_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="ADJUSTED", dr_action="AVOID")

        rows = db.get_unbatched_candidates_by_date("2026-05-08")

    symbols = {r["symbol"] for r in rows}
    assert symbols == {"GOOD1", "GOOD2"}


def test_distinct_dates_excludes_when_only_overridden_rows_exist(monkeypatch):
    with _temp_db(monkeypatch) as path:
        _insert_row(path, symbol="OVERRIDE_AVOID", recommendation="BUY",
                    dr_verdict="AVOID", dr_review_verdict="OVERRIDDEN", dr_action="AVOID",
                    ts="2026-05-07 10:00:00")
        _insert_row(path, symbol="GOOD", recommendation="BUY",
                    dr_verdict="BUY", dr_review_verdict="CONFIRMED", dr_action="BUY",
                    ts="2026-05-08 10:00:00")

        dates = db.get_distinct_dates_with_unbatched_candidates()

    assert "2026-05-07" not in dates
    assert "2026-05-08" in dates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_batch_candidate_filter.py -v`
Expected: FAIL — `OVERRIDE_AVOID` and `DR_AVOID` will appear in `rows`.

- [ ] **Step 3: Add the filter to `get_unbatched_candidates_by_date`**

In `app/database.py:620-632` change:

```python
        cursor.execute('''
            SELECT * FROM decision_points
            WHERE date(timestamp) = ?
            AND deep_research_verdict IS NOT NULL
            AND deep_research_verdict != ''
            AND deep_research_verdict != '-'
            AND deep_research_verdict != 'PENDING_REVIEW'
            AND deep_research_verdict != 'ERROR_PARSING'
            AND deep_research_verdict NOT LIKE 'UNKNOWN%'
            AND (recommendation IN ('BUY', 'STRONG BUY', 'SPECULATIVE BUY') OR recommendation LIKE '%BUY%')
            AND (batch_id IS NULL OR batch_id = '')
            ORDER BY deep_research_score DESC
        ''', (date_str,))
```

to:

```python
        cursor.execute('''
            SELECT * FROM decision_points
            WHERE date(timestamp) = ?
            AND deep_research_verdict IS NOT NULL
            AND deep_research_verdict != ''
            AND deep_research_verdict != '-'
            AND deep_research_verdict != 'PENDING_REVIEW'
            AND deep_research_verdict != 'ERROR_PARSING'
            AND deep_research_verdict NOT LIKE 'UNKNOWN%'
            AND deep_research_verdict != 'AVOID'
            AND (deep_research_review_verdict IS NULL OR deep_research_review_verdict != 'OVERRIDDEN')
            AND (deep_research_action IS NULL OR deep_research_action != 'AVOID')
            AND (recommendation IN ('BUY', 'STRONG BUY', 'SPECULATIVE BUY') OR recommendation LIKE '%BUY%')
            AND (batch_id IS NULL OR batch_id = '')
            ORDER BY deep_research_score DESC
        ''', (date_str,))
```

- [ ] **Step 4: Mirror the filter in `get_distinct_dates_with_unbatched_candidates`**

In `app/database.py:680-690` apply the same three new lines (`deep_research_verdict != 'AVOID'`, the `OVERRIDDEN` exclusion, and the `deep_research_action != 'AVOID'` exclusion) to the `SELECT DISTINCT date(timestamp) FROM decision_points WHERE ...` query.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_batch_candidate_filter.py -v`
Expected: 2 passed.

- [ ] **Step 6: Backfill: clear the misleading `batch_winner` flag for already-overridden rows**

Add a one-shot script at `scripts/backfill_clear_overridden_winners.py`:

```python
"""One-shot fix: clear batch_winner=1 on rows where DR overrode to AVOID.
The selection logic was buggy when these were named winners; the dashboard
should not display them as 🏆."""
import os
import sqlite3

DB = os.getenv("DB_PATH", "subscribers.db")


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE decision_points
        SET batch_winner = 0
        WHERE batch_winner = 1
          AND (deep_research_review_verdict = 'OVERRIDDEN'
               OR deep_research_action = 'AVOID'
               OR deep_research_verdict = 'AVOID')
        """
    )
    print(f"[Backfill] Cleared {cur.rowcount} stale batch_winner flags.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
```

Run: `python scripts/backfill_clear_overridden_winners.py`
Expected: a "Cleared N stale batch_winner flags" line.

- [ ] **Step 7: Commit**

```bash
git add app/database.py tests/test_batch_candidate_filter.py scripts/backfill_clear_overridden_winners.py
git commit -m "fix(batch): exclude DR-overridden AVOID candidates from batch selection"
```

---

## Task 6: Skip the batch entirely when too few valid candidates remain, and surface DR verdicts in the prompt

**Why:** Even with the DB filter in place we want belt-and-suspenders: the batch agent should see the DR verdict for every candidate so it cannot crown a knife-catch as the winner if some edge case slips through, and we should bail out fast when fewer than 2 valid candidates remain (a "1-stock comparison" is meaningless).

**Files:**
- Modify: `app/services/deep_research_service.py:1822-1990`

- [ ] **Step 1: Bail out early when too few candidates**

Right after `symbols = [x['symbol'] for x in candidates]` at line 1828 in `execute_batch_comparison`, insert:

```python
            if len(candidates) < 2:
                logger.info(
                    "[Deep Research] Skipping batch %s: only %d valid candidate(s) after DR filter.",
                    batch_id, len(candidates),
                )
                print(f"[Deep Research] Skipping batch {batch_id}: < 2 valid candidates.")
                if batch_id is not None:
                    try:
                        from app.database import update_batch_status
                        update_batch_status(batch_id, "SKIPPED")
                    except Exception as e:
                        logger.error(f"[Deep Research] Failed to mark batch {batch_id} as SKIPPED: {e}")
                return None
```

- [ ] **Step 2: Pull each candidate's DR verdict into the prompt context**

Inside the `for cand in candidates:` loop at lines 1847-1858, _before_ the `if report_str:` block, fetch the DR verdict. Replace the loop with:

```python
            for cand in candidates:
                sym = cand['symbol']
                # Pull the DR verdict so the batch agent treats AVOID as a hard negative.
                dr_verdict = cand.get('deep_research_verdict') or 'UNKNOWN'
                dr_review = cand.get('deep_research_review_verdict') or 'UNKNOWN'
                dr_action = cand.get('deep_research_action') or 'UNKNOWN'
                dr_score = cand.get('deep_research_score')
                dr_reason = cand.get('deep_research_reason') or ''

                report_str = self._load_council_report(sym, date_str)
                summary = self._summarize_report_context(report_str) if report_str else "(no council report)"
                context_data += (
                    f"\n--- SUPPLEMENTARY REPORT FOR {sym} ---\n"
                    f"DEEP_RESEARCH_REVIEW: review_verdict={dr_review}, "
                    f"action={dr_action}, score={dr_score}\n"
                    f"DEEP_RESEARCH_REASON: {dr_reason[:400]}\n"
                    f"{summary}\n"
                )

                print(f"\n[{sym}] SUMMARIZED CONTEXT (DR={dr_review}/{dr_action}):")
                print("-" * 40)
                print(summary)
                print("-" * 40)
```

- [ ] **Step 3: Add a hard rule to the prompt**

Update the prompt string at lines 1861-1888 by inserting this block after `CRITERIA:`:

```
HARD RULE:
- If a candidate's DEEP_RESEARCH_REVIEW shows action=AVOID or
  review_verdict=OVERRIDDEN, you MUST NOT name it the winner. Treat it as
  disqualified regardless of how compelling the supplementary report looks.
  Pick the winner from the remaining candidates.
```

- [ ] **Step 4: Verify candidates passed in carry the DR fields**

The `_recover_pending_batches` path at lines 343-345 builds `candidates = [{'symbol': s} for s in symbols]` — this loses the DR fields. Update it to load the DR fields from the DB before queuing:

Replace lines 343-345:

```python
                    candidates = [{'symbol': s} for s in symbols]
```

with:

```python
                    candidates = []
                    for s in symbols:
                        cur2 = conn.cursor()
                        cur2.row_factory = sqlite3.Row
                        cur2.execute(
                            """
                            SELECT symbol, deep_research_verdict,
                                   deep_research_review_verdict,
                                   deep_research_action, deep_research_score,
                                   deep_research_reason
                            FROM decision_points
                            WHERE symbol = ? AND batch_id = ?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (s, batch_id),
                        )
                        row = cur2.fetchone()
                        if row:
                            candidates.append(dict(row))
                        else:
                            candidates.append({'symbol': s})
```

- [ ] **Step 5: Run targeted tests**

Run: `pytest tests/ -k "batch or deep_research" -v`
Expected: existing tests still pass; no regressions.

- [ ] **Step 6: Smoke import check**

Run: `python -c "from app.services.deep_research_service import deep_research_service; print('OK')"`
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add app/services/deep_research_service.py
git commit -m "fix(batch): bail on <2 valid candidates and feed DR verdicts into batch prompt"
```

---

## Task 7: Pre-fetch structured EPS facts and inject into the PM prompt

**Why:** The TOST mistake (PM said "EPS $0.20 vs consensus $0.15 — small beat" when the company actually reported $0.27 vs $0.20 — a 35% beat narrated as a miss is a different category of error than mis-pricing). The News Agent today summarizes EPS from raw articles, and different articles cite different consensus numbers depending on when they were published. Pre-fetching from Finnhub's `/stock/earnings` endpoint gives us a single canonical fact dict with reported EPS, consensus EPS, and surprise %; passing it to the PM as a labeled block (with "USE THESE NUMBERS, do not infer EPS from news") prevents the LLM from being misled by stale or contradictory article quotes.

**Files:**
- Modify: `app/services/finnhub_service.py:155+` (add `get_earnings_facts`)
- Modify: `app/services/stock_service.py:1510-1527` (inject `earnings_facts` into `raw_data`)
- Modify: `app/services/research_service.py:1040-1062` (inject EARNINGS_FACTS block into PM prompt)
- Modify: `app/database.py:60-100` (add columns to `decision_points` schema migration)
- Test: `tests/test_finnhub_earnings_facts.py`

- [ ] **Step 1: Write the failing test for `get_earnings_facts`**

```python
# tests/test_finnhub_earnings_facts.py
"""Pre-fetch structured EPS facts from Finnhub. The PM must see actual,
consensus, and surprise % as a single dict — not a free-form LLM summary
of news articles (the TOST $0.20 vs $0.15 vs actual $0.27 incident)."""
from unittest.mock import patch, MagicMock

import pytest

from app.services.finnhub_service import FinnhubService


def test_get_earnings_facts_returns_latest_period():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = [
        {"actual": 0.27, "estimate": 0.20, "period": "2026-03-31",
         "quarter": 1, "surprise": 0.07, "surprisePercent": 35.0,
         "symbol": "TOST", "year": 2026},
        {"actual": 0.18, "estimate": 0.21, "period": "2025-12-31",
         "quarter": 4, "surprise": -0.03, "surprisePercent": -14.3,
         "symbol": "TOST", "year": 2025},
    ]
    facts = fh.get_earnings_facts("TOST")

    assert facts["reported_eps"] == 0.27
    assert facts["consensus_eps"] == 0.20
    assert facts["surprise_pct"] == 35.0
    assert facts["fiscal_quarter"] == "2026Q1"
    assert facts["period"] == "2026-03-31"
    assert facts["fetched_at"]  # ISO 8601 string


def test_get_earnings_facts_returns_none_when_no_data():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = []
    assert fh.get_earnings_facts("XYZ") is None


def test_get_earnings_facts_handles_partial_rows():
    """If actual or estimate is missing, return None rather than emit garbage."""
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = MagicMock()
    fh.client.company_earnings.return_value = [
        {"period": "2026-03-31", "quarter": 1, "year": 2026, "symbol": "ABC",
         "actual": None, "estimate": 0.20},
    ]
    assert fh.get_earnings_facts("ABC") is None


def test_get_earnings_facts_handles_no_client():
    fh = FinnhubService.__new__(FinnhubService)
    fh.client = None
    assert fh.get_earnings_facts("ANY") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finnhub_earnings_facts.py -v`
Expected: FAIL — `AttributeError: 'FinnhubService' object has no attribute 'get_earnings_facts'`.

- [ ] **Step 3: Implement `get_earnings_facts`**

In `app/services/finnhub_service.py`, add this method right after `get_latest_reported_quarter` (around line 183):

```python
    def get_earnings_facts(self, symbol: str) -> dict | None:
        """Return the latest reported quarter's EPS facts as a structured dict,
        or None if data is missing/unavailable.

        Returned shape:
            {
                "reported_eps": float,
                "consensus_eps": float,
                "surprise_pct": float,         # signed; positive = beat
                "fiscal_quarter": "YYYYQN",
                "period": "YYYY-MM-DD",
                "source": "finnhub",
                "fetched_at": "<ISO 8601 UTC>",
            }
        """
        from datetime import datetime, timezone

        if not self.client:
            return None
        try:
            rows = _call_with_retry(self.client.company_earnings, symbol)
        except Exception as e:
            print(f"[FinnhubService] company_earnings failed for {symbol}: {e}")
            return None
        if not rows:
            return None

        try:
            latest = max(rows, key=lambda r: r.get("period", ""))
        except (ValueError, TypeError):
            return None

        actual = latest.get("actual")
        estimate = latest.get("estimate")
        if actual is None or estimate is None:
            return None

        # Finnhub may already populate surprisePercent. Fall back to computing
        # it ourselves if missing or zero against a non-zero estimate.
        surprise_pct = latest.get("surprisePercent")
        if surprise_pct is None and estimate not in (0, None):
            try:
                surprise_pct = round((float(actual) - float(estimate)) / float(estimate) * 100.0, 2)
            except (TypeError, ValueError):
                surprise_pct = None

        year = latest.get("year")
        quarter = latest.get("quarter")
        fq = None
        if year is not None and quarter is not None:
            try:
                fq = f"{int(year)}Q{int(quarter)}"
            except (TypeError, ValueError):
                fq = None

        return {
            "reported_eps": float(actual),
            "consensus_eps": float(estimate),
            "surprise_pct": float(surprise_pct) if surprise_pct is not None else None,
            "fiscal_quarter": fq,
            "period": latest.get("period"),
            "source": "finnhub",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_finnhub_earnings_facts.py -v`
Expected: 4 passed.

- [ ] **Step 5: Wire into `stock_service._run_deep_analysis`**

In `app/services/stock_service.py`, locate the `raw_data = { ... }` block at line 1511. Just before that block, add:

```python
        # Pre-fetch structured EPS facts so the PM sees a canonical earnings
        # dict instead of relying on the News Agent to summarize from news
        # articles (different articles cite different consensus numbers).
        try:
            from app.services.finnhub_service import finnhub_service
            earnings_facts = finnhub_service.get_earnings_facts(symbol)
        except Exception as e:
            print(f"[Earnings Facts] Failed to fetch for {symbol}: {e}")
            earnings_facts = None
```

Then add `"earnings_facts": earnings_facts,` as a new key inside the `raw_data` dict (alongside `"metrics"`, `"indicators"`, etc.).

- [ ] **Step 6: Inject into the PM prompt**

In `app/services/research_service.py`, modify `_create_fund_manager_prompt` (line 1018). Change the signature to accept `earnings_facts` from `state` (which already gets `raw_data`), and add a new prompt block. First, ensure the state carries it — find where `MarketState` is populated in `analyze_stock` (around line 175-200) and add the assignment if not already present.

Then in `_create_fund_manager_prompt`, add this block immediately after the `RISK FACTORS` block (around line 1052) and before the `BULL CASE` block:

```python
        ef = getattr(state, "earnings_facts", None) or {}
        if ef and ef.get("reported_eps") is not None:
            earnings_block = (
                "\nEARNINGS_FACTS (canonical, from Finnhub — DO NOT infer EPS from news articles below):\n"
                f"- Reported EPS: ${ef['reported_eps']:.2f}\n"
                f"- Consensus EPS: ${ef['consensus_eps']:.2f}\n"
                f"- Surprise: {ef['surprise_pct']:+.1f}% "
                f"({'BEAT' if (ef['surprise_pct'] or 0) > 0 else 'MISS' if (ef['surprise_pct'] or 0) < 0 else 'INLINE'})\n"
                f"- Fiscal quarter: {ef.get('fiscal_quarter')}\n"
                f"- Source: {ef.get('source')} (fetched {ef.get('fetched_at')})\n"
                "Whenever your reasoning describes whether the company beat or missed, "
                "use the surprise sign above. News articles may cite stale consensus numbers; "
                "the values above are the ground truth."
            )
        else:
            earnings_block = "\nEARNINGS_FACTS: (no recent reported quarter available — drop is not earnings-driven, or facts unavailable)"
```

…and interpolate `{earnings_block}` into the f-string immediately before `BULL CASE:`.

- [ ] **Step 7: Persist `earnings_facts` to `decision_points`**

In `app/database.py`, locate the `MIGRATIONS` dict (around line 60-100, where columns are added via ALTER TABLE) and add four new columns:

```python
            "reported_eps": "REAL",
            "consensus_eps": "REAL",
            "surprise_pct": "REAL",
            "earnings_fiscal_quarter": "TEXT",
```

Then in `app/services/stock_service.py`, locate the `update_decision_point` call that happens after research completes (around line 1577-1620, in `_run_deep_analysis`), and pass:

```python
                reported_eps=(earnings_facts or {}).get("reported_eps"),
                consensus_eps=(earnings_facts or {}).get("consensus_eps"),
                surprise_pct=(earnings_facts or {}).get("surprise_pct"),
                earnings_fiscal_quarter=(earnings_facts or {}).get("fiscal_quarter"),
```

Update `app/database.py:update_decision_point` to accept and write these four kwargs (follow the existing pattern in that function).

- [ ] **Step 8: Smoke-test imports**

Run: `python -c "from app.services.finnhub_service import finnhub_service; from app.services.research_service import ResearchService; from app.database import init_db; init_db(); print('OK')"`
Expected: `OK` and a "Migrations applied: ..." line listing the four new columns on first run.

- [ ] **Step 9: Run targeted tests**

Run: `pytest tests/test_finnhub_earnings_facts.py tests/ -k "research_service or finnhub" -v`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add app/services/finnhub_service.py app/services/stock_service.py app/services/research_service.py app/database.py tests/test_finnhub_earnings_facts.py
git commit -m "feat(earnings): pre-fetch EPS facts from Finnhub and inject into PM prompt"
```

---

## Task 8: Deterministic post-PM earnings narrative consistency check

**Why:** Even with EARNINGS_FACTS in the prompt, the LLM can still narrate "modest miss" while the surprise % is positive — Task 7 reduces the rate but doesn't eliminate the failure mode. Add a deterministic post-PM check: scan `pm.reasoning` for the words "beat" / "missed" / "missed estimates" and compare against `sign(surprise_pct)`. If inconsistent, attach `EARNINGS_NARRATIVE_INCONSISTENT` to the decision and downgrade the verdict by one tier (BUY → BUY_LIMIT, BUY_LIMIT → WATCH, anything else stays). This catches the category error without trusting the LLM to police itself.

**Files:**
- Create: `app/utils/earnings_consistency.py`
- Test: `tests/test_earnings_consistency.py`
- Modify: `app/services/research_service.py` (call the helper in `analyze_stock` after `_run_risk_council_and_decision`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_earnings_consistency.py
"""Catch the TOST-style failure: PM narrates 'missed estimates' while
Finnhub reports a positive surprise %. Downgrade the verdict by one tier."""
import pytest

from app.utils.earnings_consistency import (
    check_narrative_consistency,
    downgrade_action,
)


def test_beat_narrative_with_negative_surprise_is_inconsistent():
    out = check_narrative_consistency(
        reasoning="The company beat earnings on strong margin expansion.",
        surprise_pct=-12.0,
    )
    assert out.inconsistent is True
    assert out.flag == "EARNINGS_NARRATIVE_INCONSISTENT"
    assert "beat" in out.reason.lower()


def test_miss_narrative_with_positive_surprise_is_inconsistent():
    out = check_narrative_consistency(
        reasoning="Toast missed estimates this quarter, dragging the stock down.",
        surprise_pct=35.0,
    )
    assert out.inconsistent is True
    assert out.flag == "EARNINGS_NARRATIVE_INCONSISTENT"


def test_consistent_beat_passes():
    out = check_narrative_consistency(
        reasoning="Strong earnings beat across the board.",
        surprise_pct=8.0,
    )
    assert out.inconsistent is False


def test_consistent_miss_passes():
    out = check_narrative_consistency(
        reasoning="Disappointing miss; revenue weak too.",
        surprise_pct=-5.0,
    )
    assert out.inconsistent is False


def test_neutral_narrative_passes():
    out = check_narrative_consistency(
        reasoning="Results were in line with expectations.",
        surprise_pct=0.5,
    )
    assert out.inconsistent is False


def test_skipped_when_no_surprise_data():
    out = check_narrative_consistency(
        reasoning="The company beat on earnings.",
        surprise_pct=None,
    )
    assert out.inconsistent is False
    assert out.reason == "no_surprise_data"


def test_word_boundary_avoids_false_positive_on_unbeatable():
    # 'unbeatable' contains 'beat' as a substring — must not match
    out = check_narrative_consistency(
        reasoning="The product is unbeatable in its category.",
        surprise_pct=-10.0,
    )
    assert out.inconsistent is False


def test_downgrade_buy_to_buy_limit():
    assert downgrade_action("BUY") == "BUY_LIMIT"


def test_downgrade_buy_limit_to_watch():
    assert downgrade_action("BUY_LIMIT") == "WATCH"


def test_downgrade_wait_for_stab_unchanged():
    assert downgrade_action("WAIT_FOR_STAB") == "WAIT_FOR_STAB"


def test_downgrade_avoid_unchanged():
    assert downgrade_action("AVOID") == "AVOID"


def test_downgrade_unknown_unchanged():
    assert downgrade_action("HOLD") == "HOLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_earnings_consistency.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement the helper**

Create `app/utils/earnings_consistency.py`:

```python
"""Deterministic post-PM check: did the PM narrate the earnings event
consistently with the actual surprise sign?

Catches cases like the TOST 2026-05 incident where the PM described a
beat as a miss (or vice versa). When inconsistent, the caller should
attach an EARNINGS_NARRATIVE_INCONSISTENT flag and downgrade the verdict
by one tier (BUY -> BUY_LIMIT, BUY_LIMIT -> WATCH).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Word-boundary patterns — 'unbeatable' must not match 'beat'.
_BEAT_RE = re.compile(r"\b(beat|beats|beating|outperformed|topped\s+(?:estimates|consensus|expectations))\b", re.IGNORECASE)
_MISS_RE = re.compile(r"\b(miss(?:ed|es|ing)?|underperformed|fell\s+short|below\s+(?:estimates|consensus|expectations))\b", re.IGNORECASE)


@dataclass
class ConsistencyResult:
    inconsistent: bool
    flag: Optional[str]
    reason: str


def check_narrative_consistency(
    *, reasoning: Optional[str], surprise_pct: Optional[float]
) -> ConsistencyResult:
    """Compare PM narrative ('beat' vs 'miss') against the sign of surprise_pct.

    Returns inconsistent=True when the narrative claims a beat but the surprise
    is negative, or claims a miss but the surprise is positive.
    """
    if surprise_pct is None:
        return ConsistencyResult(False, None, "no_surprise_data")
    if not reasoning:
        return ConsistencyResult(False, None, "no_reasoning")

    has_beat = bool(_BEAT_RE.search(reasoning))
    has_miss = bool(_MISS_RE.search(reasoning))

    # If the narrative talks about both, treat as ambiguous and pass.
    if has_beat and has_miss:
        return ConsistencyResult(False, None, "ambiguous_narrative")

    if has_beat and surprise_pct < 0:
        return ConsistencyResult(
            True,
            "EARNINGS_NARRATIVE_INCONSISTENT",
            f"reasoning narrates beat but surprise_pct={surprise_pct:+.1f}",
        )
    if has_miss and surprise_pct > 0:
        return ConsistencyResult(
            True,
            "EARNINGS_NARRATIVE_INCONSISTENT",
            f"reasoning narrates miss but surprise_pct={surprise_pct:+.1f}",
        )

    return ConsistencyResult(False, None, "consistent_or_neutral")


# One-tier downgrade ladder. BUY is the most aggressive; AVOID is bottom.
_DOWNGRADE = {
    "BUY": "BUY_LIMIT",
    "BUY_LIMIT": "WATCH",
}


def downgrade_action(action: str) -> str:
    """Return the next-lower tier, or the input if no downgrade applies."""
    if not action:
        return action
    return _DOWNGRADE.get(action.upper().strip(), action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_earnings_consistency.py -v`
Expected: 12 passed.

- [ ] **Step 5: Wire the check into `analyze_stock`**

In `app/services/research_service.py`, immediately after the `recompute_risk_metrics` block from Task 2 (still inside the `if _entry_low is not None:` branch — but the consistency check itself should run unconditionally, so place it just outside that branch), add:

```python
        # Deterministic earnings-narrative consistency check.
        # If the PM narrates "beat" but surprise is negative (or vice versa),
        # flag and downgrade by one tier — see TOST 2026-05 incident.
        from app.utils.earnings_consistency import check_narrative_consistency, downgrade_action
        ef = raw_data.get("earnings_facts") or {}
        consistency = check_narrative_consistency(
            reasoning=final_decision.get("reason", ""),
            surprise_pct=ef.get("surprise_pct"),
        )
        if consistency.inconsistent:
            original_action = final_decision.get("action", "")
            new_action = downgrade_action(original_action)
            logger.warning(
                "[Earnings Consistency] %s: %s. Action %s -> %s",
                state.ticker, consistency.reason, original_action, new_action,
            )
            print(
                f"  > [Earnings Consistency Flag] {state.ticker}: {consistency.reason}. "
                f"Downgrading {original_action} -> {new_action}"
            )
            final_decision["action"] = new_action
            final_decision["earnings_narrative_flag"] = consistency.flag
            existing_factors = final_decision.get("key_factors") or []
            if isinstance(existing_factors, list):
                existing_factors.append(
                    f"[FLAG] {consistency.flag}: {consistency.reason}. "
                    f"Verdict downgraded from {original_action} to {new_action}."
                )
                final_decision["key_factors"] = existing_factors
```

- [ ] **Step 6: Surface the flag in the response dict**

In the return dict at `research_service.py:494+`, add a new key (next to the existing trading-level fields):

```python
            "earnings_narrative_flag": final_decision.get("earnings_narrative_flag"),
```

And update the local `recommendation = final_decision.get("action", "AVOID").upper()` line earlier in the function so it picks up the (possibly downgraded) action — verify that the `recommendation` variable is read *after* the downgrade. If `recommendation` is computed before the downgrade block, move that assignment to immediately before the `return` statement.

- [ ] **Step 7: Persist the flag**

Add `"earnings_narrative_flag": "TEXT"` to the `MIGRATIONS` dict in `app/database.py` (alongside the four columns added in Task 7). Pass it through `update_decision_point` in `stock_service.py:1577+` as:

```python
                earnings_narrative_flag=report_data.get("earnings_narrative_flag"),
```

…and update `update_decision_point` in `database.py` to accept and write the new kwarg.

- [ ] **Step 8: Run the test suite for affected files**

Run: `pytest tests/test_earnings_consistency.py tests/test_finnhub_earnings_facts.py tests/ -k "research_service or earnings" -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add app/utils/earnings_consistency.py tests/test_earnings_consistency.py app/services/research_service.py app/services/stock_service.py app/database.py
git commit -m "feat(earnings): deterministic narrative consistency check downgrades inconsistent verdicts"
```

---

## Task 9: Gate `Owned` on deep-research completion (intermediate `Pending DR Review` state)

**Why:** Today, `app/services/stock_service.py:1571-1575` sets `status = "Owned"` the moment the council emits any "BUY" verdict. Deep research runs asynchronously after that and may override the verdict to AVOID — but the status column is never updated. Result: in the recent run, UI and APP were marked `Owned` at the council's entry zones, then DR's `_apply_trading_level_overrides` (deep_research_service.py:736) silently rewrote entry/stop columns and the dashboard kept showing them as Owned. The fix is a 3-state machine:

- Council emits BUY/BUY_LIMIT with R/R > 1.0 → `Pending DR Review` (DR will run)
- Council emits BUY/BUY_LIMIT with R/R ≤ 1.0 → `Not Owned` (low conviction; do not enter without DR)
- Council emits anything else → `Not Owned` (unchanged)
- DR completes with action ∈ {BUY, BUY_LIMIT} → `Owned`
- DR completes with action ∈ {AVOID, WATCH, HOLD} OR review_verdict = OVERRIDDEN → `Not Owned`

DR already overwrites `entry_price_low/high/stop_loss` on the row (see `_apply_trading_level_overrides` at deep_research_service.py:736-803), so the entry zone reflects the post-DR adjustment automatically — we just need to update `status` in the same transaction. Also lower `_should_trigger_deep_research`'s BUY_LIMIT cutoff from 1.25 → 1.0 so the gate cannot strand rows in `Pending DR Review` forever.

**Files:**
- Modify: `app/services/stock_service.py:1571-1606` (status assignment)
- Modify: `app/services/stock_service.py:603-624` (lower BUY_LIMIT DR threshold)
- Modify: `app/services/deep_research_service.py:722-734` (call finalize after override)
- Modify: `app/database.py` (new `finalize_position_status_after_dr()` helper)
- Test: `tests/test_pending_dr_status.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pending_dr_status.py
"""3-state position lifecycle: Pending DR Review -> Owned/Not Owned.

Verifies that:
1. The council does not advance to 'Owned' for BUY/BUY_LIMIT with R/R > 1.0.
2. DR completion atomically transitions to 'Owned' or 'Not Owned'.
3. OVERRIDDEN→AVOID can NEVER end in 'Owned'.
"""
import os
import sqlite3
import tempfile

import pytest

import app.database as db


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DB_PATH", path)
    original = db.DB_NAME
    db.DB_NAME = path
    db.init_db()
    yield path
    db.DB_NAME = original
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _insert(path, *, symbol="X", recommendation="BUY", status="Pending DR Review", rr=2.0):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO decision_points (symbol, price_at_decision, drop_percent,
           recommendation, reasoning, status, risk_reward_ratio)
           VALUES (?, 100.0, -7.0, ?, 'r', ?, ?)""",
        (symbol, recommendation, status, rr),
    )
    did = cur.lastrowid
    conn.commit()
    conn.close()
    return did


def _get_status(path, decision_id):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT status FROM decision_points WHERE id = ?", (decision_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def test_dr_buy_promotes_pending_to_owned(temp_db):
    did = _insert(temp_db, symbol="AAPL", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert _get_status(temp_db, did) == "Owned"


def test_dr_buy_limit_promotes_pending_to_owned(temp_db):
    did = _insert(temp_db, symbol="MSFT", recommendation="BUY_LIMIT", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY_LIMIT", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Owned"


def test_overridden_avoid_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="EMBJ", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="AVOID", dr_review_verdict="OVERRIDDEN",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_watch_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="UI", recommendation="BUY_LIMIT", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="WATCH", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_hold_demotes_pending_to_not_owned(temp_db):
    did = _insert(temp_db, symbol="APP", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="HOLD", dr_review_verdict="ADJUSTED",
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_dr_avoid_without_overridden_still_demotes(temp_db):
    """Belt and suspenders: even if review_verdict is missing, AVOID action
    must demote."""
    did = _insert(temp_db, symbol="X", recommendation="BUY", status="Pending DR Review")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="AVOID", dr_review_verdict=None,
    )
    assert _get_status(temp_db, did) == "Not Owned"


def test_finalize_no_op_when_decision_id_missing(temp_db):
    """Should not raise when decision_id doesn't exist in DB."""
    result = db.finalize_position_status_after_dr(
        decision_id=999999, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert result is False  # signal that no row was updated


def test_finalize_does_not_promote_already_not_owned_row(temp_db):
    """If the row was set to Not Owned by some other path (e.g. earnings
    consistency downgrade), DR completion must not silently promote it."""
    did = _insert(temp_db, symbol="X", recommendation="BUY", status="Not Owned")
    db.finalize_position_status_after_dr(
        decision_id=did, dr_action="BUY", dr_review_verdict="CONFIRMED",
    )
    assert _get_status(temp_db, did) == "Not Owned"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pending_dr_status.py -v`
Expected: FAIL with `AttributeError: module 'app.database' has no attribute 'finalize_position_status_after_dr'`.

- [ ] **Step 3: Implement `finalize_position_status_after_dr` in `app/database.py`**

Add this function in `app/database.py` immediately after `update_deep_research_data` (around line 460):

```python
def finalize_position_status_after_dr(
    *,
    decision_id: int,
    dr_action: str | None,
    dr_review_verdict: str | None,
) -> bool:
    """Atomically transition a `Pending DR Review` row to its final status
    based on the deep-research outcome.

    Rules:
        - dr_action ∈ {BUY, BUY_LIMIT}                  → Owned
        - dr_action ∈ {AVOID, WATCH, HOLD, SELL, ...}    → Not Owned
        - dr_review_verdict == OVERRIDDEN (any action)   → Not Owned

    Only updates rows whose current status is 'Pending DR Review' so that
    rows demoted by an earlier deterministic check (e.g. earnings narrative
    inconsistency) are not silently re-promoted. Returns True if a row was
    updated, False otherwise.
    """
    action_norm = (dr_action or "").upper().strip()
    review_norm = (dr_review_verdict or "").upper().strip()

    if review_norm == "OVERRIDDEN":
        new_status = "Not Owned"
    elif action_norm in ("BUY", "BUY_LIMIT", "STRONG_BUY", "SPECULATIVE_BUY"):
        new_status = "Owned"
    else:
        new_status = "Not Owned"

    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE decision_points
               SET status = ?
               WHERE id = ? AND status = 'Pending DR Review'""",
            (new_status, decision_id),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        conn.close()
        return updated
    except Exception as e:
        print(f"[finalize_position_status_after_dr] error for decision_id={decision_id}: {e}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pending_dr_status.py -v`
Expected: 8 passed.

- [ ] **Step 5: Replace the immediate `Owned`/`Not Owned` assignment in `stock_service.py`**

In `app/services/stock_service.py:1571-1575`, replace:

```python
        # Update Status — gate on recommendation text, not score
        if "BUY" in recommendation.upper():
            status = "Owned"
        else:
            status = "Not Owned"
```

with:

```python
        # 3-state position lifecycle: BUY/BUY_LIMIT with R/R > 1.0 advances
        # to 'Pending DR Review' until deep research finalizes the verdict.
        # _should_trigger_deep_research mirrors this threshold so rows are
        # never stranded in the pending state.
        rec_upper = recommendation.upper()
        try:
            rr_value = float(report_data.get("risk_reward_ratio") or 0.0)
        except (TypeError, ValueError):
            rr_value = 0.0
        will_trigger_dr = self._should_trigger_deep_research(report_data)
        if rec_upper in ("BUY", "BUY_LIMIT", "STRONG BUY", "STRONG_BUY", "SPECULATIVE BUY", "SPECULATIVE_BUY") and will_trigger_dr and rr_value > 1.0:
            status = "Pending DR Review"
        elif "BUY" in rec_upper and rr_value > 1.0:
            # BUY-flavored verdict that won't trigger DR (edge case): leave
            # as Pending too so it surfaces in the UI as awaiting review.
            status = "Pending DR Review"
        else:
            status = "Not Owned"
```

- [ ] **Step 6: Lower the BUY_LIMIT DR trigger threshold so no row is stranded**

In `app/services/stock_service.py:603-624`, change the `_should_trigger_deep_research` body. Replace:

```python
        # BUY_LIMIT: trigger when risk/reward ratio exceeds 1.25
        if action == "BUY_LIMIT":
            try:
                if float(risk_reward) > 1.25:
                    return True
            except (TypeError, ValueError):
                return False
```

with:

```python
        # BUY_LIMIT: trigger when R/R > 1.0. The Pending DR Review status
        # gate uses the same threshold; raising it would strand rows.
        if action == "BUY_LIMIT":
            try:
                if float(risk_reward) > 1.0:
                    return True
            except (TypeError, ValueError):
                return False
```

- [ ] **Step 7: Wire `finalize_position_status_after_dr` into the DR worker**

In `app/services/deep_research_service.py`, at the end of `_apply_trading_level_overrides` (around line 803, just after `print(f"  >> {verdict_icon} ...")`), add:

```python
            # Atomically finalize the position status now that DR has resolved.
            # Mirrors the 3-state machine: Pending DR Review -> Owned / Not Owned.
            try:
                from app.database import finalize_position_status_after_dr
                review_verdict = result.get('review_verdict')
                final_action = result.get('action', action)
                advanced = finalize_position_status_after_dr(
                    decision_id=decision_id,
                    dr_action=final_action,
                    dr_review_verdict=review_verdict,
                )
                if advanced:
                    logger.info(
                        "[Deep Research] Finalized position status for %s "
                        "(action=%s, review_verdict=%s)",
                        symbol, final_action, review_verdict,
                    )
            except Exception as e:
                logger.error(
                    "[Deep Research] Failed to finalize position status for %s: %s",
                    symbol, e,
                )
```

- [ ] **Step 8: Add a one-shot backfill script for existing rows misclassified as `Owned`**

Create `scripts/backfill_pending_dr_status.py`:

```python
"""One-shot fix: rows that were marked 'Owned' before DR completed and are
still BUY/BUY_LIMIT but DR ultimately overrode to AVOID should be demoted
to 'Not Owned'. Mirrors the new 3-state machine retroactively."""
import os
import sqlite3

DB = os.getenv("DB_PATH", "subscribers.db")


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    # Demote rows where DR overrode to AVOID but status is still 'Owned'.
    cur.execute(
        """
        UPDATE decision_points
        SET status = 'Not Owned'
        WHERE status = 'Owned'
          AND (deep_research_review_verdict = 'OVERRIDDEN'
               OR deep_research_action IN ('AVOID', 'WATCH', 'HOLD', 'SELL'))
        """
    )
    print(f"[Backfill] Demoted {cur.rowcount} stale 'Owned' rows to 'Not Owned'.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
```

Run: `python scripts/backfill_pending_dr_status.py`
Expected: a "Demoted N stale 'Owned' rows" line.

- [ ] **Step 9: Verify dashboard / reassess script behavior**

`scripts/reassess_positions.py:53` filters on `status.upper() == "OWNED"`. Rows in `Pending DR Review` are correctly excluded — no change needed there. Verify by running:

```bash
python -c "from scripts.reassess_positions import _get_owned_positions; print(len(_get_owned_positions()))"
```

Expected: returns an integer (count of currently-owned positions). No exception.

- [ ] **Step 10: Run the targeted test suite**

Run: `pytest tests/test_pending_dr_status.py tests/ -k "v09 or stop_guard or deep_research" -v`
Expected: all pass. Note: `tests/test_v09_changes.py:181-191` asserts the legacy `BUY → Owned` mapping. That test exercises a hand-rolled `if "BUY" in rec.upper() else` block (not the production code), so it will still pass. If a test there directly drives `_run_deep_analysis`, update its expectation to `"Pending DR Review"` for BUY/BUY_LIMIT with R/R > 1.0.

- [ ] **Step 11: Commit**

```bash
git add app/database.py app/services/stock_service.py app/services/deep_research_service.py tests/test_pending_dr_status.py scripts/backfill_pending_dr_status.py
git commit -m "feat(status): gate Owned on DR completion via Pending DR Review intermediate state"
```

---

## Task 10: Final integration check

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -x -q`
Expected: all tests pass (or pre-existing skips/xfails only).

- [ ] **Step 2: Verify dashboard imports still load**

Run: `python -c "from main import app; print('OK')"`
Expected: `OK` and no exception.

- [ ] **Step 3: Tail-check git status**

Run: `git status && git log --oneline -10`
Expected: clean tree, 9 new commits on the branch.

- [ ] **Step 4: Update the changelog/postrun docs**

If `docs/proposals/PLAN_saxo_sim_smoke_test.md` or another active postrun-fixes doc exists in the working directory, append a one-line bullet noting these five fixes shipped, with the commit SHAs.

---

## Self-Review Notes

- **Spec coverage:** Bug 1 → Tasks 1–3. Bug 2 → Task 4. Bug 3 → Tasks 5–6. Bug 4 → Tasks 7–8. Bug 5 → Task 9. Final Task 10 = integration.
- **Bug 2 sub-points the user listed (`seeking_alpha_service.py:109` 2s and `deep_research_service.py:144` 60s):** the user explicitly noted the deep-research one is in a worker thread and is fine. The Seeking Alpha 2s sleep is also in a worker thread and is benign for the event loop; lowering it is a marginal latency improvement and is intentionally left out of this plan to keep scope tight.
- **Stop-guard Task 3 prompt edit:** the existing guard remains the safety net. Even if the prompt change does not fully eliminate the issue, Tasks 1–2 ensure the displayed/persisted R/R is correct after the guard fires.
- **Backfill script in Task 5:** clears already-misleading 🏆 flags from past runs. Safe to run repeatedly; only resets `batch_winner=1` rows whose DR action is AVOID.
- **EPS facts (Task 7) + consistency check (Task 8) compose:** Task 7 *prevents* the LLM from being misled (canonical numbers in the prompt). Task 8 *catches* the residual category errors deterministically. Both are needed: the prompt change won't fully eliminate hallucination of the wrong narrative direction, and the consistency check alone wouldn't have caught the TOST $0.20-vs-$0.15 numeric error if the actual was also a beat.
- **Word boundary regex in Task 8:** the test for "unbeatable" pins down a real false-positive risk. Other near-misses to consider when reading the regex: "beating expectations" (matches — correct), "missed the mark on guidance, but beat on EPS" (matches both — falls into the ambiguous-narrative bucket and passes through, which is the conservative behavior).
- **Finnhub `company_earnings` is rate-limited but cheap (60 req/min on free tier).** One call per analyzed ticker per cycle is well within budget. No new caching needed.
- **Pending DR Review (Task 9) reuses existing entry-zone overrides:** `deep_research_service._apply_trading_level_overrides` already overwrites `entry_price_low/high/stop_loss` on the row when DR completes (see lines 736-803). Downstream consumers reading those columns automatically get DR-adjusted values once the row transitions to `Owned`. No new "effective_entry_*" columns are needed — the bug was strictly about the `status` column, not the entry zone.
- **Why guard `finalize_position_status_after_dr` on `status = 'Pending DR Review'`:** if Task 8's earnings consistency check has already demoted the row to `Not Owned`, DR completing afterward must not silently re-promote it. The `WHERE status = 'Pending DR Review'` clause makes the transition strictly forward-only.
- **Lowering BUY_LIMIT DR threshold from 1.25 → 1.0 (Step 6):** without this, a BUY_LIMIT with 1.0 < R/R ≤ 1.25 would be marked `Pending DR Review` but never trigger DR — stranding the row. The two thresholds must match.
- **`tests/test_v09_changes.py`:** the parametric tests at lines 181-191 use a hand-rolled `"Owned" if "BUY" in rec.upper() else "Not Owned"` assertion against direct DB writes; they don't exercise `_run_deep_analysis`, so they continue to pass. If a future test does drive `_run_deep_analysis` end-to-end, update its expected status to `Pending DR Review` for BUY/BUY_LIMIT with R/R > 1.0.
