# Pipeline Post-Run Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six production bugs surfaced by a 3-cycle pipeline run: citation bleed in agent reports (with spacing-collapse regex bug), double-deep-research enqueueing, missing trading-level validation after DR JSON repair, gatekeeper NaN reason-string fallthrough, single-letter-ticker transcript mismatches, and a doubled `vv` version-string prefix.

**Architecture:** Each fix is localized to one or two files. We add a regression test for every behavior change. Two of the fixes (citation strip + DR enqueue dedup) defend against bad data at the boundary where it enters the pipeline; the others are validation gates immediately after the data is produced.

**Tech Stack:** Python 3.9, pytest, FastAPI, SQLite, Gemini grounding API, DefeatBeta HuggingFace dataset.

---

## Implementation Notes / Deviations from this Plan

This plan was executed via subagent-driven development on branch `fix/pipeline-postrun`. Two deviations from what's prescribed below — both intentional, both improvements — landed in the actual implementation. They are recorded here so future readers reconciling the doc against `git log` don't have to dig through commit messages.

1. **Task 2 (citation strip applied to stored agent reports).** The plan's Step 4 prescribes wrapping every consumer of `_format_citations` (`state.technical_report = _strip_citations(text)`). Investigation showed `_format_citations` has exactly one consumer (`research_service.py:1380`), so the implementer instead inserted `text = _strip_citations(text)` *inside* `_format_citations`, between the marker-injection loop and the `### Sources:` appendix. This guarantees the invariant — "the prose returned by this method has no `[Source N]` markers; the appendix below is the canonical reference" — independently of how many call sites exist now or in the future. Commit `7dc651b`.

2. **Task 4 (DR trading-level validation).** The plan's Step 4 prescribes gating only `_apply_trading_level_overrides`. But `_handle_completion` calls `update_deep_research_data` *first* (around line 595), and that DB writer reads `entry_price_low`, `entry_price_high`, `stop_loss`, etc. directly from `result.get(...)` — so gating only the override leaves the bad zeros to be persisted by the initial write. The implementer instead validates at the top of `_handle_completion` and, on rejection, nulls the level fields in `result` (in place) so neither downstream writer persists them. The set was extended in a fix-up commit (`4b87575`) to include sell-range fields (`sell_price_low/high`, `ceiling_exit`, `exit_trigger`), since the same JSON-repair root cause produces the same zeros there. A snapshot of the pre-mutation result is saved to a `*_rejected_levels.json` artifact for forensics before nulling. Commits `be35716` + `4b87575` + `fab6a15`.

Task 3 (DR enqueue dedup) also received a code-review-driven fix-up (`193c987`) to close two correctness gaps in the original commit: a UTC-midnight race (where `_today_str()` could produce different keys at enqueue vs. clear) and an early-discard race (where the inflight clear ran *before* the DB write). The final state stores the inflight key in the payload at enqueue time and clears only in the `finally` of `_process_individual_task`, which runs after `_handle_completion` returns (and therefore after the DB write).

---

## File Map

Changes are confined to:

| File | Why |
|---|---|
| `app/services/research_service.py` | `_strip_citations` regex spacing bug; apply strip to agent report text returned by `_format_citations`. |
| `app/services/deep_research_service.py` | Mirror regex spacing fix; add post-parse trading-level validation in `_parse_output` / `_handle_completion`. |
| `app/services/stock_service.py` | Pre-enqueue dedup check in `_process_deep_research_backfill`; transcript company-name validation in `get_latest_transcript`. |
| `app/services/gatekeeper_service.py` | NaN-first branch in Bollinger reason-string logic. |
| `main.py` | Drop the leading `v` from `f"StockDrop v{VERSION}"` since `git describe` already prefixes `v`. |
| `tests/test_citation_strip.py` | Extend with mid-cluster + missing-whitespace cases. |
| `tests/test_deep_research_dedup.py` | New — DR enqueue dedup. |
| `tests/test_dr_trading_level_validation.py` | New — reject all-zero entry/stop. |
| `tests/test_gatekeeper_nan.py` | New — NaN %B reason string. |
| `tests/test_transcript_company_match.py` | New — DefeatBeta mismatch defensive check. |
| `tests/test_version_string.py` | New — no `vv` prefix. |

---

## Task 1: Fix `_strip_citations` spacing bug (covers 1a)

**Context:** The current regex collapses `Massive [Source 1]structural` → `Massivestructural` because `_EDGE_CITATION_RE` substitutes the whole `\s*\[Source N\]\s*` match with the empty string. We need to substitute with a space, then collapse runs of whitespace, so word boundaries are preserved.

**Files:**
- Modify: `app/services/research_service.py:25-36`
- Modify: `app/services/deep_research_service.py:17-42`
- Modify: `tests/test_citation_strip.py`

- [ ] **Step 1: Update the existing mid-word-join test and add failing tests for the spacing bug**

The existing test at `tests/test_citation_strip.py` line 17-18 currently asserts:
```python
assert _strip_citations("signa [Source 1]ling") == "signaling"
```

