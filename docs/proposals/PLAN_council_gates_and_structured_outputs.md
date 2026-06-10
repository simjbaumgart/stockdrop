# PLAN: Council Decision Gates + Structured Agent Outputs

**For:** Claude Code, executed in this repo (`Stock-Tracker/`). Work directly on main, test locally before committing (see `CLAUDE.md`).
**Source analysis:** `prompt_vs_outcome_analysis_2026-06-10.md` (Stockdrop project folder) — 681 decisions (2026-04-09 → 2026-06-10) linked to 7-day outcomes and full council reports.
**Goal:** Convert three statistically verified leaks into deterministic decision gates, then make agent outputs machine-readable so future tuning is possible.

---

## Why (evidence summary — do not re-derive, but sanity-check queries in Step 0)

| Finding | Numbers (Apr 9 – Jun 10, 7d marks) |
|---|---|
| PM buys have no edge | n=168 buys: 43% win, trimmed mean −0.30%, median −0.68% |
| drop_type already ranks outcomes | SECTOR_ROTATION/MACRO_SELLOFF buys: 52% win, +1.74% tmean (n=58); EARNINGS_MISS buys 39% win, −1.78%; COMPANY_SPECIFIC 37% win |
| PM over-buys earnings drops | buy-rate 40% on earnings drops vs 19% otherwise |
| Risk agent ignored | explicit falling-knife verdicts: PM still buys at base rate; those 16 buys since Apr 15 averaged −2.48% |
| DR soft vetoes lose | hard-event overrides +10.9 pts; soft-rationale −5.9 pts (killed ADSK +11%, FICO +16%, GWRE +11%) |
| SA quant predictive, unused | quant <2.5: 31% win, median −3.47%; coverage only 39% of decisions |
| News sentiment predictive, unused | bullish-news buys 54% win vs bearish-news 39% |

**Estimated combined impact of Phase 1: ≈ +2.5–3 pts per trade, win rate from ~43% → ~52% on buys.**

---

## Ground rules (from CLAUDE.md — read it first)

- Python with type hints on new functions; `async def` for I/O; never `time.sleep()` in async code.
- Agent prompt changes affect recommendation quality — keep diffs minimal and surgical.
- SQLite is accessed from multiple threads: one connection per thread.
- New columns: check `app/database.py` migration pattern before adding (decision_points has 80+ columns with migration history).
- Test locally (`uvicorn main:app --reload`), run pipeline against a known recent drop, verify report structure, then commit to main.

---

## Step 0 — Verify baseline before changing anything

Re-run the headline numbers so post-change comparison is valid:

```python
# decisions with outcomes: join decision_points (subscribers.db, root) to
# data/trade_report_full_7d.csv on (symbol, DATE(timestamp)).
# Reproduce: buys win rate ~43%, sector/macro-only buys ~52%.
```

Save the script as `scripts/analysis/gate_baseline_check.py` so it can be re-run after deployment. If numbers deviate wildly from the table above, stop and report.

---

## Phase 1 — Deterministic decision gates (highest impact, code-only)

Implement as a **post-PM gating layer**, NOT as prompt instructions. One new module:

`app/services/decision_gate_service.py`

```python
@dataclass
class GateResult:
    final_action: str          # possibly downgraded
    pre_gate_action: str       # PM's original action
    gates_fired: list[str]     # e.g. ["DROP_TYPE_GATE", "SA_QUANT_GATE"]
    gate_reasons: list[str]    # human-readable, for dashboard/email
```

Apply after the Fund Manager verdict is parsed and before persistence, wherever `final_decision` is currently saved in `research_service.py` / `stock_service.py`.

### Gate 1: drop_type gate (the +2 pt lever)
- If PM action ∈ {BUY, BUY_LIMIT} and `drop_type` ∈ {EARNINGS_MISS, COMPANY_SPECIFIC, ANALYST_DOWNGRADE} → downgrade to **WATCH**.
- Exception path: Deep Research may re-upgrade (see Gate 4) if its rationale cites a named positive catalyst (enum check, not string fuzz).
- SECTOR_ROTATION, MACRO_SELLOFF, TECHNICAL_BREAKDOWN, UNKNOWN pass through.

