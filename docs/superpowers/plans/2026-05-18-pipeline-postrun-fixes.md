# Pipeline Post-Run Fixes (2026-05-18) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the 7 prioritized issues from the 2026-05-18 review: stop-guard display sanitization, Drive disable, DefeatBeta word-boundary match, trade-report column widths, threading shutdown, TEST screener guard + cleanup, and FM-fail downstream data hygiene.

**Architecture:** Mostly small, surgical changes to existing modules plus two new scripts. The stop-guard work (Tasks 1-4) is the centerpiece: extend the deterministic guard so an unreliable widened stop nulls the stop/R/R and flags the row, propagated through console panel, DB, and trade report. Other tasks are independent and can be executed in any order after Task 0.

**Tech Stack:** Python 3.9, pytest / pytest-asyncio, SQLite (`subscribers.db`), FastAPI, pandas. Spec: `docs/superpowers/specs/2026-05-18-pipeline-postrun-fixes-design.md`.

**Branch:** `fix/pipeline-postrun` (already checked out). FM-fix reference commit `0bbed4f` landed 2026-05-14 20:48 +0200.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `app/utils/stop_loss_guard.py` | Add candidate fields + `sanitize_unreliable_stop` helper | 1 |
| `app/services/research_service.py` | Wire sanitization (action-agnostic) + distribution log; suppress stop line; Phase1/2 executor shutdown | 2, 4, 8 |
| `app/database.py` | Add `stop_unreliable`, `stop_loss_raw` columns + persist them | 3 |
| `scripts/core/generate_trade_report.py` | Render unreliable stop as N/A, drop `[:N]` truncation | 4, 7 |
| `render.yaml` | `DRIVE_UPLOAD_ENABLED=false` | 5 |
| `app/services/stock_service.py` | Word-boundary transcript match; TEST screener guard | 6, 9 |
| `app/services/analytics/cohort.py` | Phantom/insufficient-data exclusion at load boundary | 10 |
| `scripts/analysis/evaluate_decisions.py` | Same exclusion for the direct-SQL path | 10 |
| `scripts/core/cleanup_test_rows.py` (new) | Dry-run-default deletion of `ticker='TEST'` rows | 9 |
| `scripts/analysis/verify_phantom_rows.py` (new) | Report pre-fix phantom AVOID/LOW row count | 10 |
| `tests/test_stop_loss_guard.py` | Extend: sanitize helper + candidate fields | 1 |
| `tests/test_research_service_stop_guard_recompute.py` | Extend: action-agnostic suppression | 2 |
| `tests/test_transcript_match.py` (new) | Word-boundary match cases | 6 |
| `tests/test_trade_report_format.py` (new) | Column-width + unreliable-stop rendering | 4, 7 |
| `tests/test_executor_shutdown.py` (new) | `cancel_futures` shutdown behavior | 8 |
| `tests/test_screener_test_guard.py` (new) | Screener skips TEST | 9 |
| `tests/test_phantom_filter.py` (new) | Phantom/insufficient-data exclusion | 10 |

---

## Task 0: Baseline — confirm tests run

**Files:** none (verification only)

- [ ] **Step 1: Run the existing stop-guard tests to establish a green baseline**

Run: `python -m pytest tests/test_stop_loss_guard.py tests/test_research_service_stop_guard_recompute.py -q`
Expected: PASS (these exist and passed at commit `1397815`). If they fail, STOP and report — the branch is not in a known-good state.

- [ ] **Step 2: Confirm pytest collects the suite without import errors**

Run: `python -m pytest --collect-only -q 2>&1 | tail -5`
Expected: a collected-count line, no `ERROR` lines.

---

## Task 1: Stop-guard — candidate fields + sanitize helper

**Files:**
- Modify: `app/utils/stop_loss_guard.py`
- Test: `tests/test_stop_loss_guard.py`

- [ ] **Step 1: Write failing tests for the new fields and helper**

Append to `tests/test_stop_loss_guard.py`:

```python
from app.utils.stop_loss_guard import (
    widen_stop_if_too_tight,
    sanitize_unreliable_stop,
)


def test_widen_exposes_candidate_values():
    adj = widen_stop_if_too_tight(
        stop_loss=135.0, entry_low=140.0, atr=3.0,
        sma_50=120.0, sma_200=59.93, bb_lower=130.0,
    )
    assert adj.adjusted is True
    assert adj.pm_stop == 135.0
    assert adj.atr_floor == 140.0 - 2.0 * 3.0   # 134.0
    assert adj.sma_floor == 120.0               # max sub-entry SMA


def test_sanitize_unreliable_stop_nulls_numbers():
    out = sanitize_unreliable_stop(entry_low=140.0, widened_stop=59.93)
    assert out["stop_unreliable"] is True
    assert out["stop_loss"] is None
    assert out["stop_loss_raw"] == 59.93
    assert out["downside_risk_percent"] is None
    assert out["risk_reward_ratio"] is None


def test_sanitize_acceptable_stop_is_passthrough():
    out = sanitize_unreliable_stop(entry_low=140.0, widened_stop=134.0)
    assert out is None  # within ceiling -> caller keeps existing values
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `python -m pytest tests/test_stop_loss_guard.py -q -k "candidate_values or unreliable_stop or passthrough"`
Expected: FAIL — `AttributeError: 'StopLossAdjustment' object has no attribute 'pm_stop'` / `ImportError: cannot import name 'sanitize_unreliable_stop'`.

- [ ] **Step 3: Add candidate fields to `StopLossAdjustment`**

In `app/utils/stop_loss_guard.py`, replace the dataclass (lines 22-26):

```python
@dataclass
class StopLossAdjustment:
    stop_loss: Optional[float]
    adjusted: bool
    reason: str
    pm_stop: Optional[float] = None
    atr_floor: Optional[float] = None
    sma_floor: Optional[float] = None
