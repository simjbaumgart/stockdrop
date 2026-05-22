# PLAN — DR Override Gating Rule + FM JSON Parser Repair

**Date:** 2026-05-20
**Status:** Design — pending implementation
**Owner:** sim.j.baum@gmail.com
**Related memo:** May 20 DR validation checkpoint (May 11–13 cohort matured at +14d)

---

## 1. Background

The May 20 validation memo confirmed two converging signals from the live pipeline:

1. **DR override authority is asymmetrically valuable.** The +14d-matured May 11–13 cohort shifted the running scorecard from +5 pts net (May 14 baseline) to roughly **−15 pts net** across 28 calls. The drag is concentrated entirely in DR's **soft-rationale AVOID overrides on PM BUY / BUY_LIMIT verdicts** (ONON −16.6, AXON −7.6, J −5.0, CEG −1.9, FICO −8.9, IQV −11.2, ADSK −6.1 — roughly **−60 pts**). DR's **hard-event AVOIDs** continue to compound positively (UI +21 at +7d, GAP +4, POOL +8, DLTR +8, CRC +5.5, EMBJ +7 — roughly **+60 pts**). The new DR BUY UPGRADE / ADJUSTMENT mechanism (since May 15) is clean: NU +4.87%, BOLSY +4.32%, ISNPY +2.15%, all on verified hard-event / math-correction templates.

2. **Fund Manager JSON parser is silently dropping real trades.** Four parser failures in six trading days: MSTR (5/15), CHWY (5/18), RDDT (5/20, BUY_LIMIT MOD at $142.60–143.55 with R/R 0.5), EQPT (5/20). The diagnostic error messages all point at **line 21** of the FM output:
   - MSTR: `line 21 column 17 (char 757)`
   - CHWY: `line 21 column 16 (char 841)`
   - RDDT: `line 21 column 17 (char 898)`
   - EQPT: `line 21 column 16 (char 681)`
   This is not a random JSON glitch — it is a systematic prompt regression on a specific field. The MSTR fragment near that line reads `"entry_trigger": "N/A — Structural risks outweigh potential technical bounce.",` — the em-dash inside a string adjacent to the comma is suspicious. The fix must address the prompt-side cause; a JSON-repair library is only a safety net.

This plan ships two minimal, independent interventions that address both findings without touching the parts of the pipeline that are working.

---

## 2. Goals and non-goals

### Goals

- Restrict DR's authority to flip PM `BUY` / `BUY_LIMIT` → `AVOID` to cases where DR cites a **verifiable, type-specific** hard event that cross-references DR's own `verification_results` array.
- Preserve DR's full authority on `CONFIRMED`, `UPGRADED`, `ADJUSTED`, and on `OVERRIDDEN` against non-BUY PM verdicts — the data shows these earn alpha and should not be touched.
- Diagnose the FM line-21 prompt regression first, then ship the prompt fix; add `json-repair` and Flash-repair as belt-and-suspenders only.
- Make the new gate auditable and reversible: feature-flag the demotion logic, persist `what/why` for every gated decision, and define an explicit post-deploy measurement loop.

### Asymmetric design — intentional

DR upgrading PM `WATCH` / `AVOID` → `BUY` (BOLSY / ISNPY / NU template) is **not gated**. Rationale:

- N=3 wins is too small a sample to confidently regulate against.
- The downside of a bad upgrade is bounded (the trade gets entered, the stop fires); the downside of a bad override is unbounded opportunity cost.
- Gating both directions over-engineers a system we still need to learn about.

However, `UPGRADED` and `ADJUSTED` verdicts are still required to populate the `hard_event` field **for measurement only** — the gate logic returns `NOT_APPLICABLE` for these verdicts, but the field is persisted so the post-deploy scorecard can analyze whether upgrades that cited a hard event outperform those that did not.

### Non-goals

- No retroactive re-classification of historical DR overrides as `ADVISORY_AVOID`. Historical rows pre-policy are marked **`NA`** in the new gate-status column — they were not evaluated under this policy and should not be re-labeled.
- No changes to the gatekeeper, Bollinger threshold, or screener.
- No scorecard recomputation tooling in this PR. Adding `deep_research_override_gate` to the trade-report CSV is sufficient for the post-deploy validation loop in §9.
- No changes to PM, sensor, or debate prompts beyond the single FM line-21 prompt fix in §4.2.

---

## 3. Intervention #1 — DR override gating rule

### 3.1 Policy

When DR returns `review_verdict == "OVERRIDDEN"` **and** the PM verdict being overridden is `BUY` or `BUY_LIMIT` → `AVOID`, the override is **gated**: it stands only if DR cites a verifiable, type-specific hard event AND that event is cross-referenced in DR's own `verification_results` array. Otherwise the override is demoted to advisory.

The **nine hard-event types**, each with its own validation rule (see §3.3):

