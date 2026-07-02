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

| Finding | First seen | Audits survived | Regimes | Tier | Evidence | Next review |
|---|---|---|---|---|---|---|
| Earnings-drop penalty (EARNINGS_MISS/COMPANY_SPECIFIC/ANALYST_DOWNGRADE buys underperform) | Apr 2026 | Apr, May, Jun (3) | 2 | **Gate** — DROP_TYPE_GATE, NAMED_EVENT lift | card 2026-06-25: earnings n=184 mean_ret_4w +0.25% vs non-earnings n=412 +7.3% | Aug 2026 audit |
| Non-earnings dip edge (+5.7 alpha @28d, 56% win) | May 2026 | May, Jun (2) | 2 | **Prompt** — pinned card by_earnings slice | card 2026-06-25 | Jul 2026 audit |
| Edge materializes at ~4 weeks, not 1 | May 2026 | May, Jun (2) | 2 | **Plumbing** — 4w primary label; reassess default 20 trading days | PLAN_calibration_feedback_option1.md; decision_outcomes | Jul 2026 audit |
| AVOID positive alpha (Jun +10.5 @28d) | Jun 2026 | 1 — sign-flipped vs Apr | 1 | **Console** | Jun audit, n=10 | needs 2 more audits |
| BUY_LIMIT is the worst action (median excess −1.9) | Jun 2026 | 1 | 1 | **Console** (already applied as downgrade-target fix 2026-07-02) | research_service.py:1304 docstring; June: 1/9 win at 14d | Jul 2026 audit |
| Falling-knife penalty | May 2026 | signal degenerate since 2026-06-11 | — | **Suspended gate** — RISK_KNIFE_GATE | 256/264 YES (97%); re-enable when trailing-50 YES-rate < 0.60 (nightly log) | on signal recovery |