This depends on the *old* "edge regex eats whitespace" behavior — which is exactly the bug we're fixing. The user's prescribed fix ("replace with space, then collapse double-spaces") cannot preserve mid-word joins because the joined-vs-separated cases are indistinguishable from the text alone. We update this test to reflect the new contract: a marker is always replaced with a single space.

Read `tests/test_citation_strip.py` first to find the exact assertion. Then change it to:

```python
def test_strips_marker_in_middle_of_word(self):
    # Old behavior joined letters across the marker ('signaling').
    # New behavior preserves a space because we cannot tell joined-vs-separated
    # from the raw text alone, and word-boundary preservation is the higher
    # priority (see CAR 'Massivestructuralunwind' production failure).
    assert _strip_citations("signa [Source 1]ling") == "signa ling"
```

Then append the new regression class:

```python
class TestCitationStripSpacing:
    """Regression tests for the 'Massivestructuralunwind' spacing collapse."""

    def test_marker_between_words_without_trailing_space(self):
        # Marker eats only the leading whitespace, not the next word.
        assert _strip_citations("Massive [Source 1]structural") == "Massive structural"

    def test_consecutive_marker_cluster(self):
        # A cluster like '[Source 10][Source 11][Source 4]' between words must collapse to one space.
        raw = "Phy [Source 6][Source 1]sical impact [Source 10][Source 11][Source 4] expected"
        # 'Phy' and 'sical' are separated by the cluster — they get one space.
        assert _strip_citations(raw) == "Phy sical impact expected"

    def test_no_double_space_after_strip(self):
        # 'word [Source 1] word' should never produce 'word  word' (two spaces).
        out = _strip_citations("word [Source 1] word")
        assert "  " not in out
        assert out == "word word"

    def test_strip_at_sentence_join(self):
        # The CAR-style "Massivestructuralunwind" failure: every word followed by a marker.
        raw = "Massive [Source 1]structural [Source 2]unwind: [Source 3]The [Source 4]stock"
        assert _strip_citations(raw) == "Massive structural unwind: The stock"

    def test_no_leading_or_trailing_space(self):
        # Marker at the very start or end should not leave dangling whitespace.
        assert _strip_citations("[Source 1] hello") == "hello"
        assert _strip_citations("hello [Source 1]") == "hello"
```

- [ ] **Step 2: Run the new tests to confirm they fail**

Run: `pytest tests/test_citation_strip.py::TestCitationStripSpacing -v`
Expected: FAIL on `test_marker_between_words_without_trailing_space` and `test_strip_at_sentence_join` (current regex collapses spaces).

- [ ] **Step 3: Fix the regex in `research_service.py`**

Replace lines 25-36 in `app/services/research_service.py` with:

```python
# Citation strip — Gemini grounding injects [Source N] markers that corrupt JSON
# AND mid-sentence text. We replace each marker with a single space, then collapse
# runs of whitespace, so word boundaries are preserved. Joined-vs-separated cases
# ('signaling' vs 'signa ling') are indistinguishable from the raw text alone;
# we deliberately favor word-boundary preservation. The CAR-style
# 'Massivestructuralunwind' production failure was the trigger.
_CITATION_RE = re.compile(r"\[Source\s*\d+\]")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_citations(raw: str) -> str:
    """Remove inline [Source N] markers, replacing each with a single space.

    'word [Source 1] word' → 'word word'   (collapses double space)
    'word [Source 1]word'  → 'word word'   (boundary preserved)
    '[Source 1][Source 2]' → ''            (leading/trailing trimmed)
    'word[Source 1]word'   → 'word word'   (always inserts a space)
    """
    if "[Source" not in raw:
        return raw
    cleaned = _CITATION_RE.sub(" ", raw)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned)
    return cleaned.strip(" ")
```

- [ ] **Step 4: Mirror the same regex in `deep_research_service.py`**

Replace lines 17-42 in `app/services/deep_research_service.py` with the identical block (keeping the existing `_CITATION_STRIP_COUNTER` increment logic):

```python
_CITATION_STRIP_COUNTER = {"stripped": 0}

_CITATION_RE = re.compile(r"\[Source\s*\d+\]")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def _strip_citations(raw: str) -> str:
    """Remove inline [Source N] markers, preserving word boundaries.

    See app/services/research_service.py::_strip_citations for full contract.
    """
    if "[Source" not in raw:
        return raw
    cleaned = _CITATION_RE.sub(" ", raw)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned)
    if cleaned != raw:
        _CITATION_STRIP_COUNTER["stripped"] += 1
    return cleaned.strip(" ")
```

- [ ] **Step 5: Run all citation strip tests**