| Type | Description |
| ---- | ----------- |
| `LAWSUIT` | Litigation filing, class action, settlement |
| `REGULATOR_ACTION` | SEC, DOJ, FTC, EPA, OFAC, state AG enforcement |
| `RESTATED_GUIDANCE` | Formal guidance cut, withdrawal, or restatement |
| `COVENANT_BREACH` | Debt covenant violation, technical default |
| `INSIDER_FORM_4` | Recent Form 4 sale or buy by named insider |
| `ANALYST_DOWNGRADE_NAMED_TARGET` | Named bank, named analyst, target number |
| `FDA_ACTION` | CRL, AdCom vote, label change, recall, warning letter |
| `ANTITRUST_PROBE` | DOJ Antitrust, FTC, EC, state AG with named investigator |
| `M_AND_A_TRIGGER` | Announced acquisition, termination, hostile bid, regulatory blocker |

All other rationales (sector momentum concern, valuation, broad sentiment, technical bearishness, "feels overextended", "risk/reward unfavorable", "macro headwind", "insiders have been trimming") are **soft rationale** and do not pass the gate.

#### The materiality vs. existence problem

The whole point of the gate is to demote rationales like ONON's "wholesale deceleration + insider trimming" — where the Form 4 *exists* but the *materiality* is soft. Without explicit anti-pattern checks, DR will learn to tag every soft override with a structured `hard_event` payload and the gate becomes ceremonial. §3.3 defines type-specific minimums that close this loop.

### 3.2 Scope of the gate

The gate applies **only** to: `review_verdict == "OVERRIDDEN"` flipping a PM `BUY` or `BUY_LIMIT` to `AVOID`.

The gate explicitly does **not** apply to:

- `CONFIRMED`, `UPGRADED`, `ADJUSTED` → DR retains full authority. (See §2 asymmetric design.)
- `OVERRIDDEN` against a PM `WATCH_FOR_STAB`, `PASS`, or `PASS_INSUFFICIENT_DATA` → no BUY signal to protect.
- `OVERRIDDEN` downgrading PM `BUY`/`BUY_LIMIT` to `WATCH` (not all the way to AVOID) → not in the measured dataset, narrow scope at first.
- `INCOMPLETE_TRADING_LEVELS` or `PENDING_REVIEW` → already non-flipping states.

Keeping scope narrow to BUY/BUY_LIMIT → AVOID matches the dataset the May 20 memo measured. If we later observe that PM BUY → DR WATCH downgrades on soft rationale also leak alpha, the scope expansion is a follow-up.

### 3.3 Schema extension — type-specific anti-patterns

Extend the DR JSON schema (in `deep_research_service.py` — the Senior Reviewer prompt at line ~1224 and the repair-fallback schema at line ~1660) with one new optional field:

```json
"hard_event": {
  "type": "LAWSUIT | REGULATOR_ACTION | RESTATED_GUIDANCE | COVENANT_BREACH | INSIDER_FORM_4 | ANALYST_DOWNGRADE_NAMED_TARGET | FDA_ACTION | ANTITRUST_PROBE | M_AND_A_TRIGGER",
  "named_entity": "Specific party (must be type-appropriate — see below)",
  "source_url": "URL to filing, press release, or research note",
  "filing_date": "YYYY-MM-DD — when the underlying instrument was filed/issued",
  "material_development_date": "YYYY-MM-DD — most recent development (hearing, ruling, new filing, decision). May equal filing_date for instantaneous events.",
  "docket_or_identifier": "Case docket number, SEC filing accession, Form 4 transaction code, etc. — type-dependent, see below",
  "summary": "One-sentence factual statement of the event",
  "verification_xref": "Substring that must match the `claim` field of at least one VERIFIED entry in this same response's verification_results array"
}
```

The prompt instructs DR: *"If your `review_verdict` is `OVERRIDDEN` and the council recommended BUY or BUY_LIMIT, you MUST populate `hard_event` with a verifiable, type-appropriate named event. The event must also appear as a VERIFIED claim in your `verification_results` array, and `verification_xref` must contain a distinctive substring of that claim. If you cannot meet both conditions, return `CONFIRMED` or `ADJUSTED` instead — soft rationale alone is not grounds for override. The grading rubric is unforgiving: the gate will demote payloads that fail the type-specific checks below."*

#### Type-specific validation rules

These are the post-processor anti-pattern checks. Each rule is documented inline in the prompt so DR sees the rubric:

| Type | `named_entity` must… | `source_url` must… | `docket_or_identifier` must… | Recency rule |
| ---- | ------------------- | ------------------ | --------------------------- | ------------ |
| `LAWSUIT` | Name the plaintiff or court | Resolve to `courtlistener.com`, `pacer.uscourts.gov`, official court site, or company 8-K | Match docket regex `\d{1,2}:\d{2}-cv-\d{4,5}` or include a case-number-shaped token | `material_development_date` within last **60 days** |
| `REGULATOR_ACTION` | Be one of: SEC, DOJ, FTC, EPA, OFAC, CFPB, FINRA, OCC, EC, FCA, BaFin, state AG (NY/CA/TX named) | Host on agency domain (`sec.gov`, `justice.gov`, `ftc.gov`, etc.) OR be the company's 8-K | Reference the agency's matter/enforcement ID where one exists | `material_development_date` within **60 days** |
| `RESTATED_GUIDANCE` | Equal the company itself (e.g., "ONON management" not "an analyst") | Resolve to company IR site, 8-K filing on sec.gov, or wire (PR Newswire, Business Wire, GlobeNewswire) | SEC accession number for 8-K, or null for press release | `material_development_date` within **30 days** |
| `COVENANT_BREACH` | Name the lender/agent OR the company's disclosure | 8-K filing on sec.gov, or named-lender press release | SEC accession number | `material_development_date` within **60 days** |
| `INSIDER_FORM_4` | Name the insider (CEO/CFO/Director name) AND their role | Resolve to `sec.gov/Archives/edgar/data/` | SEC accession number | **`filing_date` within last 14 days** — "insiders have been selling for months" is explicitly disqualifying |
| `ANALYST_DOWNGRADE_NAMED_TARGET` | Be one of a recognized sell-side firm (see whitelist below) AND name the individual analyst | Resolve to the firm's domain, a recognized aggregator (`tipranks.com`, `marketbeat.com`, `benzinga.com`, `seekingalpha.com`), or a wire | Include the target price as a parseable number in `summary` | `material_development_date` within **14 days** |
| `FDA_ACTION` | Be "FDA" or a named division (CDER, CBER, CDRH) | Host on `fda.gov`, company 8-K, or wire | FDA action identifier (e.g., NDA/BLA/PMA number, CRL reference) where available | `material_development_date` within **60 days** |
| `ANTITRUST_PROBE` | Name the agency AND, where public, the named investigator/division | Host on agency domain or wire | Matter ID where public | `material_development_date` within **90 days** |
| `M_AND_A_TRIGGER` | Name both parties to the transaction | 8-K filing or wire | SEC accession number where applicable | `material_development_date` within **30 days** |

**Recognized sell-side firms whitelist** (for `ANALYST_DOWNGRADE_NAMED_TARGET`, case-insensitive substring match on `named_entity`):

```
Goldman Sachs, Morgan Stanley, JPMorgan, JP Morgan, Bank of America, BofA, Citigroup, Citi,
Wells Fargo, Barclays, Deutsche Bank, UBS, Credit Suisse, BNP Paribas, Societe Generale,
HSBC, RBC Capital, RBC, BMO Capital, BMO, TD Cowen, Cowen, Jefferies, Evercore, Lazard,
Houlihan Lokey, Piper Sandler, Stifel, Raymond James, Baird, Robert W. Baird, Oppenheimer,
Wedbush, Wolfe Research, Mizuho, Nomura, Macquarie, CLSA, Bernstein, Redburn, Atlantic Equities,
KeyBanc, Truist, Argus, Needham, Roth, Canaccord, Susquehanna, SIG, William Blair,
Guggenheim, BTIG, Loop Capital, Citizens JMP, JMP Securities, B. Riley
```

The whitelist lives in `app/services/dr_gate_whitelist.py` so it can be updated without touching the gate logic.

#### Two-condition gate

For a hard event to pass:

1. **Self-classification check** — type-specific rules above all pass.
2. **Cross-reference check** — `hard_event.verification_xref` is a non-empty substring (≥ 12 chars) of the `claim` field on at least one entry in `verification_results` where `verdict == "VERIFIED"` and `source_url` is a syntactically valid http(s) URL.

Both must hold. The cross-reference closes the loop where DR self-classifies its own opinion as a hard event — it forces DR to have already independently verified the event in its own pipeline.

### 3.4 Post-processing: the gate

After DR returns and JSON is parsed, before persisting the result, run the gate in `deep_research_service._handle_completion`. The whole demotion block is wrapped in a feature flag (see §8):

```python
DR_OVERRIDE_GATING_ENABLED = os.getenv("DR_OVERRIDE_GATING_ENABLED", "true").lower() == "true"

if DR_OVERRIDE_GATING_ENABLED and review_verdict == "OVERRIDDEN" and pm_action in ("BUY", "BUY_LIMIT"):
    passed, reason = hard_event_passes_gate(
        result.get("hard_event"),
        result.get("verification_results", []),
        drop_date=task.get("date"),
    )
    if passed:
        gate_status = "GATED_HARD_EVENT"
        gate_failure_reason = None
        # override stands — proceed as today
    else:
        gate_status = "ADVISORY_AVOID"
        gate_failure_reason = reason  # e.g., "INSIDER_FORM_4 filing_date too old"
        # demote: preserve PM verdict, surface DR rationale as advisory
        review_verdict = "ADVISORY_AVOID"
        action = None  # signals downstream code to leave trading columns alone
        # null DR-provided trading levels in result before persistence — they
        # belonged to a flipped verdict that no longer applies (mirrors
        # INCOMPLETE_TRADING_LEVELS handling in _handle_completion today)
        for f in ("entry_price_low", "entry_price_high", "stop_loss",
                  "take_profit_1", "take_profit_2",
                  "upside_percent", "downside_risk_percent", "risk_reward_ratio",
                  "sell_price_low", "sell_price_high", "ceiling_exit", "exit_trigger"):
            result[f] = None
else:
    gate_status = "NOT_APPLICABLE"
    gate_failure_reason = None
```

