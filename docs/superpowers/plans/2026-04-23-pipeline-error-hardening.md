# Pipeline Error Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five high-impact reliability bugs exposed by the 04-22 and 04-23 pipeline runs: silent data loss on slash-containing tickers, fake-high-conviction decisions on truncated Phase-1 reports, Deep Research silently overriding PM verdicts when JSON parsing fails, unbounded agent retry stalls, and noisy Google Drive quota errors.

**Architecture:** Five focused changes across three services and one helper. Each task is independently shippable and independently testable. Ordered by impact-per-line-of-code: ticker-path sanitization first (5 lines, prevents data loss), then the short-report hard fail (preserves decision integrity on BBY-style outages), then Deep Research parse-failure handling (preserves PM verdict when repair fails), then a wall-clock budget on agent retries (prevents QXO/PB-style multi-hour stalls), then the Drive quota gate (silences the log spam).

**Tech Stack:** Python 3.9, pytest, pytest-asyncio. Touches `app/services/research_service.py`, `app/services/quality_control_service.py`, `app/services/deep_research_service.py`, `app/services/drive_service.py`, plus one new helper `app/utils/ticker_paths.py`.

---

## File Structure

### New files

- `app/utils/ticker_paths.py` — single-responsibility helper that sanitizes ticker symbols for use in file paths. One function, one test file.
- `tests/test_ticker_paths.py` — unit tests for the helper.
- `tests/test_deep_research_parse_fallback.py` — unit tests for the new parse-failure behavior in `deep_research_service._parse_output`.
- `tests/test_phase1_hard_fail.py` — integration test that a Phase-1 run with every agent truncated produces an abstention, not a HIGH-conviction PM decision.
- `tests/test_agent_wall_clock_budget.py` — unit test that `_call_grounded_model` gives up once the wall-clock budget is exhausted, even if retries remain.

### Modified files

- `app/services/research_service.py` — replace three ad-hoc path strings with the sanitizer; tighten the Phase-1 quality gate; add a per-agent wall-clock budget to `_call_grounded_model`.
- `app/services/quality_control_service.py` — align the short-report threshold with `_is_real_report` (both 200 chars) and make the marker easier to assert on.
- `app/services/deep_research_service.py` — bump the repair timeout from 30s to 90s; on parse failure, return `PENDING_REVIEW` without clobbering the PM's action.
- `app/services/drive_service.py` — suppress the "Error creating spreadsheet" log when the breaker is tripped; add a `DRIVE_UPLOAD_ENABLED` env gate that short-circuits at init.
- `app/database.py` — add a migration for `deep_research_verdict = 'PENDING_REVIEW'` (no schema change, just a value documentation comment) and confirm existing code paths don't treat PENDING_REVIEW as ERROR_PARSING.
- `scripts/run_deep_research_backfill.py` — use the sanitizer when reading news-context files, so backfills work for slash tickers.

---

## Task 1: Ticker-path sanitization helper

**Why first:** QXO/PB silently lost Council 1, Council 2, and news-context files on every run. Fix is ~5 lines of logic; payoff is "we never drop another report." Touching the helper first lets the other tasks consume it immediately.

**Files:**
- Create: `app/utils/ticker_paths.py`
- Create: `tests/test_ticker_paths.py`
- Modify: `app/services/research_service.py:293-298` (council1 save)
- Modify: `app/services/research_service.py:328-333` (council2 save)
- Modify: `app/services/research_service.py:693-700` (news-context log)
- Modify: `app/services/deep_research_service.py:1605-1607` (council1 read)
- Modify: `app/services/stock_service.py:720-721` (council1/council2 read)
- Modify: `scripts/run_deep_research_backfill.py:183` (news-context read)

---

- [ ] **Step 1: Write the failing test for the sanitizer**

Create `tests/test_ticker_paths.py`:

```python
from app.utils.ticker_paths import safe_ticker_path


def test_plain_ticker_unchanged():
    assert safe_ticker_path("AAPL") == "AAPL"


def test_slash_replaced_with_underscore():
    assert safe_ticker_path("QXO/PB") == "QXO_PB"


def test_backslash_replaced():
    assert safe_ticker_path("FOO\\BAR") == "FOO_BAR"


def test_path_separator_collisions_stripped():
    # os.sep is '/' on posix, '\\' on win — both must be handled.
    assert "/" not in safe_ticker_path("A/B/C")
    assert "\\" not in safe_ticker_path("A\\B\\C")


def test_none_or_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        safe_ticker_path("")
    with pytest.raises(ValueError):
        safe_ticker_path(None)  # type: ignore[arg-type]


def test_dots_and_dashes_preserved():
    # BRK.B, BRK-B etc. are valid file-name characters and common ticker styles.
    assert safe_ticker_path("BRK.B") == "BRK.B"
    assert safe_ticker_path("BRK-B") == "BRK-B"
```

