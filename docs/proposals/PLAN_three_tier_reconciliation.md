# Three-Tier Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five drift points between the merged three-tier feedback architecture (PR #18 + the stacked §3.6/pin-tracking/shadow commits, merged to main 2026-07-03) and the original idea, so every feedback channel conforms to its tier's rules and the monthly audit loop is operational.

**Architecture:** No new subsystems. One two-line code change (remove the imperative footer from the Tier-2 injection — must land BEFORE shadow A/B pairs accumulate, since changing treatment wording later invalidates paired comparisons), one operational message (pin script tells you to commit the card), and three documentation deliverables (ledger truth-up, monthly audit runbook, proposal update).

**Tech Stack:** Python 3.9, pytest (targeted files only), markdown.

## Global Constraints

- Work on a fresh branch off origin/main: `git checkout main && git pull && git checkout -b chore/three-tier-reconciliation` (the old feature branch is merged; do not commit to it).
- Python 3.9: no `X | Y` unions.
- NEVER run the full pytest suite (hangs on import-time API scripts). Only: `python3 -m pytest tests/test_calibration_pinning.py -v` (use `python3`; no `python` alias).
- Do NOT change any agent prompt text in `research_service.py` / `claude_dr_prompts.py` — the §3.6 stripped state is correct and is the A/B control arm.
- Do NOT touch `CALIBRATION_ENABLED` / `CALIBRATION_SHADOW` values (shadow is live in prod; ENABLED stays 0 until `eval_calibration_ab.py` clears its ship gate).
- `data/calibration_card_pinned.json` is git-tracked BY DESIGN (Render's repo checkout is ephemeral); the candidate card stays ignored. Do not "fix" the `.gitignore`.
- Adjudications encoded in this plan (from the reconciliation audit): (1) the injection footer is removed — Tier 2 is data, not instructions; (2) the PM prompt's BUY_LIMIT drop-type restriction STAYS as an action-definition rule but is recorded in the ledger as a PROVISIONAL prompt-tier item (n=1 audit) with an explicit gate-promotion/demotion criterion; (3) audit counts in the ledger are NOT incremented — the Jul 2 card rebuild was a data refresh, not a formal re-audit.

## File Structure

| File | Role |
|---|---|
| `app/services/calibration_service.py` (modify) | Drop the imperative footer from `calibration_block` |
| `tests/test_calibration_pinning.py` (modify) | Assert the footer is gone; assert pin-approve output names the commit step |
| `scripts/analysis/pin_calibration_card.py` (modify) | Approve path prints the git-commit/deploy step; docstring documents it |
| `docs/audits/FINDINGS_LEDGER.md` (modify) | Truth-up: rows for the 3 unlisted live gates, BUY_LIMIT re-tiering, R/R row, Aug 2026 review dates |
| `docs/audits/MONTHLY_AUDIT_RUNBOOK.md` (create) | The operational monthly loop (first run: Aug 1, 2026) |
| `docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md` (modify) | Record implemented status + the two post-merge design amendments |

---

### Task 1: Remove the imperative footer from the Tier-2 injection

The injected block currently ends with `"Weigh these base rates; do not let a compelling narrative override a poor base rate."` — an instruction, which violates the Tier-2 rule the ledger itself states ("never instructions"). This must land before shadow pairs accumulate: the treatment prompt's wording is what the A/B measures, and changing it mid-collection splits the sample. Shadow went live 2026-07-02/03, so pre-change rows are at most a day or two — note for the eval: filter `calibration_shadow_runs` to timestamps after this change deploys.

**Files:**
- Modify: `app/services/calibration_service.py:133-136`
- Test: `tests/test_calibration_pinning.py` (extend `test_block_serves_fresh_pin`)

**Interfaces:**
- Produces: `calibration_block(...)` returns `header + data lines` only (no trailing instruction). Signature unchanged. No caller parses the footer (verified: only tests assert on block content, none on the footer).

- [ ] **Step 1: Extend the test to fail on the footer**

In `tests/test_calibration_pinning.py`, `test_block_serves_fresh_pin` currently ends:

```python
    block = cs.calibration_block(is_earnings=True, force=True)
    assert "earnings drops overall" in block
    assert cs.pinned_card_age_days(today) == 2
```

Append two assertions:

```python
    block = cs.calibration_block(is_earnings=True, force=True)
    assert "earnings drops overall" in block
    assert cs.pinned_card_age_days(today) == 2
    # Tier 2 is data, not instructions: the block must end on a data line,
    # with no trailing imperative (THREE_TIER_FEEDBACK_PROPOSAL.md rule 1).
    assert "Weigh these base rates" not in block
    assert block.rstrip().endswith(")")  # last char of "... (n=184)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_calibration_pinning.py::test_block_serves_fresh_pin -v`
Expected: FAIL on `assert "Weigh these base rates" not in block`

- [ ] **Step 3: Remove the footer**

In `app/services/calibration_service.py`, replace:

```python
    header = f"HISTORICAL BASE RATES (4-week, n>={card.get('min_n')}, as of {card.get('as_of')}):"
    footer = ("Weigh these base rates; do not let a compelling narrative override "
              "a poor base rate.")
    return "\n".join([header, *lines, footer])
```

with:

```python
    # Data, not instructions (Tier 2 rule): header + base-rate lines only.
    # Any change to this block's wording mid-shadow splits the A/B sample —
    # record the deploy date and filter eval_calibration_ab accordingly.
    header = f"HISTORICAL BASE RATES (4-week, n>={card.get('min_n')}, as of {card.get('as_of')}):"
    return "\n".join([header, *lines])
```

- [ ] **Step 4: Run the test file to verify it passes**

Run: `python3 -m pytest tests/test_calibration_pinning.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/calibration_service.py tests/test_calibration_pinning.py
git commit -m "fix(calibration): Tier-2 injection is data only — drop the imperative footer"
```

---

### Task 2: Pin script announces the commit/deploy step

The pinned card is git-tracked and prod reads it from the repo checkout, but the pin script's approve path says nothing about committing — a pin that isn't committed and pushed silently never reaches prod.

**Files:**
- Modify: `scripts/analysis/pin_calibration_card.py` (module docstring Usage section + the approve-path print in `run`)
- Test: `tests/test_calibration_pinning.py` (extend `test_pin_run_requires_approve`)

**Interfaces:**
- Consumes/Produces: `run(approve, audited_at) -> int` unchanged; only printed output and docstring change.

- [ ] **Step 1: Extend the test to fail on the missing instruction**

In `tests/test_calibration_pinning.py`, `test_pin_run_requires_approve` ends:

```python
    assert pin.run(approve=True, audited_at="2026-06-30") == 0
    assert json.loads(pinned.read_text())["audited_at"] == "2026-06-30"
```

Change the approve call to capture output and assert the commit instruction (add `capsys` to the test's parameters: `def test_pin_run_requires_approve(tmp_path, monkeypatch, capsys):`):

```python
    assert pin.run(approve=True, audited_at="2026-06-30") == 0
    assert json.loads(pinned.read_text())["audited_at"] == "2026-06-30"
    out = capsys.readouterr().out
    # Prod reads the card from the repo (Render checkout is ephemeral):
    # a pin that is not committed+pushed never reaches production.
    assert "git add data/calibration_card_pinned.json" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_calibration_pinning.py::test_pin_run_requires_approve -v`
Expected: FAIL on the `git add` assertion

- [ ] **Step 3: Implement**

(a) In `run()`, after the existing approve-path print:

```python
    print(f"[pin_calibration_card] pinned -> {PINNED_JSON} "
          f"(audited_at={pinned['audited_at']}, ref={pinned['pinned_git_ref']})")
    return 0
```

insert before `return 0`:

```python
    print("[pin_calibration_card] NOW COMMIT IT — prod reads the card from the "
          "repo (Render checkout is ephemeral):\n"
          f"  git add data/calibration_card_pinned.json && "
          f"git commit -m \"chore(calibration): pin card {pinned['audited_at']}\" && git push")
```

(b) In the module docstring's Usage block, append the line:

```
After --approve: commit and push data/calibration_card_pinned.json — the file
is git-tracked because Render's repo checkout is ephemeral; an uncommitted pin
never reaches production.
```

- [ ] **Step 4: Run the test file to verify it passes**

Run: `python3 -m pytest tests/test_calibration_pinning.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/analysis/pin_calibration_card.py tests/test_calibration_pinning.py
git commit -m "feat(calibration): pin script announces the commit+push step (prod reads the repo)"
```

---

### Task 3: Findings-ledger truth-up

The ledger claims to be the promotion state machine, but three live gates aren't in it, the BUY_LIMIT row no longer reflects reality (its evidence pointer was deleted by §3.6, and a prompt-tier restriction now exists on an n=1 finding), and the R/R finding enforced in the prompt has no row. Grandfathered items are recorded honestly rather than back-justified.

**Files:**
- Modify: `docs/audits/FINDINGS_LEDGER.md`

**Interfaces:**
- Produces: ledger consumed by the monthly runbook (Task 4). Row set below is the contract.

- [ ] **Step 1: Replace the table and add the scope sentence**

After the existing sentence "The monthly audit MUST re-test every non-console row..." add:

```markdown
Scope rule: EVERY live gate in `decision_gate_service.py` and every
outcome-derived instruction in an agent prompt must have a row here — if it
is not in the ledger, it is not sanctioned feedback.
```

Replace the entire table with:

```markdown
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
```

- [ ] **Step 2: Verify the table renders**

Run: `python3 - <<'EOF'
content = open("docs/audits/FINDINGS_LEDGER.md").read()
rows = [l for l in content.splitlines() if l.startswith("|")]
assert all(l.count("|") == rows[0].count("|") for l in rows), "ragged table"
assert "Scope rule" in content
print(f"ledger ok: {len(rows) - 2} findings")
EOF`
Expected: `ledger ok: 10 findings`

- [ ] **Step 3: Commit**

```bash
git add docs/audits/FINDINGS_LEDGER.md
git commit -m "docs(audits): ledger truth-up — rows for all live gates, provisional BUY_LIMIT rule, R/R; Aug review dates"
```

---

### Task 4: Monthly audit runbook

The whole idea hinges on "updated by hand monthly after an audit" and "promote only after 2–3 audits" — but nothing operationalizes the audit. This runbook is the loop. First run: Aug 1, 2026.

**Files:**
- Create: `docs/audits/MONTHLY_AUDIT_RUNBOOK.md`

- [ ] **Step 1: Create the file with this content**

```markdown
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
- Filter to rows after the footer-removal deploy (see PLAN_three_tier_reconciliation Task 1).
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
```

- [ ] **Step 2: Verify no placeholders and it parses as a checklist**

Run: `python3 - <<'EOF'
c = open("docs/audits/MONTHLY_AUDIT_RUNBOOK.md").read()
assert "TBD" not in c and "TODO" not in c
for section in ["Pipe health", "Refresh the evidence", "Re-test the ledger",
                "Shadow A/B checkpoint", "Re-pin", "Knife re-enable", "Close out"]:
    assert section in c, section
print("runbook ok")
EOF`
Expected: `runbook ok`

- [ ] **Step 3: Commit**

```bash
git add docs/audits/MONTHLY_AUDIT_RUNBOOK.md
git commit -m "docs(audits): monthly audit runbook — the loop that re-earns every gate and prompt line"
```

---

### Task 5: Proposal doc records the two post-merge amendments

`THREE_TIER_FEEDBACK_PROPOSAL.md` still says the pinned card lives in gitignored `data/` and shows the injection ending in an instruction — both superseded. Future readers (and future audits) treat the proposal as the tier contract, so it must match reality.

**Files:**
- Modify: `docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md`

- [ ] **Step 1: Update the status line and add an amendments section**

(a) Change the status line at the top from:

```markdown
**Status:** Proposal (2026-07-02)
```

to:

```markdown
**Status:** Implemented (PR #18, merged 2026-07-03) + amendments below
```

(b) Append at the end of the document:

```markdown
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
```

- [ ] **Step 2: Verify**

Run: `grep -c "Post-merge amendments" docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md`
Expected: `1`

- [ ] **Step 3: Commit**

```bash
git add docs/proposals/THREE_TIER_FEEDBACK_PROPOSAL.md
git commit -m "docs(proposals): three-tier proposal marked implemented; record post-merge amendments"
```

---

## Final verification (after all tasks)

- [ ] `python3 -m pytest tests/test_calibration_pinning.py tests/test_gate_degeneracy.py tests/test_decision_gate_service.py -v` — all pass (do NOT run the full suite).
- [ ] `python3 -c "from app.services.calibration_service import calibration_block; b = calibration_block(is_earnings=True, force=True); print(b); assert 'Weigh' not in b"` — prints the pinned-card block with no instruction line.
- [ ] Ledger check prints `ledger ok: 10 findings`; runbook check prints `runbook ok`.
- [ ] Open a PR to main; after deploy, hit `/health` and confirm `outcomes.shadow_runs` is increasing and `pinned_card_age_days` is small — this is the prod-side confirmation that the shadow A/B is actually collecting (it could not be verified from this machine).

## Deliberately out of scope (tracked elsewhere)

- §3.4 DR additions — deferred to the Aug 1 audit by prior decision.
- §4.4 QC symbol normalization (9 perpetually-stale tickers) — separate task.
- Whether to git-track `scripts/data/*.py` (real source currently hidden by the old broad ignore) — user decision.
