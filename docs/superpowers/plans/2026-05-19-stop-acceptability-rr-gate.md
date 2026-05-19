# Stop-Acceptability Gate: Switch from Downside% Ceiling to R/R Floor + Widen Downside Backstop

> **For agentic workers (Claude Code):** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Work directly on `main` — no worktrees, no PRs, no GitHub. The project's CLAUDE.md mandates this for local-dev iteration speed.

**Goal:** Replace the current absolute-downside ceiling in `evaluate_stop_acceptability` with an R/R-based primary gate (reject if `R/R < 0.3`), and widen the downside backstop from 15% → 50%. The current rule kills any setup with post-widen downside > 15% regardless of upside, which prevents asymmetric high-R/R trades (e.g., 3.0x R/R with -20% downside) from being published. The new rule only kills mathematically-broken outputs (`R/R < 0.3x`) and catastrophically-wide stops (> 50% downside).

**Architecture:** All changes localized to `app/utils/stop_loss_guard.py` (constants + function signature) and a single line in the only caller, `app/services/research_service.py`. The R/R value is already computed by `recompute_risk_metrics` (lines 567-575) and stored in `final_decision["risk_reward_ratio"]` immediately *before* `evaluate_stop_acceptability` is invoked at line 577 — so threading it through is mechanical. The function signature stays backward-compatible: the new `risk_reward_ratio` arg defaults to `None`, in which case the R/R gate is skipped and only the downside backstop applies (legacy behavior, just with a wider ceiling).

**Tech Stack:** Python 3.9, pytest.

---

## File map

**Files modified:**

- `app/utils/stop_loss_guard.py` — constants, dataclass, gate logic
- `app/services/research_service.py` — pass `risk_reward_ratio` to the gate (one line)
- `tests/test_stop_loss_guard.py` — migrate boundary tests from 15% to 50%, add R/R-gate tests

**Files read for context (no edits):**

- `app/services/research_service.py:565-596` — existing call site
- `app/utils/stop_loss_guard.py` — full file
- `tests/test_stop_loss_guard.py` — existing test fixtures
- `tests/test_recompute_risk_metrics.py` — sanity check that R/R math is unchanged

**Inputs:**

- Clean checkout on `main`
- Working pytest install

**Outputs:**

- 2 modified Python files + 1 extended test file
- All tests pass (`pytest tests/test_stop_loss_guard.py tests/test_recompute_risk_metrics.py -v`)
- Updated docstrings reflecting the new thresholds and gate order

---

## Behavior matrix (target)

| Scenario | R/R | Downside | Old (15% ceiling) | **New (R/R 0.3 floor + 50% backstop)** |
|---|---|---|---|---|
| AAOI prior session | 0.2x | -56% | reject | reject (both gates) |
| FORM (2026-05-18) | 0.2x | -35.8% | reject | reject (R/R gate) |
| VICR (2026-05-18) | 0.1x | n/a | accept | reject (R/R gate) |
| IREN (2026-05-19) | 0.3x | -23.1% | reject | **accept** (R/R at floor, strict `<`) |
| CRWV (2026-05-19) | 0.4x | -19.9% | reject | **accept** |
| MIELY (2026-05-19) | 0.4x | -15.5% | reject (the one the current guard caught) | **accept** (stays BUY_LIMIT) |
| Asymmetric (hypo) | 3.0x | -40% | reject | **accept** ← motivating case |
| Extreme (hypo) | 5.0x | -60% | reject | reject (downside backstop) |
| Normal BUY | 1.5x | -10% | accept | accept |

---

## Task 1: Update constants and gate logic in `stop_loss_guard.py`

**Why:** Two thresholds change; the function needs to accept R/R; the dataclass needs a new field so callers can introspect the R/R that was evaluated.

**Files:**

- Modify: `app/utils/stop_loss_guard.py`

---

- [ ] **Step 1: Update the module-level constant block**