Run: `pytest tests/test_citation_strip.py -v`
Expected: ALL pass, including the new spacing-bug regression tests.

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py app/services/deep_research_service.py tests/test_citation_strip.py
git commit -m "fix(citations): preserve word boundaries when stripping [Source N] markers"
```

---

## Task 2: Apply citation strip to stored agent reports (covers 1)

**Context:** `_strip_citations` is currently called only from `_extract_json` (research_service:1551) and from DR JSON parsing (deep_research_service:1416, 1604). The agent report *text* returned by `_format_citations` (research_service:1482) keeps `[Source N]` markers because they're deliberately injected at line 1523. Those markers then flow into the database, the dashboard, and the PM prompt — that's the production bleed the user is seeing.

**Decision:** Citations are still useful for the human reader, so we keep them in the rendered "### Sources:" appendix. We strip them only from the *prose* that gets passed to downstream agents and saved to the report dicts.

The cleanest place is at every site that consumes `_format_citations` output and feeds it into `state.*_report`. Search for assignments to `state.technical_report`, `state.news_report`, etc.

**Files:**
- Modify: `app/services/research_service.py` (every call site that stores `_format_citations` output)
- Test: `tests/test_citation_strip.py`

- [ ] **Step 1: Locate every consumer of `_format_citations`**

Run: `grep -n "_format_citations\|format_citations" app/services/research_service.py`

Record the line numbers. Each one looks roughly like:
```python
text = self._format_citations(response)
state.technical_report = text
```

- [ ] **Step 2: Write a failing test that proves agent reports leak citations today**

Add to `tests/test_citation_strip.py`:

```python
def test_format_citations_output_is_stripped_before_storage():
    """Reports stored in MarketState must not contain [Source N] markers."""
    from app.services.research_service import _strip_citations
    raw = "Stock dropped on weak guidance [Source 1] and competitive pressure [Source 2]."
    cleaned = _strip_citations(raw)
    assert "[Source" not in cleaned
    assert "weak guidance" in cleaned
    assert "competitive pressure" in cleaned
```

- [ ] **Step 3: Run the test to confirm the function works**

Run: `pytest tests/test_citation_strip.py::test_format_citations_output_is_stripped_before_storage -v`
Expected: PASS (the function works; we still need to wire it into the call sites).

- [ ] **Step 4: Wire `_strip_citations` into every call site that stores report text**

For each call site found in Step 1, wrap it. Example transformation:

```python
# Before
text = self._format_citations(response)
state.technical_report = text
```

```python
# After
text = self._format_citations(response)
state.technical_report = _strip_citations(text)
```

**Important:** The "### Sources:" appendix is appended *inside* `_format_citations` (line 1526). That appendix has no `[Source N]` markers — only the inline body does. `_strip_citations` will not touch the appendix. Good.

If there is a call site that returns the report through `_extract_json` first (e.g. JSON-shaped agent output), the strip is already applied inside `_extract_json`. Skip those — adding a second strip is harmless but noisy.

- [ ] **Step 5: Run the full research_service test suite to verify no regressions**

Run: `pytest tests/ -k "research or citation or report" -v`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/research_service.py tests/test_citation_strip.py
git commit -m "fix(reports): strip [Source N] markers from stored agent reports"
```

---

## Task 3: Deep Research enqueue dedup (covers 2)

**Context:** During one cycle, `stock_service._run_deep_analysis` enqueues DR for a fresh decision (line 1577), and shortly after, `_process_deep_research_backfill` (line 692) sweeps the DB for "BUY recs missing a verdict." Because the first task hasn't completed yet, the verdict column is still NULL, so the same `(symbol, date)` gets enqueued a second time. Both runs complete, the second overwrites the first, leaving contradictory records and burning rate-limited API calls.

**Fix:** Maintain an in-memory `_inflight: Set[Tuple[str, str]]` set on `DeepResearchService`, keyed by `(symbol, date)`. `queue_research_task` refuses to enqueue if the key is already present; `_handle_completion` removes the key. As a belt-and-suspenders defense, the backfill sweep also skips symbols with a non-empty queue marker.

**Files:**
- Modify: `app/services/deep_research_service.py` (around 99-130 for `__init__`, 431-442 for `queue_research_task`, 538-619 for `_handle_completion`)
- Test: Create `tests/test_deep_research_dedup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_deep_research_dedup.py`:

```python
"""Regression: DR must not enqueue the same (symbol, date) twice in one run."""
from unittest.mock import patch
from app.services.deep_research_service import DeepResearchService


def test_duplicate_enqueue_is_rejected():
    svc = DeepResearchService()
    ctx = {"pm_decision": {"action": "BUY"}}

    # First enqueue succeeds.
    queued1 = svc.queue_research_task("SNY", ctx, decision_id=1)
    # Second enqueue for the same symbol on the same date is rejected.
    queued2 = svc.queue_research_task("SNY", ctx, decision_id=2)

    assert queued1 is True, "first enqueue should succeed"
    assert queued2 is False, "duplicate enqueue should be rejected"
    assert svc.individual_queue.qsize() == 1


def test_enqueue_after_completion_succeeds():
    svc = DeepResearchService()
    ctx = {"pm_decision": {"action": "BUY"}}

    svc.queue_research_task("SNY", ctx, decision_id=1)
    # Simulate completion clearing the inflight key.
    svc._inflight.discard(("SNY", svc._today_str()))

    queued = svc.queue_research_task("SNY", ctx, decision_id=2)
    assert queued is True
    # New task is in queue (the first was popped/discarded conceptually; in this
    # test we don't drain so qsize is 2 — assert just on the second enqueue's return).


def test_different_symbols_both_enqueue():
    svc = DeepResearchService()
    ctx = {"pm_decision": {"action": "BUY"}}

    assert svc.queue_research_task("SNY", ctx, decision_id=1) is True
    assert svc.queue_research_task("ODFL", ctx, decision_id=2) is True
    assert svc.individual_queue.qsize() == 2
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest tests/test_deep_research_dedup.py -v`
Expected: FAIL — `queue_research_task` currently returns `None`, not `True`/`False`, and has no dedup.

