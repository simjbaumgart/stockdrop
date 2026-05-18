# 2026-05-18 Pipeline Post-Run Fixes — Design

## Context

Source: the 2026-05-18 session review. The FM-fail → `PASS_INSUFFICIENT_DATA`
fix landed and works (MSTR JSON-parse failure produced an honest verdict instead
of phantom AVOID/LOW). This spec covers the remaining prioritized issues from
that review, on branch `fix/pipeline-postrun`.

Priority order (from the review, preserved here):

1. Stop-guard sanity floor — display sanitization + distribution logging
2. Drive: disable upload now (OAuth migration deferred)
3. DefeatBeta name normalizer — word-boundary match
4. Column-width fixes for the trade report
5. Threading shutdown — Ctrl+C hang
6. TEST data cleanup
7. FM-fail downstream verification (data hygiene, not a live-path bug)

## Problem statements (root causes confirmed in code)

### 1. Stop-guard — numbers not sanitized

`app/utils/stop_loss_guard.py` already defines `MAX_ACCEPTABLE_DOWNSIDE_PCT =
15.0` and `evaluate_stop_acceptability()` (commit `1397815`, 2026-05-14), wired
into `app/services/research_service.py` (~lines 549-596). The gap:

- When downside > 15% it forces `action=AVOID` but leaves `stop_loss`,
  `downside_risk_percent`, and `risk_reward_ratio` untouched. The trade report,
  console panel, and DB row still show e.g. AAOI `Stop $59.93 / R/R 0.2x`
  (entry $137–$140, `widened_to_sma_200`, −56.3% downside).
- The override is gated on `action.upper().startswith("BUY")`. If the PM already
  produced AVOID/WAIT for another reason, the absurd stop is printed unsanitized.
- No per-ticker distribution logging exists, so the cap can't be tuned against
  real data.

Frequency root cause: `widen_stop_if_too_tight` picks `min(atr_floor,
sma_floor)` — the farther/lower of the two floors (`stop_loss_guard.py:73`). On
deep single-day drops `widened_to_sma_200` lands far below entry, producing very
large downside %. Observed this run: VICR, CRDO, APLD, AXTI, MP, AU, ALAB, and
the worst case AAOI.

### 2. Drive quota

`app/services/drive_service.py` authenticates service-account-only
(`SERVICE_ACCOUNT_FILE = 'service_account.json'`, lines 60-71). Service accounts
have ~0 Drive quota: `list` works, `create` fails with `storageQuotaExceeded`.
The circuit breaker works (trips after 3 failures, 24h cooldown). The
`DRIVE_UPLOAD_ENABLED=false` env path already exists (lines 31-34) but is not set
in deploy config. No OAuth personal-credentials path exists.

### 3. DefeatBeta normalizer

`app/services/stock_service.py:1353` matches with naive substring containment:
`expected_lower in head or first_token in head`. `"arco" in head` matches
"marco"; short tokens collide with common words. The suffix-stripping normalizer
(lines 1322-1349) is fine; only the final match test is too loose.

### 4. Trade-report truncation

`scripts/core/generate_trade_report.py:339-340` hard-slices `conviction[:4]` and
`drop_type[:14]` *before* the dynamic column-width block (lines 373-377) that
already sizes columns to content. Result: `MODERATE`→`MODE`,
`COMPANY_SPECIFIC`→`COMPANY_SPECIF`.

### 5. Threading shutdown hang

`app/services/research_service.py:284` (Phase 1, max_workers=8) and `:719`
(Phase 2, max_workers=6) use `with concurrent.futures.ThreadPoolExecutor(...)`.
The context manager's `__exit__` calls `shutdown(wait=True)`, so Ctrl+C
mid-scan blocks on in-flight agent calls. No `cancel_futures`, no
`thread_name_prefix`, no `KeyboardInterrupt` handling on the scan path.
`app/services/tradingview_service.py:45,136,494` use the same pattern.

### 6. TEST ticker / SHOP duplicate

No literal-`TEST` guard exists in the screener. Dedup
(`app/services/stock_service.py:415-427`) is by symbol only and keeps the
larger-drop entry. SHOP duplication implies it is entering via a path that runs
before that dedup, or a second code path; needs investigation.

### 7. FM-fail downstream pollution

The batch-candidate query filters `recommendation LIKE '%BUY%'`
(`app/database.py:556`), so phantom AVOID rows don't leak there. But
`scripts/analysis/combined_signal_analysis.py` and
`scripts/analysis/evaluate_decisions.py` read `decision_points` broadly with no
filter excluding pre-fix phantom AVOID/LOW rows (FM JSON-parse failures before
commit `0bbed4f`). Historical correlation/accuracy stats are polluted until
those rows are excluded or backfilled.

## Design

### Component A — Stop-guard display sanitization (Priority 1)

Decision: **flag + suppress** (not ATR-fallback). These rows are AVOID anyway;
a tradable-looking stop on an untradable row is more misleading, not less.

`app/utils/stop_loss_guard.py`:

- Extend `StopLossAdjustment` with `pm_stop`, `atr_floor`, `sma_floor` so the
  caller can log the distribution.
- Add a helper that, given the unacceptable (entry_low, stop) pair, returns the
  sanitized field set (or fold this into the caller — implementation detail for
  the plan).

`app/services/research_service.py` (~lines 549-596):