```

- [ ] **Step 4: Populate the new fields in every return path of `widen_stop_if_too_tight`**

In the same function, replace the early-return and final-return statements so each carries the candidates. Specifically:

Replace the `missing_stop` / `missing_atr` / `within_tolerance` returns:

```python
    if stop_loss is None:
        return StopLossAdjustment(None, False, "missing_stop", pm_stop=stop_loss)
    if not atr or atr <= 0:
        return StopLossAdjustment(stop_loss, False, "missing_atr", pm_stop=stop_loss)

    tolerance = entry_low - 1.5 * atr
    if stop_loss <= tolerance:
        return StopLossAdjustment(
            stop_loss, False, "within_tolerance", pm_stop=stop_loss,
        )

    # Candidate 1: 2x ATR below entry
    atr_floor = entry_low - 2.0 * atr

    # Candidate 2: nearest SMA below entry_low (= max of sub-entry SMAs)
    sma_candidates = [s for s in (sma_50, sma_200) if s is not None and s < entry_low]
    sma_floor = max(sma_candidates) if sma_candidates else None
```

Then replace the final `return StopLossAdjustment(stop_loss=round(new_stop, 2), adjusted=True, reason=reason)` with:

```python
    return StopLossAdjustment(
        stop_loss=round(new_stop, 2),
        adjusted=True,
        reason=reason,
        pm_stop=stop_loss,
        atr_floor=round(atr_floor, 2),
        sma_floor=round(sma_floor, 2) if sma_floor is not None else None,
    )