### Gate 2: SA quant gate
- If `sa_quant_rating` is not NULL and < 2.5 and action ∈ {BUY, BUY_LIMIT} → downgrade to WATCH.
- Missing rating does NOT block (coverage is only 39%; see Phase 3 coverage fix).

### Gate 3: Risk knife gate
- Requires the structured risk verdict from Phase 2 (field `falling_knife`).
- If `falling_knife == "YES"` and action == BUY → downgrade to **BUY_LIMIT**; if conviction also LOW → WATCH.
- Until Phase 2 lands, interim regex on the risk report: `verdict[^a-zA-Z]{0,5}(yes|.{0,30}falling knife)` (case-insensitive) — this parses ~11% of current reports, which is exactly the explicit-verdict subset that mattered.

### Gate 4: DR override basis gate
In `deep_research_service.py`:
- Add `"override_basis": "NAMED_EVENT" | "JUDGMENT"` + `"named_event": str|null` to the Senior Reviewer JSON schema (prompt ~line 1382-1500 and the schema block ~line 1821).
- Prompt addition (STEP 5): "OVERRIDDEN requires override_basis=NAMED_EVENT: a specific, verifiable, dated event — lawsuit/regulatory action, SEC filing, restated guidance, insider transaction, analyst downgrade with target. General macro, valuation, or 'structurally challenged' concerns are JUDGMENT — record them but do not override."
- In the verdict-application code (~line 680–790): honor OVERRIDDEN→AVOID only when `override_basis == "NAMED_EVENT"`. JUDGMENT overrides are stored (new column `deep_research_override_basis`) and surfaced in the dashboard as an advisory flag, but the council action stands.
- This same NAMED_EVENT path is the exception that can lift Gate 1's WATCH back to BUY_LIMIT (positive named catalyst).

### Persistence + visibility
- New columns on `decision_points` (follow existing migration pattern in `app/database.py`): `pre_gate_action TEXT`, `gates_fired TEXT`, `deep_research_override_basis TEXT`.
- Dashboard (`templates/`, `app/routers/views.py`): show gated decisions as e.g. "WATCH (gated from BUY: EARNINGS_MISS)".
- Email summary: same annotation.

**Why store pre_gate_action:** it gives a free ongoing A/B — gated vs ungated performance — without shadow infrastructure.

### Tests (pytest, `tests/`)
- Unit-test `decision_gate_service` exhaustively: every gate, combinations, missing fields (drop_type NULL, quant NULL), exception path.
- Integration: run pipeline on one known recent earnings-drop ticker; assert BUY → WATCH with `gates_fired=["DROP_TYPE_GATE"]`.

---

## Phase 2 — Structured agent outputs (transport fix)

All in `app/services/research_service.py`. Pattern: each agent appends a fenced JSON block; add one shared parser with fallback.

```
=== STRUCTURED_VERDICT ===
{ ...agent-specific JSON... }
```

Parser: `_extract_structured_verdict(report: str) -> dict | None` — find last fenced JSON in report, `json.loads`, on failure return None and log to `parser_failures/` (directory already exists in `data/`). Never crash the pipeline on a parse failure.

Per-agent JSON (add to the OUTPUT section of each prompt):

| Agent (prompt location) | Required JSON |
|---|---|
| Technical (~1200) | `{"signal": "BREAKDOWN\|PULLBACK\|OVERSOLD_BOUNCE", "support_held": bool}` |
| News (~1237) | `{"sentiment": "BULLISH\|NEUTRAL\|BEARISH", "drop_reason_confirmed": bool, "named_catalyst": str\|null}` — replaces the `NEEDS_ECONOMICS:`/`REASON_FOR_DROP_IDENTIFIED:` free-text markers (keep both during transition; remove text markers once parse success >95%) |
| Competitive (~1426) | `{"attribution": "SECTOR\|IDIOSYNCRATIC\|MIXED", "confidence": int 0-10}` |
| Bull (~1465) | `{"case_strength": int 0-10, "target_sell_low": float, "target_sell_high": float}` — replaces prose TARGET_SELL_RANGE; add to prompt: "A weak bull case is valuable information. Do not inflate case_strength." |
| Bear (~1504) | `{"bear_verdict": "NO_TRADE\|SHORT\|TOLERABLE", "top_risk": str, "exit_ceiling": float}` — replaces prose BEAR_EXIT_CEILING |
| Risk (~2408) | `{"falling_knife": "YES\|NO", "top_risk": str}` |

