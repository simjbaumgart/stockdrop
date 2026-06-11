# Review Fixes v0.8.2-288 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six findings from the v0.8.2-288 code review: test pollution of the production DB, PM output truncation, stop-guard nonsense on AVOID verdicts, wasted Phase 1 council on preferred-share tickers, unauditable DR grounding-redirect URLs, and three minor hardening items.

**Architecture:** All fixes are small, surgical changes to existing modules. The only structural addition is an autouse pytest fixture in `tests/conftest.py` that makes it impossible for any test to write to the production `subscribers.db`, plus a pure-helper extraction in `stop_loss_guard.py` so the new skip rule is unit-testable.

**Tech Stack:** Python 3.9+, pytest, sqlite3, google-genai V2 SDK (`new_types.GenerateContentConfig`), requests.

**Ground rules (CLAUDE.md):** never run the full pytest suite in one process — run the targeted files named in each task. SQLite: one connection per thread. Commit after each task.

---

## Background facts (verified against the codebase, 2026-06-11)

- Polluted rows in production `subscribers.db` (repo root): ids 367–370, 375 (symbols `TEST`, `TEST_T3`, `TEST_T4`, `TEST_T5`, `TEST_T12`, dated 2026-05-09) and ids 771–778 (`DRTEST` ×6, `GATETEST`, `CLEANTEST`, dated 2026-06-10).
- `tests/conftest.py` exists but only defines an opt-in `temp_db` fixture — there is **no** autouse guard. Seven test modules mutate `app.database.DB_NAME` at import time; pytest imports all modules at collection, so the *last* module's assignment wins at run time and `monkeypatch` teardowns can restore `DB_NAME` to `"subscribers.db"` mid-suite. This is the pollution mechanism.
- `app/database.py:10`: `DB_NAME = os.getenv("DB_PATH", "subscribers.db")`. `deep_research_service._apply_trading_level_overrides` (line ~893) reads `os.getenv("DB_PATH", "subscribers.db")` **at call time** — a guard must set both the module attr and the env var.
- Stop guard: call site `app/services/research_service.py:818-875` (runs unconditionally when `entry_price_low` or `close` exists); widening logic `app/utils/stop_loss_guard.py:32-94` (`widen_stop_if_too_tight`, no cap on how far the SMA floor can drag the stop).
- PM grounded call config: `app/services/research_service.py:2213-2218` — `GenerateContentConfig(tools=..., temperature=0.7, http_options=...)`, **no `max_output_tokens`** (Gemini 3 thinking tokens count against the default output cap → recurring mid-JSON truncation). `finish_reason` is read at line 2239 but only checked for value 10 (FunctionCall).
- FM semantic check: `app/services/research_service.py:244-283` (`_fm_semantic_check`) — tolerates any key_factors count; AAOI's repaired 2-of-3 mangled factors passed.
- Source-depth gate: `research_service.py:722-736`, currently AFTER Phase 1. `_source_depth_insufficient` (line 1939) reads only `raw_data["seeking_alpha_local_counts"]` (set by `stock_service.py:1709` **before** `analyze_stock` is called) and `raw_data["news_items"]` — it can run before any agent is dispatched.
- Screener: `app/services/tradingview_service.py:116-172` (`get_top_movers`) — no filter for preferred-share tickers (TradingView notation `ORCL/PD`, i.e. symbol contains `/`).
- DR verification URLs: `deep_research_service.py:69-109` (`normalize_verification_results`), applied in `_handle_completion` at line ~659. No redirect resolution; `vertexaisearch.cloud.google.com/grounding-api-redirect/...` URLs persist as-is.
- DR Flash repair: `_parse_output` calls `_repair_json_using_flash(...)` (search for that call inside `_parse_output`); the raw unparseable text is not saved anywhere.
- News structured verdict already carries `drop_reason_confirmed` (bool) — parsed into `structured_verdicts["news"]` in `analyze_stock`; the gate call site is the `apply_decision_gates(...)` block (search for `apply_decision_gates(` in `research_service.py`).

---

### Task 1: One-off cleanup of test rows in the production DB

**Files:**
- Create: `scripts/core/cleanup_test_rows.py`

No TDD here — it is a one-off data fix, but it must be SELECT-first and idempotent.

- [ ] **Step 1: Write the cleanup script**

