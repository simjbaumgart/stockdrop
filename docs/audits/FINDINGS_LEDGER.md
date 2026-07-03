# Findings Ledger — promotion state machine for feedback

Every audited finding lives here with an explicit tier. Promotion path is
strictly **console → prompt → gate**; demotion may skip levels. Rules
(THREE_TIER_FEEDBACK_PROPOSAL.md):

- **Gate** (enforced in `app/services/decision_gate_service.py`): ≥3 audits
  survived (or ≥2 spanning ≥2 regimes), stable sign, deterministic predicate on
  structured fields, escape hatch defined.
- **Prompt** (pinned card via `scripts/analysis/pin_calibration_card.py`):
  ≥2 audits survived, n ≥ 20, expressed as base rates — never instructions,
  never per-verdict lines, never mistake narratives.
- **Console** (`scripts/analysis/verdict_alpha_rolling.py`, candidate card):
  everything else, including anything regime-dependent or small-n.

The monthly audit MUST re-test every non-console row (the "Next review"
column) before hunting new findings.

Scope rule: EVERY live gate in `decision_gate_service.py` and every
outcome-derived instruction in an agent prompt must have a row here — if it
is not in the ledger, it is not sanctioned feedback.

| Finding | First seen | Audits survived | Regimes | Tier | Evidence | Next review |
|---|---|---|---|---|---|---|
| Earnings-drop penalty (EARNINGS_MISS/COMPANY_SPECIFIC/ANALYST_DOWNGRADE buys underperform) | Apr 2026 | Apr, May, Jun (3) | 2 | **Gate** — DROP_TYPE_GATE, NAMED_EVENT lift | card 2026-06-25: earnings n=184 mean_ret_4w +0.25% vs non-earnings n=412 +7.3% | Aug 2026 audit |
| Non-earnings dip edge (+5.7 alpha @28d, 56% win) | May 2026 | May, Jun (2) | 2 | **Prompt** — pinned card by_earnings slice | card 2026-06-25; re-pinned 2026-07-02 (669 labeled) | Aug 2026 audit |
| Edge materializes at ~4 weeks, not 1 | May 2026 | May, Jun (2) | 2 | **Plumbing** — 4w primary label; reassess default 20 trading days | PLAN_calibration_feedback_option1.md; decision_outcomes | Aug 2026 audit |
| SA quant < 2.5 buys underperform (31% win, median −3.47%) | Jun 2026 | 1 — GRANDFATHERED (gate promoted 2026-06-10, pre-policy) | 1 | **Gate** — SA_QUANT_GATE (floor 2.5; missing rating does not fire) | prompt_vs_outcome_analysis 2026-06-10 (681 decisions Apr 9–Jun 10, 7d marks) | Aug 2026 audit — needs 2 clean re-tests to conform to gate criteria |
| Bearish-news buys need a named catalyst (39% vs 54% win) | Jun 2026 | 1 — GRANDFATHERED (gate promoted 2026-06-10, pre-policy) | 1 | **Gate** — NEWS_SENTIMENT_GATE | prompt_vs_outcome_analysis 2026-06-10 | Aug 2026 audit — needs 2 clean re-tests |
| Unconfirmed drop reason → no immediate BUY | Jun 2026 | 1 — GRANDFATHERED (gate promoted 2026-06-11 after the PTC incident) | 1 | **Gate** — UNCONFIRMED_DROP_GATE (fires only on explicit not-confirmed) | PTC 2026-06-11; downgrade target BUY_LIMIT→WATCH 2026-07-02 | Aug 2026 audit — needs 2 clean re-tests |
| BUY_LIMIT is the worst action; adverse selection on limit fills | Jun 2026 | 1 | 1 | **Prompt (PROVISIONAL)** — PM action rule restricts BUY_LIMIT to SECTOR_ROTATION/MACRO_SELLOFF (2026-07-02); also applied as gate downgrade-target fix. Early promotion on n=1: if it survives Aug + Sep audits, promote to a deterministic gate (non-SECTOR/MACRO BUY_LIMIT → WATCH); if it fails either, demote to Console and strip the prompt rule. | June 2026: 1/9 win at 14d, median excess −1.9 | Aug 2026 audit |
| Self-stated R/R is anti-predictive (corr ≈ −0.15…−0.20) | May 2026 | 2 (May, Jun) | 2 | **Prompt (qualitative)** — conviction instructions: "do not cite it"; NEVER threshold-gate on R/R (only reject ≤ 0 as repair artifacts) | R/R-vs-outcome analyses May + Jun 2026 | Aug 2026 audit |
| AVOID positive alpha (Jun +10.5 @28d) | Jun 2026 | 1 — sign-flipped vs Apr | 1 | **Console** | Jun audit, n=10 | needs 2 more audits |
| Falling-knife penalty | May 2026 | signal degenerate since 2026-06-11 | — | **Suspended gate** — RISK_KNIFE_GATE. RISK_KNIFE_GATE_ENABLED is a MANUAL master switch; once re-enabled, the nightly degeneracy monitor governs via gate_suspensions.json | 256/264 YES (97%); re-enable only after trailing-50 YES-rate < 0.60 sustained (nightly log) | on signal recovery |