PM prompt changes (~1543):
1. Inject the structured verdicts as a compact block ABOVE the prose reports (PM reads signal first, narrative second).
2. **Remove "EARNINGS_MISS with beat" from the HIGH-conviction examples** in INSTRUCTIONS FOR CONVICTION — this single phrase is the likely cause of the 2× earnings-drop buy rate.
3. Replace the R/R>2:1 conviction anchor: "HIGH conviction requires at least three of: attribution=SECTOR, news sentiment != BEARISH, sa_quant ≥ 3.5, bear_verdict=TOLERABLE, falling_knife=NO. Self-calculated risk/reward is NOT evidence of conviction." (Empirical basis: projected R/R 2–3 buys won 31% vs 50% for R/R 1.5–2.)
4. Bear rebuttal requirement: "If bear_verdict is NO_TRADE or SHORT, quote the bear's top_risk verbatim and rebut it specifically before any BUY."

Persist the structured fields — new columns: `tech_signal`, `news_sentiment`, `comp_attribution`, `bull_case_strength`, `bear_verdict`, `risk_falling_knife` (all TEXT/INTEGER on decision_points). These make the next analysis round trivial instead of regex archaeology.

Gate 3 switches from regex to `risk_falling_knife` once parse success >90% over a rolling 50 decisions.

Add Gate 5 once news field lands: BUY with `news_sentiment == "BEARISH"` requires `named_catalyst != null`, else WATCH.

### Tests
- Parser unit tests: valid JSON, malformed JSON, missing block, JSON with trailing prose.
- One integration run: assert all six structured fields persisted non-NULL for a fresh decision.

---

## Phase 3 — Cost + hygiene (mechanical, do last)

1. **Delete the pasted research-paper sentence** "We argue that contexts should function not as concise summaries, but as comprehensive, evolving playbooks—detailed, inclusive, and rich with domain insights." — 4 occurrences in `research_service.py` (~lines 1233, 1367, 1422, 2426). Replace with: "Be thorough but information-dense. Maximum ~600 words before the structured verdict." Expect report sizes to drop from ~5k words to ~1k.
2. **Daily macro snapshot:** compute Market Sentiment + Economics ONCE per trading day (first stock triggers it), cache in `data/` with a date key, inject the cached text for subsequent stocks. Add to the economics output: `{"macro_sensitivity": "HIGH|MED|LOW", "direction": "TAILWIND|NEUTRAL|HEADWIND"}` evaluated per stock against the cached snapshot (cheap flash-model call instead of a full research call). ~25% pipeline cost cut.
3. **SA quant coverage fix:** investigate why `sa_quant_rating` is NULL for 61% of decisions (`seeking_alpha_service.py` + `yahoo_ticker_resolver.py` — likely ticker-resolution misses on non-US listings). Log resolution failures; backfill where the RapidAPI endpoint has the data. Goal: >70% coverage.
4. **Strip balance-sheet dumps** from the Seeking Alpha report (the raw "Operating lease right-of-use assets…" tables) — keep ratings, rank, and article summaries.

---

## Rollout order & checkpoints

1. Step 0 baseline script → commit.
2. Phase 1 Gates 1+2 (need no prompt changes) + columns + dashboard annotation → integration test → commit. **This alone captures most of the impact.**
3. Phase 1 Gate 4 (DR basis) → commit.
4. Phase 2 structured outputs, one agent per commit (risk first — it activates Gate 3 properly; then news → Gate 5; then the rest).
5. Phase 3 hygiene.
6. **Checkpoint after 2 weeks of live decisions:** run `gate_baseline_check.py` comparing `pre_gate_action` vs `final_action` outcomes. Success criteria: gated-away trades underperform kept trades; buy win rate trending ≥ 50%. If EARNINGS_MISS gated names are outperforming at +28d, revisit Gate 1 (the 7d horizon may under-credit slow earnings recoveries — known measurement debt while `decision_tracking` is empty).

## Out of scope (deliberately)
- Backtesting harness, tracking backfill (separate P0, see audit_2026-06-09).
- Conviction recalibration from bull `case_strength` logs — needs ~2 months of data first.
- Gatekeeper tier changes, LOO, sell council.