```python
"""One-off: delete test-fixture rows that leaked into the production DB.

v0.8.2-288 review finding #1: GATETEST/CLEANTEST/DRTEST (2026-06-10) and
TEST/TEST_T* (2026-05-09) rows sit in decision_points and pollute the trade
report. SELECT-first, prints what it deletes, idempotent.

Usage:
    python scripts/core/cleanup_test_rows.py            # dry-run (default)
    python scripts/core/cleanup_test_rows.py --execute  # actually delete
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.path.join(ROOT, "subscribers.db")

# LIKE '%TEST%' per the review, but list real-ticker exceptions explicitly
# if any ever exist (none today — no NYSE/Nasdaq ticker contains 'TEST').
SELECT_SQL = "SELECT id, symbol, DATE(timestamp), recommendation FROM decision_points WHERE symbol LIKE '%TEST%'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="delete instead of dry-run")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute(SELECT_SQL).fetchall()
    if not rows:
        print("No test rows found — nothing to do.")
        return 0
    print(f"{'DELETING' if args.execute else 'WOULD DELETE'} {len(rows)} rows:")
    for r in rows:
        print(f"  id={r[0]:>5}  {r[1]:<12} {r[2]}  {r[3]}")
    ids = [r[0] for r in rows]
    if args.execute:
        ph = ",".join("?" * len(ids))
        # children first (FK decision_id), then parents
        for table in ("decision_tracking", "agent_token_usage"):
            try:
                cur.execute(f"DELETE FROM {table} WHERE decision_id IN ({ph})", ids)
                print(f"  deleted {cur.rowcount} child rows from {table}")
            except sqlite3.OperationalError:
                pass  # table may not exist in this DB
        cur.execute(f"DELETE FROM decision_points WHERE id IN ({ph})", ids)
        conn.commit()
        remaining = cur.execute(SELECT_SQL).fetchall()
        print(f"Done. Remaining test rows: {len(remaining)}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Dry-run and inspect**

Run: `python3 scripts/core/cleanup_test_rows.py`
Expected: lists exactly the 13 known rows (ids 367-370, 375, 771-778), no real tickers.

- [ ] **Step 3: Execute and verify**

Run: `python3 scripts/core/cleanup_test_rows.py --execute`
Then: `sqlite3 subscribers.db "SELECT COUNT(*) FROM decision_points WHERE symbol LIKE '%TEST%'"`
Expected: `0`

- [ ] **Step 4: Regenerate the trade report so the CSV stops showing test rows**

Run: `python3 scripts/core/generate_trade_report.py` (same flags the hourly background task uses — check its `__main__` block; if it needs none, run bare).
Expected: `data/trade_report_full_7d.csv` no longer contains GATETEST/CLEANTEST/DRTEST lines: `grep -c "TEST" data/trade_report_full_7d.csv` → `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/core/cleanup_test_rows.py
git commit -m "chore(db): one-off cleanup of test-fixture rows leaked into production"
```

---

### Task 2: conftest autouse guard — tests can never write the production DB

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_gate_persistence.py` (drop import-time DB hacks)
- Modify: `tests/test_dr_override_basis.py` (comment only — see Step 5)
- Test: `tests/test_db_guard.py`

**Caveat discovered during planning:** importing `app.services.deep_research_service` runs the singleton's batch-winner sync, which performs DB writes **at import time** — before any fixture can run. Files that import it (like `test_dr_override_basis.py`) MUST keep their module-level `DB_PATH`/`DB_NAME` redirect; the autouse guard only protects test run time.

- [ ] **Step 1: Write the failing meta-test**

```python
# tests/test_db_guard.py
"""The autouse guard in conftest.py must ensure no test ever sees the
production subscribers.db as its database — regardless of import-order
games other test modules play with app.database.DB_NAME."""

import os


def test_db_name_never_production():
    import app.database as db
    assert os.path.basename(str(db.DB_NAME)) != "subscribers.db", (
        "app.database.DB_NAME points at production inside a test"
    )


def test_db_path_env_never_production():
    # deep_research_service._apply_trading_level_overrides reads DB_PATH
    # from the environment at call time — it must be redirected too.
    assert os.getenv("DB_PATH", "subscribers.db") != "subscribers.db"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_db_guard.py -q`
Expected: both tests FAIL (DB_NAME is "subscribers.db" without the guard).

- [ ] **Step 3: Add the autouse guard to tests/conftest.py**

Append to `tests/conftest.py` (keep the existing `temp_db` fixture):