- [ ] **Step 2: Run test, verify it fails with ImportError**

Run: `pytest tests/test_ticker_paths.py -v`
Expected: `ModuleNotFoundError: No module named 'app.utils.ticker_paths'`

- [ ] **Step 3: Implement the helper**

Create `app/utils/ticker_paths.py`:

```python
"""Helpers for turning ticker symbols into filesystem-safe identifiers.

Some tickers contain characters (notably '/') that are interpreted as
directory separators when interpolated into file paths. This module
provides a single sanitizer used everywhere the pipeline writes or reads
ticker-keyed files, so we never silently drop a report again.
"""


def safe_ticker_path(ticker: str) -> str:
    """Return a filesystem-safe version of *ticker*.

    Replaces any path-separator characters with underscores. Other
    characters (letters, digits, '.', '-') are preserved, which covers
    every ticker format we currently see (AAPL, BRK.B, BRK-B, QXO/PB).

    Raises ValueError on empty or None input — ticker must always be set.
    """
    if not ticker or not isinstance(ticker, str):
        raise ValueError(f"safe_ticker_path requires a non-empty ticker, got {ticker!r}")
    return ticker.replace("/", "_").replace("\\", "_")
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `pytest tests/test_ticker_paths.py -v`
Expected: 6 passed.

- [ ] **Step 5: Apply sanitizer at the three write sites in `research_service.py`**

In `app/services/research_service.py`, add near the top of the file, next to the other `app.services` imports:

```python
from app.utils.ticker_paths import safe_ticker_path
```

Then modify line 293-298 (Council 1 save):

```python
        # --- Save Council 1 Output to JSON ---
        try:
            council_dir = "data/council_reports"
            os.makedirs(council_dir, exist_ok=True)
            council_file = f"{council_dir}/{safe_ticker_path(state.ticker)}_{state.date}_council1.json"

            with open(council_file, "w") as f:
                json.dump(state.reports, f, indent=4)

            print(f"  > [System] AI Council 1 Reports saved to {council_file}")
        except Exception as e:
            logger.error(f"Failed to save Council 1 reports: {e}")
```

Modify line 328-333 (Council 2 save):

```python
        # --- Save Council 2 Output to JSON (Phase 1 + Phase 2: bull/bear/risk) ---
        try:
            council_dir = "data/council_reports"
            os.makedirs(council_dir, exist_ok=True)
            council2_file = f"{council_dir}/{safe_ticker_path(state.ticker)}_{state.date}_council2.json"

            with open(council2_file, "w") as f:
                json.dump(state.reports, f, indent=4)

            print(f"  > [System] AI Council 2 Reports (Phase 1+2) saved to {council2_file}")
        except Exception as e:
            logger.error(f"Failed to save Council 2 reports: {e}")
```

Modify line 693-700 (News-context log):

```python
        # --- LOGGING NEWS CONTEXT ---
        try:
            log_dir = "data/news"
            os.makedirs(log_dir, exist_ok=True)
            log_file = f"{log_dir}/{safe_ticker_path(state.ticker)}_{state.date}_news_context.txt"

            with open(log_file, "w") as f:
                f.write(f"NEWS CONTEXT FOR {state.ticker} ({state.date})\n")
                f.write("==================================================\n\n")
                f.write(news_summary)

            print(f"  > [News Agent] Logged news context to {log_file}")
        except Exception as e:
            print(f"  > [News Agent] Error logging news context: {e}")
```

- [ ] **Step 6: Apply sanitizer at the three read sites**

In `app/services/deep_research_service.py` around line 1605-1607, replace the `council1` path with:

```python
            from app.utils.ticker_paths import safe_ticker_path
            # Pattern: data/council_reports/{safe_symbol}_{date}_council1.json
            expected_file = os.path.join(
                report_dir, f"{safe_ticker_path(symbol)}_{date_str}_council1.json"
            )
```

In `app/services/stock_service.py` around line 720-721:

```python
                from app.utils.ticker_paths import safe_ticker_path
                _safe = safe_ticker_path(symbol)
                council1_path = f"{council_dir}/{_safe}_{date_str}_council1.json"
                council2_path = f"{council_dir}/{_safe}_{date_str}_council2.json"
```

In `scripts/run_deep_research_backfill.py` around line 183:

```python
    from app.utils.ticker_paths import safe_ticker_path
    news_path = f"data/news/{safe_ticker_path(symbol)}_{date_str}_news_context.txt"
```

- [ ] **Step 7: Write an integration test that a QXO/PB run produces real files**

Append to `tests/test_ticker_paths.py`:

```python
import os
import tempfile
from unittest.mock import patch


