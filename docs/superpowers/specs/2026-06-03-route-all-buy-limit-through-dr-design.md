# Route all BUY_LIMIT verdicts through Deep Research

**Date:** 2026-06-03
**Status:** Approved — ready for implementation

## Problem

Buy-side PM verdicts are supposed to be validated by Deep Research (DR), which
can override or upgrade the PM recommendation. Plain `BUY` verdicts always route
through DR. `BUY_LIMIT` verdicts only route when risk/reward (R/R) > 1.0.

This creates a recurring gap: `BUY_LIMIT` candidates with R/R ≤ 1.0 never reach
DR. Observed cases:

- **AFRM** — `BUY_LIMIT`, R/R 0.7x → no DR.
- **OSCR** — same pattern in a prior session.

Meanwhile a plain `BUY` (e.g. GPN) gets reviewed and, in GPN's case, upgraded.
Limit orders systematically slip under the DR threshold while the highest-leverage
fix — routing every buy-side verdict through DR — remains undone.

## Goal

Every buy-side PM verdict (`BUY` and `BUY_LIMIT`) is routed through Deep Research,
regardless of R/R. `WATCH` and `AVOID` remain excluded (no position is taken).

## Mechanism (current state)

Two coupled locations in `app/services/stock_service.py` both gate on R/R:

1. **`_should_trigger_deep_research`** (line ~619) — decides whether DR runs.
   - `BUY` → always `True`.
   - `BUY_LIMIT` → `True` only when `risk_reward_ratio > 1.0`.
   - else → `False`.

2. **Status mirror** in `_run_deep_analysis` (line ~1714) — sets the position
   lifecycle status. A row only advances to `"Pending DR Review"` when
   `will_trigger_dr AND rr_value > 1.0`.

The two are intentionally kept in sync (a comment at line ~1707 notes the mirror).
If only the gate changed, a `BUY_LIMIT` at R/R 0.7 would trigger DR but the status
would be set to `"Not Owned"`. `db.finalize_position_status_after_dr` cannot promote
a `"Not Owned"` row back to `"Owned"` (proven by
`tests/test_pending_dr_status.py::test_finalize_does_not_promote_already_not_owned_row`),
so the DR upgrade would be silently discarded. **Both spots must change together.**

## Changes

### 1. The gate — `_should_trigger_deep_research`

Remove the R/R condition for `BUY_LIMIT`. R/R no longer participates in the DR
routing decision:

```python
def _should_trigger_deep_research(self, report_data: dict) -> bool:
    """Trigger deep research for every buy-side verdict.

    - BUY: always trigger.
    - BUY_LIMIT: always trigger (R/R no longer gates this — every buy-side
      verdict is routed through DR so limit orders can't slip past review).
    """
    action = report_data.get("recommendation", "AVOID").upper()
    return action in ("BUY", "BUY_LIMIT")
```

The `risk_reward_ratio` read is removed from this method.

### 2. The status mirror — `_run_deep_analysis` (line ~1714)

Drive the status purely off `will_trigger_dr` so any row DR processes is parked in
`"Pending DR Review"`:

```python
will_trigger_dr = self._should_trigger_deep_research(report_data)
if will_trigger_dr:
    status = "Pending DR Review"
else:
    status = "Not Owned"
```

The existing `elif "BUY" in rec_upper and rr_value > 1.0` edge-case branch becomes
unreachable for BUY/BUY_LIMIT (the gate now catches them) and is removed, leaving a
clean two-way branch. `rr_value` may no longer be needed in this block; remove the
now-dead local if so.

## Out of scope

- `WATCH` / `AVOID` verdicts — no position, stay excluded.
- `STRONG_BUY` / `SPECULATIVE_BUY` variants — not emitted by the current PM schema.

## Testing (TDD)

`tests/test_deep_research_trigger.py`:

- Rewrite the tests that assert `BUY_LIMIT` returns `False` for R/R ≤ 1.0 to assert
  `True` (R/R is now irrelevant for BUY_LIMIT): `test_buy_limit_does_not_trigger_rr_at_threshold`,
  `test_buy_limit_does_not_trigger_rr_below_threshold`.
- The invalid/None-R/R cases (`test_buy_limit_handles_invalid_rr`,
  `test_buy_limit_handles_none_rr`) now assert `True` — a malformed R/R no longer
  blocks a BUY_LIMIT from DR.
- Add `test_buy_limit_triggers_low_rr` for the AFRM case: `BUY_LIMIT`, R/R 0.7 → `True`.
- Keep `test_buy_always_triggers`, the AVOID/HOLD non-trigger tests unchanged.

`tests/test_pending_dr_status.py` — already covers the finalize transitions; stays
green unchanged.

## Risk

DR is rate-limited (60s minimum between requests, dual priority queue). Routing all
BUY_LIMITs adds DR volume, but BUY_LIMIT verdicts are already gatekeeper-filtered and
relatively infrequent — the increase is small and is exactly the intended behavior.