`hard_event_passes_gate(hard_event, verification_results, drop_date)` lives in a new module `app/services/dr_gate.py` and returns `(passed: bool, failure_reason: Optional[str])`. The failure_reason is a short string code like `"missing_hard_event"`, `"type_invalid"`, `"named_entity_not_in_whitelist"`, `"source_url_wrong_domain"`, `"filing_date_too_old"`, `"verification_xref_not_found"`. These codes are recorded in the new `deep_research_gate_failure_reason` column for offline analysis.

### 3.5 ADVISORY_AVOID semantics

`ADVISORY_AVOID` is a new terminal DR state that lives alongside `OVERRIDDEN`, `CONFIRMED`, `UPGRADED`, `ADJUSTED`, `INCOMPLETE_TRADING_LEVELS`, and `PENDING_REVIEW`:

- **DB:** `deep_research_review_verdict` = `"ADVISORY_AVOID"`. `action` is left null (preserve PM verdict). Trading-level columns are not overwritten by `_apply_trading_level_overrides`.
- **Report rendering:** the report shows a yellow warning block above the PM verdict: *"Deep Research flagged concerns but did not cite a verifiable hard event (failure: `<gate_failure_reason>`); PM verdict preserved. DR rationale: <reason>."*
- **Email summary:** the verdict line reads `<PM verdict> [DR advisory]` so the human reviewer sees the disagreement.
- **Composite scoring (`_calculate_deep_research_score`):** map `ADVISORY_AVOID` → 10 pts (between `OVERRIDDEN` at 5 and `ADJUSTED` at 20). This is informational — it does not feed into trading.

### 3.6 New DB columns and schema migration

Three columns added to `decision_points`:

```sql
ALTER TABLE decision_points ADD COLUMN deep_research_override_gate TEXT NOT NULL DEFAULT 'NA';
ALTER TABLE decision_points ADD COLUMN deep_research_hard_event TEXT DEFAULT NULL;
ALTER TABLE decision_points ADD COLUMN deep_research_gate_failure_reason TEXT DEFAULT NULL;
```

Using `DEFAULT 'NA'` on the gate column means SQLite backfills historical rows in a single ALTER TABLE statement — no separate `UPDATE` needed, no separate backfill script. Newly inserted rows also default to `'NA'` and are overwritten by `_handle_completion` once DR runs.

`deep_research_override_gate` values:

- `GATED_HARD_EVENT` — DR override flipped PM BUY/BUY_LIMIT to AVOID, hard event passed both gate checks, override stands.
- `ADVISORY_AVOID` — DR override was demoted because the hard event was missing, malformed, or failed the type-specific or cross-reference check.
- `NOT_APPLICABLE` — DR verdict was not an override of a PM BUY/BUY_LIMIT (most rows going forward).
- `NA` — historical rows pre-policy, or rows where DR has not yet completed. Never re-evaluated.

`deep_research_hard_event` holds the JSON-serialized `hard_event` object as DR emitted it (even for `ADVISORY_AVOID` rows — operators investigating "why was this demoted?" need to see the raw payload). Null when DR did not emit the field.

`deep_research_gate_failure_reason` holds the short code from `hard_event_passes_gate` when `gate_status == 'ADVISORY_AVOID'`. Null otherwise.

These three columns are also added to the **trade report CSV** (the 60-minute periodic export from `tracking_service`) so the post-deploy scorecard query in §9 is a single SELECT, not a join.

### 3.7 Migration mechanics

A startup migration in `app/database.py` runs the three `ALTER TABLE … DEFAULT 'NA'` statements idempotently (catch `sqlite3.OperationalError` on "duplicate column name"). No separate `UPDATE` statement and no backfill script — `DEFAULT 'NA'` does the work. Per user direction, historical DRs are **not** retroactively reclassified.

---

## 4. Intervention #2 — Fund Manager JSON parser

### 4.1 Root cause analysis comes first

The four failure messages all point at line 21 of the FM JSON output:

| Date | Symbol | Error position |
| ---- | ------ | -------------- |
| 5/15 | MSTR | line 21 column 17 (char 757) |
| 5/18 | CHWY | line 21 column 16 (char 841) |
| 5/20 | RDDT | line 21 column 17 (char 898) |
| 5/20 | EQPT | line 21 column 16 (char 681) |

Four consecutive failures on the same line of the same schema is not a JSON glitch — it is a **systematic prompt regression** on a specific field. The MSTR fragment captured near that line is:

```
"entry_trigger": "N/A — Structural risks outweigh potential technical bounce.",
```