def test_safe_ticker_path_produces_writable_filename(tmp_path):
    """Sanity-check: the sanitized name is usable as a single path component."""
    from app.utils.ticker_paths import safe_ticker_path

    sanitized = safe_ticker_path("QXO/PB")
    target = tmp_path / f"{sanitized}_2026-04-22_council1.json"
    target.write_text("{}")
    assert target.exists()
    # And it's a single file, not a nested directory.
    assert target.parent == tmp_path
```

Run: `pytest tests/test_ticker_paths.py -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/utils/ticker_paths.py tests/test_ticker_paths.py \
        app/services/research_service.py app/services/deep_research_service.py \
        app/services/stock_service.py scripts/run_deep_research_backfill.py
git commit -m "fix(pipeline): sanitize slash in tickers (QXO/PB) before file paths

QXO/PB-style tickers were silently dropping council1/council2 reports
and news context files because '/' was interpreted as a directory
separator. Add safe_ticker_path() helper and use it at every read/write
site. Fixes 04-22 and 04-23 silent data loss."
```

---

## Task 2: Hard-fail Phase-1 when reports are truncated

**Why:** The 04-22 BBY run produced a HIGH-conviction AVOID on 58–75 chars per section. The existing quality gate (`MIN_REAL_PHASE1_REPORTS = 3`) only requires 3-of-5 real reports *and* its threshold (`len(report) >= 200`) disagrees with the QC warning threshold (`len(content) >= 100`). Unify the thresholds, tighten the gate, and add a one-shot retry for failed Phase-1 agents before giving up.

**Files:**
- Modify: `app/services/quality_control_service.py:19` — raise the short threshold from 100 to 200 so it matches `_is_real_report` (`research_service.py:77`).
- Modify: `app/services/research_service.py:58` — raise `MIN_REAL_PHASE1_REPORTS` from 3 to 4 (require 4-of-5 core agents real; seeking_alpha stays optional because OTC tickers legitimately have no coverage).
- Modify: `app/services/research_service.py:147-216` (Phase-1 execution block) — after the initial pass, identify which core agents failed, retry them once sequentially, then apply the gate.
- Test: `tests/test_phase1_hard_fail.py` — new file.

---

- [ ] **Step 1: Write the failing test for the aligned threshold**

Create `tests/test_phase1_hard_fail.py`:

```python
from app.services.quality_control_service import QualityControlService


def test_short_threshold_is_200():
    reports = {"technical": "x" * 150}
    out = QualityControlService.validate_reports(reports, "TEST", ["technical"])
    # 150 chars should now be flagged as SHORT INPUT, matching _is_real_report.
    assert out["technical"].startswith("[SHORT INPUT DETECTED:")


def test_200_chars_passes():
    reports = {"technical": "x" * 250}
    out = QualityControlService.validate_reports(reports, "TEST", ["technical"])
    assert not out["technical"].startswith("[SHORT INPUT DETECTED:")
```

- [ ] **Step 2: Run test, confirm failure**

Run: `pytest tests/test_phase1_hard_fail.py::test_short_threshold_is_200 -v`
Expected: FAIL — at threshold 100, a 150-char report is NOT flagged.

- [ ] **Step 3: Align the threshold**

In `app/services/quality_control_service.py:19`, change:

```python
                if len(content) < 100:
```

to:

```python
                if len(content) < 200:
```

Run: `pytest tests/test_phase1_hard_fail.py -v`
Expected: both pass.

- [ ] **Step 4: Raise the Phase-1 gate from 3 to 4**

In `app/services/research_service.py:58`:

```python
# Phase 1 quality gate: abort if fewer than this many core agents return real reports.
# Four-of-five is deliberate: we tolerate a single flaky sensor (e.g. seeking_alpha
# on an OTC ticker with no coverage) but refuse to produce a decision when the
# majority of sensors are error stubs. The 04-22 BBY outage (5/5 truncated
# outputs producing a HIGH-conviction AVOID) is the canonical motivator.
MIN_REAL_PHASE1_REPORTS = 4
```

- [ ] **Step 5: Add a one-shot retry for failed Phase-1 agents**

Between the existing Phase-1 parallel block (ending around line 280 with `state.reports = {...}`) and the QC validation line (`state.reports = QualityControlService.validate_council_reports(...)` at line 288), insert this retry block:

```python
        # --- Phase 1 one-shot retry for failed agents ---
        # Each agent that produced an error stub (or nothing at all) gets
        # a second attempt SEQUENTIALLY. We deliberately avoid parallel
        # retries because the most common cause of Phase-1 failure is
        # Gemini instability, and hammering the API in parallel during
        # an outage just wastes retries.
        retry_prompt_map = {
            "technical": (tech_prompt, "Technical Agent"),
            "news": (news_prompt, "News Agent"),
            "market_sentiment": (sentiment_prompt, "Market Sentiment Agent"),
            "competitive": (comp_prompt, "Competitive Landscape Agent"),
        }
        for key, (prompt, agent_label) in retry_prompt_map.items():
            current = state.reports.get(key)
            if _is_real_report(current):
                continue
            print(f"  > [Phase 1 Retry] {agent_label} failed first pass; retrying once...")
            try:
                retry_result = self._call_agent(prompt, agent_label, state)
                if _is_real_report(retry_result):
                    state.reports[key] = retry_result
                    print(f"  > [Phase 1 Retry] {agent_label} succeeded on retry.")
            except Exception as e:
                logger.warning(f"[Phase 1 Retry] {agent_label} retry raised: {e}")
