# Three-Tier Feedback Architecture: Code Gates, Static Base Rates, Console-Only Monitoring

**Status:** Implemented (PR #18, merged 2026-07-03) + amendments below
**Supersedes / refines:** `PLAN_calibration_feedback_option1.md` (Option-1 card is implemented on `feat/calibration-feedback-option1`; this proposal changes its update policy and defines what may and may not flow into prompts)

## Overview

We now have a working outcome-labeling loop (`decision_outcomes`, 581 labeled decisions at the 4w horizon) and two channels for feeding findings back into the pipeline: deterministic gates (`app/services/decision_gate_service.py`) and prompt injection (`app/services/calibration_service.py`). The June audit produced evidence in **both** directions — the NAMED_EVENT rule added +39.7 pts precisely because it is a rule, while the falling-knife signal fed to the Risk agent collapsed to 97% YES and downgraded the entire 17-stock June PM BUY book into BUY_LIMIT, the desk's worst bucket (1/9 win at 14d).

The lesson is not "feed back more" or "feed back less" — it is that **findings belong in different tiers depending on how proven they are**, and that each tier has a different failure mode:

| Tier | Channel | Visibility to agents | Qualifies when | Failure mode guarded against |
|------|---------|---------------------|----------------|------------------------------|
| 1. Code gate | `decision_gate_service.py` | Invisible (enforced post-hoc) | Confirmed in ≥3 audits, stable across regimes, expressible as a deterministic predicate on structured fields | Drift, persuasive-bull-case override |
| 2. Prompt base rates | Static calibration card, hand-pinned | Visible, advisory | Survived ≥2 audits, n ≥ 20, presented as data (never instructions) | Noise amplification via auto-feedback |
| 3. Console only | Rolling alpha dashboard / analysis scripts | Never shown to agents | Everything else: regime-dependent, small-n, single-audit | Overfitting agents to the last regime |
| — (plumbing) | `decision_outcomes`, reassess cadence | n/a | Findings about *measurement*, not behavior | Misfiling evaluation changes as agent feedback |

One explicit anti-goal: **no "you were often wrong about X" narratives in any prompt, ever.** Agents given a strong signal about past mistakes overcorrect into mode collapse rather than calibrate — the falling-knife gate is the in-house proof (97% YES = 256/264 since Jun 11, zero information content).

## Background: the June evidence, both directions

**Rules work.** The NAMED_EVENT lift (`app/database.py:879` `lift_gated_watch_to_buy_limit`) — DROP_TYPE-gated WATCH is lifted back to BUY_LIMIT only when DR cites a specific, dated, verifiable catalyst via the structured `deep_research_override_basis` field — contributed +39.7 pts in June. It is testable, cannot drift, and cannot be argued out of by a persuasive bull case.

**The earnings-drop penalty is now confirmed three times** and held in April, May, and June. Current card numbers (as of 2026-06-25): EARNINGS_MISS n=184, mean ret_4w +0.25%, 46.2% recovery vs. non-earnings n=412, mean ret_4w +7.3%, 54.9% recovery. Historical audit slices: earnings-drop buys −1.6 to −3.3 alpha at 33% win rate; non-earnings +5.7 alpha at 28d, 56% win. This is gate-grade.

**Prompt-visible mistake-signals mode-collapse.** The Risk agent's structured `falling_knife` verdict, once it became gate-relevant, degenerated to 97% YES. The gate mechanically downgraded all 17 June PM BUYs to BUY_LIMIT — adverse selection made this the worst possible action (limit orders fill precisely when the stock keeps falling; median excess −1.9). The gate is now suspended (`RISK_KNIFE_GATE_ENABLED = False`) behind a trailing-50 YES-rate ceiling of 0.60.

**Regime-dependent findings flip.** The AVOID finding reversed sign between April and May–June; June's AVOID alpha of +10.5 @28d is n=10. Any agent fed that number would overfit to the last regime.

**The edge materializes at ~4 weeks, not 1.** This is a finding about *measurement*, not agent behavior — it belongs in plumbing (evaluation window, reassess cadence), not in prompts or gates.

## Tier 1 — Code gates (enforced, invisible to agents)

### Promotion criteria

A finding may become a gate only when **all** of:

1. Confirmed in ≥3 monthly audits (or ≥2 audits spanning ≥2 distinct market regimes).
2. Stable sign and magnitude across those audits (no April-vs-May flips).
3. Expressible as a deterministic predicate on **structured fields** (drop_type, SA quant rating, `news_drop_reason_confirmed`, …) — never on regex over free-text agent prose. The interim `_KNIFE_RE` regex is the anti-pattern; structured fields are the pattern.
4. Has an escape hatch of the NAMED_EVENT kind: a structured, evidence-requiring way for DR to lift the gate (specific, dated, verifiable, priced-in event). Gates without escape hatches turn systematic caution into systematic paralysis.

### What is promoted now

The **earnings-drop cap** already exists as DROP_TYPE_GATE (`decision_gate_service.py:116-121`: EARNINGS_MISS / COMPANY_SPECIFIC / ANALYST_DOWNGRADE caps BUY/BUY_LIMIT at WATCH, liftable by DR NAMED_EVENT). Under this proposal it is confirmed at Tier 1 — the three-audit criterion is met. No code change needed; the change is bookkeeping: record its evidence basis in the findings ledger (below) so future audits re-test it rather than re-litigate it.

### New requirement: degeneracy monitors on every gate input

The falling-knife collapse generalizes. Any structured agent signal that feeds a gate must carry a **firing-rate monitor**: trailing-50 rate of the gate-triggering value, with a per-gate ceiling (existing precedent: `KNIFE_YES_RATE_CEILING = 0.60`). If the ceiling is breached, the gate auto-suspends and QC alerts loudly — it does not keep firing on a signal that has stopped discriminating.

```python
# decision_gate_service.py sketch
GATE_DEGENERACY_CEILINGS = {
    "DROP_TYPE_GATE": 0.80,      # share of candidates classified into gated drop_types
    "NEWS_SENTIMENT_GATE": 0.80, # share of candidates with BEARISH + no catalyst
    "SA_QUANT_GATE": None,       # external numeric input, cannot mode-collapse
}

def check_gate_degeneracy(gate_name: str, trailing_fire_rate: float) -> bool:
    """Returns True if the gate should be suspended. QC-alert on flip."""
```

Implementation detail: firing rates are already derivable from the `gates_fired` list persisted per decision (`GateResult`); the monitor is a nightly query plus a module-level suspension flag, same shape as `RISK_KNIFE_GATE_ENABLED`.

### Demotion

A gate is suspended (not deleted — flag off, evidence preserved) when its degeneracy monitor trips **or** a subsequent audit shows the finding failed out-of-sample. RISK_KNIFE_GATE is the template: suspended with a documented re-enable condition.

## Tier 2 — PM/DR prompt base rates (visible, advisory, hand-pinned)

### The policy change: kill the nightly auto-update

This is the one place the existing Option-1 implementation must change. Today, `run_outcome_marking()` (main.py:247-301) rebuilds the card nightly and calls `reload_card()`, so agent prompts silently shift every night. With ~30 actionable decisions/month and ~2 pts of monthly alpha, an auto-feedback loop is thin enough to **amplify noise into the very prompts generating the next month's data** — a slow-motion version of the falling-knife collapse.

Proposed mechanism — split candidate from pinned:

- `scripts/analysis/build_calibration_card.py` keeps running nightly, but writes to `data/calibration_card_candidate.json` / `.md`. This is **console-only** (Tier 3): it is what you read during the monthly audit.
- `calibration_service._load_card()` reads only `data/calibration_card_pinned.json`. Pinning is a deliberate manual act after an audit:

```bash
python scripts/analysis/pin_calibration_card.py --approve   # copies candidate -> pinned, stamps audit date + git ref
```

- `run_outcome_marking()` drops the `reload_card()` call. The pinned card changes at most monthly, by hand, never automatically.

### Content constraints (enforced by the pin script)

The pinned card is a small static base-rate table — three or four lines of the form:

```
Historical base rates (audited 2026-06-30, 4w horizon):
- Earnings-drop buys: −1.6 to −3.3 alpha, 33% win rate (n=184)
- Non-earnings dip buys: +5.7 alpha at 28d, 56% win rate (n=412)
- Recovery edge materializes at ~4 weeks, not 1 week
```

Hard rules, validated at pin time:

1. **Data, not instructions.** Base rates with n. No imperatives ("be more cautious about…"), no second person, no mistake narratives ("the desk was often wrong about…").
2. **Only findings that survived ≥2 audits** and have n ≥ MIN_N (20) in every quoted bucket.
3. **≤4 lines.** If a fifth line seems necessary, one of the four has probably earned Tier-1 promotion instead.
4. **No per-verdict alpha lines** (AVOID/BUY/BUY_LIMIT breakdowns) — those are regime-dependent (Tier 3) and, worse, self-referential: telling the PM how its own verdicts performed is exactly the mistake-narrative shape that causes mode collapse.

Injection points stay where they are: PM at `research_service.py:1886-1891` (earnings slice only — drop_type is unclassified at PM time) and DR at `claude_dr_prompts.py:404-406` (drop_type + earnings slices). `CALIBRATION_ENABLED` remains the master flag and remains OFF until the Stage-1 shadow A/B (`CALIBRATION_SHADOW`, `calibration_shadow_runs`, `eval_calibration_ab.py`) clears its ship gate — this proposal does not shortcut that.

## Tier 3 — Console/dashboard only (never shown to agents)

Everything regime-dependent or small-n stays here: the AVOID sign flip (Apr negative, May–June positive, June +10.5 @28d on n=10), per-verdict alpha generally, single-audit findings, and the nightly candidate card.

New deliverable: a **rolling per-verdict alpha view at 7/14/21/28 days** —

- `scripts/analysis/verdict_alpha_rolling.py`: per PM verdict and per DR verdict, mean/median excess return and win rate at each horizon, over trailing 30/60/90-day decision windows, printed as a table (and optionally surfaced on the existing performance dashboard via `app/routers/performance.py`).
- Sources: `decision_points` join `decision_outcomes` (ret_1w/2w/4w; 21d approximated by 3w if we add it, else interpolated from 2w/4w — see open questions).

### The findings ledger — the promotion state machine

A finding's tier is an explicit, recorded status, not folklore. New file `docs/audits/FINDINGS_LEDGER.md`, one row per finding:

| Finding | First seen | Audits survived | Regimes | Current tier | Evidence refs | Next review |
|---|---|---|---|---|---|---|
| Earnings-drop penalty | Apr 2026 | Apr, May, Jun (3) | 2 | **Gate** (DROP_TYPE_GATE) | card 2026-06-25; audit notes | monthly re-test |
| Non-earnings +5.7 @28d | May 2026 | May, Jun (2) | 2 | **Prompt** (pinned card) | card 2026-06-25 | Jul audit |
| AVOID positive alpha | Jun 2026 | 1 (sign-flipped vs Apr) | 1 | **Console** | Jun audit, n=10 | needs 2 more |
| Falling-knife penalty | May 2026 | signal degenerate | — | **Suspended gate** | 256/264 YES | YES-rate < 0.60 |

Promotion path is strictly console → prompt → gate; demotion can skip levels. The monthly audit's output is precisely: update the ledger, re-pin the card if warranted, and file gate promotions/suspensions.

## Neither prompts nor gates: the 28d ramp is plumbing

The finding that edge materializes at ~4 weeks changes how we *measure*, not how agents *decide*:

1. **Evaluation window** — already done: `decision_outcomes` carries ret_1w/2w/4w/8w with 4w as the locked primary label; `decision_tracking` (the never-scheduled raw price log) is superseded and should be formally deprecated (stop referencing it in new code; keep the table for history).
2. **Reassess cadence** — `reassess_in_days` (decision_points column, `app/database.py:94`) is PM-set and optional, and nothing consumes it. Wire a default of **28** for BUY/BUY_LIMIT decisions when the PM leaves it unset, and have the Sell Council / reassessment tooling (`scripts/reassess_positions.py`) treat it as the trigger for a scheduled re-look. Do **not** tell the PM "the edge takes 4 weeks" as an instruction — the pinned card's base-rate line already carries that information as data.

## Implementation plan

**Phase A — pin/candidate split (small, do first)**
- `build_calibration_card.py` output → `*_candidate.json/.md`; new `pin_calibration_card.py --approve` with content validation (rules 1–4 above); `calibration_service` reads pinned only; remove `reload_card()` from `run_outcome_marking()`.
- Acceptance: nightly job runs, prompts byte-stable across nights; pin script rejects a card containing per-verdict lines or n < 20 buckets.

**Phase B — gate degeneracy monitors**
- Nightly firing-rate query over persisted `gates_fired`; per-gate ceilings; auto-suspend flag + QC alert. Backtest the monitor against June data: it must flag RISK_KNIFE by ~Jun 13 given the 256/264 stream.
- Acceptance: replayed June history trips the knife monitor; no false trips on DROP_TYPE_GATE.

**Phase C — Tier-3 tooling**
- `verdict_alpha_rolling.py` (7/14/21/28d × trailing windows); seed `docs/audits/FINDINGS_LEDGER.md` with the four findings above.
- Acceptance: script reproduces the June audit's headline numbers from `decision_outcomes`.

**Phase D — 28d plumbing**
- Default `reassess_in_days=28` for BUY/BUY_LIMIT; wire consumption in reassessment tooling; deprecation note on `decision_tracking`.

Phases are independent; A and B are the ones that prevent recurrence of the two observed failure modes (noise amplification, mode collapse).

## Risks and open questions

- **21d horizon:** `decision_outcomes` has 1w/2w/4w/8w but not 3w. Either add a `ret_3w` column (one migration + backfill rerun — script is idempotent) or let the rolling view show 7/14/28/56d instead. Recommend adding 3w only if the 21d point actually changes decisions; otherwise skip (YAGNI).
- **Pin discipline:** a hand-pinned card can silently go stale. Mitigation: the pin file carries `audited_at`; QC alerts if the pinned card is >45 days old while `CALIBRATION_ENABLED=1`.
- **Ledger honesty:** "audits survived" only means something if each monthly audit actually re-tests prior findings rather than only hunting new ones. The ledger's "next review" column is the checklist for that.
- **Degeneracy ceilings are guesses:** 0.80 for DROP_TYPE/NEWS gates should be sanity-checked against the trailing base rate of those classifications before Phase B ships.

## Post-merge amendments (2026-07-03)

1. **The pinned card is git-tracked** (`.gitignore`: `/data/*` +
   `!/data/calibration_card_pinned.json`). Render's repo checkout is
   ephemeral and the card paths are repo-relative, so a gitignored pin never
   reached production. Consequence: "pin" = run the script **and commit+push
   the file** — which also makes every pin a reviewable git commit,
   strengthening the hand-updated-monthly policy rather than weakening it.
   The candidate card remains ignored and ephemeral.
2. **The injection block is header + base-rate lines only.** The original
   sketch ended with an instruction ("Weigh these base rates…"); that
   violated this proposal's own rule 1 (data, not instructions) and was
   removed before shadow A/B data accumulated.
3. **Single source of numbers is enforced (§3.6, commit 06cc71d):** the PM
   prompt and gate reason-strings carry no hand-pasted statistics; numbers
   exist only in the pinned card injection and `FINDINGS_LEDGER.md`. The
   operational loop lives in `docs/audits/MONTHLY_AUDIT_RUNBOOK.md`.