The em-dash inside the string value, adjacent to the closing quote-comma, is the most plausible source. Possible mechanisms: an upstream prompt-side change that started encouraging em-dashes in `entry_trigger`; a model-version drift in Gemini that started emitting smart quotes paired with em-dashes; an unescaped quote where the model tried to embed a quote inside the string.

**The repair library cannot diagnose what the model is doing wrong.** Adding `json-repair` before establishing the root cause masks the regression and lets it spread to other fields.

### 4.2 Three-step fix, in order

**Step 1 — Instrument first (ship this on its own commit).** Modify `_extract_json` in `research_service.py:1919` so that on any `json.JSONDecodeError`, it dumps the raw FM output to disk under `data/parser_failures/<symbol>_<date>_fm_raw.txt` and logs the JSONDecodeError's `pos`, `lineno`, `colno`, and the 80 characters surrounding the failure point. This is a passive change — no behavior change for successful parses, no behavior change for failures except the on-disk artifact. Ships first because every subsequent fix depends on having captured the actual bad output.

After deploy, wait for the next FM failure (or replay the four failed cases from logs if the raw text is still available) and confirm whether line 21 is `entry_trigger` and whether the failure character is an em-dash, a smart quote, or something else.

**Step 2 — Prompt-side fix (ship after step 1 confirms the cause).** Depending on what step 1 reveals, one of:

- *If em-dash inside `entry_trigger`:* update the FM prompt to require ASCII-only in `entry_trigger` and use `null` when no entry is recommended (instead of `"N/A — …"`). This eliminates the em-dash failure mode at the source. Most actionable when no entry is recommended is the AVOID case where `entry_trigger` should always be `null` anyway.
- *If smart quotes:* add a normalization pre-step that replaces `‘`, `’`, `“`, `”` with ASCII equivalents before parsing. Cheap, no prompt change.
- *If unescaped quote:* tighten the prompt's escaping guidance with a concrete bad-example/good-example pair.
- *If something else:* address the actual cause directly.

**Step 3 — Belt-and-suspenders repair layer (ship after step 2).** Even with the prompt fix, models drift and new failure modes will appear. Add the two-stage repair pipeline as a safety net:

- *Stage 1 — `json-repair` library.* Try the existing `find('{')` / `rfind('}')` + `json.loads` path first. If `json.JSONDecodeError`, run the candidate substring through `json-repair` (deterministic, no network, handles trailing commas, missing quotes, dangling content). Add to `requirements.txt`, pin a known version.
- *Stage 2 — Flash repair.* If `json-repair` still fails, call the existing DR Flash-repair mechanism. Extract `_repair_json_using_flash` from `deep_research_service.py` into a new shared module `app/services/json_repair_service.py` with a schema registry — `'fund_manager'` and `'deep_research'`. Reuse DR's existing 90s timeout.
- If both stages fail, behavior is unchanged: caller sees `None`, decision becomes `PASS_INSUFFICIENT_DATA`.

### 4.3 FM schema for Flash repair

The shared `json_repair_service` schema registry holds the canonical FM output JSON contract. The exact field list is extracted from `_create_fund_manager_prompt` and matched verbatim — no new fields invented in this PR. Minimum surface:

```json
{
  "action": "BUY | BUY_LIMIT | WATCH_FOR_STAB | PASS | PASS_INSUFFICIENT_DATA | AVOID",
  "conviction": "HIGH | MODERATE | LOW | NONE",
  "reason": "string",
  "drop_type": "string",
  "key_factors": ["list", "of", "strings"],
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit_1": 0.0,
  "take_profit_2": null,
  "risk_reward_ratio": 0.0,
  "entry_trigger": null
}
```

### 4.4 Telemetry

Log a structured line on every repair attempt (both DR and FM paths now flow through the shared service):

```
[JSON Repair] schema=fund_manager symbol=AAPL stage=local outcome=success raw_len=2147
[JSON Repair] schema=fund_manager symbol=RDDT stage=json_repair outcome=success raw_len=4128 repair_len=2031
[JSON Repair] schema=fund_manager symbol=EQPT stage=flash outcome=success raw_len=3201
[JSON Repair] schema=fund_manager symbol=XXX stage=flash outcome=failed_total
```

Daily roll-up added to the existing trade-report CSV: `parse_local_ok`, `parse_json_repair_ok`, `parse_flash_ok`, `parse_total_failed`, broken out by `schema`. Pipeline-wide visibility into how often we are masking a regression with the repair layer — if `parse_json_repair_ok` count for `fund_manager` is non-trivial after step 2 lands, that's a signal the prompt fix is incomplete and a real regression remains.

---

## 5. Components touched