```python
@pytest.fixture(autouse=True)
def _no_production_db(monkeypatch, tmp_path):
    """Safety net (v0.8.2-288 review #1): no test may touch subscribers.db.

    If the running test's module didn't already redirect app.database to its
    own test DB, point both the module attr and the DB_PATH env var (read at
    call time by deep_research_service) at a throwaway per-test file.
    monkeypatch auto-restores after each test, so cross-module leakage from
    import-time DB_NAME assignments can no longer land on production.
    """
    import app.database as db

    current = os.path.basename(str(db.DB_NAME))
    if current == "subscribers.db":
        guard_db = str(tmp_path / "guard.db")
        monkeypatch.setattr(db, "DB_NAME", guard_db)
        monkeypatch.setenv("DB_PATH", guard_db)
    elif os.getenv("DB_PATH", "subscribers.db") == "subscribers.db":
        # Module redirected DB_NAME but not the env var — align them.
        monkeypatch.setenv("DB_PATH", str(db.DB_NAME))
```

- [ ] **Step 4: Run the meta-test to verify it passes**

Run: `python3 -m pytest tests/test_db_guard.py -q`
Expected: 2 passed.

- [ ] **Step 5: Remove import-time DB mutation from the two recent polluters**

In `tests/test_gate_persistence.py` delete these module-level lines:

```python
TEST_DB = "test_gate_persistence.db"
os.environ["DB_PATH"] = TEST_DB
...
import app.database
app.database.DB_NAME = TEST_DB
```

and replace its `fresh_db` fixture with one that relies on the guard:

```python
@pytest.fixture()
def fresh_db(_no_production_db):
    from app.database import init_db
    init_db()  # runs against the guard's per-test tmp DB
    yield
```

(Delete the `os.path.exists(TEST_DB)`/`os.remove(TEST_DB)` lines — tmp_path cleans itself.)

Do **NOT** apply the same change to `tests/test_dr_override_basis.py` — it imports `deep_research_service`, whose singleton writes to the DB during import (batch-winner sync), before any fixture exists. Its module-level redirect is load-bearing. Instead, add this comment above its `TEST_DB` lines so nobody "cleans it up" later:

```python
# KEEP this import-time redirect: importing deep_research_service below runs
# the singleton's batch-winner sync, which WRITES to the DB at import time —
# before the conftest autouse guard can intervene.
```

- [ ] **Step 6: Run both refactored files plus the guard test together**

Run: `python3 -m pytest tests/test_db_guard.py tests/test_gate_persistence.py tests/test_dr_override_basis.py tests/test_v09_changes.py -q`
Expected: all pass.

- [ ] **Step 7: Prove no production writes happened**

Run: `sqlite3 subscribers.db "SELECT COUNT(*) FROM decision_points WHERE symbol LIKE '%TEST%'"`
Expected: `0` (rows were cleaned in Task 1 and the guarded run added none).

- [ ] **Step 8: Commit**

```bash
git add tests/conftest.py tests/test_db_guard.py tests/test_gate_persistence.py tests/test_dr_override_basis.py
git commit -m "test(db): autouse guard — tests can never write production subscribers.db"
```

---

### Task 3: Stop-guard — skip for non-buy verdicts, cap SMA widening at 3×ATR

**Files:**
- Modify: `app/utils/stop_loss_guard.py`
- Modify: `app/services/research_service.py:818-875` (guard call site)
- Test: `tests/test_stop_guard_cap_and_skip.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stop_guard_cap_and_skip.py
"""v0.8.2-288 review #3 (AAOI): the stop-guard widened 137.38 -> 73.15 via
SMA200 on an AVOID verdict, publishing -52% downside / R/R 0.1. Two rules:
the guard only runs for buy-side verdicts, and SMA widening is capped at
3x ATR below entry."""

from app.utils.stop_loss_guard import widen_stop_if_too_tight, should_run_stop_guard


def test_sma_widening_capped_at_3x_atr():
    # entry 137, ATR 5 -> hard floor 122. SMA200 way below at 73.15 must NOT win.
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=73.15, bb_lower=None,
    )
    assert adj.adjusted
    assert adj.stop_loss == 122.0          # entry - 3*ATR, not the distant SMA
    assert adj.reason == "capped_at_3x_atr"


def test_sma_within_cap_still_used():
    # SMA200 at 126 sits between 2x (127) and 3x (122) ATR floors -> keep SMA.
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=126.0, bb_lower=None,
    )
    assert adj.adjusted
    assert adj.stop_loss == 126.0
    assert adj.reason == "widened_to_sma_200"


def test_plain_2x_atr_widen_unaffected():
    adj = widen_stop_if_too_tight(
        stop_loss=136.0, entry_low=137.0, atr=5.0,
        sma_50=None, sma_200=None, bb_lower=None,
    )
    assert adj.stop_loss == 127.0
    assert adj.reason == "widened_to_2x_atr"


def test_should_run_stop_guard_only_for_buys():
    assert should_run_stop_guard("BUY")
    assert should_run_stop_guard("BUY_LIMIT")
    assert should_run_stop_guard("buy_limit")    # case-insensitive
    assert not should_run_stop_guard("AVOID")
    assert not should_run_stop_guard("WATCH")
    assert not should_run_stop_guard("")
    assert not should_run_stop_guard(None)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_stop_guard_cap_and_skip.py -q`