```

- [ ] **Step 5: Add the `sanitize_unreliable_stop` helper**

Append to `app/utils/stop_loss_guard.py` (after `evaluate_stop_acceptability`):

```python
def sanitize_unreliable_stop(
    entry_low: Optional[float], widened_stop: Optional[float]
) -> Optional[Dict[str, object]]:
    """If (entry_low, widened_stop) exceeds the downside ceiling, return the
    field overrides that blank the stop + R/R and flag the row. Returns None
    when the stop is acceptable (caller keeps existing values).
    """
    verdict = evaluate_stop_acceptability(entry_low, widened_stop)
    if verdict.acceptable:
        return None
    return {
        "stop_unreliable": True,
        "stop_loss": None,
        "stop_loss_raw": widened_stop,
        "downside_risk_percent": None,
        "risk_reward_ratio": None,
        "stop_unreliable_reason": verdict.reason,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_stop_loss_guard.py -q`
Expected: PASS (all, including the pre-existing ones).

- [ ] **Step 7: Commit**

```bash
git add app/utils/stop_loss_guard.py tests/test_stop_loss_guard.py
git commit -m "feat(stop-guard): expose widen candidates + sanitize_unreliable_stop helper"
```

---

## Task 2: Stop-guard — action-agnostic wiring + distribution log

**Files:**
- Modify: `app/services/research_service.py:548-596`
- Test: `tests/test_research_service_stop_guard_recompute.py`

- [ ] **Step 1: Write a failing test for action-agnostic suppression**

Append to `tests/test_research_service_stop_guard_recompute.py` (mirror the existing tests' construction of `final_decision` / `raw_data`; this test asserts the post-guard dict, so build the smallest fixture the existing tests use and add):

```python
def test_unreliable_stop_suppressed_even_when_action_avoid(monkeypatch):
    """A PM AVOID with a wildly-wide widened stop must still null stop + R/R
    and set stop_unreliable (previously gated on action.startswith('BUY'))."""
    from app.utils.stop_loss_guard import (
        widen_stop_if_too_tight, recompute_risk_metrics, sanitize_unreliable_stop,
    )
    final_decision = {
        "action": "AVOID", "conviction": "LOW",
        "entry_price_low": 140.0, "stop_loss": 139.0,
        "upside_percent": 9.3, "reason": "weak setup",
    }
    inds = {"atr": 3.0, "sma50": 120.0, "sma200": 59.93, "close": 140.0}

    guard = widen_stop_if_too_tight(
        stop_loss=final_decision["stop_loss"], entry_low=140.0,
        atr=inds["atr"], sma_50=inds["sma50"], sma_200=inds["sma200"],
        bb_lower=None,
    )
    final_decision["stop_loss"] = guard.stop_loss
    san = sanitize_unreliable_stop(140.0, guard.stop_loss)
    assert san is not None
    final_decision.update(san)

    assert final_decision["stop_unreliable"] is True
    assert final_decision["stop_loss"] is None
    assert final_decision["risk_reward_ratio"] is None
    assert final_decision["downside_risk_percent"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_research_service_stop_guard_recompute.py -q -k action_avoid`
Expected: FAIL — `ImportError: cannot import name 'sanitize_unreliable_stop'` is already resolved by Task 1, so this fails on the assertion only if Task 1 incomplete; if Task 1 done, this test should PASS at unit level. If it PASSES here, still proceed — Steps 3-5 wire it into the live path which the next test (Step 6) covers.

- [ ] **Step 3: Add the distribution log + import the helper**

In `app/services/research_service.py`, change the import block (lines 539-543) to:

```python
        from app.utils.stop_loss_guard import (
            widen_stop_if_too_tight,
            recompute_risk_metrics,
            evaluate_stop_acceptability,
            sanitize_unreliable_stop,
        )
```

Inside the `if _guard.adjusted:` block (after the existing `logger.info("[PM stop-guard] ...")`, ~line 561), add the distribution log:

```python
                logger.info(
                    "[stop-dist] %s pm=%s atr_floor=%s sma_floor=%s chosen=%s",
                    state.ticker, _guard.pm_stop, _guard.atr_floor,
                    _guard.sma_floor, _guard.stop_loss,
                )
```

- [ ] **Step 4: Replace the acceptability override (lines 577-596) with action-agnostic sanitization**

Replace the whole block from `acceptability = evaluate_stop_acceptability(` through the end of the `final_decision["reason"] = (...).strip()` assignment with:

```python
            _san = sanitize_unreliable_stop(
                entry_low=float(_entry_low),
                widened_stop=final_decision.get("stop_loss"),
            )
            if _san is not None:
                _reason = _san.pop("stop_unreliable_reason", "stop_too_wide")
                logger.warning(
                    "[Stop-acceptability] %s: %s — flagging stop_unreliable, "
                    "nulling stop + R/R, forcing AVOID/NONE.",
                    state.ticker, _reason,
                )
                print(
                    f"  > [Stop-acceptability] {state.ticker}: {_reason}. "
                    f"Stop unreliable — no tradable R/R panel."
                )
                final_decision.update(_san)
                final_decision["action"] = "AVOID"
                final_decision["conviction"] = "NONE"
                final_decision["rejected_reason"] = "stop_too_wide"
                _existing = final_decision.get("reason") or ""
                final_decision["reason"] = (
                    f"[STOP-REJECTED] {_reason}. {_existing}"
                ).strip()
```

(Note: this removes the `action.upper().startswith("BUY")` gate — sanitization now applies regardless of the PM's action.)

- [ ] **Step 5: Run the unit test to verify it passes**

Run: `python -m pytest tests/test_research_service_stop_guard_recompute.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py tests/test_research_service_stop_guard_recompute.py
git commit -m "feat(stop-guard): action-agnostic sanitize + per-ticker distribution log"
```

---

## Task 3: DB columns for stop_unreliable + stop_loss_raw

**Files:**
- Modify: `app/database.py` (new_columns dict ~line 76; trading_fields whitelist ~line 295)
- Test: `tests/test_db_stop_unreliable.py` (new)

- [ ] **Step 1: Write a failing test for the migration + persistence**

Create `tests/test_db_stop_unreliable.py`:

```python
import importlib
import sqlite3


def test_stop_unreliable_columns_exist(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(db))
    import app.database as database
    importlib.reload(database)
    database.DB_NAME = str(db)
    database.init_db()

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(decision_points)")}
    conn.close()
    assert "stop_unreliable" in cols
    assert "stop_loss_raw" in cols
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_db_stop_unreliable.py -q`
Expected: FAIL — `assert 'stop_unreliable' in cols`.

- [ ] **Step 3: Add the two columns to the migration dict**

In `app/database.py`, in the `new_columns` dict, immediately after `"risk_reward_ratio": "REAL",` (line 82) add:

```python
            # Stop-guard sanity floor (2026-05-18): when a widened stop implies
            # downside beyond MAX_ACCEPTABLE_DOWNSIDE_PCT, stop_loss is nulled,
            # the raw widened value is preserved here, and the row is flagged.
            "stop_unreliable": "INTEGER",
            "stop_loss_raw": "REAL",
```

- [ ] **Step 4: Add them to the `update_decision_point` whitelist**

In `app/database.py`, in the `trading_fields` list, after `"upside_percent", "downside_risk_percent", "risk_reward_ratio",` (line 295) add:

```python
            "stop_unreliable", "stop_loss_raw",
```

Note: `update_decision_point` skips `None` values (`if field in kwargs and kwargs[field] is not None`). When the stop is unreliable `stop_loss` is `None`, so the existing `stop_loss` is NOT overwritten to NULL by this path. To force the NULL, add — directly before the `query += " WHERE id = ?"` line (line 315):

```python
        if kwargs.get("stop_unreliable"):
            query += ", stop_loss = ?, downside_risk_percent = ?, risk_reward_ratio = ?"
            params.extend([None, None, None])
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_db_stop_unreliable.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_db_stop_unreliable.py
git commit -m "feat(db): add stop_unreliable + stop_loss_raw columns, force-NULL stop when flagged"
```

---

## Task 4: Display propagation — console panel + trade report

**Files:**
- Modify: `app/services/research_service.py:619` (stop-loss print line)
- Modify: `scripts/core/generate_trade_report.py` (stop column build)
- Test: `tests/test_trade_report_format.py` (new)

- [ ] **Step 1: Write a failing test for trade-report stop rendering**

Create `tests/test_trade_report_format.py`:

```python
from scripts.core import generate_trade_report as gtr


def test_format_stop_cell_unreliable():
    assert gtr.format_stop_cell(None, stop_unreliable=1) == "N/A (unreliable)"


def test_format_stop_cell_normal():
    assert gtr.format_stop_cell(134.0, stop_unreliable=0) == "134.00"


def test_format_stop_cell_missing():
    assert gtr.format_stop_cell(None, stop_unreliable=0) == "-"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_trade_report_format.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'format_stop_cell'`.

- [ ] **Step 3: Add `format_stop_cell` to the trade report module**

In `scripts/core/generate_trade_report.py`, add at module scope (above `def main`):

```python
def format_stop_cell(stop_loss, stop_unreliable) -> str:
    """Render the Stop column. Flagged rows show an explicit unreliable marker
    instead of the misleading raw widened value."""
    if stop_unreliable:
        return "N/A (unreliable)"
    if stop_loss is None:
        return "-"
    try:
        return f"{float(stop_loss):.2f}"
    except (TypeError, ValueError):
        return "-"
```

- [ ] **Step 4: Use it in the row dict and suppress R/R for flagged rows**

In `scripts/core/generate_trade_report.py`, in the R/R block (lines 324-331) wrap the existing logic so a flagged row prints `-`:

```python
        rr_val = d.get('risk_reward_ratio')
        if d.get('stop_unreliable'):
            rr_str = "-"
        elif rr_val is not None:
            try:
                rr_str = f"{float(rr_val):.2f}x"
            except (TypeError, ValueError):
                rr_str = "-"
        else:
            rr_str = "-"
```

Then add a `"Stop"` entry to the `row` dict (after the `"R/R": rr_str,` line):

```python
            "Stop": format_stop_cell(d.get('stop_loss'), d.get('stop_unreliable')),
```

And add `"Stop"` to the `headers` list (line 372) immediately after `"R/R"`:

```python
    headers = ["Date", "Symbol", "Market", "Rec", "R/R", "Stop", "Conv", "Drop Type", "Limit", "Price @ Dec", f"Price +{window_days}d", "Performance", f"Alpha vs SP500 {window_days}d", "Price +14d", "Perf 2W", "Price +28d", "Perf 4W", f"SP500 {window_days}d", f"Dow {window_days}d", f"DAX {window_days}d", "Verdict", "SA Rank", "Batch", "Status", "Evidence"]
```

- [ ] **Step 5: Suppress the misleading stop in the console panel**

In `app/services/research_service.py`, replace the Stop Loss print line (line 619):

```python
        _stop_disp = "N/A (unreliable)" if final_decision.get("stop_unreliable") else f"${final_decision.get('stop_loss', 'N/A')}"
        print(f"  Stop Loss: {_stop_disp} | TP1: ${final_decision.get('take_profit_1', 'N/A')} | TP2: ${final_decision.get('take_profit_2', 'N/A')}")
```

(The R/R block at lines 621-626 already calls `format_rr_block`, which renders `rr=None`/`downside=None` as `n/a` — no change needed there because Task 2 nulls those values.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_trade_report_format.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/research_service.py scripts/core/generate_trade_report.py tests/test_trade_report_format.py
git commit -m "feat(display): suppress misleading stop + R/R for stop_unreliable rows"
```

---

## Task 5: Drive — disable upload in deploy config

**Files:**
- Modify: `render.yaml`

- [ ] **Step 1: Inspect the current env block**

Run: `grep -n "envVars\|DRIVE_UPLOAD_ENABLED\|key:" render.yaml | head -20`
Expected: an `envVars:` section with `- key: ... value: ...` entries; no `DRIVE_UPLOAD_ENABLED` yet.

- [ ] **Step 2: Add the disable flag**

In `render.yaml`, under the existing `envVars:` list (match the existing indentation exactly — typically 6 spaces for `- key:`), add:

```yaml
      - key: DRIVE_UPLOAD_ENABLED
        value: "false"
```

- [ ] **Step 3: Verify YAML is still valid**

Run: `python -c "import yaml,sys; yaml.safe_load(open('render.yaml')); print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add render.yaml
git commit -m "chore(drive): disable Drive upload via DRIVE_UPLOAD_ENABLED=false (service-account quota)"
```

---

## Task 6: DefeatBeta — word-boundary transcript match

**Files:**
- Modify: `app/services/stock_service.py:1351-1353`
- Test: `tests/test_transcript_match.py` (new)

- [ ] **Step 1: Write failing tests for word-boundary matching**

Create `tests/test_transcript_match.py`:

```python
from app.services.stock_service import StockService

m = StockService._transcript_matches_company


def test_substring_false_positive_rejected():
    # "Arco" must NOT match because "marco" contains "arco"
    assert m("Welcome, this is the Marco Polo Group earnings call.", "Arco Platform Ltd") is False


def test_legit_full_name_matches():
    assert m("MP Materials Corp. fourth quarter earnings call", "MP Materials Corp.") is True


def test_legit_first_token_matches_on_boundary():
    assert m("Operator: Welcome to the Vodafone results presentation", "Vodafone Group Plc") is True


def test_short_normalized_name_rejected():
    # "(The)" normalizes to empty -> reject
    assert m("Some unrelated transcript text here", "(The)") is False


def test_empty_company_is_backward_compatible():
    assert m("anything", "") is True
```

- [ ] **Step 2: Run them to verify the false-positive test fails**

Run: `python -m pytest tests/test_transcript_match.py -q`
Expected: `test_substring_false_positive_rejected` FAILS (current substring logic returns True because `"arco" in "...marco..."`).

- [ ] **Step 3: Replace the substring match with word-boundary regex**

In `app/services/stock_service.py`, replace lines 1351-1353 (`# Match either the full stripped name...` through the `return` statement) with:

```python
        # Match the full stripped name or its first significant token, but
        # only on a word boundary — naive `in` containment caused false
        # positives ("arco" inside "marco", "mp" inside "company").
        def _has_word(term: str) -> bool:
            return re.search(r"\b" + re.escape(term) + r"\b", head) is not None

        first_token = expected_lower.split()[0]
        if _has_word(expected_lower):
            return True
        return len(first_token) >= 3 and _has_word(first_token)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_transcript_match.py -q`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add app/services/stock_service.py tests/test_transcript_match.py
git commit -m "fix(transcript): word-boundary company match, kills substring false positives"
```

---

## Task 7: Trade report — remove hard column truncation

**Files:**
- Modify: `scripts/core/generate_trade_report.py:339-340`
- Test: `tests/test_trade_report_format.py` (extend Task 4's file)

- [ ] **Step 1: Write a failing test for full conviction / drop-type rendering**

Append to `tests/test_trade_report_format.py`:

```python
def test_conviction_and_drop_type_not_truncated():
    import inspect
    src = inspect.getsource(gtr.main)
    # The hard slices [:4] / [:14] must be gone; dynamic width handles sizing.
    assert "(d.get('conviction') or \"-\")[:4]" not in src
    assert "(d.get('drop_type') or \"-\")[:14]" not in src
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_trade_report_format.py -q -k truncated`
Expected: FAIL — the slices are still present in the source.

- [ ] **Step 3: Remove the truncation slices**

In `scripts/core/generate_trade_report.py`, replace lines 339-340:

```python
            "Conv": d.get('conviction') or "-",
            "Drop Type": d.get('drop_type') or "-",
```

(The dynamic-width block at lines 373-377 already sizes every column to its widest value, so `MODERATE` / `COMPANY_SPECIFIC` now render in full.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_trade_report_format.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add scripts/core/generate_trade_report.py tests/test_trade_report_format.py
git commit -m "fix(trade-report): drop hard [:4]/[:14] truncation; rely on dynamic width"
```

---

## Task 8: Threading — non-blocking executor shutdown on Ctrl+C

**Files:**
- Modify: `app/services/research_service.py:284-307` (Phase 1), `:719-735` (Phase 2)
- Test: `tests/test_executor_shutdown.py` (new)

- [ ] **Step 1: Write a failing test asserting the shutdown helper exists and cancels**

Create `tests/test_executor_shutdown.py`:

```python
import concurrent.futures
import time

from app.services.research_service import _shutdown_executor


def test_shutdown_executor_cancels_pending():
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="t")
    running = ex.submit(time.sleep, 0.3)        # occupies the single worker
    pending = ex.submit(time.sleep, 5)          # queued, not started
    _shutdown_executor(ex)
    # Pending task must be cancelled, not awaited.
    assert pending.cancelled() is True
    running.result(timeout=2)                   # the in-flight one finishes fast
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_executor_shutdown.py -q`
Expected: FAIL — `ImportError: cannot import name '_shutdown_executor'`.

- [ ] **Step 3: Add the shutdown helper at module scope**

In `app/services/research_service.py`, near the top-level imports/helpers (after the existing imports, before the class), add:

```python
def _shutdown_executor(executor) -> None:
    """Drop pending futures immediately so a Ctrl+C during a scan doesn't block
    on the ThreadPoolExecutor's default wait=True join (Python 3.9 supports
    cancel_futures)."""
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:  # pragma: no cover - <3.9 fallback
        executor.shutdown(wait=False)
```

- [ ] **Step 4: Convert the Phase 1 executor to explicit construction + finally-shutdown**

In `app/services/research_service.py`, replace `with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:` (line 284) and its body so the `with` becomes try/finally:

```python
        # Increase max_workers to prevent starvation when agents hit 503 and retry
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="phase1"
        )
        try:
            futures = {
                executor.submit(run_agent, "Technical Agent", self._call_agent, tech_prompt, "Technical Agent", state): "technical",
                executor.submit(run_agent, "News Agent", self._call_agent, news_prompt, "News Agent", state): "news",
                executor.submit(run_agent, "Market Sentiment Agent", self._call_agent, sentiment_prompt, "Market Sentiment Agent", state): "sentiment",
                executor.submit(run_agent, "Competitive Landscape Agent", self._call_agent, comp_prompt, "Competitive Landscape Agent", state): "competitive",
                executor.submit(run_agent, "Seeking Alpha Agent", seeking_alpha_service.get_evidence, state.ticker): "seeking_alpha"
            }

            for future in concurrent.futures.as_completed(futures):
                agent_name, result = future.result()
                short = agent_short_names.get(agent_name, agent_name)
                completed_agents.append((short, _is_real_report(result)))

                if agent_name == "Technical Agent":
                    tech_report = result
                elif agent_name == "News Agent":
                    news_report = result
                elif agent_name == "Market Sentiment Agent":
                    sentiment_report = result
                elif agent_name == "Competitive Landscape Agent":
                    comp_report = result
                elif agent_name == "Seeking Alpha Agent":
                    sa_report = result
        except KeyboardInterrupt:
            _shutdown_executor(executor)
            raise
        finally:
            _shutdown_executor(executor)
```

- [ ] **Step 5: Convert the Phase 2 executor the same way**

In `app/services/research_service.py`, replace `with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:` (line 719) and its body:

```python
        phase2_completed = []
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=6, thread_name_prefix="phase2"
        )
        try:
            futures = {
                executor.submit(run_agent, "Bull Researcher", self._call_agent, bull_prompt, "Bull Researcher", state): "bull",
                executor.submit(run_agent, "Bear Researcher", self._call_agent, bear_prompt, "Bear Researcher", state): "bear",
                executor.submit(run_agent, "Risk Management Agent", self._call_agent, risk_prompt, "Risk Management Agent", state): "risk"
            }

            agent_short = {"Bull Researcher": "Bull", "Bear Researcher": "Bear", "Risk Management Agent": "Risk"}
            for future in concurrent.futures.as_completed(futures):
                agent_name, result = future.result()
                phase2_completed.append(agent_short.get(agent_name, agent_name))
                if agent_name == "Bull Researcher":
                    bull_report = result
                elif agent_name == "Bear Researcher":
                    bear_report = result
                elif agent_name == "Risk Management Agent":
                    risk_report = result
        except KeyboardInterrupt:
            _shutdown_executor(executor)
            raise
        finally:
            _shutdown_executor(executor)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/test_executor_shutdown.py -q`
Expected: PASS.

- [ ] **Step 7: Regression-check the research-service stop-guard tests still pass**

Run: `python -m pytest tests/test_research_service_stop_guard_recompute.py -q`
Expected: PASS (no behavioral change to the agent loop, only shutdown semantics).

- [ ] **Step 8: Commit**

```bash
git add app/services/research_service.py tests/test_executor_shutdown.py
git commit -m "fix(threading): cancel_futures executor shutdown so Ctrl+C exits cleanly"
```

---

## Task 9: TEST ticker — screener guard + cleanup script

**Files:**
- Modify: `app/services/stock_service.py:415-427` (dedup block)
- Create: `scripts/core/cleanup_test_rows.py`
- Test: `tests/test_screener_test_guard.py` (new)

- [ ] **Step 1: Write a failing test for the screener guard predicate**

Create `tests/test_screener_test_guard.py`:

```python
from app.services.stock_service import _is_synthetic_symbol


def test_bare_test_is_synthetic():
    assert _is_synthetic_symbol("TEST") is True
    assert _is_synthetic_symbol("test") is True


def test_underscore_symbol_is_synthetic():
    assert _is_synthetic_symbol("TEST_T3") is True


def test_real_ticker_is_not_synthetic():
    assert _is_synthetic_symbol("SHOP") is False
    assert _is_synthetic_symbol("BRK.B") is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_screener_test_guard.py -q`
Expected: FAIL — `ImportError: cannot import name '_is_synthetic_symbol'`.

- [ ] **Step 3: Add the predicate (mirrors analytics `_is_test_symbol`)**

In `app/services/stock_service.py`, add at module scope (near the top, after imports):

```python
import re as _re_synth

_BARE_TEST_RE = _re_synth.compile(r"^TEST$", _re_synth.IGNORECASE)


def _is_synthetic_symbol(symbol: object) -> bool:
    """Synthetic/placeholder symbols (bare 'TEST' or anything with '_') must
    never enter the pipeline or the DB. Mirrors analytics cohort filtering so
    the screener and analytics agree."""
    if symbol is None:
        return False
    s = str(symbol).strip()
    if not s:
        return False
    if "_" in s:
        return True
    return bool(_BARE_TEST_RE.match(s))
```

- [ ] **Step 4: Apply the guard in the dedup loop**

In `app/services/stock_service.py`, replace the dedup loop (lines 416-424) so synthetic symbols are skipped before insertion:

```python
        unique_stocks = {}
        for s in large_cap_movers:
            sym = s["symbol"]
            if _is_synthetic_symbol(sym):
                continue
            if sym not in unique_stocks:
                unique_stocks[sym] = s
            else:
                # Keep the one with more negative change (larger drop)
                if s["change_percent"] < unique_stocks[sym]["change_percent"]:
                    unique_stocks[sym] = s
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_screener_test_guard.py -q`
Expected: PASS.

- [ ] **Step 6: Create the cleanup script (dry-run by default)**

Create `scripts/core/cleanup_test_rows.py`:

```python
"""Delete synthetic TEST rows from decision_points. Dry-run by default.

Usage:
    python -m scripts.core.cleanup_test_rows            # report only
    python -m scripts.core.cleanup_test_rows --apply    # actually delete
"""
import argparse
import os
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Perform the delete. Without this flag, only reports.")
    args = parser.parse_args()

    db = os.getenv("DB_PATH", "subscribers.db")
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM decision_points "
            "WHERE UPPER(symbol) = 'TEST' OR symbol LIKE '%\\_%' ESCAPE '\\'"
        )
        n = cur.fetchone()[0]
        print(f"[cleanup_test_rows] {n} synthetic TEST/underscore rows found in {db}.")
        if not args.apply:
            print("[cleanup_test_rows] DRY RUN — pass --apply to delete.")
            return
        cur.execute(
            "DELETE FROM decision_points "
            "WHERE UPPER(symbol) = 'TEST' OR symbol LIKE '%\\_%' ESCAPE '\\'"
        )
        conn.commit()
        print(f"[cleanup_test_rows] Deleted {cur.rowcount} rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Smoke-test the cleanup script in dry-run mode against a temp DB**

Run:
```bash
python - <<'PY'
import os, sqlite3, tempfile, subprocess, sys
d = tempfile.mkdtemp(); db = os.path.join(d, "t.db")
c = sqlite3.connect(db)
c.execute("CREATE TABLE decision_points (symbol TEXT)")
c.executemany("INSERT INTO decision_points VALUES (?)", [("TEST",), ("TEST_T3",), ("SHOP",)])
c.commit(); c.close()
env = dict(os.environ, DB_PATH=db)
out = subprocess.run([sys.executable, "-m", "scripts.core.cleanup_test_rows"], env=env, capture_output=True, text=True)
print(out.stdout)
assert "2 synthetic" in out.stdout, out.stdout
assert "DRY RUN" in out.stdout
print("OK")
PY
```
Expected: prints `2 synthetic ...`, `DRY RUN`, `OK`.

- [ ] **Step 8: Commit**

```bash
git add app/services/stock_service.py scripts/core/cleanup_test_rows.py tests/test_screener_test_guard.py
git commit -m "fix(screener): skip synthetic TEST symbols; add dry-run cleanup script"
```

> SHOP-duplicate note: with the synthetic guard plus the existing symbol dedup (lines 416+), a genuine duplicate SHOP entry collapses to one row (largest drop kept). If SHOP still appears twice in a live run after this, it is entering via a code path that bypasses this dedup — record it as a follow-up; do NOT expand scope here.

---

## Task 10: FM-fail downstream — phantom exclusion + verification

**Files:**
- Modify: `app/services/analytics/cohort.py:50-82`
- Modify: `scripts/analysis/evaluate_decisions.py:126-127`
- Create: `scripts/analysis/verify_phantom_rows.py`
- Test: `tests/test_phantom_filter.py` (new)

- [ ] **Step 1: Write failing tests for the exclusion predicate**

Create `tests/test_phantom_filter.py`:

```python
from app.services.analytics.cohort import _is_phantom_or_insufficient


def test_pass_insufficient_data_excluded():
    assert _is_phantom_or_insufficient(
        recommendation="PASS_INSUFFICIENT_DATA", conviction="NONE",
        reasoning="x", decision_date="2026-05-17",
    ) is True


def test_pre_fix_phantom_avoid_low_excluded():
    assert _is_phantom_or_insufficient(
        recommendation="AVOID", conviction="LOW",
        reasoning="", decision_date="2026-05-10",
    ) is True


def test_real_avoid_low_after_fix_kept():
    assert _is_phantom_or_insufficient(
        recommendation="AVOID", conviction="LOW",
        reasoning="Valuation stretched; deteriorating fundamentals and weak guidance.",
        decision_date="2026-05-17",
    ) is False


def test_real_buy_kept():
    assert _is_phantom_or_insufficient(
        recommendation="BUY", conviction="HIGH",
        reasoning="strong setup", decision_date="2026-05-01",
    ) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_phantom_filter.py -q`
Expected: FAIL — `ImportError: cannot import name '_is_phantom_or_insufficient'`.

- [ ] **Step 3: Add the predicate to cohort.py**

In `app/services/analytics/cohort.py`, add after `_is_excluded_symbol` (line 43):

```python
# FM-fail honest-verdict fix landed in commit 0bbed4f on 2026-05-14. Before
# that, Fund Manager JSON-parse failures defaulted to a phantom AVOID/LOW with
# an empty/boilerplate reason. Such rows are not real signals.
FM_FIX_DATE = "2026-05-14"


def _is_phantom_or_insufficient(
    recommendation, conviction, reasoning, decision_date
) -> bool:
    rec = str(recommendation or "").strip().upper()
    if rec == "PASS_INSUFFICIENT_DATA":
        return True
    conv = str(conviction or "").strip().upper()
    rsn = str(reasoning or "").strip()
    try:
        before_fix = str(decision_date)[:10] < FM_FIX_DATE
    except Exception:
        before_fix = False
    # Pre-fix phantom signature: AVOID + LOW + empty/very short/parse-y reason.
    return (
        before_fix
        and rec == "AVOID"
        and conv == "LOW"
        and (len(rsn) < 40 or "parse" in rsn.lower())
    )
```

- [ ] **Step 4: Apply the filter at the load boundary**

In `app/services/analytics/cohort.py`, inside `load_cohort`, immediately after the existing test/excluded-symbol drop block (after line 77, before the `start_date` filter), add:

```python
    phantom_mask = df.apply(
        lambda r: _is_phantom_or_insufficient(
            r.get("recommendation"), r.get("conviction"),
            r.get("reasoning"), r.get("decision_date"),
        ),
        axis=1,
    )
    if phantom_mask.any():
        df = df.loc[~phantom_mask].reset_index(drop=True)
```

- [ ] **Step 5: Apply the same exclusion in the direct-SQL evaluator**

In `scripts/analysis/evaluate_decisions.py`, replace lines 126-127:

```python
    # Filter out SKIP recommendations and phantom / insufficient-data rows
    from app.services.analytics.cohort import _is_phantom_or_insufficient
    results = [
        r for r in results
        if r.get('recommendation') != 'SKIP'
        and not _is_phantom_or_insufficient(
            r.get('recommendation'), r.get('conviction'),
            r.get('reasoning'), r.get('timestamp'),
        )
    ]
```

- [ ] **Step 6: Run the predicate tests to verify they pass**

Run: `python -m pytest tests/test_phantom_filter.py -q`
Expected: PASS (all 4).

- [ ] **Step 7: Create the verification script**

Create `scripts/analysis/verify_phantom_rows.py`:

```python
"""Report pre-fix phantom AVOID/LOW rows so a human can decide filter vs backfill.

Read-only. Usage: python -m scripts.analysis.verify_phantom_rows
"""
import os
import sqlite3

from app.services.analytics.cohort import _is_phantom_or_insufficient


def main() -> None:
    db = os.getenv("DB_PATH", "subscribers.db")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT symbol, recommendation, conviction, reasoning, timestamp "
            "FROM decision_points"
        ).fetchall()
    finally:
        conn.close()

    phantom = [
        r for r in rows
        if _is_phantom_or_insufficient(
            r["recommendation"], r["conviction"], r["reasoning"], r["timestamp"]
        )
    ]
    pid = [r for r in phantom if str(r["recommendation"]).upper() == "PASS_INSUFFICIENT_DATA"]
    pre = [r for r in phantom if str(r["recommendation"]).upper() == "AVOID"]
    print(f"[verify_phantom_rows] DB={db}  total={len(rows)}")
    print(f"  PASS_INSUFFICIENT_DATA rows (post-fix, honest): {len(pid)}")
    print(f"  pre-fix phantom AVOID/LOW rows:                 {len(pre)}")
    for r in pre[:25]:
        print(f"    {r['timestamp'][:10]}  {r['symbol']:<8}  "
              f"reason='{(r['reasoning'] or '')[:50]}'")
    if len(pre) > 25:
        print(f"    ... +{len(pre) - 25} more")
    print("\nThese are now EXCLUDED from analytics (cohort + evaluate_decisions).")
    print("Backfill to PASS_INSUFFICIENT_DATA is optional and NOT done here.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Smoke-test the verification script against a temp DB**

Run:
```bash
python - <<'PY'
import os, sqlite3, tempfile, subprocess, sys
d = tempfile.mkdtemp(); db = os.path.join(d, "t.db")
c = sqlite3.connect(db)
c.execute("CREATE TABLE decision_points (symbol TEXT, recommendation TEXT, conviction TEXT, reasoning TEXT, timestamp TEXT)")
c.executemany("INSERT INTO decision_points VALUES (?,?,?,?,?)", [
  ("AAA","AVOID","LOW","","2026-05-10 09:00:00"),
  ("BBB","PASS_INSUFFICIENT_DATA","NONE","fm fail","2026-05-17 09:00:00"),
  ("CCC","BUY","HIGH","good","2026-05-01 09:00:00"),
])
c.commit(); c.close()
env = dict(os.environ, DB_PATH=db)
out = subprocess.run([sys.executable,"-m","scripts.analysis.verify_phantom_rows"], env=env, capture_output=True, text=True)
print(out.stdout, out.stderr)
assert "pre-fix phantom AVOID/LOW rows:                 1" in out.stdout, out.stdout
assert "PASS_INSUFFICIENT_DATA rows (post-fix, honest): 1" in out.stdout
print("OK")
PY
```
Expected: prints the counts (1 and 1) and `OK`.

- [ ] **Step 9: Commit**

```bash
git add app/services/analytics/cohort.py scripts/analysis/evaluate_decisions.py scripts/analysis/verify_phantom_rows.py tests/test_phantom_filter.py
git commit -m "fix(analytics): exclude phantom AVOID/LOW + PASS_INSUFFICIENT_DATA; add verify script"
```

---

## Task 11: Full regression + final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q 2>&1 | tail -15`
Expected: no new failures versus the Task 0 baseline. Pre-existing unrelated failures (if any existed at baseline) are acceptable; record them. New failures in any file this plan touched are NOT acceptable — fix before proceeding.

- [ ] **Step 2: Run the phantom-row verification against the live DB (read-only)**

Run: `python -m scripts.analysis.verify_phantom_rows`
Expected: prints counts. Record the two numbers in the final summary so the user can decide whether a backfill is warranted (the plan deliberately does NOT backfill).

- [ ] **Step 3: Confirm spec coverage**

Re-read `docs/superpowers/specs/2026-05-18-pipeline-postrun-fixes-design.md` Components A-G and confirm each maps to a committed task (A→1-4, B→5, C→6, D→7, E→8, F→9, G→10). Report any gap.

- [ ] **Step 4: Final summary commit (if any docs/notes changed)**

```bash
git status --short
```
If clean, nothing to commit. Otherwise stage only intentional changes and commit with a `chore:` message.

---

## Self-Review Notes

- **Spec coverage:** A→Tasks 1-4, B→5, C→6, D→7, E→8, F→9, G→10. All Components mapped. "Out of scope" items (OAuth, NLTK_DATA, mid-cycle regen, agent-quota scoping) intentionally absent.
- **Decision points resolved:** Component A helper placement → `sanitize_unreliable_stop` in `stop_loss_guard.py` (Task 1). Component G backfill-vs-filter → filter applied automatically; backfill deliberately deferred to a human decision after `verify_phantom_rows` (Task 10/11), matching the spec.
- **Type consistency:** `_is_phantom_or_insufficient` signature identical in cohort.py (def), evaluate_decisions.py (call), verify script (call), and tests. `_shutdown_executor`, `format_stop_cell`, `_is_synthetic_symbol`, `sanitize_unreliable_stop` names consistent across definition, call sites, and tests. `stop_unreliable` / `stop_loss_raw` column names consistent across stop_loss_guard, research_service, database, generate_trade_report.
- **No placeholders:** every code step shows full code; every run step shows the command + expected output.