| File | Change |
| ---- | ------ |
| `app/services/deep_research_service.py` | Add `hard_event` (with all six sub-fields) to Senior Reviewer prompt + Flash-repair schema. Add gate logic in `_handle_completion`. Map `ADVISORY_AVOID` in `_calculate_deep_research_score`. Move `_repair_json_using_flash` body to shared service. |
| `app/services/dr_gate.py` *(new)* | `hard_event_passes_gate(hard_event, verification_results, drop_date)` and its nine type-specific sub-validators. Returns `(passed, failure_reason_code)`. |
| `app/services/dr_gate_whitelist.py` *(new)* | Sell-side firm whitelist for `ANALYST_DOWNGRADE_NAMED_TARGET`. Single Python list; editable without touching gate logic. |
| `app/services/json_repair_service.py` *(new)* | Two-stage repair helper (`json-repair` → Flash). Schema registry for `'fund_manager'` and `'deep_research'`. Shared Flash-repair caller. Structured telemetry logging. |
| `app/services/research_service.py` | Step 1: add raw-output dump on `JSONDecodeError` at line 1919. Step 2: prompt fix in `_create_fund_manager_prompt` (after step 1 confirms cause). Step 3: replace `_extract_json` call site at line 883 with `json_repair_service.parse_or_repair(text, schema='fund_manager')`. |
| `app/services/pm_verdict_formatters.py` | Render `ADVISORY_AVOID` advisory block in the report and email summary; include `gate_failure_reason` in the warning text. |
| `app/database.py` | Add three new columns with `DEFAULT 'NA'` / `DEFAULT NULL`. Extend `update_deep_research_data` signature with `gate_status`, `hard_event_json`, `gate_failure_reason`. Idempotent ALTER on startup. |
| `app/services/tracking_service.py` (CSV export) | Include `deep_research_override_gate`, `deep_research_hard_event`, `deep_research_gate_failure_reason` columns in the trade-report CSV. |
| `requirements.txt` | Add `json-repair` (pinned). |
| `.env.example` | Add `DR_OVERRIDE_GATING_ENABLED=true`. |
| `tests/` | New test files (see §7). |

---

## 6. Data flow

```
PM produces decision JSON
  └─ research_service._extract_json  →  json_repair_service.parse_or_repair(text, schema='fund_manager')
        ├─ Stage 0: raw-output dump to data/parser_failures/ on any JSONDecodeError (passive)
        ├─ Stage 1: local json.loads on find('{')..rfind('}') slice
        ├─ Stage 2: json-repair library
        └─ Stage 3: Flash repair via gemini-2.5-flash
        └─ on total failure: caller maps to PASS_INSUFFICIENT_DATA (unchanged)

DR produces senior-reviewer JSON (with new hard_event field)
  └─ deep_research_service._parse_research_output
        └─ json_repair_service.parse_or_repair(text, schema='deep_research')
  └─ deep_research_service._handle_completion
        ├─ if DR_OVERRIDE_GATING_ENABLED and review_verdict == OVERRIDDEN and pm_action in (BUY, BUY_LIMIT):
        │     ├─ hard_event_passes_gate(hard_event, verification_results, drop_date)
        │     │     ├─ self-classification check  (type-specific rules)
        │     │     └─ cross-reference check       (xref → verification_results VERIFIED entry)
        │     ├─ pass  → gate_status = GATED_HARD_EVENT, override stands
        │     └─ fail  → gate_status = ADVISORY_AVOID
        │                ├─ review_verdict = ADVISORY_AVOID
        │                ├─ action = None
        │                ├─ trading levels nulled
        │                └─ gate_failure_reason persisted
        └─ update_deep_research_data persists gate_status, hard_event JSON, gate_failure_reason
```

---

## 7. Testing

### 7.1 Gate logic — type-specific validators

New `tests/test_dr_override_gate.py`. Per-type table-driven tests:

- For each of the nine types: one pass case (valid hard_event + cross-ref hit) → `GATED_HARD_EVENT`; one fail case per validation dimension (bad named_entity, bad source_url domain, missing docket where required, date too old, missing xref) → `ADVISORY_AVOID` with the correct `gate_failure_reason` code.
- The **ONON anti-pattern test**: feed in a synthetic `hard_event` of type `INSIDER_FORM_4` with a Form 4 filing dated 90 days before drop date. Expect demotion (filing_date > 14 days), `gate_failure_reason == "filing_date_too_old"`.
- The **UI defense test**: feed in a synthetic `hard_event` of type `LAWSUIT` with `filing_date = 2024-08-01` but `material_development_date = 2026-05-05` (ITC determination 3 days before drop). Expect pass — the 60-day window applies to `material_development_date`, not `filing_date`.
- The **soft-rationale-with-decoy-xref test**: feed `hard_event` with valid-looking fields but `verification_xref` that does not match any `verification_results` entry. Expect demotion, `gate_failure_reason == "verification_xref_not_found"`.
- The **fabricated-analyst test**: `ANALYST_DOWNGRADE_NAMED_TARGET` with `named_entity = "Acme Research Partners"` (not in whitelist). Expect demotion.
- Scope tests: `OVERRIDDEN` + PM WATCH_FOR_STAB → `NOT_APPLICABLE`; `CONFIRMED` + PM BUY → `NOT_APPLICABLE`; `UPGRADED` + PM BUY → `NOT_APPLICABLE`.
- Feature flag test: `DR_OVERRIDE_GATING_ENABLED=false` → gate always returns `NOT_APPLICABLE`, override stands as today.

