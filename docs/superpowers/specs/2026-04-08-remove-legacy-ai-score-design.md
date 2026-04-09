# Remove Legacy `ai_score` Field

## Problem

Every stock receives a score of 50/100. The `ai_score` field in `research_service.py` defaults to 50 when the LLM response lacks a `score` key. Since the PM prompt no longer asks for a numeric score, this fallback fires every time. The code itself acknowledges this: line 277 comments "ai_score has no predictive signal" and line 312 labels it "Legacy -- no longer in prompt, kept for backward compat."

Displaying a meaningless 50/100 undermines trust in the prioritization queue.

## Scope

Remove the legacy `ai_score` from the entire write/display pipeline. The DB column stays so historical data is preserved and notebooks that query old data still work.

**In scope:** Stop producing, passing, storing, and displaying `ai_score` for new analyses.
**Out of scope:** The `deep_research_score` (computed, differentiated), `priority_score` (internal queue ordering), and `sentiment_score` (internal analyst metric) are unaffected.

## Changes

### 1. research_service.py

- **Line 312**: Remove `"score": final_decision.get("score", 50)` from the return dict.
- **Line 1454**: Remove `**Score:** {state.final_decision.get('score')}/100` from the investment memo template in `_format_full_report()`.

### 2. stock_service.py

- **Line 1577**: Remove `score = report_data.get("score", "N/A")`.
- **Line 1596**: Change `print(f"*** DECISION FOR {symbol}: {recommendation} (Score: {score}/100) ***")` to `print(f"*** DECISION FOR {symbol}: {recommendation} ***")`.
- **Line 1631**: Remove `ai_score=float(score) ...` from the `update_decision_point()` call.
- **Line 1693**: Remove `"ai_score"` from the result dict.

### 3. database.py

- **Lines 255-257**: Remove the `ai_score` branch from `update_decision_point()`. Keep the `ai_score` parameter in the function signature but ignore it (or remove it and update callers).
- **Line 228**: Remove `ai_score` from `add_decision_point()` signature and INSERT statement (lines 234-236).
- DB column `ai_score REAL` stays in schema and migrations -- historical data preserved.

### 4. scripts/generate_report_v2.py

- **Line 20**: Remove `ai_score` from the SELECT query.
- **Lines 119-124**: Replace `ai_score`-based ranking in `rank_row()` with `deep_research_score` (or just rely on `has_deep` presence).
- **Line 234**: Remove `"Score"` column from the output table row.

### 5. scripts/core/generate_trade_report.py

- **Line 177**: Remove `score = d.get('ai_score')` and any downstream usage of that variable.

### 6. Tests

- **tests/test_v09_changes.py**: 
  - Line 167: Remove `ai_score=42.0` from `update_decision_point` call.
  - Line 171: Remove `ai_score` assertion.
  - Line 222: Remove `ai_score=30.0` from test setup.
  - Line 415: Remove `ai_score=None` from test setup.

### 7. Verification script

- **scripts/verification/verify_score.py**: Delete this file entirely -- it exists solely to test the `ai_score` pipeline.

### 8. Notebooks (no changes)

Notebooks query historical data from the DB. Since the `ai_score` column remains, they continue to work. Old rows keep their stored values; new rows will have `ai_score = NULL`.

### 9. Templates (no changes needed)

`dashboard.html` and `decisions.html` display `dp.recommendation` and `dp.deep_research_score`, not `ai_score`. No template changes required.

## Migration / Rollback

- **Forward**: New analyses write `NULL` to `ai_score`. No schema migration needed.
- **Rollback**: Revert the commit. The column still exists, so re-enabling the code restores behavior.

## Risks

- **Low**: Some notebook visualizations (e.g., `sns.scatterplot(x='ai_score', ...)` in Performance_Analysis.ipynb) will show NULLs for new data. This is acceptable -- those plots were already misleading with all-50 values.
- **Low**: `scripts/archive/` files reference `ai_score` but are archived/unused.