```

- [ ] **Step 6: Write the integration-style regression test**

Append to `tests/test_phase1_hard_fail.py`:

```python
from unittest.mock import MagicMock, patch
from app.services.research_service import ResearchService, _is_real_report


def test_bby_scenario_aborts_instead_of_high_conviction():
    """All 5 core agents return 70-char error stubs -> pipeline must abort,
    not produce a PM decision."""
    svc = ResearchService()
    # Force _call_agent to always return a 70-char error stub.
    fake_stub = "[Error in Agent: Connection reset by peer after 3 retries]"
    assert len(fake_stub) < 200  # guard: must be shorter than the real threshold

    with patch.object(svc, "_call_agent", return_value=fake_stub), \
         patch.object(svc, "_check_and_increment_usage", return_value=True):
        result = svc.analyze_stock(
            "BBY",
            {"change_percent": -6.1, "price": 50.0, "volume": 1_000_000},
        )

    # Assert we produced an abstention, not a HIGH-conviction verdict.
    assert result.get("recommendation") in ("ABSTAIN", "INSUFFICIENT_DATA", "PASS")
    assert result.get("conviction") != "HIGH"


def test_is_real_report_rejects_short_stubs():
    assert not _is_real_report("[Error: short]")
    assert not _is_real_report("")
    assert not _is_real_report(None)
    assert _is_real_report("x" * 400)
```

If the exact return keys from `_build_insufficient_data_response` differ (check `app/services/research_service.py:1083-1100`), adapt the assertion to match — do NOT loosen it to `assert True`; read the function first.

- [ ] **Step 7: Run tests, confirm pass**

Run: `pytest tests/test_phase1_hard_fail.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add app/services/quality_control_service.py app/services/research_service.py \
        tests/test_phase1_hard_fail.py
git commit -m "fix(research): hard-fail Phase 1 on truncated reports + one-shot retry

- Align QC short-report threshold (100 -> 200) with _is_real_report.
- Raise MIN_REAL_PHASE1_REPORTS from 3/5 to 4/5.
- Retry each failed core Phase-1 agent once before applying the gate.
Prevents BBY-style HIGH-conviction decisions on 58-char agent outputs."
```

---

## Task 3: Preserve PM verdict when Deep Research JSON parsing fails

**Why:** The 04-22 ADBE run had the PM produce BUY_LIMIT, then Deep Research timed out on the repair step and the raw-fallback overrode everything to AVOID. The right behavior: parse failure means "we don't know," not "downgrade to AVOID." Mark as `PENDING_REVIEW`, keep the PM's action, and let it be re-queued.

**Files:**
- Modify: `app/services/deep_research_service.py:1483` — bump repair timeout from 30s to 90s.
- Modify: `app/services/deep_research_service.py:1557-1592` — change the parse-failure fallback to preserve the PM verdict by returning `review_verdict = "PENDING_REVIEW"` and `action = None` (signaling "don't override").
- Modify: `app/services/deep_research_service.py:548-551` (`_apply_trading_level_overrides` guard) — skip overrides when `action is None`.
- Modify: `app/services/deep_research_service.py:481-560` (`_handle_completion`) — when `review_verdict == "PENDING_REVIEW"`, write the DB row as PENDING_REVIEW but do not touch the main verdict / limit columns.
- Test: `tests/test_deep_research_parse_fallback.py` — new file.

---

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research_parse_fallback.py`:

```python
from unittest.mock import MagicMock, patch
from app.services.deep_research_service import DeepResearchService


def test_parse_failure_returns_pending_review_not_avoid():
    """When Flash repair times out, _parse_output must not silently
    downgrade the verdict to AVOID. It should return a PENDING_REVIEW
    sentinel with action=None so the PM verdict is preserved upstream."""
    svc = DeepResearchService.__new__(DeepResearchService)  # bypass __init__
    svc.api_key = "fake"

    poll_data = {
        "outputs": [
            {"text": "not valid json and not repairable either"}
        ]
    }

    # Force the repair helper to return None (simulating the 30s timeout).
    with patch.object(svc, "_repair_json_using_flash", return_value=None):
        result = svc._parse_output(poll_data, schema_type="individual")

    assert result is not None
    assert result["review_verdict"] == "PENDING_REVIEW"
    assert result["action"] is None, "action must be None so PM verdict is preserved"
    assert "raw_report_full" in result


def test_apply_trading_level_overrides_skips_when_action_none():
    """A PENDING_REVIEW result must NOT rewrite the entry/stop/tp columns."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    called = {"update": False}

    def fake_execute(*args, **kwargs):
        called["update"] = True

    with patch("app.services.deep_research_service.sqlite3") as mock_sql:
        conn = mock_sql.connect.return_value
        conn.cursor.return_value.execute.side_effect = fake_execute
        svc._apply_trading_level_overrides(
            decision_id=1,
            symbol="ADBE",
            result={"action": None, "review_verdict": "PENDING_REVIEW"},
        )

    assert called["update"] is False, "PENDING_REVIEW must not touch trading columns"
```