Replay fixtures: pull from `data/council_reports/` for the May 11–13 ONON / AXON / J / CEG decisions. Run the gate against the real DR output JSON. Expect all four to demote (none cite a hard event meeting the gate). Pull UI / GAP / POOL — if available — and check whether their rationales contain hard events. If UI rationale does not cite the ITC case as a structured event, that confirms the DR prompt needs to be re-run on the new schema before the gate can save UI-template alpha. Document this finding in the spec follow-up.

### 7.2 Parser repair — replay fixtures

New `tests/test_fm_parser_repair.py`:

- **Step 1 instrumentation:** synthesize a malformed FM output, call `_extract_json`, assert the raw-output dump file exists at the expected path and the log line contains `pos`, `lineno`, `colno`.
- **Step 2 prompt fix:** specific to whichever fix step 1 motivates (e.g., if em-dash, test that the post-normalization string has no `—`). Test added after step 1 confirms cause.
- **Step 3 layered repair:**
  - Clean JSON → Stage 1 success.
  - JSON with trailing comma → Stage 1 fails, Stage 2 (json-repair) succeeds.
  - JSON with `### Sources:` block appended → Stage 1 fails, Stage 2 succeeds.
  - JSON with em-dash + quote-comma (the MSTR-style failure) → confirms Stage 2 handles or Stage 3 (mocked Flash) handles.
  - Truncated JSON → Stage 2 fails, Stage 3 (mocked) succeeds.
  - Total garbage → all stages fail, return None.
- **Replay fixtures from production failures:** capture raw FM output (from `data/parser_failures/` once step 1 lands) for MSTR / CHWY / RDDT / EQPT. Assert each parses under the new helper, and the resulting decision matches the underlying setup from the May 20 memo (RDDT → BUY_LIMIT MOD $142.60–143.55 stop $128.60 TP1 $156.55).

### 7.3 Migration

New `tests/test_dr_gate_migration.py`:

- Pre-migration DB has 100 rows without the three new columns. Run startup migration. All rows have `deep_research_override_gate == 'NA'`, `deep_research_hard_event IS NULL`, `deep_research_gate_failure_reason IS NULL`.
- Re-running migration is a no-op.
- New row inserted after migration → `gate_status` reflects actual evaluation (default `'NA'` until `_handle_completion` overwrites).

### 7.4 Manual verification

After steps 1+2 of intervention #2 land, monitor `data/parser_failures/` and the JSON-repair telemetry for one week. Confirm the line-21 failure mode disappears in production (target: zero `parse_json_repair_ok` hits in the `fund_manager` schema before step 3 ships).

After intervention #1 lands, run a smoke pass against a known recent drop (ADI from May 20) and inspect the resulting decision row to confirm `deep_research_override_gate = 'NOT_APPLICABLE'` (PM produced BUY_LIMIT, DR confirmed). Then run a backfill replay over the May 11–13 ONON case with the new DR schema; confirm the report renders the `ADVISORY_AVOID` advisory block and the verdict is preserved as BUY.

---

## 8. Rollout

Ship order (each as its own commit so we can bisect):