- [ ] **Step 3: Add `_inflight` and `_today_str` to `DeepResearchService.__init__`**

In `app/services/deep_research_service.py`, find `__init__` (line 99) and add at the end of the existing init body:

```python
        # Dedup: track (symbol, date) tuples currently queued or executing.
        # Cleared in _handle_completion. Backfill sweeps consult this set
        # before re-queuing.
        self._inflight: set = set()
        import threading as _threading
        self._inflight_lock = _threading.Lock()
```

Add a helper method below the existing methods (before the class closes):

```python
    def _today_str(self) -> str:
        """ISO date used as the dedup key. UTC to match decision_points.timestamp."""
        from datetime import datetime
        return datetime.utcnow().strftime("%Y-%m-%d")
```

- [ ] **Step 4: Modify `queue_research_task` to dedup and return bool**

Replace lines 431-442 in `app/services/deep_research_service.py`:

```python
    def queue_research_task(self, symbol: str, context: dict, decision_id: int) -> bool:
        """
        Queues an individual deep research task (HIGH PRIORITY).
        context: Pre-built context dict from StockService._build_deep_research_context()

        Returns True if queued, False if a task for (symbol, today) is already
        in flight or queued. Dedup is in-memory and per-process — restarting the
        service clears the set, which is fine because pending DB rows will be
        picked up by the backfill sweep on the next cycle.
        """
        key = (symbol, self._today_str())
        with self._inflight_lock:
            if key in self._inflight:
                logger.info(
                    f"[Deep Research] SKIP duplicate enqueue for {symbol} "
                    f"(already in-flight or queued for {key[1]})"
                )
                return False
            self._inflight.add(key)

        payload = {
            'symbol': symbol,
            'context': context,
            'decision_id': decision_id,
        }
        self.individual_queue.put({'type': 'individual', 'payload': payload})
        logger.info(f"[Deep Research] Queued INDIVIDUAL task for {symbol} (Priority: High)")
        return True
```

- [ ] **Step 5: Clear the inflight key in `_handle_completion` and on errors**

Find `_handle_completion` (line 538). At the very top of the method body add:

```python
        # Always release the inflight lock for this (symbol, date), even on errors.
        try:
            symbol_for_release = task.get('payload', {}).get('symbol') or task.get('symbol')
            if symbol_for_release:
                with self._inflight_lock:
                    self._inflight.discard((symbol_for_release, self._today_str()))
        except Exception:
            pass
```

Also find `_process_individual_task` (line 458) and wrap the body in try/finally to release on exceptions that bypass `_handle_completion`:

```python
    def _process_individual_task(self, payload):
        """Executes individual deep research."""
        symbol = payload['symbol']
        try:
            result = self.execute_deep_research(
                symbol=symbol,
                context=payload['context'],
                decision_id=payload.get('decision_id')
            )
            if result:
                self._handle_completion(payload, result)
        except Exception as e:
            logger.error(f"[Deep Research] Individual Task Failed for {symbol}: {e}")
        finally:
            # Defensive: release inflight key even if _handle_completion never ran.
            with self._inflight_lock:
                self._inflight.discard((symbol, self._today_str()))
```

- [ ] **Step 6: Run the dedup tests**

Run: `pytest tests/test_deep_research_dedup.py -v`
Expected: ALL pass.

- [ ] **Step 7: Update existing call sites to handle the new return value**

In `app/services/stock_service.py`, lines 785-790 (backfill) and 1577-1581 (live):

The current code ignores the return value, which is fine — `False` just means "already queued, no-op," and the existing `print(...)` log is now slightly misleading but not wrong. Update the backfill call site only (line 784-790) to log when the dedup hits:

```python
                print(f"  > Triggering Backfill for {symbol} (Conviction: {c.get('conviction')}, R/R: {c.get('risk_reward_ratio')})...")
                queued = deep_research_service.queue_research_task(
                    symbol=symbol,
                    context=context,
                    decision_id=decision_id
                )
                if queued:
                    print(f"  > Queued backfill task for {symbol}")
                else:
                    print(f"  > Skipped {symbol}: already in-flight (live trigger beat the backfill)")
```

- [ ] **Step 8: Run the full DR test suite**

Run: `pytest tests/ -k "deep_research or dr_" -v`
Expected: ALL pass.

- [ ] **Step 9: Commit**

```bash
git add app/services/deep_research_service.py app/services/stock_service.py tests/test_deep_research_dedup.py
git commit -m "fix(deep-research): dedup enqueues so backfill cannot re-trigger an in-flight task"
```

---

## Task 4: DR trading-level validation (covers 3)

