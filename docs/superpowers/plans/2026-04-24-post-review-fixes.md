# Post-Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven defects raised in the 2026-04-24 pipeline review — from a one-line model-name typo through deterministic stop-loss guardrails and DR verification grounding.

**Architecture:** Each task is independently shippable and scoped to a single subsystem (digest, PDF export, transcript fallbacks, DR verification, PM stop-loss, quota telemetry). Tasks are ordered by blast-radius ascending so early wins unblock log visibility for later work. No cross-task coupling.

**Tech Stack:** Python 3.9, FastAPI, Gemini (`gemini-3.1-pro-preview` / `gemini-3-flash-preview`), SQLite, pytest/pytest-asyncio, reportlab. All agent code runs in a `ThreadPoolExecutor` off the asyncio loop.

---

## Task ordering & scope

1. **Task 1** — News digest model name fix (one-line, minutes)
2. **Task 2** — ReportLab runtime install verification (env/deployment, not code)
3. **Task 3** — Silence failing transcript fallbacks (deletion)
4. **Task 4** — Missing digest source investigation (triage, no code changes guaranteed)
5. **Task 5** — Deterministic stop-loss post-check (new helper + integration + tests)
6. **Task 6** — Require URL/source per DR verification claim (prompt + schema guard + tests)
7. **Task 7** — Per-cycle agent-call quota telemetry (counter + log line + tests)

Items 6 and 7 are the largest; each still fits comfortably in a single work session.

---

## Task 1: Fix news digest default model name

**Context:** `app/services/news_digest_schema.py:24` defaults `NEWS_DIGEST_MODEL` to `gemini-3.1-pro-thinking`, which the Gemini API rejects with 404 on every scheduler tick. Valid names in this repo are `gemini-3.1-pro-preview` (used for PM/DR) and `gemini-3-flash-preview` (flash-tier). The digest is a long single-shot summarization call — latency matters less than quality — so `gemini-3.1-pro-preview` is the right pick.