1. **Parser fix step 1** — instrumentation (raw-output dump + structured failure log). Passive; ships immediately. No flag.
2. **DB migration** — three new columns with `DEFAULT 'NA'`. Idempotent ALTERs in startup migration block. No behavior change.
3. **JSON repair shared service** — extract `_repair_json_using_flash` into `json_repair_service.py`, add `json-repair` dependency, telemetry. No behavior change for already-passing parses.
4. **Parser fix step 2** — prompt-side fix (specific change driven by what step 1's dumps revealed). Update the FM prompt.
5. **Parser fix step 3** — wire the new repair helper into the FM `_extract_json` call site. The previous three steps make this strictly additive.
6. **DR schema + gate** — add `hard_event` to the DR prompt and `_handle_completion` gate, gated by `DR_OVERRIDE_GATING_ENABLED` (default `true`). The flag is a one-line `if` around the demotion block — total cost ~30 minutes, total benefit is instant disable without git revert during the first two weeks of live observation.

### Feature flag rationale

The parser fix is pure improvement — code revert is fine if it goes wrong. The gating rule has real false-positive surface: a legitimate hard event that DR fails to structure-tag correctly gets demoted to ADVISORY, and a trade that would have saved alpha gets skipped. If that happens on a high-conviction UI-template call, it costs real money. `DR_OVERRIDE_GATING_ENABLED=false` set in the environment turns the rule off without a deploy.

### Watch the first two weeks

- Demotion rate among `OVERRIDDEN + PM BUY/BUY_LIMIT` calls. Expected ~70–80% based on the May 14–20 distribution. Below 30% means DR is gaming the field; above 95% means DR has stopped flagging anything useful.
- Distribution of `gate_failure_reason` codes. Hot codes (e.g., always `verification_xref_not_found`) are signals the prompt instruction is unclear and worth a one-line clarification.
- Manual review of the first ten `GATED_HARD_EVENT` rows — open every `source_url`, confirm it resolves and substantiates the cited event.

---

## 9. Post-deploy validation plan

The spec ships the policy; this section says how we know it worked.

### 9.1 Weekly scorecard query

Every Friday for four weeks post-deploy, re-pull the DR override scorecard with the same window logic the May 20 memo used (decision_points joined with decision_tracking for +7d / +14d performance), but split rows three ways using the new column:

```sql
SELECT
  deep_research_override_gate,
  COUNT(*) AS n_calls,
  AVG(perf_7d) AS avg_perf_7d,
  AVG(perf_14d) AS avg_perf_14d,
  SUM(CASE WHEN perf_7d > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS win_rate_7d
FROM decision_points dp
JOIN decision_tracking dt ON dp.id = dt.decision_id
WHERE dp.deep_research_review_verdict IN ('OVERRIDDEN', 'ADVISORY_AVOID')
  AND dp.date >= '2026-05-21'
GROUP BY deep_research_override_gate;
```

### 9.2 Success criteria

- **`GATED_HARD_EVENT` bucket:** net P&L stays positive. On a typical 5-call/week cadence, that means an average per-call contribution ≥ +1 pt to the saved-alpha column (≥ +5 pts/wk). The hard-event AVOID alpha was averaging ~+10 pts per call pre-policy (UI / POOL / DLTR / etc.); if the gate is doing its job, this bucket should look similar.
- **`ADVISORY_AVOID` bucket:** net P&L hovers near zero relative to PM verdict. The PM verdict prevails for these names so this is really measuring PM accuracy on names DR disagreed with on soft grounds — which from the memo data we expect to be mildly positive (the soft-rationale flips were where DR was destroying alpha; preserving PM should recover it).
- **`NOT_APPLICABLE` bucket:** baseline. No expected change vs. pre-policy.

### 9.3 Revert triggers

If **`GATED_HARD_EVENT` net P&L goes negative for two consecutive weeks**, flip `DR_OVERRIDE_GATING_ENABLED=false` and reopen the design. The gate is failing — either DR is fabricating fake hard events that pass our validators, or the type-specific thresholds are letting through events that don't actually predict downside.

If **`ADVISORY_AVOID` net P&L goes meaningfully negative** (i.e., PM verdict is wrong on these names and DR was actually right), that's a different signal — the gate is too strict and is suppressing real saves. Loosen the type-specific rules or widen the recency windows. Do not revert the rule; tune it.

---

## 10. Risks and mitigations

| Risk | Mitigation |
| ---- | ---------- |
| DR self-classifies soft rationale into a structured `hard_event` to pass the gate. | Two-condition gate: type-specific anti-patterns (§3.3) PLUS cross-reference to `verification_results`. Weekly manual review of first ten `GATED_HARD_EVENT` rows (§8). |
| Legitimate hard event mis-formatted by DR (e.g., UI's ITC case structured with old filing_date but missing material_development_date). | Two-field date model (§3.3). Feature flag for instant disable (§8). Replay fixture in §7.1 explicitly tests this case. |
| Sell-side whitelist is incomplete and a real downgrade gets demoted. | Whitelist lives in a single Python list (`dr_gate_whitelist.py`) editable in one commit. Hot `gate_failure_reason` of `named_entity_not_in_whitelist` is a clear signal to update. |
| Parser repair masks a real prompt regression. | Step 1 (instrumentation) and step 2 (prompt fix) ship **before** step 3 (repair library). Telemetry breaks out `parse_json_repair_ok` per schema so a non-trivial count after step 2 signals an unresolved regression. |
| `json-repair` library introduces a security or compat issue. | Pinned version; library is small, MIT-licensed, widely used. |
| ADVISORY_AVOID rendering confuses the human reviewer. | Render as yellow warning block clearly labeled above (not replacing) the PM verdict. Email uses `<PM verdict> [DR advisory]` suffix. Include `gate_failure_reason` in the warning for transparency. |
| Migration concurrency. | Migration runs on startup before background tasks spin up. Single connection. Idempotent ALTER with `DEFAULT 'NA'`. |

---

## 11. Out of scope

- Scorecard recomputation tooling. The new columns on the trade-report CSV enable the post-deploy validation loop directly.
- Tuning the soft/hard event taxonomy beyond what's in §3.3. Ships as defined; tuning is a follow-up after the first month of live data.
- Backtest harness integration. CLAUDE.md backlog #1 is unaffected.
- Changes to existing DR `UPGRADED` / `ADJUSTED` / `CONFIRMED` paths — they earn alpha and are untouched (except for adding `hard_event` to the schema for measurement only, per §2 asymmetric design).
- Expanding the gate to PM `BUY` → DR `WATCH` downgrades or PM `WATCH` → DR `AVOID` flips. Scope explicitly narrow at first (§3.2).

---

## 12. Open questions

None gating implementation. Implementation can begin once this spec is approved.
