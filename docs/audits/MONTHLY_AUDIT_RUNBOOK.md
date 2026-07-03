# Monthly Audit Runbook — three-tier feedback loop

Run on the 1st of each month (first run: **Aug 1, 2026**). Everything the
agents are told, and every gate that binds them, is re-earned here monthly.
Companion docs: `FINDINGS_LEDGER.md` (state machine),
`docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md` (tier rules).

## 0. Pipe health (5 min)
- `GET https://stock-tracker.onrender.com/health` (Render service `stock-tracker`
  per render.yaml; adjust if the dashboard shows a suffixed hostname) →
  `outcomes` block: `days_behind` ≤ ~14
  (it has a ~7–8d floor from mark maturation), `shadow_runs` growing,
  `pinned_card_age_days` < 45.
- If marks are stale: run `python3 -m scripts.maintenance.backfill_outcomes`
  locally and investigate the nightly job.

## 1. Refresh the evidence (10 min)
- `python3 -m scripts.analysis.build_calibration_card` → review
  `data/calibration_card_candidate.md` (console-only).
- `python3 -m scripts.analysis.verdict_alpha_rolling` → per-verdict view at
  1w/2w/4w/8w over trailing 30/60/90d. This output is NEVER shown to agents.

## 2. Re-test the ledger (20 min)
For every non-console row in `FINDINGS_LEDGER.md` (the Scope rule means this
covers all live gates and prompt items):
- Does the finding still hold in the fresh candidate card / rolling view,
  with the same sign and comparable magnitude?
- Held → increment "Audits survived", set next review.
- Failed/flipped → demote per the row's demotion note (gates: suspend via
  code constant; prompt items: strip; console: leave). Record what happened.
- GRANDFATHERED gate rows need 2 clean re-tests before they count as
  policy-conforming.

## 3. Shadow A/B checkpoint (10 min)
- `python3 -m scripts.analysis.eval_calibration_ab`
- Filter to rows after the footer-removal deploy (see docs/proposals/PLAN_three_tier_reconciliation.md Task 1).
- Ship gate for flipping `CALIBRATION_ENABLED=1` (all three): treatment−control
  4w delta CI excludes 0; DR straight-BUY guardrail holds; conviction
  calibration flat-or-up. Until then ENABLED stays 0. Do not enable blind.

## 4. Re-pin the card if warranted (5 min)
Only if §2 changed what the card should say, or the pin is aging out (45d):
- `python3 -m scripts.analysis.pin_calibration_card` (dry-run) → review
- `python3 -m scripts.analysis.pin_calibration_card --approve --audited-at YYYY-MM-DD`
- `git add data/calibration_card_pinned.json && git commit && git push` —
  prod reads the card from the repo; an uncommitted pin never deploys.
- Wording of the injection template must NOT change while the shadow A/B is
  collecting (it splits the sample); content updates via re-pin are fine —
  record the pin date so eval can segment.

## 5. Knife re-enable check (2 min)
- Nightly degeneracy log line shows trailing-50 knife YES-rate. Only when it
  has been < 0.60 sustained (not one night) may `RISK_KNIFE_GATE_ENABLED`
  be flipped to True — a deliberate, reviewed commit; the monitor then
  governs via `data/gate_suspensions.json`.

## 6. Close out (5 min)
- Update `FINDINGS_LEDGER.md` rows + dates; add new findings at **Console**.
- Promotion is strictly console → prompt → gate, one level per audit, with
  the thresholds in the ledger header. Demotion may skip levels.
- Agenda carry-over for Aug 1, 2026: §3.4 DR additions (straight-BUY decay,
  DR-BUY_LIMIT discouragement) — deliberately deferred to this audit;
  §4.4 QC symbol-normalization noise (9 perpetual stale tickers).