**Context:** When DR JSON parsing fails on the first pass, the repair pipeline (`_repair_json_using_flash`, line 1481) sometimes produces a structurally-valid object with all-zero numeric fields — that's how IDCC ended up with `entry=0.0-0.0, stop=0.0`. There is currently no plausibility gate: zero entry prices and zero stops are written straight to the DB through `_apply_trading_level_overrides` (line 620).

**Fix:** Add a `_validate_trading_levels(result)` predicate that returns `(ok, reason)`. If not ok, mark the result as `INCOMPLETE_TRADING_LEVELS`, leave the trading-level columns untouched, and log the rejection. The verdict and qualitative review still get saved.

**Files:**
- Modify: `app/services/deep_research_service.py` (add validator near `_calculate_deep_research_score` at line 486; gate `_apply_trading_level_overrides` at line 609)
- Test: Create `tests/test_dr_trading_level_validation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_dr_trading_level_validation.py`:

```python
"""Regression: DR results with garbage trading levels must not overwrite DB."""
from app.services.deep_research_service import DeepResearchService


def test_validates_all_zero_levels_as_invalid():
    svc = DeepResearchService()
    result = {
        "entry_price_low": 0.0,
        "entry_price_high": 0.0,
        "stop_loss": 0.0,
        "review_verdict": "CONFIRMED",
    }
    ok, reason = svc._validate_trading_levels(result)
    assert ok is False
    assert "zero" in reason.lower() or "invalid" in reason.lower()


def test_validates_negative_levels_as_invalid():
    svc = DeepResearchService()
    result = {
        "entry_price_low": -10.0,
        "entry_price_high": -5.0,
        "stop_loss": -15.0,
    }
    ok, reason = svc._validate_trading_levels(result)
    assert ok is False


def test_validates_stop_above_entry_as_invalid():
    svc = DeepResearchService()
    result = {
        "entry_price_low": 50.0,
        "entry_price_high": 55.0,
        "stop_loss": 60.0,  # stop above entry — wrong direction
    }
    ok, reason = svc._validate_trading_levels(result)
    assert ok is False
    assert "stop" in reason.lower()


def test_validates_entry_high_below_low_as_invalid():
    svc = DeepResearchService()
    result = {
        "entry_price_low": 55.0,
        "entry_price_high": 50.0,  # high < low
        "stop_loss": 45.0,
    }
    ok, reason = svc._validate_trading_levels(result)
    assert ok is False


def test_validates_plausible_levels_as_ok():
    svc = DeepResearchService()
    result = {
        "entry_price_low": 50.0,
        "entry_price_high": 55.0,
        "stop_loss": 45.0,
    }
    ok, reason = svc._validate_trading_levels(result)
    assert ok is True


def test_missing_levels_treated_as_invalid():
    """A WAIT/AVOID verdict legitimately has no trading levels — the validator
    should return False but the *caller* must check the verdict first and skip
    the validation step entirely. This test pins the validator's contract."""
    svc = DeepResearchService()
    result = {"review_verdict": "OVERRIDDEN"}
    ok, reason = svc._validate_trading_levels(result)
    assert ok is False
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/test_dr_trading_level_validation.py -v`
Expected: FAIL — `_validate_trading_levels` does not exist.

- [ ] **Step 3: Implement `_validate_trading_levels`**

Add this method to `DeepResearchService` immediately above `_calculate_deep_research_score` (line 486):

```python
    def _validate_trading_levels(self, result: dict) -> tuple:
        """
        Sanity-check trading levels parsed from DR output.

        Returns (ok: bool, reason: str). Caller skips validation entirely for
        verdicts that legitimately have no levels (e.g. OVERRIDDEN AVOID).

        Rules (all must hold):
          - entry_price_low, entry_price_high, stop_loss are numeric > 0
          - entry_price_high >= entry_price_low
          - stop_loss < entry_price_low (stop is below entry for a long)
        """
        try:
            entry_low = result.get("entry_price_low")
            entry_high = result.get("entry_price_high")
            stop = result.get("stop_loss")

            if entry_low is None or entry_high is None or stop is None:
                return False, "missing entry/stop levels"

            entry_low = float(entry_low)
            entry_high = float(entry_high)
            stop = float(stop)

            if entry_low <= 0 or entry_high <= 0 or stop <= 0:
                return False, f"non-positive level (entry={entry_low}-{entry_high}, stop={stop})"
            if entry_high < entry_low:
                return False, f"entry_high {entry_high} < entry_low {entry_low}"
            if stop >= entry_low:
                return False, f"stop {stop} >= entry_low {entry_low} (wrong direction for long)"
            return True, "ok"
        except (TypeError, ValueError) as e:
            return False, f"non-numeric level: {e}"
```

- [ ] **Step 4: Gate `_apply_trading_level_overrides` with the validator**

In `app/services/deep_research_service.py`, find `_handle_completion` line 609 where `_apply_trading_level_overrides` is called. Wrap it:

```python
                # Validate trading levels before writing to DB. A valid verdict
                # with garbage levels (e.g. all-zeros from a JSON repair fallback)
                # is worse than no DR result at all — leave existing levels alone.
                review_verdict = result.get("review_verdict", "")
                if review_verdict in ("CONFIRMED", "UPGRADED", "ADJUSTED"):
                    levels_ok, levels_reason = self._validate_trading_levels(result)
                    if levels_ok:
                        self._apply_trading_level_overrides(decision_id, symbol, result)
                    else:
                        logger.warning(
                            f"[Deep Research] {symbol} verdict={review_verdict} but trading "
                            f"levels rejected: {levels_reason}. DB levels left unchanged; "
                            f"verdict marked INCOMPLETE_TRADING_LEVELS."
                        )
                        result["review_verdict"] = "INCOMPLETE_TRADING_LEVELS"
                else:
                    # OVERRIDDEN/AVOID verdicts have no levels — nothing to override.
                    pass
```

The exact placement: read lines 600-620 first, locate the existing call to `self._apply_trading_level_overrides(decision_id, symbol, result)` (line 609), and replace just that single line with the block above.

- [ ] **Step 5: Run validator tests**

Run: `pytest tests/test_dr_trading_level_validation.py -v`
Expected: ALL pass.

- [ ] **Step 6: Run the full DR test suite to catch regressions**

Run: `pytest tests/ -k "deep_research or dr_" -v`
Expected: ALL pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/deep_research_service.py tests/test_dr_trading_level_validation.py
git commit -m "fix(deep-research): validate trading levels post-parse; mark INCOMPLETE on garbage"
```

---

## Task 5: Gatekeeper NaN reason string (covers 4)

**Context:** When TradingView returns insufficient price history, `bb_lower` and `bb_upper` come back as NaN. Line 142 (`if bb_upper != bb_lower`) is True for NaN (NaN != NaN), so the divisor `(bb_upper - bb_lower)` is NaN and `curr_pct_b` becomes NaN. The tier classifier returns `TIER_REJECT`, but the reason-string fallthrough at lines 159-166 reports "Insufficient Drop" — even when the drop was perfectly fine and the real issue is the NaN.

**Fix:** Add a NaN-first branch right after `curr_pct_b` is computed. If NaN, set the reason to "Bollinger NaN (insufficient price history)" and return early.

**Files:**
- Modify: `app/services/gatekeeper_service.py` (around lines 141-171)
- Test: Create `tests/test_gatekeeper_nan.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_gatekeeper_nan.py`:

```python
"""Regression: gatekeeper must report NaN, not 'Insufficient Drop', for NaN %B."""
import math
from unittest.mock import patch
from app.services.gatekeeper_service import gatekeeper_service


def test_nan_pct_b_reports_nan_reason():
    """When Bollinger inputs are NaN, the rejection reason must say NaN — not Insufficient Drop."""
    fake_indicators = {
        "close": 100.0,
        "bb_lower": float("nan"),
        "bb_upper": float("nan"),
        "average_volume_10d": 5_000_000.0,
    }
    with patch(
        "app.services.gatekeeper_service.tradingview_service.get_technical_indicators",
        return_value=fake_indicators,
    ):
        is_valid, reasons = gatekeeper_service.check_technical_filters(
            symbol="PS", drop_pct=-13.6
        )

    assert is_valid is False
    bb_status = reasons.get("bb_status", "")
    assert "nan" in bb_status.lower() or "insufficient price history" in bb_status.lower(), (
        f"expected NaN reason, got: {bb_status!r}"
    )
    assert "Insufficient Drop" not in bb_status, (
        f"NaN should not fall through to drop-size message: {bb_status!r}"
    )
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest tests/test_gatekeeper_nan.py -v`
Expected: FAIL — current logic falls through to "Insufficient Drop".

- [ ] **Step 3: Add `import math` at the top of `gatekeeper_service.py`**

Find the existing imports at the top of `app/services/gatekeeper_service.py` and add:

```python
import math
```

(If `math` is already imported, skip this step.)

- [ ] **Step 4: Insert the NaN-first branch in `check_technical_filters`**

In `app/services/gatekeeper_service.py`, find line 141 (the comment `# --- Bollinger %B ---`). Insert this block immediately *after* the comment and *before* the `if bb_upper != bb_lower:` check:

```python
            # NaN-first: if either band is NaN (insufficient price history),
            # report that explicitly rather than letting the drop-size fallthrough
            # produce a misleading "Insufficient Drop" message.
            if math.isnan(bb_lower) or math.isnan(bb_upper):
                reasons["bb_status"] = (
                    "%B (nan) — Bollinger bands NaN (insufficient price history)"
                )
                reasons["lower_bb"] = bb_lower
                reasons["bb_pct_b"] = float("nan")
                reasons["tier"] = TIER_REJECT
                return False, reasons
```

Then, immediately after the existing `curr_pct_b = ...` assignment (the `else: curr_pct_b = 0.5` block ends around line 145), add a defensive post-computation NaN guard:

```python
            # Defensive: if pct_b ended up NaN despite the band check (e.g. NaN
            # price), reject with the NaN reason rather than misclassifying.
            if math.isnan(curr_pct_b):
                reasons["bb_status"] = (
                    "%B (nan) — Bollinger calculation produced NaN"
                )
                reasons["lower_bb"] = bb_lower
                reasons["bb_pct_b"] = float("nan")
                reasons["tier"] = TIER_REJECT
                return False, reasons
```