Replace lines 87-91 (the `MAX_ACCEPTABLE_DOWNSIDE_PCT = 15.0` block) with:

```python
# Maximum downside% (entry → stop) we're willing to publish as a real trade.
# Beyond this, the widened stop is effectively no stop at all — a hard
# portfolio-risk backstop, applied regardless of R/R.
MAX_ACCEPTABLE_DOWNSIDE_PCT = 50.0

# Minimum R/R below which we treat the PM output as mathematically broken
# and refuse to publish it as a BUY (historical offenders: AAOI 0.2x,
# FORM 0.2x, VICR 0.1x). The R/R floor is the PRIMARY gate; the downside
# ceiling above is only a catastrophic-widen backstop.
MIN_ACCEPTABLE_RR = 0.3
```

---

- [ ] **Step 2: Extend the `StopAcceptability` dataclass**

Replace lines 94-98:

```python
@dataclass
class StopAcceptability:
    acceptable: bool
    downside_pct: Optional[float]
    risk_reward_ratio: Optional[float]
    reason: str
```

---

- [ ] **Step 3: Update `evaluate_stop_acceptability` signature and body**

Replace lines 101-116 with:

```python
def evaluate_stop_acceptability(
    entry_low: Optional[float],
    stop_loss: Optional[float],
    risk_reward_ratio: Optional[float] = None,
) -> StopAcceptability:
    """Two-gate acceptability check on a (possibly post-widen) trade.

    Gates (any failure → reject):
        1. Downside backstop: reject if downside_pct > MAX_ACCEPTABLE_DOWNSIDE_PCT
           (50%). Always applied when entry_low and stop_loss are present.
        2. R/R primary gate: reject if risk_reward_ratio < MIN_ACCEPTABLE_RR
           (0.3x). Only applied when risk_reward_ratio is provided
           (None → R/R gate skipped, legacy behavior).

    Conservative defaults: when entry_low or stop_loss is None / invalid, we
    accept and return reason="insufficient_data". Caller has nothing better
    to do.

    Boundary semantics:
        - Downside 50.0% exactly → accept (strict `>`)
        - R/R 0.3x exactly → accept (strict `<`)
    """
    if entry_low is None or stop_loss is None or entry_low <= 0:
        return StopAcceptability(True, None, risk_reward_ratio, "insufficient_data")

    downside_pct = abs(entry_low - stop_loss) / entry_low * 100.0

    if downside_pct > MAX_ACCEPTABLE_DOWNSIDE_PCT:
        return StopAcceptability(
            False,
            downside_pct,
            risk_reward_ratio,
            f"downside {downside_pct:.1f}% exceeds ceiling {MAX_ACCEPTABLE_DOWNSIDE_PCT:.1f}%",
        )

    if risk_reward_ratio is not None and risk_reward_ratio < MIN_ACCEPTABLE_RR:
        return StopAcceptability(
            False,
            downside_pct,
            risk_reward_ratio,
            f"R/R {risk_reward_ratio:.1f}x below floor {MIN_ACCEPTABLE_RR:.1f}x",
        )

    return StopAcceptability(True, downside_pct, risk_reward_ratio, "within_acceptable")
```

---

## Task 2: Thread `risk_reward_ratio` into the call site

**Why:** The R/R is already computed and stored in `final_decision["risk_reward_ratio"]` by `recompute_risk_metrics` at lines 567-575, immediately before the acceptability check. One-line plumbing change.

**Files:**

- Modify: `app/services/research_service.py` around line 577

---

- [ ] **Step 1: Update the call to pass R/R**

Replace lines 577-580 (the existing `acceptability = evaluate_stop_acceptability(...)` call) with:

```python
            acceptability = evaluate_stop_acceptability(
                entry_low=float(_entry_low),
                stop_loss=final_decision.get("stop_loss"),
                risk_reward_ratio=final_decision.get("risk_reward_ratio"),
            )
```