- [ ] **Step 2: Run tests, confirm failure**

Run: `pytest tests/test_deep_research_parse_fallback.py -v`
Expected: both FAIL — the current fallback returns `review_verdict = "ERROR_PARSING"`, `action = "AVOID"`, and `_apply_trading_level_overrides` does not check for `action is None`.

- [ ] **Step 3: Bump repair timeout**

In `app/services/deep_research_service.py:1483`, change:

```python
            # Timeout of 30s is enough for Flash
            response = requests.post(url, headers=headers, json=payload, timeout=30)
```

to:

```python
            # 90s: repair-via-Flash needs more headroom than a normal Flash call;
            # the prompt includes the full truncated report plus a schema, and a
            # 30s cap was timing out in production (see 04-22 ADBE incident).
            response = requests.post(url, headers=headers, json=payload, timeout=90)
```

- [ ] **Step 4: Change the parse-failure fallback**

In `app/services/deep_research_service.py:1557-1592`, replace the fallback dict with:

```python
            logger.warning(f"[Deep Research] JSON Parse & Repair failed. Using PENDING_REVIEW fallback. Length: {len(final_text)}")

            # PENDING_REVIEW fallback: we do NOT know what the reviewer
            # concluded, so we refuse to override the PM verdict. Setting
            # action=None signals to _apply_trading_level_overrides to
            # leave the trading columns alone. The task can be re-queued
            # from the PENDING_REVIEW state later.
            return {
                "review_verdict": "PENDING_REVIEW",
                "action": None,
                "conviction": "LOW",
                "drop_type": "UNKNOWN",
                "risk_level": "Unknown",
                "catalyst_type": "Parse Error",
                "entry_price_low": None,
                "entry_price_high": None,
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "upside_percent": None,
                "downside_risk_percent": None,
                "risk_reward_ratio": None,
                "pre_drop_price": None,
                "entry_trigger": None,
                "reassess_in_days": None,
                "global_market_analysis": "See Raw Report",
                "local_market_analysis": "See Raw Report",
                "swot_analysis": {
                    "strengths": [], "weaknesses": [], "opportunities": [], "threats": []
                },
                "verification_results": [
                    "JSON Parsing Failed — task marked PENDING_REVIEW.",
                    "Raw Output Below:",
                    final_text[:3000]
                ],
                "council_blindspots": [],
                "knife_catch_warning": False,
                "reason": "Deep research output could not be parsed; PM verdict preserved, flagged for re-review.",
                "raw_report_full": final_text
            }
```

- [ ] **Step 5: Guard `_apply_trading_level_overrides` and `_handle_completion` against `action is None`**

In `app/services/deep_research_service.py:550`:

```python
            # --- Deep Research overrides main trading-level columns ---
            # Deep Research gets the final call on recommendation & limit prices.
            # action=None (PENDING_REVIEW) means we do NOT override.
            if decision_id and action in ('BUY_LIMIT', 'BUY', 'WATCH', 'AVOID'):
                self._apply_trading_level_overrides(decision_id, symbol, result)
```

This is already guarded by the `in ('BUY_LIMIT', ...)` check — `None` won't match. Verify by reading line 548-551 and confirming. Also verify line 506 (`verdict_for_db = action`): when `action is None`, `verdict_for_db` becomes `None`, which `update_deep_research_data` must tolerate. Change line 506 to:

```python
            # PENDING_REVIEW: don't overwrite verdict column, let caller keep PM verdict.
            verdict_for_db = action if action is not None else "PENDING_REVIEW"
```

- [ ] **Step 6: Run tests, confirm pass**

Run: `pytest tests/test_deep_research_parse_fallback.py -v`
Expected: both pass.

- [ ] **Step 7: Add update_deep_research_data PENDING_REVIEW documentation**

In `app/database.py:384`, above the `update_deep_research_data` signature, add a comment:

```python
# Valid values for verdict parameter:
#   BUY | BUY_LIMIT | WATCH | AVOID    (normal Deep Research outputs)
#   PENDING_REVIEW                      (parse failure; PM verdict preserved, task re-queuable)
#   ERROR_PARSING                       (legacy; superseded by PENDING_REVIEW for new runs)
def update_deep_research_data(decision_id: int, verdict: str, ...):
```