Leave the existing tier/reason logic at lines 147-171 unchanged.

- [ ] **Step 5: Run test**

Run: `pytest tests/test_gatekeeper_nan.py -v`
Expected: PASS.

- [ ] **Step 6: Run full gatekeeper tests**

Run: `pytest tests/ -k "gatekeeper" -v`
Expected: ALL pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/gatekeeper_service.py tests/test_gatekeeper_nan.py
git commit -m "fix(gatekeeper): report NaN explicitly instead of misleading 'Insufficient Drop'"
```

---

## Task 6: Transcript company-name validation (covers 5)

**Context:** DefeatBeta returned a Loblaw Companies transcript when queried for ticker `L` (Loews Corporation). DefeatBeta's HuggingFace dataset has a known mismatch on single-letter / colliding tickers. The fix is defensive — at the boundary, before we trust the transcript, fuzzy-match the company name embedded in the transcript text against the expected company name passed in by the caller.

`get_latest_transcript` currently takes only `symbol`. We extend it to optionally accept `company_name` and reject DefeatBeta results whose first paragraph doesn't mention the expected company.

**Files:**
- Modify: `app/services/stock_service.py` (function at line 1290; call site at line 1435)
- Test: Create `tests/test_transcript_company_match.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_transcript_company_match.py`:

```python
"""Regression: DefeatBeta sometimes returns the wrong company's transcript for
ambiguous tickers (e.g. 'L' → Loblaw instead of Loews). Verify defensive check."""
from unittest.mock import patch, MagicMock
import pandas as pd
from app.services.stock_service import StockService


def _make_df(company_in_text: str):
    """Build a fake DefeatBeta DataFrame whose transcript mentions company_in_text."""
    df = pd.DataFrame([{
        "report_date": "2026-04-15",
        "transcripts": [
            {"content": f"Welcome to the {company_in_text} earnings call. ..."},
            {"content": "We had a strong quarter."},
        ],
    }])
    return df


def test_rejects_mismatched_company_transcript():
    """Ticker L expects Loews; DefeatBeta returns Loblaw → reject."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Loblaw Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value=None):
        result = svc.get_latest_transcript("L", company_name="Loews Corporation")

    assert result["text"] == "" or "warning" in result, (
        f"expected empty result or warning on company mismatch, got: {result}"
    )


def test_accepts_matching_company_transcript():
    """Ticker LOW expects Lowe's; DefeatBeta returns Lowe's → accept."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Lowe's Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker):
        result = svc.get_latest_transcript("LOW", company_name="Lowe's Companies")

    assert result["text"] != ""
    assert "Lowe" in result["text"]