- After widen + `recompute_risk_metrics`, evaluate acceptability. If not
  acceptable, **regardless of `action`**:
  - `final_decision["stop_unreliable"] = True`
  - `final_decision["stop_loss_raw"] = <widened value>` (audit-only)
  - `final_decision["stop_loss"] = None`
  - `final_decision["downside_risk_percent"] = None`
  - `final_decision["risk_reward_ratio"] = None`
  - keep the existing force to AVOID / NONE and the `[STOP-REJECTED]` reason
    prefix, but remove the `startswith("BUY")` gate.
- Emit one INFO log line per widen:
  `[stop-dist] {ticker} pm={pm_stop} atr_floor={atr_floor} sma_floor={sma_floor} chosen={chosen} downside={pct}%`

Display / DB propagation:

- Console panel (`research_service.py:619-626`) and `format_rr_block`: when
  `stop_unreliable`, render stop as `N/A — stop unreliable` and suppress the R/R
  block.
- `scripts/core/generate_trade_report.py`: same — blank/`N/A` stop, no R/R for
  flagged rows.
- `app/database.py`: add columns `stop_unreliable` (int/bool) and
  `stop_loss_raw` (real), following the existing additive-migration pattern for
  the 40+ column `decision_points` table. Persist `stop_loss` as NULL when
  flagged.

### Component B — Drive disable

Set `DRIVE_UPLOAD_ENABLED=false` in `render.yaml` (env block). No code change.
The runtime path already honors it. OAuth personal-credentials migration is
**out of scope** here and documented as a deferred follow-up.

### Component C — DefeatBeta word-boundary match

`app/services/stock_service.py:1351-1353`: replace substring containment with
word-boundary regex —
`re.search(r"\b" + re.escape(term) + r"\b", head)` — for both the full
normalized name and the first token. Keep the `len(first_token) >= 3` guard and
the `< 3 chars → reject` rule.

### Component D — Trade-report column widths

`scripts/core/generate_trade_report.py:339-340`: remove the `[:4]` and `[:14]`
slices. The dynamic-width block (lines 373-377) already sizes columns to the
widest value, so full `MODERATE` / `COMPANY_SPECIFIC` render correctly.

### Component E — Threading shutdown

`app/services/research_service.py:284,719`: replace `with
ThreadPoolExecutor(...)` with explicit construction, passing
`thread_name_prefix="phase1"/"phase2"`, wrapped in `try/finally` that calls
`executor.shutdown(wait=False, cancel_futures=True)`. Catch `KeyboardInterrupt`
on the scan loop and break cleanly so a single Ctrl+C exits. Apply the same
pattern to `app/services/tradingview_service.py:45,136,494` if they sit on the
Ctrl+C path (confirm during implementation).

### Component F — TEST ticker guard + cleanup

- Screener: before the dedup at `stock_service.py:415`, skip candidates whose
  symbol is in a small denylist (`{"TEST"}`).
- Add `scripts/core/cleanup_test_rows.py`: deletes `decision_points` rows where
  `ticker='TEST'`; defaults to `--dry-run` (prints count, no delete);
  `--apply` performs the delete. Safe for the live tool.
- Investigate the SHOP-duplicate code path; document the root cause. Fix only if
  the root cause is a clear screener bug; otherwise record it as a follow-up.

### Component G — FM-fail downstream verification

- Add a verification query (standalone script under `scripts/analysis/`, or a
  notebook cell) counting pre-fix phantom rows: `recommendation='AVOID' AND
  conviction='LOW'` whose agent-report columns are null/empty and whose
  timestamp predates commit `0bbed4f`.
- Add an exclusion filter to `combined_signal_analysis.py` and
  `evaluate_decisions.py`: exclude `PASS_INSUFFICIENT_DATA` and the phantom
  heuristic from signal correlation.
- Decision point (resolve once the count is known, recorded in the plan):
  filter-only vs one-time backfill of phantom rows to `PASS_INSUFFICIENT_DATA`.
  Backfill, if chosen, is an additive UPDATE behind a `--dry-run` script.

## Testing

- `tests/test_stop_loss_guard.py` (extend): unreliable-stop sanitization returns
  `stop_unreliable=True`, NULL stop, NULL R/R; distribution fields populated.
- `tests/test_research_service_stop_guard_recompute.py` (extend): AAOI-shaped
  input → AVOID with suppressed R/R regardless of original action.
- New: DefeatBeta word-boundary cases — `"arco"` must not match "marco";
  legitimate "MP Materials" still matches; first-token < 3 chars rejected.
- New: trade-report rendering — full `MODERATE` / `COMPANY_SPECIFIC` not
  truncated; flagged rows show `N/A` stop and no R/R.
- New: executor shutdown — `cancel_futures=True` asserted; KeyboardInterrupt
  breaks the scan loop without hang.
- New: screener skips `TEST`; cleanup script dry-run reports count without
  deleting.
- New: analytics exclusion filter removes `PASS_INSUFFICIENT_DATA` + phantom
  rows from the correlation dataset.
- Integration: replay an AAOI-shaped record end-to-end; assert DB row has
  `stop_unreliable=1`, `stop_loss IS NULL`, `risk_reward_ratio IS NULL`.

## Out of scope (documented deferrals)

- OAuth personal-credentials migration for Drive (separate task; headless
  refresh-token flow on Render).
- NLTK_DATA persistent path (`NLTK Security Violation` on /tmp wipe).
- Mid-cycle trade-report regeneration.
- Agent-quota `rolling_24h` session-scoping (resets between runs).
- DefeatBeta suffix-list expansion beyond the word-boundary fix.

These are the review's "persistent, unchanged" items; they are lower priority
and explicitly not addressed in this plan.