Do NOT change the function body — PENDING_REVIEW is just another string and the existing code already handles arbitrary verdict values.

- [ ] **Step 8: Confirm existing repair scripts still handle PENDING_REVIEW**

Read `scripts/archive/repair_deep_research.py:41`. The current WHERE clause is:

```sql
WHERE symbol = ? AND deep_research_verdict IN ('ERROR_PARSING', 'UNKNOWN (Parse Error)')
```

Add `'PENDING_REVIEW'` to the set:

```python
            WHERE symbol = ? AND deep_research_verdict IN ('ERROR_PARSING', 'UNKNOWN (Parse Error)', 'PENDING_REVIEW')
```

Same pattern for `scripts/run_deep_research_backfill.py:52` — add `OR deep_research_verdict = 'PENDING_REVIEW'` to the condition, so the backfill picks up parse-failed rows too.

- [ ] **Step 9: Commit**

```bash
git add app/services/deep_research_service.py app/database.py \
        scripts/archive/repair_deep_research.py scripts/run_deep_research_backfill.py \
        tests/test_deep_research_parse_fallback.py
git commit -m "fix(deep-research): preserve PM verdict on JSON parse failure

- Raise Flash repair timeout 30s -> 90s.
- On parse failure, return PENDING_REVIEW with action=None so the PM
  verdict is preserved instead of silently downgraded to AVOID.
- Backfill + repair scripts now pick up PENDING_REVIEW rows.
Fixes 04-22 ADBE incident where BUY_LIMIT was overridden to AVOID."
```

---

## Task 4: Wall-clock budget on agent retries

**Why:** The 04-22 QXO/PB run spent ~17 hours looping between Bear Researcher retries because the retry count had a bound but total wall-clock time did not. Add a 10-minute per-agent-call budget that overrides the retry count.

**Files:**
- Modify: `app/services/research_service.py:1212-1340` (`_call_grounded_model`) — add a `budget_deadline` kwarg that defaults to `time.time() + 600` on the first call and is threaded through recursion.
- Test: `tests/test_agent_wall_clock_budget.py` — new file.

---

- [ ] **Step 1: Read the current `_call_grounded_model` signature and retry structure**

Run: `grep -n "def _call_grounded_model\|MAX_GROUNDING_RETRIES\|time.sleep" app/services/research_service.py | head -30`

Note the retry entry points (recursive calls to `_call_grounded_model(prompt, model_name=..., retry_count=retry_count+1)`). You'll thread the deadline through these same calls.

- [ ] **Step 2: Write the failing test**

Create `tests/test_agent_wall_clock_budget.py`:

```python
import time
from unittest.mock import MagicMock, patch
from app.services.research_service import ResearchService


def test_call_grounded_model_respects_wall_clock_budget():
    """Even if the retry counter would allow another attempt, an expired
    wall-clock budget must stop the loop. Simulates the QXO/PB 17h stall."""
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()

    # Mock the API call to always raise a retryable error and take no real time.
    from google.api_core.exceptions import ServiceUnavailable

    def boom(*a, **kw):
        raise ConnectionResetError("simulated 503")

    svc.grounding_client.models.generate_content.side_effect = boom

    # Pass a budget deadline in the past — the method must give up on the
    # FIRST attempt rather than consuming all its retries.
    start = time.time()
    result = svc._call_grounded_model(
        prompt="x",
        model_name="gemini-3-flash-preview",
        agent_context="Test Agent",
        retry_count=0,
        budget_deadline=start - 1,  # already expired
    )
    # Must return an error stub, not hang.
    assert isinstance(result, str)
    assert "budget" in result.lower() or "[Error" in result or "[Grounding Error" in result
    # And must not have consumed more than ~1 second of real time.
    assert (time.time() - start) < 5
```

- [ ] **Step 3: Run test, confirm failure**

Run: `pytest tests/test_agent_wall_clock_budget.py -v`
Expected: FAIL with `TypeError: _call_grounded_model() got an unexpected keyword argument 'budget_deadline'`.

- [ ] **Step 4: Add the budget parameter and enforcement**

In `app/services/research_service.py`, modify the `_call_grounded_model` signature (around line 1212):