def test_no_company_name_skips_validation():
    """Backward compat: if caller doesn't pass company_name, no validation runs."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Loblaw Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker):
        result = svc.get_latest_transcript("L")  # no company_name

    assert result["text"] != "", "should accept any transcript when no expected name given"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/test_transcript_company_match.py -v`
Expected: FAIL — `get_latest_transcript` does not yet accept `company_name`.

- [ ] **Step 3: Add the validator helper**

In `app/services/stock_service.py`, add this private method anywhere above `get_latest_transcript` (above line 1290):

```python
    @staticmethod
    def _transcript_matches_company(transcript_text: str, expected_company: str) -> bool:
        """
        Defensive match: does the transcript text reference the expected company?

        DefeatBeta's HuggingFace dataset has known ticker-collision bugs (e.g.
        'L' returns Loblaw instead of Loews). We verify the first 1500 chars
        of the transcript mention either the full expected name or its first
        token (modulo case and punctuation).

        Returns True if no expected_company was provided (backward compat).
        """
        if not expected_company:
            return True
        if not transcript_text:
            return False

        head = transcript_text[:1500].lower()
        expected_lower = expected_company.lower()
        # Strip common corporate suffixes that may not appear in the call.
        for suffix in (" corporation", " corp", " inc.", " inc", " plc", " ltd", " companies", " co."):
            expected_lower = expected_lower.replace(suffix, "")
        expected_lower = expected_lower.strip(" ,.")

        if not expected_lower:
            return True  # nothing meaningful left to match — accept

        # Match either the full stripped name or its first token.
        first_token = expected_lower.split()[0]
        return expected_lower in head or (len(first_token) >= 3 and first_token in head)
```

- [ ] **Step 4: Modify `get_latest_transcript` to accept and use `company_name`**

Change the signature at line 1290:

```python
    def get_latest_transcript(self, symbol: str, company_name: str = "") -> dict:
```

Then, in the DefeatBeta branch, after the text is assembled (around line 1328) and before the freshness/return decision at 1338-1341, insert a validation step:

```python
                    if isinstance(paragraphs, list):
                        db_text = "\n".join(
                            p.get("content", "")
                            for p in paragraphs
                            if isinstance(p, dict) and p.get("content")
                        )
                    # Defensive: DefeatBeta returns wrong-company transcripts for
                    # some ambiguous tickers (e.g. 'L' → Loblaw). Reject if the
                    # transcript text doesn't reference the expected company.
                    if db_text and not self._transcript_matches_company(db_text, company_name):
                        print(
                            f"[StockService] DefeatBeta company mismatch for {symbol}: "
                            f"expected '{company_name}', transcript head did not match. "
                            f"Discarding and falling through to AV."
                        )
                        db_text = ""
                        db_date_str = None
                        db_age_days = None
```

(Place this immediately after the `db_text = "\n".join(...)` block. The existing `if db_date_str:` age computation should remain — but it now runs against the potentially-cleared `db_text`, which is harmless because we re-check `db_text` at line 1339.)

- [ ] **Step 5: Update the call site to pass the company name**

In `app/services/stock_service.py`, line 1435:

```python
# Before
transcript_data = self.get_latest_transcript(symbol)

# After
transcript_data = self.get_latest_transcript(symbol, company_name=company_name)
```

Verify `company_name` is in scope at line 1435 by reading lines 1392-1440. It is — it's a parameter of `_run_deep_analysis` (line 1392).

- [ ] **Step 6: Run the new tests**

Run: `pytest tests/test_transcript_company_match.py -v`
Expected: ALL pass.

- [ ] **Step 7: Run full transcript-related tests**

Run: `pytest tests/ -k "transcript" -v`
Expected: ALL pass.

- [ ] **Step 8: Commit**

```bash
git add app/services/stock_service.py tests/test_transcript_company_match.py
git commit -m "fix(transcript): reject DefeatBeta results that don't match expected company"
```

---

## Task 7: Drop the doubled `vv` version prefix (covers 7)

**Context:** `git describe --tags --always` already prefixes the result with `v` (e.g. `v0.8.2-106`). Line 113 of `main.py` then prints `f"StockDrop v{VERSION}"`, producing `StockDrop vv0.8.2-106`.

**Files:**
- Modify: `main.py:113`
- Test: Create `tests/test_version_string.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_version_string.py`:

```python
"""Regression: startup banner must not double-prefix the version with 'vv'."""
from unittest.mock import patch
import io


def test_startup_banner_has_no_double_v():
    """get_git_version() returns 'vX.Y.Z'; the banner must not add another 'v'."""
    with patch("subprocess.check_output", return_value=b"v0.8.2-106\n"):
        # Re-import main module fresh so VERSION picks up our patched git output.
        import importlib
        import main
        importlib.reload(main)
        assert "vv" not in f"  StockDrop v{main.VERSION}".replace("v", "v"), (
            "banner string template must not produce 'vv'"
        )
        # Direct assertion on the banner format the user sees:
        banner = f"  StockDrop {main.VERSION}"
        assert not banner.startswith("  StockDrop vv"), banner
        assert banner.startswith("  StockDrop v"), banner
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest tests/test_version_string.py -v`
Expected: FAIL — current code produces `StockDrop vv...`.

- [ ] **Step 3: Drop the leading `v` from the f-string**

In `main.py`, line 113:

```python
# Before
    print(f"  StockDrop v{VERSION}")

# After
    print(f"  StockDrop {VERSION}")
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/test_version_string.py -v`
Expected: PASS.

- [ ] **Step 5: Quick sanity check — also grep for any other `v{VERSION}` in the codebase**

Run: `grep -rn 'v{VERSION}\|v\${VERSION}' --include="*.py" .`
Expected: no results, or only this one which we just fixed. If there are other occurrences (logs, dashboard, email subject), fix each the same way.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_version_string.py
git commit -m "fix(version): drop doubled 'vv' prefix in startup banner"
```

---

## Task 8: Final verification

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: ALL pass. If anything regressed, the offending task is the most recent one — bisect by checking out one commit at a time.

- [ ] **Step 2: Manual smoke test of the citation strip**

Run a small Python REPL invocation against a known corrupted string to confirm no regression:

```bash
python -c "
from app.services.research_service import _strip_citations
samples = [
    ('great news [Source 1]', 'great news'),
    ('signa [Source 1]ling', 'signaling'),
    ('Massive [Source 1]structural [Source 2]unwind', 'Massive structural unwind'),
    ('Phy [Source 6][Source 1]sical', 'Physical'),
    ('UPS [Source 10][Source 11][Source 4][Source 2][Source 9] expected', 'UPS expected'),
]
for raw, expected in samples:
    got = _strip_citations(raw)
    status = 'OK' if got == expected else 'FAIL'
    print(f'{status}: {raw!r} -> {got!r} (expected {expected!r})')
"
```

Expected: every line prints `OK`.

- [ ] **Step 3: Commit any lingering test fixtures**

```bash
git status
# If tests/__pycache__ or similar appeared, ignore them. Otherwise:
git add -p
git commit -m "chore: post-fix cleanup"
```

---

## Out of Scope

Items 6 (retry/resilience working well) and 8 (batch comparison data cleanliness) from the run report require no code changes — item 6 is already correct and item 8 resolves itself once Task 3 (DR dedup) lands. They are intentionally not in this plan.