**Do not touch the override block at lines 581-596.** The existing condition `if not acceptability.acceptable and final_decision.get("action", "").upper().startswith("BUY"):` correctly handles both gate-failure modes (downside or R/R). The new `[STOP-REJECTED] R/R 0.2x below floor 0.3x` reason string will flow through to the printed Reason field and the DB row automatically.

---

- [ ] **Step 2: Verify there are no other call sites**

Run:

```bash
grep -rn "evaluate_stop_acceptability" app/ scripts/ tests/
```

Expected: exactly one production call site (`app/services/research_service.py`) plus the test file. If anything else turns up, audit it.

---

## Task 3: Migrate and extend `tests/test_stop_loss_guard.py`

**Why:** Existing tests assert the 15% boundary which is moving to 50%. The R/R gate is new behavior that needs coverage.

**Files:**

- Modify: `tests/test_stop_loss_guard.py`

---

- [ ] **Step 1: Read the existing test file and identify boundary tests**

Find any test using `entry_low/stop_loss` pairs that target the 15% boundary (typical: `entry=100, stop=84` for 16% reject; `entry=100, stop=85` for 15% accept). Update those pairs to target the new 50% boundary (`entry=100, stop=49` for 51% reject; `entry=100, stop=50` for 50% accept). Update any assertion on `acceptability.reason` strings to match the new format `"downside X% exceeds ceiling 50.0%"`.

---

- [ ] **Step 2: Add R/R gate test cases**

Append the following tests to the file:

```python
def test_rr_below_floor_is_rejected():
    """R/R 0.2x with mild downside should still reject on the R/R gate."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=0.2
    )
    assert not result.acceptable
    assert "R/R" in result.reason


def test_rr_exactly_at_floor_is_accepted():
    """R/R 0.3x exactly (strict <) should be accepted."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=0.3
    )
    assert result.acceptable


def test_rr_above_floor_with_moderate_downside_accepted():
    """R/R 0.5x, -20% downside passes under new rules (would have failed old)."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=80.0, risk_reward_ratio=0.5
    )
    assert result.acceptable


def test_asymmetric_high_rr_trade_accepted():
    """Motivating case: 3.0x R/R with -40% downside is now publishable."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=60.0, risk_reward_ratio=3.0
    )
    assert result.acceptable


def test_downside_backstop_fires_even_with_high_rr():
    """5.0x R/R, -60% downside → reject on backstop, not R/R."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=40.0, risk_reward_ratio=5.0
    )
    assert not result.acceptable
    assert "downside" in result.reason.lower()


def test_rr_none_skips_rr_gate():
    """When R/R is None, only the downside backstop applies."""
    ok = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=80.0, risk_reward_ratio=None
    )
    assert ok.acceptable
    rej = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=40.0, risk_reward_ratio=None
    )
    assert not rej.acceptable


def test_dataclass_carries_rr_through():
    """The returned dataclass should expose the R/R that was evaluated."""
    result = evaluate_stop_acceptability(
        entry_low=100.0, stop_loss=90.0, risk_reward_ratio=1.5
    )
    assert result.risk_reward_ratio == 1.5
```

---

- [ ] **Step 3: Run the full guard test suite**

```bash
pytest tests/test_stop_loss_guard.py -v
pytest tests/test_recompute_risk_metrics.py -v
```

Expected: all pass. `recompute_risk_metrics` is unchanged, so the second file should pass without any edits.

---

## Task 4: Manual pipeline smoke verification

**Why:** Ensure no regression in the live PM-decision write path. The change touches every PM-decision that reaches the stop-acceptability check.

---

- [ ] **Step 1: Run a short timed cycle**

```bash
python main.py --run-for 5
```

Five minutes is enough to hit the screener and process at least one ticker through Council 1 + 2 + PM. Watch the log for:

- `[PM stop-guard]` lines should still appear when the PM stop is widened (unchanged from before)
- `[Stop-acceptability]` lines should appear when a setup is rejected, and the reason string should follow the new format: either `downside X.X% exceeds ceiling 50.0%` or `R/R X.Xx below floor 0.3x`
- No tracebacks
- No tickers that used to pass the 15% gate should now fail (would indicate a logic bug — the new rules are strictly more permissive on downside)

---

- [ ] **Step 2: Spot-check the trade report**

```bash
head -50 data/trade_report_full_7d.csv
```

Confirm that any new rows written during the smoke run carry sensible `R/R`, `downside`, and `reason` fields. Rows with `[STOP-REJECTED]` should now use the new reason format.

---

- [ ] **Step 3: Append a verification note to this plan file**

At the bottom of this file (`docs/superpowers/plans/2026-05-19-stop-acceptability-rr-gate.md`), under a new `## Verification log` heading, record:

- Date/time of smoke run
- Number of tickers processed
- Any setups that triggered the new gate (with R/R and downside values)
- Confirmation that all tests pass

This is the **documentation step** — short, factual, post-implementation. No need to write more than ~10 lines.

---

## Risks and notes

- **MIELY-style cases will no longer be downgraded.** Under the new thresholds, MIELY (R/R 0.4x, -15.5% downside) from the 2026-05-19 cycle — which the current 15% gate caught and overrode to AVOID/NONE — would now stay as BUY_LIMIT. This is the **intended consequence** of the user's design choice to make the gate more permissive. Do not "fix" this; it is the spec.
- **Backward compatibility:** the `risk_reward_ratio` argument defaults to `None`. Any caller that doesn't supply it gets the downside-backstop-only behavior. No existing test or call site will break from the signature change.
- **No DB schema changes.** No migrations. No new columns. The `rejected_reason = "stop_too_wide"` flag in `research_service.py:592` is unchanged — if you later want to distinguish R/R-rejection from downside-rejection downstream, that's a follow-up, not in scope.
- **No external API or model changes.** This is pure local logic.
- **The override at `research_service.py:581` only acts on `action.startswith("BUY")`.** AVOID rows still publish their (now possibly ugly-looking) R/R values to the trade report. Suppressing R/R column on AVOID rows is a separate follow-up; **not in this plan**.

---

End of plan.

## Verification log

- **2026-05-19** — Implemented via subagent-driven-development. Tasks 1–3 landed in commit `23a588e`; the strict-`>` 50% boundary accept test (a plan-required case the first pass missed) added in `a24c269`.
- **Tests: 27/27 pass** at HEAD (`venv/bin/python -m pytest tests/test_stop_loss_guard.py tests/test_recompute_risk_metrics.py tests/test_research_service_stop_guard_recompute.py`). `recompute_risk_metrics` unchanged and green.
- **Spec compliance:** independently verified ✅ — constants, dataclass field order, two-gate logic (downside-then-R/R, strict `>` / `<`, R/R skipped when None), call-site plumbing, override block untouched. `widen_stop_if_too_tight`/`recompute_risk_metrics` byte-identical to base `9fc57ca`.
- **Code quality:** approved. Declined (with reason): R/R message uses spec-mandated `:.1f` (tautology only in narrow R/R∈[0.25,0.295), outside the plan's behavior matrix — left verbatim per spec); the 7 R/R tests keep verbatim hardcoded boundary literals.
- **Task 4 (live smoke) intentionally NOT run** — user decision. Reasons: implementation is exhaustively unit-verified; the smoke command incurs paid Gemini inference + an unconditional GCS upload attempt; and a separate concurrent Claude (Sonnet 4.6) session was committing its own stop-guard work (`de12c97`, `bb4db55` adding `sanitize_unreliable_stop`) to this same branch, making a live run noisy. Verification is tests-only by design.
- **Behavior matrix:** all 11 cases confirmed by the spec reviewer (R/R 0.2x→reject, 0.3x→accept, 3.0x/-40%→accept, 5.0x/-60%→backstop reject, None→legacy, 50.0%→accept, 51%→reject, None inputs→insufficient_data).