```python
    def _call_grounded_model(
        self,
        prompt: str,
        model_name: str,
        agent_context: str = "",
        retry_count: int = 0,
        budget_deadline: Optional[float] = None,
    ) -> str:
        """
        ...existing docstring...

        Wall-clock budget:
        - budget_deadline (unix timestamp) is set on the first call to
          time.time() + AGENT_WALL_CLOCK_BUDGET_SEC (default 600 = 10 min)
          and threaded through recursive retries. If the deadline is past
          when we enter this method, we return an error stub immediately
          regardless of remaining retry_count. This prevents a single
          agent call from stalling the pipeline indefinitely, as happened
          with QXO/PB on 04-22 (Bear Researcher retried for 17 hours).
        """
        import time as _time

        # First call: stamp the deadline.
        if budget_deadline is None:
            budget_deadline = _time.time() + AGENT_WALL_CLOCK_BUDGET_SEC

        # Short-circuit if budget already spent.
        if _time.time() >= budget_deadline:
            logger.warning(
                f"[{agent_context}] Wall-clock budget exhausted after {retry_count} retries; giving up."
            )
            return (
                f"[Error: {agent_context} exceeded {AGENT_WALL_CLOCK_BUDGET_SEC}s wall-clock "
                f"budget after {retry_count} retries]"
            )

        attempt_label = f"attempt {retry_count + 1}/{MAX_GROUNDING_RETRIES + 1}"
        # ...rest of existing method body unchanged, EXCEPT every recursive
        # self._call_grounded_model(...) call at the bottom must now pass
        # budget_deadline=budget_deadline as a kwarg.
```

Add the module-level constant near the top of the file (next to `MIN_REAL_PHASE1_REPORTS` at line 58):

```python
# Wall-clock budget per grounded agent call. 10 minutes is generous
# for a normal agent but hard-caps the QXO/PB-style multi-hour stalls.
AGENT_WALL_CLOCK_BUDGET_SEC = 600
```

Find every `return self._call_grounded_model(...)` recursive call inside the method body and append `budget_deadline=budget_deadline` to its kwargs. Example:

```python
                return self._call_grounded_model(
                    prompt,
                    model_name=model_name,
                    agent_context=agent_context,
                    retry_count=retry_count + 1,
                    budget_deadline=budget_deadline,
                )
```

- [ ] **Step 5: Run tests, confirm pass**

Run: `pytest tests/test_agent_wall_clock_budget.py -v`
Expected: pass.

Then run the broader grounding tests to make sure the signature change didn't break anything:

Run: `pytest tests/test_grounding.py -v` (if it exists) or `pytest tests/ -k grounded -v`
Expected: all green, or skip with "no tests matched" (OK).

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py tests/test_agent_wall_clock_budget.py
git commit -m "fix(research): 10-min wall-clock budget per grounded agent call

QXO/PB on 04-22 spent ~17 hours in Bear Researcher retry loops because
retry count was bounded but total time was not. Add
AGENT_WALL_CLOCK_BUDGET_SEC = 600 threaded through _call_grounded_model
recursive calls. First call stamps the deadline; subsequent retries
abort if it has passed regardless of remaining retry_count."
```

---

## Task 5: Silence Drive quota spam via env gate

**Why:** Every decision logs `Error creating spreadsheet: The user's Drive storage quota has been exceeded`. Doesn't break the pipeline but pollutes logs and burns API calls. Add an opt-out env var and also ensure the breaker records quota failures from `_get_or_create_spreadsheet` (not just `upload_data`), so it trips faster.

**Files:**
- Modify: `app/services/drive_service.py:56-67` (`_authenticate`) — honor `DRIVE_UPLOAD_ENABLED=false`.
- Modify: `app/services/drive_service.py:95-117` (`_get_or_create_spreadsheet`) — record a quota failure on any exception and return early when the breaker is tripped. Demote the log to DEBUG when the breaker is already tripped.

---

- [ ] **Step 1: Read existing breaker state helpers**

Re-read lines 69-93 of `app/services/drive_service.py`. Confirm `_record_quota_failure` and `_breaker_tripped` exist and the breaker trips after 3 failures in 24h.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_drive_circuit_breaker.py` (file already exists per the codebase):

```python
import os
from unittest.mock import patch
from app.services.drive_service import GoogleDriveService


def test_drive_upload_enabled_false_disables_service():
    with patch.dict(os.environ, {"DRIVE_UPLOAD_ENABLED": "false"}):
        svc = GoogleDriveService()
        assert svc.sheets_service is None
        assert svc.drive_service is None