Expected: FAIL — `ImportError: cannot import name 'should_run_stop_guard'`.

- [ ] **Step 3: Implement in `app/utils/stop_loss_guard.py`**

Add after the `StopLossAdjustment` dataclass:

```python
# Verdicts the stop-guard applies to. For AVOID/WATCH no position is
# recommended, so widening the stop only distorts the displayed R/R
# (AAOI 2026-06-11: 137.38 -> 73.15, -52% downside on an AVOID).
def should_run_stop_guard(action: "Optional[str]") -> bool:
    return (action or "").strip().upper() in ("BUY", "BUY_LIMIT")


# Never let the SMA floor drag the stop further than 3x ATR below entry —
# beyond that the "stop" is a portfolio-risk statement, not a trade level.
MAX_WIDEN_ATR_MULT = 3.0
```

Then in `widen_stop_if_too_tight`, replace the final block (lines 73-94, from `if sma_floor is not None:` through the closing `return StopLossAdjustment(...)`) with:

```python
    if sma_floor is not None:
        # Pick the farther (lower) of the two floors so we don't pull the stop
        # back toward the entry when a nearby SMA is inside 2*ATR.
        new_stop = min(atr_floor, sma_floor)
        if new_stop == sma_floor and sma_floor == sma_50 and (sma_200 is None or sma_50 >= sma_200):
            reason = "widened_to_sma_50"
        elif new_stop == sma_floor and sma_floor == sma_200:
            reason = "widened_to_sma_200"
        else:
            reason = "widened_to_2x_atr"
    else:
        new_stop = atr_floor
        reason = "widened_to_2x_atr"

    # Cap: an SMA far below entry must not blow out the published downside.
    cap_floor = entry_low - MAX_WIDEN_ATR_MULT * atr
    if new_stop < cap_floor:
        new_stop = cap_floor
        reason = "capped_at_3x_atr"

    return StopLossAdjustment(
        stop_loss=round(new_stop, 2),
        adjusted=True,
        reason=reason,
        pm_stop=stop_loss,
        atr_floor=round(atr_floor, 2),
        sma_floor=round(sma_floor, 2) if sma_floor is not None else None,
    )
```

- [ ] **Step 4: Gate the call site in `research_service.py`**

At line ~826 (`_tv_inds = raw_data.get("indicators", {})`), import `should_run_stop_guard` in the existing `from app.utils.stop_loss_guard import (...)` statement, then change:

```python
        if _entry_low is not None:
```

to:

```python
        _pm_action = (final_decision.get("action") or "").strip().upper()
        if _entry_low is not None and should_run_stop_guard(_pm_action):
```