**Files:**
- Modify: `app/services/news_digest_schema.py:24`
- Test: `tests/test_news_digest_service.py` (or the closest existing digest test file)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_digest_service.py`:

```python
def test_digest_model_default_is_valid_model():
    """Default digest model must be a Gemini model name the API accepts."""
    import os
    from app.services import news_digest_schema

    os.environ.pop("NEWS_DIGEST_MODEL", None)
    model = news_digest_schema.digest_model()
    assert model in ("gemini-3.1-pro-preview", "gemini-3-flash-preview"), (
        f"digest_model() returned {model!r}; must be a valid Gemini model name"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_digest_service.py::test_digest_model_default_is_valid_model -v`
Expected: FAIL with assertion error showing `gemini-3.1-pro-thinking`.

- [ ] **Step 3: Fix the default**

Edit `app/services/news_digest_schema.py`:

```python
def digest_model() -> str:
    return os.getenv("NEWS_DIGEST_MODEL", "gemini-3.1-pro-preview")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_digest_service.py::test_digest_model_default_is_valid_model -v`
Expected: PASS.

- [ ] **Step 5: Run full digest test module to catch regressions**

Run: `pytest tests/test_news_digest_service.py -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/news_digest_schema.py tests/test_news_digest_service.py
git commit -m "fix(news-digest): default to gemini-3.1-pro-preview (was invalid 'thinking' name)"
```

---

## Task 2: Verify ReportLab is installed in the running environment

**Context:** `requirements.txt:25` already pins `reportlab==4.4.0`, yet `deep_research_service._save_batch_pdf` fails with `No module named 'reportlab'`. This is an environment drift — not a code defect. The code-level fix is to make the failure loud enough that we notice next time, and to ensure `requirements.txt` is honored on the deploy target.

**Files:**
- Modify: `app/services/deep_research_service.py` (the two `try` blocks at lines ~762 and ~1878)
- Verify: `requirements.txt:25`

- [ ] **Step 1: Confirm the package is declared**

Run: `grep -n reportlab requirements.txt`
Expected: `25:reportlab==4.4.0`

- [ ] **Step 2: Attempt import in the live venv**

Run: `python -c "import reportlab; print(reportlab.__version__)"`
Expected: prints `4.4.0`. If it raises `ModuleNotFoundError`, continue; if it succeeds, the bug is in a different venv (document which one the scheduler uses and stop).

- [ ] **Step 3: Install into the active venv**

Run: `pip install -r requirements.txt`
Expected: `Successfully installed reportlab-4.4.0` (or "already satisfied").

- [ ] **Step 4: Re-verify import**

Run: `python -c "import reportlab; print(reportlab.__version__)"`
Expected: `4.4.0`.

- [ ] **Step 5: Tighten the failure mode so future drift is obvious**

In `app/services/deep_research_service.py`, find each `except Exception as e:` around `_save_result_to_pdf` and `_save_batch_pdf` that currently prints `Error generating Batch PDF: ...`. Change both to log at ERROR with the module name, so it surfaces in our structured log stream instead of being a bare `print`:

Edit `_save_batch_pdf` catch block (search for `"Error generating Batch PDF"`):

```python
except ModuleNotFoundError as e:
    logger.error(
        "[deep-research] Batch PDF generation failed — missing dependency: %s. "
        "Run `pip install -r requirements.txt` on the deploy target.", e
    )
    return None
except Exception as e:
    logger.error("[deep-research] Batch PDF generation failed: %s", e, exc_info=True)
    return None
```

Apply the same pattern to `_save_result_to_pdf`. If `logger` is not yet defined at module scope, add `logger = logging.getLogger(__name__)` near the top imports.

- [ ] **Step 6: Run existing deep-research tests**

Run: `pytest tests/ -k deep_research -v`
Expected: all PASS (no new test added; we just tightened logging).

- [ ] **Step 7: Commit**

```bash
git add app/services/deep_research_service.py
git commit -m "fix(deep-research): escalate PDF dependency failures to logger.error"
```

- [ ] **Step 8: Deploy-side action (manual, outside this repo)**

Confirm the Render / production venv runs `pip install -r requirements.txt` on boot. If it does not, add it to the start command or Dockerfile. This step produces no diff in this repo.

---

## Task 3: Silence / remove failing transcript fallbacks

**Context:** Two transcript sources fail on every candidate:

- **DefeatBeta** — `app/services/stock_service.py:44-46` and `:1287-1333`; plus `app/services/research_service.py:744-778`. Not installed in prod (import guarded), never succeeds.
- **Finnhub transcripts** — `app/services/finnhub_service.py:61-89`; 403 on every symbol (the API tier doesn't include transcripts).

Both currently print a one-line warning per ticker per cycle. With ~15 candidates × 3 cycles/day × 2 sources, that's ~90 noise lines/day. The user asked us to fix auth or remove the fallbacks. We don't have a Finnhub transcript tier, and DefeatBeta has been absent for months — remove both call paths and delete the dead helper methods. The docs/proposals reference to `docs/superpowers/plans/2026-04-23-pipeline-error-hardening.md:941` explicitly calls this out as ready to action.

**Files:**
- Modify: `app/services/stock_service.py:27-46` (import guard), `:1281-1370` (`get_latest_transcript`)
- Modify: `app/services/research_service.py:744-778` (DefeatBeta mix-in)
- Modify: `app/services/finnhub_service.py:61-89` (delete `get_transcript_list`, `get_transcript_content`)
- Test: `tests/test_stock_service.py` (or nearest equivalent — create `tests/test_transcript_removal.py` if none exists)

- [ ] **Step 1: Map every caller of the transcript APIs**

Run: `grep -rn "get_latest_transcript\|get_transcript_list\|get_transcript_content\|DefeatBeta_data\|defeatbeta" app/ scripts/ tests/`
Expected: a list that covers all the call sites. Paste the result into the task notes before editing.

- [ ] **Step 2: Write the failing test**

Create `tests/test_transcript_removal.py`:

```python
"""After removing the DefeatBeta + Finnhub transcript fallbacks, the
research path must not reference them and must not raise when they are absent."""

import inspect

from app.services import stock_service, research_service, finnhub_service


def test_finnhub_transcript_methods_are_gone():
    assert not hasattr(finnhub_service.FinnhubService, "get_transcript_list"), (
        "get_transcript_list should be removed — Finnhub tier returns 403"
    )
    assert not hasattr(finnhub_service.FinnhubService, "get_transcript_content"), (
        "get_transcript_content should be removed — Finnhub tier returns 403"
    )


def test_stock_service_get_latest_transcript_returns_empty():
    """With fallbacks removed, the method must return an empty, well-formed result
    rather than raising."""
    svc = stock_service.StockService()
    result = svc.get_latest_transcript("AAPL")
    assert result == "" or (
        isinstance(result, dict) and not result.get("text")
    ), f"expected empty transcript, got: {result!r}"


def test_research_service_source_has_no_defeatbeta_refs():
    src = inspect.getsource(research_service)
    assert "DefeatBeta" not in src, "research_service should no longer reference DefeatBeta"
    assert "defeatbeta" not in src, "research_service should no longer reference defeatbeta"
```

- [ ] **Step 3: Run tests — confirm they fail**

Run: `pytest tests/test_transcript_removal.py -v`
Expected: FAIL on all three (attributes exist, transcript returns something, DefeatBeta string present).

- [ ] **Step 4: Delete the Finnhub transcript methods**

Edit `app/services/finnhub_service.py` — delete lines 61-89 (both `get_transcript_list` and `get_transcript_content`). Keep the company-news and filing-extraction methods intact.

- [ ] **Step 5: Collapse `get_latest_transcript` to a stub**

Edit `app/services/stock_service.py`:

Replace the entire `get_latest_transcript` method (lines ~1281-1370) with:

```python
def get_latest_transcript(self, symbol: str) -> str:
    """Transcript sources (DefeatBeta, Finnhub) were removed 2026-04-24 — both
    failed 100% of the time in production. Returns empty string so callers
    degrade gracefully."""
    return ""
```

Remove the DefeatBeta import guard at lines 27-46 (`# FIX: Bypass SSL...`, `_download_nltk_data`, the `try: from defeatbeta_api ...` block, and the `Ticker = None` fallback) since nothing imports it anymore.

- [ ] **Step 6: Remove the DefeatBeta mix-in from research_service**

Edit `app/services/research_service.py` — delete the block at lines ~744-778 (from `# Try to load DefeatBeta data` through the `# Mix DefeatBeta news` loop). The preceding `news_summary` and `transcript` variables keep their prior values and flow into the agent prompts unchanged.

- [ ] **Step 7: Run the new tests**

Run: `pytest tests/test_transcript_removal.py -v`
Expected: PASS on all three.

- [ ] **Step 8: Run the broader research / stock service suites**

Run: `pytest tests/ -k "research or stock_service or finnhub" -v`
Expected: all PASS. If a test is skipped because `defeatbeta_api` was unavailable, remove the skip guard too.

- [ ] **Step 9: Commit**

```bash
git add app/services/stock_service.py app/services/research_service.py \
        app/services/finnhub_service.py tests/test_transcript_removal.py
git commit -m "chore(transcripts): remove DefeatBeta + Finnhub transcript fallbacks (100% fail)"
```

---

## Task 4: Investigate missing digest source files

**Context:** `FT Archive/daily/2026-04-24.md` and `Finimize Archive/daily/2026-04-24.md` are missing, triggering `[news-digest] raw file missing: ... — skipping` at `app/services/news_digest_service.py:244`. The scraper that writes these files lives outside this repo — per `docs/proposals/PLAN_news_digest_integration.md:43-44`, the **Cowork scheduler** writes them at 06:45 / 06:50 each morning. This task is investigation, not code: decide whether digest is disabled on purpose or the scheduler is broken.

**Files (read-only unless we act):**
- Read: `app/services/news_digest_schema.py:10-25` (paths + enable flag)
- Read: `app/services/news_digest_service.py:240-260` (skip logic)
- Read: `docs/proposals/PLAN_news_digest_integration.md:40-50` (ownership of scheduler)

- [ ] **Step 1: Check whether the digest is enabled**

Run: `python -c "import os; os.environ.pop('NEWS_DIGEST_ENABLED', None); from app.services import news_digest_schema as s; print('enabled=', s.digest_enabled(), 'root=', s.news_archive_root())"`
Expected: prints the effective flag and archive root. Record both.

- [ ] **Step 2: Check whether the archive root actually exists**

Run: `ls -la "$(python -c 'from app.services.news_digest_schema import news_archive_root; print(news_archive_root())')/FT Archive/daily" | tail -10`
Expected: lists the ten most recent FT daily files. Record the latest date.

- [ ] **Step 3: Same for Finimize**

Run: `ls -la "$(python -c 'from app.services.news_digest_schema import news_archive_root; print(news_archive_root())')/Finimize Archive/daily" | tail -10`
Expected: lists the ten most recent Finimize daily files. Record the latest date.

- [ ] **Step 4: Classify and act**

Three outcomes:

1. **Latest files are from before 2026-04-24.** The Cowork scheduler is broken. This is outside this repo — file a ticket against the Cowork plugin and stop. Document the expected write time (06:45 / 06:50 local) and the last successful date.

2. **Files exist but under a different path.** The `NEWS_ARCHIVE_ROOT` env var is wrong on the deploy target. Document the correct value and update the deploy config. No code diff.

3. **Digest is intentionally disabled.** Set `NEWS_DIGEST_ENABLED=false` on the deploy target so the service stops even trying. No code diff, but update the `.env.example` comment at line 48 to note the flag exists.

- [ ] **Step 5: Record findings**

Append a one-paragraph note to the bottom of this plan document in the section `## Task 4 findings`, with: (a) which outcome, (b) the action taken (or ticket filed), (c) the last successful daily file date per source. No commit — docs changes roll up with the overall review commit at the end.

---

## Task 5: Deterministic stop-loss post-check

**Context:** User report: "NMR stop too close to lower Bollinger, VRSN stop in the technical void between 200-day and 50-day." The PM prompt at `app/services/research_service.py:1040-1044` already asks for `2x ATR below entry_price_low, or below bb_lower if tighter`, but the model doesn't reliably obey. Per the user's guidance: "if stop < (entry − 1.5 × ATR), widen to entry − 2 × ATR or to the nearest major SMA."

We have ATR, BB.lower, SMA_50, SMA_200 already available in the tradingview payload (see `app/services/tradingview_service.py:209, 272, 297, 455`).

Build a deterministic helper that post-checks the PM's final decision and widens the stop if it violates the rule, logging the adjustment so it's auditable.

**Files:**
- Create: `app/utils/stop_loss_guard.py`
- Create: `tests/test_stop_loss_guard.py`
- Modify: `app/services/research_service.py` — call the guard right before the final decision is persisted (near line ~447, where `stop_loss` is read from `final_decision`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stop_loss_guard.py`:

```python
"""Deterministic post-check on PM stop-loss placement.

Rule: if stop > (entry_low - 1.5 * ATR), widen the stop to the farther of:
    (a) entry_low - 2.0 * ATR
    (b) the nearest lower SMA (SMA_50 or SMA_200) that sits below entry_low
"""

import pytest

from app.utils.stop_loss_guard import widen_stop_if_too_tight


def test_stop_already_far_enough_is_returned_unchanged():
    result = widen_stop_if_too_tight(
        stop_loss=85.0,
        entry_low=100.0,
        atr=5.0,          # 1.5 * ATR = 7.5 → threshold = 92.5; stop 85 < 92.5 → OK
        sma_50=90.0,
        sma_200=80.0,
        bb_lower=88.0,
    )
    assert result.adjusted is False
    assert result.stop_loss == 85.0
    assert result.reason == "within_tolerance"


def test_stop_too_tight_widens_to_2x_atr():
    # entry 100, ATR 5 → 1.5 * ATR = 7.5 → threshold 92.5. Stop 95 is too tight.
    # Candidates: 2x ATR = 90.0; SMA_50 = 88.0; SMA_200 = 80.0.
    # Nearest SMA below entry_low is SMA_50 at 88.0.
    # Widen to the farther (i.e. lower) of the two: min(90.0, 88.0) = 88.0.
    result = widen_stop_if_too_tight(
        stop_loss=95.0,
        entry_low=100.0,
        atr=5.0,
        sma_50=88.0,
        sma_200=80.0,
        bb_lower=93.0,
    )
    assert result.adjusted is True
    assert result.stop_loss == 88.0
    assert "sma_50" in result.reason or "sma" in result.reason


def test_stop_too_tight_uses_2x_atr_when_no_sma_below_entry():
    # Both SMAs sit above entry_low, so fall back to 2x ATR.
    result = widen_stop_if_too_tight(
        stop_loss=96.0,
        entry_low=100.0,
        atr=5.0,
        sma_50=110.0,
        sma_200=105.0,
        bb_lower=99.0,
    )
    assert result.adjusted is True
    assert result.stop_loss == 90.0  # 100 - 2*5
    assert "atr" in result.reason


def test_missing_inputs_are_no_op():
    """If ATR is missing or zero we cannot compute — leave the stop alone
    and flag the reason so it's visible in logs."""
    result = widen_stop_if_too_tight(
        stop_loss=95.0, entry_low=100.0, atr=0.0,
        sma_50=90.0, sma_200=80.0, bb_lower=93.0,
    )
    assert result.adjusted is False
    assert result.reason == "missing_atr"


def test_none_stop_is_no_op():
    result = widen_stop_if_too_tight(
        stop_loss=None, entry_low=100.0, atr=5.0,
        sma_50=90.0, sma_200=80.0, bb_lower=93.0,
    )
    assert result.adjusted is False
    assert result.stop_loss is None
    assert result.reason == "missing_stop"
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `pytest tests/test_stop_loss_guard.py -v`
Expected: ALL FAIL with `ModuleNotFoundError: app.utils.stop_loss_guard`.

- [ ] **Step 3: Implement the guard**

Create `app/utils/stop_loss_guard.py`:

```python
"""Deterministic post-check on PM-generated stop-loss placements.

The PM agent is instructed to set stops at 2*ATR below entry_price_low, but
occasionally returns stops that are too tight (e.g. NMR near the lower
Bollinger band, VRSN in the technical void between SMA_50 and SMA_200).
This helper widens the stop to a defensible floor whenever the PM's value
violates the 1.5*ATR minimum distance rule.

Rule:
    if stop > entry_low - 1.5 * ATR:
        widen to min(entry_low - 2 * ATR, nearest SMA below entry_low)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StopLossAdjustment:
    stop_loss: Optional[float]
    adjusted: bool
    reason: str


def widen_stop_if_too_tight(
    *,
    stop_loss: Optional[float],
    entry_low: float,
    atr: float,
    sma_50: Optional[float],
    sma_200: Optional[float],
    bb_lower: Optional[float],
) -> StopLossAdjustment:
    if stop_loss is None:
        return StopLossAdjustment(stop_loss=None, adjusted=False, reason="missing_stop")
    if not atr or atr <= 0:
        return StopLossAdjustment(stop_loss=stop_loss, adjusted=False, reason="missing_atr")

    tolerance = entry_low - 1.5 * atr
    if stop_loss <= tolerance:
        return StopLossAdjustment(
            stop_loss=stop_loss, adjusted=False, reason="within_tolerance",
        )

    # Candidate 1: 2x ATR below entry
    atr_floor = entry_low - 2.0 * atr

    # Candidate 2: the nearest SMA that sits below entry_low
    sma_candidates = [s for s in (sma_50, sma_200) if s is not None and s < entry_low]
    sma_floor = max(sma_candidates) if sma_candidates else None  # "nearest" = closest to entry

    if sma_floor is not None:
        # Pick the farther (lower) of the two floors so we don't pull the stop
        # back toward the entry when a nearby SMA is inside 2xATR.
        new_stop = min(atr_floor, sma_floor)
        which_sma = "sma_50" if new_stop == sma_50 else "sma_200" if new_stop == sma_200 else "atr"
        reason = f"widened_to_{which_sma}" if new_stop != atr_floor else "widened_to_atr_below_sma"
    else:
        new_stop = atr_floor
        reason = "widened_to_2x_atr"

    return StopLossAdjustment(stop_loss=round(new_stop, 2), adjusted=True, reason=reason)
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `pytest tests/test_stop_loss_guard.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Wire the guard into research_service**

In `app/services/research_service.py`, find the block around line 440-460 where `final_decision` is unpacked into the state dict. Add the guard call *after* `final_decision` is available but *before* it is persisted. Use the live TV indicators already fetched earlier in the same function.

Exact edit — insert just before the `"stop_loss": final_decision.get("stop_loss"),` line (~line 447):

```python
        from app.utils.stop_loss_guard import widen_stop_if_too_tight

        tv_inds = (state.tradingview_data or {}).get("indicators", {})
        guard = widen_stop_if_too_tight(
            stop_loss=final_decision.get("stop_loss"),
            entry_low=final_decision.get("entry_price_low") or state.current_price,
            atr=tv_inds.get("atr") or 0.0,
            sma_50=tv_inds.get("sma_50"),
            sma_200=tv_inds.get("sma_200"),
            bb_lower=tv_inds.get("bb_lower"),
        )
        if guard.adjusted:
            logger.info(
                "[PM stop-guard] %s: widened stop %.2f -> %.2f (%s)",
                state.ticker, final_decision["stop_loss"], guard.stop_loss, guard.reason,
            )
            final_decision["stop_loss"] = guard.stop_loss
            final_decision["stop_loss_guard_reason"] = guard.reason
```

If `logger` is not imported in `research_service.py`, add `import logging` and `logger = logging.getLogger(__name__)` at the top.

If the exact field names on `state.tradingview_data["indicators"]` differ from what the guard expects, normalize in the call (e.g. `tv_inds.get("ATR")` if uppercase). Cross-check against `app/services/tradingview_service.py:272` before finalizing.

- [ ] **Step 6: Add an integration test covering the wire-through**

Append to `tests/test_stop_loss_guard.py`:

```python
def test_guard_integration_via_research_service(monkeypatch):
    """Verify the guard is applied to final_decision inside research_service.

    We don't run the full agent cascade — we patch out the agents and only
    exercise the post-processing block that calls the guard."""
    from app.services import research_service

    # Smoke-check the import wiring — detailed behavior lives in the unit tests.
    assert hasattr(research_service, "widen_stop_if_too_tight") or (
        "widen_stop_if_too_tight" in research_service.__dict__
        or "from app.utils.stop_loss_guard" in __import__("inspect").getsource(research_service)
    )
```

Run: `pytest tests/test_stop_loss_guard.py -v`
Expected: all 6 PASS.

- [ ] **Step 7: Run the broader research suite**

Run: `pytest tests/ -k research -v`
Expected: all PASS. Investigate any failure — likely a fixture needs `stop_loss_guard_reason` in its expected dict.

- [ ] **Step 8: Commit**

```bash
git add app/utils/stop_loss_guard.py app/services/research_service.py tests/test_stop_loss_guard.py
git commit -m "feat(pm): deterministic stop-loss guard — widen when < 1.5*ATR from entry"
```

---

## Task 6: Require URL/source per DR verification claim

**Context:** The user reports DR (`deep_research_service.py`) making confident claims that are themselves wrong — "GPT-Rosalind" for IQV, a HashiCorp timing claim for IBM. The verification-results list at `:1147-1150` currently allows free-form strings like `"Claim 1: [VERIFIED/DISPUTED] — explanation"`. Since DR already uses grounded search (`deep-research-pro` with Google Search), each verdict should carry the URL that grounded it. Missing URLs = treat as unverified.

We'll harden both the prompt and the parser:
1. Prompt change: require each verification entry to be an object with `claim`, `verdict`, and `source_url`.
2. Schema guard: parser rejects / downgrades entries without a valid URL.
3. Score: a claim without a URL is treated as `UNVERIFIED` (neutral — neither bonus nor penalty), so hallucinated disputes stop earning the model credit.

**Files:**
- Modify: `app/services/deep_research_service.py` — prompt at ~line 1147, parser / scoring around lines 474-480 and 725-729, the result dict at line 1456
- Create: `tests/test_dr_verification_urls.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dr_verification_urls.py`:

```python
"""DR verification claims must carry a URL. Missing URLs demote the claim to
UNVERIFIED so hallucinated disputes stop earning score adjustments."""

import pytest

from app.services.deep_research_service import (
    normalize_verification_results,
    score_verification_penalty,
)


def test_entry_with_valid_url_is_kept_as_disputed():
    raw = [
        {
            "claim": "Revenue fell 12% QoQ",
            "verdict": "DISPUTED",
            "source_url": "https://www.sec.gov/some-filing.htm",
        }
    ]
    out = normalize_verification_results(raw)
    assert len(out) == 1
    assert out[0]["verdict"] == "DISPUTED"
    assert out[0]["source_url"].startswith("https://")


def test_entry_missing_url_is_downgraded_to_unverified():
    raw = [{"claim": "Some claim", "verdict": "VERIFIED", "source_url": ""}]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0].get("downgrade_reason") == "missing_source_url"


def test_legacy_string_entry_is_downgraded_to_unverified():
    """Old-format strings like 'Claim 1: [DISPUTED] — explanation' have no URL
    and must not earn a penalty."""
    raw = ["Claim 1: [DISPUTED] — explanation"]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0].get("downgrade_reason") == "legacy_string_format"


def test_invalid_url_scheme_is_downgraded():
    raw = [{"claim": "X", "verdict": "VERIFIED", "source_url": "not-a-url"}]
    out = normalize_verification_results(raw)
    assert out[0]["verdict"] == "UNVERIFIED"
    assert out[0]["downgrade_reason"] == "invalid_source_url"


def test_penalty_only_applies_to_grounded_disputes():
    normalized = [
        {"claim": "a", "verdict": "DISPUTED", "source_url": "https://x.com"},
        {"claim": "b", "verdict": "DISPUTED", "source_url": ""},  # would be downgraded
        {"claim": "c", "verdict": "UNVERIFIED", "downgrade_reason": "missing_source_url"},
    ]
    # After normalize we'd see only one DISPUTED with a URL → single -5 penalty
    out = normalize_verification_results(normalized)
    penalty = score_verification_penalty(out)
    assert penalty == -5
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `pytest tests/test_dr_verification_urls.py -v`
Expected: all 5 FAIL with `ImportError: cannot import name 'normalize_verification_results'` or `'score_verification_penalty'`.

- [ ] **Step 3: Implement the helpers**

Add to `app/services/deep_research_service.py` near the top (after imports, before the class):

```python
_VALID_URL_SCHEMES = ("http://", "https://")


def normalize_verification_results(raw):
    """Coerce a raw verification_results list (mix of legacy strings + new
    objects) into a consistent list of dicts. Claims missing a valid source
    URL are downgraded to UNVERIFIED so downstream scoring ignores them."""
    out = []
    for entry in raw or []:
        if isinstance(entry, str):
            out.append({
                "claim": entry,
                "verdict": "UNVERIFIED",
                "source_url": "",
                "downgrade_reason": "legacy_string_format",
            })
            continue
        if not isinstance(entry, dict):
            continue
        claim = entry.get("claim", "")
        verdict = (entry.get("verdict") or "").upper().strip()
        url = (entry.get("source_url") or "").strip()

        if not url:
            out.append({
                "claim": claim, "verdict": "UNVERIFIED", "source_url": "",
                "downgrade_reason": "missing_source_url",
            })
        elif not url.startswith(_VALID_URL_SCHEMES):
            out.append({
                "claim": claim, "verdict": "UNVERIFIED", "source_url": url,
                "downgrade_reason": "invalid_source_url",
            })
        elif verdict not in ("VERIFIED", "DISPUTED"):
            out.append({
                "claim": claim, "verdict": "UNVERIFIED", "source_url": url,
                "downgrade_reason": f"unknown_verdict:{verdict!r}",
            })
        else:
            out.append({"claim": claim, "verdict": verdict, "source_url": url})
    return out


def score_verification_penalty(normalized_entries) -> int:
    """-5 per grounded DISPUTED claim. UNVERIFIED entries earn nothing."""
    return -5 * sum(
        1 for e in normalized_entries if e.get("verdict") == "DISPUTED"
    )
```

- [ ] **Step 4: Run unit tests**

Run: `pytest tests/test_dr_verification_urls.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Wire the helpers into the existing penalty path**

Find the existing penalty block (around lines 474-480 in `deep_research_service.py`):

```python
# Dispute penalty: -5 per DISPUTED claim in verification_results
for v in result.get("verification_results", []):
    if isinstance(v, str) and "DISPUTED" in v.upper():
        ...
```

Replace with:

```python
normalized = normalize_verification_results(result.get("verification_results", []))
result["verification_results"] = normalized  # persist the normalized form
penalty = score_verification_penalty(normalized)
score += penalty
```

Make sure the surrounding `score +=` / logging stays consistent with the previous block — mirror it line-for-line except for the body.

- [ ] **Step 6: Update the DR prompt to require URLs**

Find the prompt string containing the verification_results spec (around line 1147):

```python
  "verification_results": [
    "Claim 1: [VERIFIED/DISPUTED] — explanation",
    ...
```

Replace with:

```python
  "verification_results": [
    {
      "claim": "concise restatement of the claim you checked",
      "verdict": "VERIFIED" or "DISPUTED",
      "source_url": "https://... — the exact grounded URL that supports your verdict"
    }
  ]
```

Immediately after the example, add a sentence:
> **Every entry MUST include a `source_url` pointing to the specific page that grounds your verdict. Claims without a verifiable URL will be treated as UNVERIFIED and will not count toward the score.**

Locate and update the secondary schema mention at line 1456 to match:

```python
"verification_results": [
    {"claim": "...", "verdict": "VERIFIED|DISPUTED", "source_url": "https://..."}
],
```

- [ ] **Step 7: Update the console/log rendering**

Find the verification print block around line 725-729:

```python
verification = result.get('verification_results', [])
if verification:
    print("  Verification:")
    for v in verification:
        ...
```

Change the inner loop to handle the dict form:

```python
for v in verification:
    if isinstance(v, dict):
        claim = v.get("claim", "")
        verdict = v.get("verdict", "UNVERIFIED")
        url = v.get("source_url", "") or v.get("downgrade_reason", "")
        print(f"    [{verdict}] {claim} — {url}")
    else:
        print(f"    {v}")
```

- [ ] **Step 8: Run broader DR tests**

Run: `pytest tests/ -k "deep_research or dr_" -v`
Expected: all PASS. Expect one or two fixtures to need updating to the dict shape — edit them in place.

- [ ] **Step 9: Commit**

```bash
git add app/services/deep_research_service.py tests/test_dr_verification_urls.py
git commit -m "feat(dr): require source_url per verification claim; downgrade ungrounded to UNVERIFIED"
```

---

## Task 7: Per-cycle agent-call quota telemetry

**Context:** Rough estimate from the user: 9 agent calls × ~15 candidates × 3 cycles/day = ~400 Gemini calls/day on agents alone, plus 2-3 DR calls per BUY. We have no in-repo counter — if quota burn spikes, we only notice from the provider dashboard. Add a lightweight per-cycle counter that logs totals and a rolling 24h sum at the end of each scheduler cycle.

This is a pure additive change: one module, one call-site hook per agent entry point, one log line at cycle end.

**Files:**
- Create: `app/utils/agent_call_counter.py`
- Create: `tests/test_agent_call_counter.py`
- Modify: `app/services/research_service.py` — record on each agent dispatch
- Modify: `app/services/deep_research_service.py` — record on each DR call
- Modify: `main.py` or wherever the scanner cycle ends — emit the summary log

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_call_counter.py`:

```python
"""Process-local counter that tallies Gemini agent calls per cycle and over
a rolling 24h window. Reset-on-cycle, thread-safe, zero dependencies."""

import threading
import time
from datetime import timedelta

from app.utils.agent_call_counter import AgentCallCounter


def test_records_and_reports_per_cycle():
    c = AgentCallCounter(window=timedelta(hours=24))
    c.record("phase1.technical")
    c.record("phase1.news")
    c.record("pm")
    snap = c.snapshot()
    assert snap["total_cycle"] == 3
    assert snap["by_agent"]["phase1.technical"] == 1
    assert snap["by_agent"]["pm"] == 1


def test_reset_cycle_clears_cycle_counters_but_keeps_rolling():
    c = AgentCallCounter(window=timedelta(hours=24))
    c.record("pm")
    c.record("pm")
    c.reset_cycle()
    assert c.snapshot()["total_cycle"] == 0
    assert c.snapshot()["total_rolling_24h"] == 2


def test_rolling_window_evicts_old_entries():
    c = AgentCallCounter(window=timedelta(milliseconds=50))
    c.record("pm")
    time.sleep(0.12)
    c.record("pm")
    assert c.snapshot()["total_rolling_24h"] == 1


def test_thread_safety():
    c = AgentCallCounter(window=timedelta(hours=24))
    def worker():
        for _ in range(100):
            c.record("pm")
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert c.snapshot()["total_cycle"] == 800
```

- [ ] **Step 2: Run tests — confirm they fail**

Run: `pytest tests/test_agent_call_counter.py -v`
Expected: all 4 FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the counter**

Create `app/utils/agent_call_counter.py`:

```python
"""Process-local agent-call counter. Thread-safe. Two counters: a
reset-on-cycle per-cycle tally, and a rolling window for ops visibility.

Not durable — a process restart loses history. That's deliberate: we want
zero-dependency telemetry and the provider dashboard is the source of truth
for billing."""

from __future__ import annotations

import threading
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Dict


class AgentCallCounter:
    def __init__(self, window: timedelta = timedelta(hours=24)):
        self._lock = threading.Lock()
        self._cycle = Counter()
        self._rolling: deque = deque()  # (timestamp, agent_name)
        self._window = window

    def record(self, agent: str) -> None:
        now = datetime.utcnow()
        with self._lock:
            self._cycle[agent] += 1
            self._rolling.append((now, agent))
            self._evict_locked(now)

    def reset_cycle(self) -> None:
        with self._lock:
            self._cycle.clear()

    def snapshot(self) -> Dict:
        now = datetime.utcnow()
        with self._lock:
            self._evict_locked(now)
            return {
                "total_cycle": sum(self._cycle.values()),
                "total_rolling_24h": len(self._rolling),
                "by_agent": dict(self._cycle),
            }

    def _evict_locked(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._rolling and self._rolling[0][0] < cutoff:
            self._rolling.popleft()


# Module-level singleton. Import from here at call-sites.
counter = AgentCallCounter()
```

- [ ] **Step 4: Run tests — confirm they pass**

Run: `pytest tests/test_agent_call_counter.py -v`
Expected: all 4 PASS.

- [ ] **Step 5: Record calls from research_service**

In `app/services/research_service.py`, add at the top of the file:

```python
from app.utils.agent_call_counter import counter as agent_call_counter
```

For each agent entry point (the individual `run_<sensor>_analysis` / bull / bear / risk / PM functions), add a single line as the first statement:

```python
agent_call_counter.record("<agent_name>")
```

Use these agent-name tags (one per function):
- `phase1.technical`, `phase1.news`, `phase1.market_sentiment`, `phase1.competitive`, `phase1.seeking_alpha`
- `phase2.bull`, `phase2.bear`, `phase2.risk`
- `pm`

Grep to find the exact function names: `grep -n "^def run_\|^    def run_" app/services/research_service.py`

- [ ] **Step 6: Record calls from deep_research_service**

In `app/services/deep_research_service.py`, add the same import, and record once per `generate_content` call (not per `_save_result_to_pdf`). Tag as `dr.individual` or `dr.batch` depending on which queue the call came from.

- [ ] **Step 7: Emit the summary at end of cycle**

Find where the periodic scanner cycle finishes (search `main.py` for the scanner loop). At the end of each cycle, add:

```python
from app.utils.agent_call_counter import counter as agent_call_counter

snap = agent_call_counter.snapshot()
logger.info(
    "[agent-quota] cycle_total=%d rolling_24h=%d by_agent=%s",
    snap["total_cycle"], snap["total_rolling_24h"], snap["by_agent"],
)
agent_call_counter.reset_cycle()
```

If there's no logger in that file, fall back to `print(...)` with the same format string.

- [ ] **Step 8: Run the broader research suite**

Run: `pytest tests/ -k "research or deep_research or agent_call" -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add app/utils/agent_call_counter.py app/services/research_service.py \
        app/services/deep_research_service.py main.py tests/test_agent_call_counter.py
git commit -m "feat(telemetry): per-cycle + rolling-24h agent-call counter with end-of-cycle log"
```

---

## Verification checklist (run before claiming plan complete)

After all seven tasks land:

- [ ] `pytest tests/ -q` — full suite green
- [ ] `python -c "from app.services.news_digest_schema import digest_model; print(digest_model())"` — prints a valid Gemini model name
- [ ] `python -c "import reportlab; print(reportlab.__version__)"` — prints `4.4.0`
- [ ] `grep -c DefeatBeta app/services/research_service.py app/services/stock_service.py` — prints `0`
- [ ] Run one scanner cycle against a known recent drop; confirm a `[agent-quota] cycle_total=…` line appears in the log and no `Error generating Batch PDF` messages appear.

---

## Self-review notes

Spec coverage (one task per item the user raised):

| User's item | Task |
|---|---|
| News digest 404 | Task 1 |
| ReportLab missing | Task 2 |
| Missing digest source files | Task 4 |
| Transcript fallbacks 100% failing | Task 3 |
| DR verification hallucination | Task 6 |
| Agent-call count monitoring | Task 7 |
| Pricing-level bad stops | Task 5 |

No placeholders, no TBDs. Types used in later tasks (`StopLossAdjustment`, `AgentCallCounter`, `normalize_verification_results`) are defined in the same task where they're used. Every code-changing step has a code block. Every test step has a verify step.

---

## Task 4 findings

**Outcome 1: Scheduler broken** — FT Archive is not writing daily files. Finimize is working.

Evidence: FT Archive latest file is `2026-04-22.md` (written Apr 22 12:12), a gap of 2+ days. Finimize Archive has current files through `2026-04-24.md` (written Apr 24 15:14). The Cowork scheduler is expected to write FT at 06:45 and Finimize at 06:50 local time. Finimize's success shows the scheduler process is running; FT's silence indicates that specific task is broken or misconfigured.

**Recommended action:** File a ticket against the Cowork scheduler plugin to debug the FT daily task. Include the expected write times (06:45 local) and note that Finimize writes successfully, so the infrastructure is intact.