def test_drive_upload_enabled_unset_defaults_to_enabled_behaviour(tmp_path, monkeypatch):
    """When the flag is unset, behaviour matches the pre-flag default
    (still depends on service_account.json availability, but does NOT
    short-circuit at init)."""
    monkeypatch.delenv("DRIVE_UPLOAD_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)  # no service_account.json here
    svc = GoogleDriveService()
    # Without creds, sheets_service is None anyway, but by a different code path.
    # The test's purpose is to ensure the env flag didn't accidentally disable us.
    assert getattr(svc, "_disabled_by_env", False) is False


def test_get_or_create_spreadsheet_records_quota_failure_on_error():
    svc = GoogleDriveService.__new__(GoogleDriveService)
    svc._consecutive_quota_failures = 0
    svc._disabled_until = None
    svc._breaker_state_path = "/tmp/test_breaker.json"

    class BadDrive:
        def files(self):
            raise Exception("The user's Drive storage quota has been exceeded.")

    svc.drive_service = BadDrive()
    svc.FOLDER_ID = "x"
    svc.SPREADSHEET_NAME = "x"

    result = svc._get_or_create_spreadsheet()
    assert result is None
    assert svc._consecutive_quota_failures == 1
```

- [ ] **Step 3: Run tests, confirm failure**

Run: `pytest tests/test_drive_circuit_breaker.py -v -k "upload_enabled or records_quota"`
Expected: FAIL — the env flag isn't read and `_get_or_create_spreadsheet` doesn't call `_record_quota_failure` on exceptions.

- [ ] **Step 4: Add the env gate in `__init__` / `_authenticate`**

In `app/services/drive_service.py`, modify `__init__` at line 24:

```python
    def __init__(self):
        self.creds = None
        self.sheets_service = None
        self.drive_service = None
        self._breaker_state_path = self.BREAKER_STATE_FILE
        self._consecutive_quota_failures = 0
        self._disabled_until: Optional[datetime.datetime] = None
        self._disabled_by_env = os.getenv("DRIVE_UPLOAD_ENABLED", "true").lower() == "false"
        if self._disabled_by_env:
            print("[Google Drive] Upload disabled via DRIVE_UPLOAD_ENABLED=false.")
            return
        self._load_breaker_state()
        self._authenticate()
```

- [ ] **Step 5: Record quota failures in `_get_or_create_spreadsheet`**

Replace the method (lines 95-117) with:

```python
    def _get_or_create_spreadsheet(self):
        if not self.drive_service:
            return None
        if self._breaker_tripped():
            return None
        query = (
            f"name = '{self.SPREADSHEET_NAME}' and '{self.FOLDER_ID}' in parents "
            f"and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
        )
        try:
            results = self.drive_service.files().list(
                q=query, spaces='drive', fields='files(id, name)'
            ).execute()
        except Exception as e:
            self._record_quota_failure()
            # Demote to debug when breaker is already tripped — this is
            # expected noise during a quota outage; no need to log every call.
            if self._breaker_tripped():
                return None
            print(f"Error listing spreadsheet: {e}")
            return None

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
            self._record_success()
            return file.get('id')
        except Exception as e:
            self._record_quota_failure()
            if self._breaker_tripped():
                return None
            print(f"Error creating spreadsheet: {e}")
            return None
```

- [ ] **Step 6: Run tests, confirm pass**

Run: `pytest tests/test_drive_circuit_breaker.py -v`
Expected: all pass (including the pre-existing breaker tests).

- [ ] **Step 7: Document the env flag in README / CLAUDE.md**

Not applicable — per project convention, don't add README entries unless asked. The code is self-documenting via the `print` at startup. Skip this step.

- [ ] **Step 8: Commit**

```bash
git add app/services/drive_service.py tests/test_drive_circuit_breaker.py
git commit -m "fix(drive): env-gate uploads + record quota failures on list/create

- DRIVE_UPLOAD_ENABLED=false short-circuits at init for noisy quota-out state.
- _get_or_create_spreadsheet now records quota failures from both
  files().list and files().create, so the breaker trips after 3 failures
  instead of dribbling errors forever.
- Demote log to silent once breaker is already tripped."
```

---

## Out of scope

Not in this plan:

- Transcript sources (DefeatBeta, Finnhub 403) — the user flagged these as lower priority. Split into its own plan (`docs/superpowers/plans/YYYY-MM-DD-transcript-sources.md`) if and when we want to act on it.
- Seeking Alpha empty responses on OTC tickers — expected behaviour, not a bug.
- ReportLab install — one-line fix (`pip install reportlab` + add to requirements.txt), doesn't need a plan. Do inline if it comes up.
- Polygon DNS blip — transient, already retries.
- FRED 500 retry — already working.

---

## Self-review

**Spec coverage:**

- ✅ #1 QXO/PB path sanitization → Task 1
- ✅ #2a short-report hard fail → Task 2
- ✅ #2b wall-clock retry budget → Task 4
- ✅ #2c model fallback for Phase 2 — partially covered by Task 4 budget (stops infinite retry) + existing 503 fallback at `research_service.py:1199-1207` for Pro→standard Pro. A full Pro→Flash fallback is intentionally out of scope; the 10-minute budget is the hard safety net. If we later want Pro→Flash degradation, that's a separate plan.
- ✅ #3 Deep Research parse-failure → Task 3
- ✅ #4 Drive quota noise → Task 5
- ✅ #5 transcript sources → explicitly out of scope
- ✅ #6 minor/one-off items → explicitly out of scope

**Placeholder scan:** no TBDs, no "add appropriate error handling," every code step has complete code. Paths verified against existing file structure.

**Type consistency:** `safe_ticker_path` signature consistent across all call sites; `budget_deadline: Optional[float]` consistent in Task 4; `action=None` flows are guarded in Task 3.