(The whole widen/recompute/acceptability block at lines 830-875 stays inside this `if` — AVOID/WATCH verdicts keep the PM's stop and R/R untouched.)

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_stop_guard_cap_and_skip.py tests/test_research_service_stop_guard_recompute.py tests/test_safety_stop.py -q`
Expected: all pass. If `test_research_service_stop_guard_recompute.py` fails because its fixtures use a non-buy action, update those fixtures to `action="BUY"` — the regression they pin is about widening math, not verdict routing.

- [ ] **Step 6: Commit**

```bash
git add app/utils/stop_loss_guard.py app/services/research_service.py tests/test_stop_guard_cap_and_skip.py
git commit -m "fix(stop-guard): skip non-buy verdicts, cap SMA widening at 3x ATR"
```

---

### Task 4: PM truncation — raise output cap, surface MAX_TOKENS, tighten semantic check

**Files:**
- Modify: `app/services/research_service.py:2213-2218` (config), `:2237-2242` (finish_reason), `:244-283` (`_fm_semantic_check`)
- Test: `tests/test_fm_semantic_check_factor_count.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fm_semantic_check_factor_count.py
"""v0.8.2-288 review #2 (AAOI): Flash repair of a truncated PM answer
returned 2 of 3 key_factors with mangled heads — and passed the semantic
check. The PM prompt mandates exactly 3 factors; a present-but-short list
is the truncation signature and must fail (-> triggers the re-prompt path)."""

from app.services.research_service import _fm_semantic_check

BASE = {"action": "BUY", "conviction": "HIGH"}


def test_two_factors_fail():
    ok, reason = _fm_semantic_check({**BASE, "key_factors": [
        "con firms the May 2026 $600M ATM filing remains an overhang",
        "Technical support held at the 50-day SMA",
    ]})
    assert not ok
    assert "key_factors" in reason


def test_three_factors_pass():
    ok, _ = _fm_semantic_check({**BASE, "key_factors": [
        "Earnings beat by 5.4% per canonical Finnhub facts",
        "Sector peers down in sympathy, attribution SECTOR",
        "Falling-knife flag NO from risk agent",
    ]})
    assert ok


def test_missing_or_empty_factors_still_tolerated():
    # Honest truncation BEFORE the key_factors field repairs to None/[] —
    # that remains acceptable (NIO 2026-05-22 precedent).
    assert _fm_semantic_check({**BASE})[0]
    assert _fm_semantic_check({**BASE, "key_factors": []})[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_fm_semantic_check_factor_count.py -q`
Expected: `test_two_factors_fail` FAILS (currently passes the check), other two pass.

- [ ] **Step 3: Implement the three changes**

(a) In `_fm_semantic_check`, after the per-item loop (`for item in factors or []:` block, line ~264-270), add:

```python
    # The PM prompt mandates exactly 3 key_factors. A present-but-shorter
    # list is the repair-after-truncation signature (AAOI 2026-06-11: 2 of 3
    # factors survived, heads mangled). None/[] stays tolerated above.
    if factors and len(factors) < _FM_MIN_KEY_FACTORS:
        return False, f"key_factors has {len(factors)} items (expected >= {_FM_MIN_KEY_FACTORS})"
```

and define next to the other `_FM_*` constants (search `_FM_PRICE_KEYS`):

```python
_FM_MIN_KEY_FACTORS = 3
```

(b) In `_call_grounded_model` (line 2213), add an explicit output cap — Gemini 3 thinking tokens count against the default cap, which is the likely truncation cause:

```python
            config = new_types.GenerateContentConfig(
                tools=[{"google_search": {}}],
                temperature=0.7,
                # Thinking tokens count against the output cap on Gemini 3;
                # the default repeatedly truncated PM JSON mid-string
                # (NXT, NIO, AAOI). 16384 leaves headroom for thinking + JSON.
                max_output_tokens=16384,
                # Use GenAI's typed config for HTTP options (timeout is in millis if integer on some SDK versions, but 600 ensures a long enough wait in any unit)
                http_options=new_types.HttpOptions(timeout=600000)
            )
```

(c) After `finish_reason = candidate.finish_reason if candidate else None` (line 2239), add visibility:

```python
            if finish_reason is not None and "MAX_TOKENS" in str(finish_reason):
                logger.warning(
                    "[%s] Output truncated at max_output_tokens (finish_reason=%s) — "
                    "downstream JSON is likely cut mid-string.",
                    agent_context, finish_reason,
                )
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_fm_semantic_check_factor_count.py tests/test_fm_repair_semantic_check.py tests/test_fund_manager_json_tolerant.py -q`
Expected: all pass. If `test_fm_repair_semantic_check.py` has fixtures with 1-2 key_factors that previously passed, update those fixtures to 3 factors (the old expectation is the bug).

- [ ] **Step 5: Commit**

```bash
git add app/services/research_service.py tests/test_fm_semantic_check_factor_count.py tests/test_fm_repair_semantic_check.py
git commit -m "fix(pm): 16k output cap + MAX_TOKENS warning + 3-factor semantic floor"
```

---

### Task 5: Source-depth gate before Phase 1 + preferred-ticker screener filter

**Files:**
- Modify: `app/services/research_service.py` (move block from :722-736 to before prompt prep at :455)
- Modify: `app/services/tradingview_service.py:165-172`
- Test: `tests/test_source_depth_pre_phase1.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_source_depth_pre_phase1.py
"""v0.8.2-288 review #4 (ORCL/PD): a thin-coverage preferred-share ticker
burned 5 Phase 1 agent calls before the source-depth gate fired. The gate
must run BEFORE any agent dispatch, and the screener must drop
preferred-share notation (symbol contains '/') outright."""

from types import SimpleNamespace

import pytest

import app.services.research_service as rs
from app.services.tradingview_service import exclude_non_common_tickers


def test_source_depth_aborts_before_any_agent_call(monkeypatch):
    svc = rs.ResearchService.__new__(rs.ResearchService)
    monkeypatch.setattr(svc, "_check_and_increment_usage", lambda: True)
    monkeypatch.setattr(
        rs.gatekeeper_service, "check_market_regime", lambda: None
    )

    def _boom(*a, **k):
        raise AssertionError("agent dispatched despite thin sources")

    monkeypatch.setattr(svc, "_call_agent", _boom)
    monkeypatch.setattr(svc, "_run_market_sentiment_cached", _boom)

    thin = {
        "change_percent": -6.0,
        "news_items": [],
        "seeking_alpha_local_counts": {"analysis": 0, "news": 0, "press_releases": 0},
        "indicators": {},
    }
    result = svc.analyze_stock("ORCLPD", thin, decision_id=None)
    assert result["recommendation"] == "PASS_INSUFFICIENT_DATA"
    assert result["aborted_reason"] == "insufficient_source_depth"


def test_exclude_non_common_tickers():
    movers = [
        {"symbol": "ORCL", "change_percent": -5.0},
        {"symbol": "ORCL/PD", "change_percent": -6.0},   # preferred series D
        {"symbol": "BRK.B", "change_percent": -5.5},     # share class dot is fine
    ]
    kept = exclude_non_common_tickers(movers)
    assert [m["symbol"] for m in kept] == ["ORCL", "BRK.B"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_source_depth_pre_phase1.py -q`
Expected: FAIL — `ImportError: cannot import name 'exclude_non_common_tickers'`; the abort test fails with the AssertionError from `_boom` (agents currently dispatch first).

- [ ] **Step 3: Move the gate in `research_service.py`**

Cut the block at lines 722-736 (`# --- Phase 1 Source-Depth Gate --- ... return response`) and paste it immediately after `drop_str` is computed (search for the line assigning `drop_str = ` below `drop_percent = raw_data.get('change_percent', -5.0)` at :457), changing `real_count=real_count` to `real_count=0` since no agents have run yet:

```python
        # --- Source-Depth Gate (pre-Phase 1) ---
        # SA/news counts are known before any agent dispatch
        # (stock_service populates seeking_alpha_local_counts) — abort thin
        # tickers HERE instead of burning a 5-agent council first
        # (ORCL/PD 2026-06-11 burned full Phase 1 before the old gate fired).
        depth_aborted, depth_reason = self._source_depth_insufficient(raw_data)
        if depth_aborted:
            msg = f"[ABORT] Source-depth gate failed for {state.ticker}: {depth_reason}"
            print(f"\n{'=' * 50}\n  {msg}\n{'=' * 50}\n")
            logger.error(msg)
            response = self._build_insufficient_data_response(
                state, failed_agents=["source_depth"], real_count=0
            )
            response["aborted_reason"] = "insufficient_source_depth"
            response["executive_summary"] = depth_reason
            return response
```

Delete the old block at its original location entirely (the Phase-1 liveness gate directly above it stays).

- [ ] **Step 4: Add the screener filter in `tradingview_service.py`**

Add a module-level function (above the class or next to other helpers):

```python
def exclude_non_common_tickers(movers: List[Dict]) -> List[Dict]:
    """Drop preferred shares / warrants (TradingView notation contains '/',
    e.g. ORCL/PD). They share the common stock's news flow but have their own
    price series — analyzing them burns a full council for an untradable row."""
    kept = [m for m in movers if "/" not in (m.get("symbol") or "")]
    skipped = len(movers) - len(kept)
    if skipped:
        print(f"  > Screener: skipped {skipped} preferred/warrant ticker(s) (symbol contains '/').")
    return kept
```

Then in `get_top_movers`, before deduplication (line ~165):

```python
        all_movers = exclude_non_common_tickers(all_movers)

        # Deduplicate by symbol (just in case) and sort
        unique_movers = {m['symbol']: m for m in all_movers}.values()
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_source_depth_pre_phase1.py tests/test_us_only.py tests/test_tv_exchange_resolver.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py app/services/tradingview_service.py tests/test_source_depth_pre_phase1.py
git commit -m "fix(pipeline): source-depth gate before Phase 1; screener drops preferred tickers"
```

---

### Task 6: Resolve DR grounding-redirect URLs before persisting

**Files:**
- Modify: `app/services/deep_research_service.py` (new function next to `normalize_verification_results` at :69; call site in `_handle_completion` at the `normalized = normalize_verification_results(...)` line)
- Test: `tests/test_dr_redirect_resolution.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dr_redirect_resolution.py
"""v0.8.2-288 review #5 (IT): vertexaisearch.cloud.google.com
grounding-api-redirect URLs were persisted to JSON/DB — they expire and are
unauditable. Resolve to the final URL before saving; keep the original on
any failure."""

from types import SimpleNamespace

import app.services.deep_research_service as drs
from app.services.deep_research_service import resolve_redirect_urls

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc123"


def _entry(url):
    return {"claim": "c", "verdict": "VERIFIED", "source_url": url}


def test_redirect_resolved_and_original_kept(monkeypatch):
    def fake_get(url, allow_redirects, timeout, stream):
        assert url == REDIRECT
        return SimpleNamespace(url="https://www.reuters.com/article/real", close=lambda: None)

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry(REDIRECT)])
    assert out[0]["source_url"] == "https://www.reuters.com/article/real"
    assert out[0]["grounding_redirect"] == REDIRECT


def test_non_redirect_urls_untouched(monkeypatch):
    def fake_get(*a, **k):
        raise AssertionError("must not fetch non-redirect URLs")

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry("https://example.com/x")])
    assert out[0]["source_url"] == "https://example.com/x"
    assert "grounding_redirect" not in out[0]


def test_failure_keeps_original(monkeypatch):
    def fake_get(*a, **k):
        raise drs.requests.RequestException("boom")

    monkeypatch.setattr(drs.requests, "get", fake_get)
    out = resolve_redirect_urls([_entry(REDIRECT)])
    assert out[0]["source_url"] == REDIRECT


def test_lookup_budget_capped(monkeypatch):
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return SimpleNamespace(url="https://resolved.example/x", close=lambda: None)

    monkeypatch.setattr(drs.requests, "get", fake_get)
    entries = [_entry(REDIRECT) for _ in range(20)]
    resolve_redirect_urls(entries, max_lookups=5)
    assert len(calls) == 5  # the rest keep the redirect URL, bounded wall-clock
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_dr_redirect_resolution.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_redirect_urls'`.

- [ ] **Step 3: Implement in `deep_research_service.py`**

Add directly below `normalize_verification_results` (line ~109):

```python
_GROUNDING_REDIRECT_PREFIX = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"


def resolve_redirect_urls(entries, timeout: int = 8, max_lookups: int = 12):
    """Replace Vertex grounding-redirect source URLs with their resolved
    destination (review v0.8.2-288 #5: redirects expire and are unauditable).

    Bounded: at most `max_lookups` HTTP round-trips, `timeout`s each, so a
    redirect-heavy result can't stall the DR worker thread. Failures keep the
    original URL. The redirect is preserved in `grounding_redirect` for audit.
    """
    out = []
    budget = max_lookups
    for entry in entries or []:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        url = (entry.get("source_url") or "").strip()
        if url.startswith(_GROUNDING_REDIRECT_PREFIX) and budget > 0:
            budget -= 1
            try:
                resp = requests.get(url, allow_redirects=True, timeout=timeout, stream=True)
                final_url = resp.url
                resp.close()
                if final_url and final_url != url:
                    entry = {**entry, "source_url": final_url, "grounding_redirect": url}
            except requests.RequestException as e:
                logger.warning("[Deep Research] Could not resolve grounding redirect: %s", e)
        out.append(entry)
    return out
```

Then at the call site in `_handle_completion` (search for `normalized = normalize_verification_results`), change:

```python
        normalized = normalize_verification_results(result.get("verification_results", []))
```

to:

```python
        normalized = resolve_redirect_urls(
            normalize_verification_results(result.get("verification_results", []))
        )
```

(`requests` is already imported at module top; verify with `grep -n "^import requests" app/services/deep_research_service.py`.)

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_dr_redirect_resolution.py tests/test_dr_override_basis.py tests/test_deep_research_parse_shapes.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/services/deep_research_service.py tests/test_dr_redirect_resolution.py
git commit -m "fix(dr): resolve grounding-redirect URLs before persisting verification results"
```

---

### Task 7: Minors — DR raw-output logging on repair + unconfirmed-drop gate

**Files:**
- Modify: `app/services/deep_research_service.py` (`_parse_output`, before the `_repair_json_using_flash(` call)
- Modify: `app/services/decision_gate_service.py`
- Modify: `app/services/research_service.py` (the `apply_decision_gates(...)` call)
- Test: `tests/test_decision_gate_service.py` (extend)

- [ ] **Step 1: Write the failing gate tests**

Append to `tests/test_decision_gate_service.py`:

```python
# ---------------------------------------------------------------------------
# Gate 6: unconfirmed drop reason
# ---------------------------------------------------------------------------

def test_gate6_unconfirmed_drop_demotes_buy_to_limit():
    r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None,
                             news_drop_reason_confirmed=False)
    assert r.final_action == "BUY_LIMIT"
    assert r.gates_fired == ["UNCONFIRMED_DROP_GATE"]


def test_gate6_confirmed_or_unknown_passes():
    for val in (True, None):
        r = apply_decision_gates("BUY", "SECTOR_ROTATION", "HIGH", None,
                                 news_drop_reason_confirmed=val)
        assert r.gates_fired == []


def test_gate6_does_not_touch_buy_limit():
    r = apply_decision_gates("BUY_LIMIT", "SECTOR_ROTATION", "HIGH", None,
                             news_drop_reason_confirmed=False)
    assert r.gates_fired == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_decision_gate_service.py -q`
Expected: new tests FAIL with `TypeError: unexpected keyword argument 'news_drop_reason_confirmed'`.

- [ ] **Step 3: Implement Gate 6 in `decision_gate_service.py`**

Add the parameter to `apply_decision_gates` after `news_named_catalyst`:

```python
    news_named_catalyst: Optional[str] = None,
    news_drop_reason_confirmed: Optional[bool] = None,
) -> GateResult:
```

Add the gate logic directly after the Gate 5 (NEWS_SENTIMENT_GATE) block, before `if targets:`:

```python
    # Gate 6: the News agent explicitly could NOT confirm why the stock
    # dropped (drop_reason_confirmed=False, PTC 2026-06-11 went BUY anyway).
    # An immediate BUY on an unexplained drop becomes a limit order; None
    # (unparsed verdict) never fires.
    if news_drop_reason_confirmed is False and pre_gate == "BUY":
        targets.append("BUY_LIMIT")
        result.gates_fired.append("UNCONFIRMED_DROP_GATE")
        result.gate_reasons.append(
            "News agent could not confirm the drop reason — no immediate entry"
        )
```

Also add one line to the module docstring's gate list:

```
  * Gate 6 (UNCONFIRMED_DROP_GATE): BUY on a drop whose reason the News
    agent explicitly could not confirm is demoted to BUY_LIMIT.
```

- [ ] **Step 4: Wire it at the call site in `research_service.py`**

In the `apply_decision_gates(...)` call inside `analyze_stock`, add after `news_named_catalyst=...`:

```python
            news_drop_reason_confirmed=news_verdict.get("drop_reason_confirmed"),
```

- [ ] **Step 5: Add DR raw-output dump before Flash repair**

In `deep_research_service._parse_output`, immediately before the line that calls `self._repair_json_using_flash(` (search for it), insert:

```python
        # Repair rate was 2/3 results in run v0.8.2-288 — persist the raw
        # output so the failure mode (truncation vs markdown vs prose) is
        # diagnosable instead of guessing from the repaired JSON.
        try:
            os.makedirs(os.path.join("data", "parser_failures"), exist_ok=True)
            _dump = os.path.join(
                "data", "parser_failures",
                f"dr_raw_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.txt",
            )
            with open(_dump, "w") as _f:
                _f.write(text or "")
            logger.warning(
                "[Deep Research] Raw output needed Flash repair (len=%d, tail=%r) — saved to %s",
                len(text or ""), (text or "")[-120:], _dump,
            )
        except Exception as _e:
            logger.warning("[Deep Research] Could not dump raw output: %s", _e)
```

(Check the local variable name holding the raw text in `_parse_output` — it is `text` after citation cleaning; if the surrounding code uses a different name at that point, use that name. Verify `from datetime import datetime` / `import os` exist at module top; both are already imported.)

- [ ] **Step 6: Run tests**

Run: `python3 -m pytest tests/test_decision_gate_service.py tests/test_deep_research_parse_shapes.py tests/test_gate_persistence.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/decision_gate_service.py app/services/research_service.py app/services/deep_research_service.py tests/test_decision_gate_service.py
git commit -m "feat(gates): Gate 6 unconfirmed-drop demotion; log DR raw output before repair"
```

---

## Out of scope / user actions

- **SA grades CSV refresh** (review #6c): the export is 16+ days old. This is a manual browser flow — run the `sa-score-extract` skill in a Claude session to regenerate `data/SAgrades/SA_Quant_Ranked_Clean.csv`. No code change.
- Root-causing the exact import-order interleaving that wrote to production during full-suite runs: the Task 2 guard makes every interleaving safe; forensics would add no protection.
- Refactoring the five other legacy test modules that mutate `DB_NAME` at import — the guard covers them; touch them only if they fail.

## Final verification (after all tasks)

```bash
python3 -m pytest tests/test_db_guard.py tests/test_gate_persistence.py tests/test_dr_override_basis.py \
  tests/test_stop_guard_cap_and_skip.py tests/test_fm_semantic_check_factor_count.py \
  tests/test_source_depth_pre_phase1.py tests/test_dr_redirect_resolution.py \
  tests/test_decision_gate_service.py tests/test_v09_changes.py -q
sqlite3 subscribers.db "SELECT COUNT(*) FROM decision_points WHERE symbol LIKE '%TEST%'"   # -> 0
python3 -c "import main"   # app still boots
```
