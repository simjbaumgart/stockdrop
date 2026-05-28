# Three Operational Fixes: Stale PENDING, DefeatBeta Ticker Fallback, Screener Tie-Break

**Date:** 2026-05-28
**Status:** Approved for implementation
**Scope:** Three small, independent bug fixes in the screener / trade report / transcript-matching layers.

## Problem Statement

Three recurring issues observed in daily operation on 2026-05-27 and 2026-05-28:

1. **Stale PENDING rows persist in the trade report.** A Ctrl-C'd run on 2026-05-27 left a `PODD` row with `recommendation = 'PENDING'`. The 60-minute trade report task re-emits the row every cycle. Two days running now.
2. **DefeatBeta company-name mismatch on tickers with legal-form/abbreviation variants.** `CM` (Canadian Imperial Bank of Commerce / CIBC) fails the company-match check, forcing an unnecessary Alpha Vantage fallback. Same class of bug previously hit `TIGO`, `BP`, `AZO`.
3. **Screener queue ties resolve alphabetically.** When `get_priority_score` ties (always 100 for all US-open stocks), the stable two-stage sort leaves alphabetical order intact. Stocks with bigger drops can miss the cycle's processing window because they come late in the alphabet. Observed today: `BURL, CM, FLY, IDCBF, MLI` all tied at 100 while `P` (with a -18% drop) was processed only because it appeared early enough.

## Fix 1 — Stale PENDING Rows

### Current behavior

- `scripts/core/generate_trade_report.py:22-33` runs `SELECT * FROM decision_points ORDER BY timestamp DESC` with no filtering.
- A row written with `recommendation = 'PENDING'` (in-flight state) persists forever if the run that owned it was Ctrl-C'd or crashed before the PM verdict was written back.

### Design

Two-layer fix: cleanup + filter.

**Layer 1 — Stale sweep.** Add a function in `app/services/tracking_service.py` (which already owns the decision_points lifecycle):

```python
def sweep_stale_pending(stale_age_hours: int = 2) -> int:
    """Mark PENDING decision_points rows older than stale_age_hours as INCOMPLETE.
    Returns number of rows affected. Preserves the audit trail."""
```

Trigger this sweep in two places:
- Once at FastAPI startup in `main.py` (catches whatever Ctrl-C left behind across restarts).
- At the top of every periodic trade-report generation cycle (catches mid-day Ctrl-C without waiting for the next restart).

Threshold rationale: 2 hours comfortably exceeds the longest agent pipeline cycle (Phase 1 + Phase 2 + PM, parallel, typically under 20 minutes for a single ticker). 2 hours is short enough that today's in-flight rows are untouched, long enough that abandoned ones get cleaned up promptly.

**Layer 2 — Report filter.** Update the SQL in `generate_trade_report.py`:

```sql
SELECT * FROM decision_points
WHERE recommendation NOT IN ('PENDING', 'INCOMPLETE')
ORDER BY timestamp DESC
```

This is a belt-and-suspenders fix: even if the sweep hasn't fired yet, the report won't show abandoned rows. It also keeps INCOMPLETE rows in the DB for forensic value (auditable: "we attempted PODD on 2026-05-27 and abandoned").

### Tests

`tests/test_stale_pending_sweep.py` (new):
- Insert a PENDING row with timestamp 3 hours ago → `sweep_stale_pending(2)` marks it INCOMPLETE.
- Insert a PENDING row with timestamp 30 minutes ago → unchanged.
- After sweep, run `get_decision_points` (extended with the new filter) → INCOMPLETE row excluded.

### Risk

Negligible. Renaming a sentinel value in 1-N rows; the value is only ever read by display code. No schema change.

## Fix 2 — DefeatBeta Ticker-Symbol Fallback

### Current behavior

`StockService._transcript_matches_company` (stock_service.py:1314-1387) lowercases the first 1500 chars of the transcript and the expected company name, strips trailing parentheticals and a curated suffix list, then checks that either the stripped full name or its first significant token (≥ 3 chars) appears on a word boundary.

This is appropriately strict and correctly rejects the prior `L → Loblaw` false-positive class. But it rejects valid transcripts when:
- Legal-form variants differ ("Bank of Commerce" vs "Commerce Bank")
- Abbreviation-heavy transcripts open with "CIBC", "BP plc", "AutoZone" without the full registered name in the head

### Design

Add a **ticker-symbol contextual fallback** to `_transcript_matches_company`. After the existing checks fail and before returning `False`, try one more pattern: does the ticker symbol appear next to an exchange or "ticker"/"symbol" qualifier in the head?

Implementation sketch:

```python
@staticmethod
def _transcript_matches_company(
    transcript_text: str,
    expected_company: str,
    symbol: str = "",
) -> bool:
    ...
    # existing logic unchanged: returns True if matched
    ...

    # New fallback: exchange-qualified ticker mention.
    # Matches "(NYSE: CM)", "(NASDAQ:AAPL)", "Ticker: BP",
    # "Symbol: AZO", "TSX: CM". Case-insensitive, ticker
    # comparison is exact-word.
    if symbol:
        sym_re = re.escape(symbol.upper())
        pattern = (
            rf"(?:nyse|nasdaq|tsx|amex|lse|otc|nyseamerican)\s*:\s*{sym_re}\b"
            rf"|\b(?:ticker|symbol)\s*[:#]?\s*{sym_re}\b"
        )
        if re.search(pattern, transcript_text[:1500], re.IGNORECASE):
            return True

    return False
```

Caller (`get_latest_transcript`, stock_service.py:1431) passes `symbol` through:

```python
if db_text and not self._transcript_matches_company(db_text, company_name, symbol):
    ...
```

### Why this is safe

- The patterns require an exchange prefix or a `Ticker:`/`Symbol:` label. These tokens don't appear randomly in unrelated text.
- A 2-3 letter ticker like `CM` will not false-match because the regex requires the exchange prefix as context, not bare `CM`.
- The existing word-boundary `\b{symbol}\b` check (which we are NOT adding) would be risky for short symbols; we're not doing that.

### Tests

Extend `tests/test_transcript_matches_company.py` (or the file currently exercising this function — verify on implementation):
- New positive: `CM` + "Canadian Imperial Bank of Commerce" + transcript head "...Welcome to the CIBC Q2 earnings call (TSX: CM)..." → True.
- New positive: `BP` + "BP plc" + transcript head with "(NYSE: BP)" → True.
- New negative: `CM` + "Canadian Imperial Bank of Commerce" + transcript head with bare "CM" and no exchange prefix → False (still rejects).
- Regression: existing `L → Loblaw` and `MP Materials Corp.` cases continue to pass.

### Risk

Low. The fallback is additive — it can only flip rejections into acceptances when the exchange-prefix pattern is present, which is a high-confidence signal.

## Fix 3 — Screener Compound-Key Sort

### Current behavior

`stock_service.py:444-448`:

```python
large_cap_movers.sort(key=lambda x: x["symbol"])              # alpha
large_cap_movers.sort(key=get_priority_score, reverse=True)   # then by score
```

Two stable sorts. When `get_priority_score` returns the same value for all US-open stocks (constant 100), the first sort's alphabetical order survives unchanged.

### Design

Single compound-key sort:

```python
large_cap_movers.sort(
    key=lambda x: (
        -get_priority_score(x),    # priority score DESC
        x["change_percent"],       # drop magnitude DESC (change_percent is negative for drops)
        x["symbol"],               # final deterministic tiebreak ASC
    )
)
```

Replace lines 444-448 with this one sort. Add a short comment explaining the tiebreak order.

### Why drop_percent as second key

The user's stated intent: stocks with bigger drops should be processed first within a priority bucket, so the cycle doesn't run out of budget on alphabetically-early small-drop names before reaching late-alphabet large-drop names.

Using `change_percent` directly (it is negative for drops) sorts ascending, which puts the most negative — biggest drop — first. No `abs()` call needed.

### Tests

`tests/test_screener_sort_order.py` (new, or extend existing screener test):
- Build a list of 4 stocks: all priority_score 100, change_percent of -3, -18, -7, -3. Sort with the new key. Assert order: -18 first, -7 second, then -3s alphabetically by symbol.
- Edge case: empty list, single element.
- Edge case: mixed priority scores (60 and 100) — verify priority score still wins over drop size across buckets.

### Risk

Negligible. Pure ordering change in a list that's then iterated for processing. No external side effects.

## Out of Scope

- Re-thinking `get_priority_score` itself (currently constant for US-open stocks; user mentioned this is a known limitation but not in scope for this fix).
- Maintaining an alias dictionary for ticker-to-name mapping. The exchange-prefix fallback handles the observed cases without per-ticker maintenance.
- Schema migration for a richer PENDING/INCOMPLETE state machine. Single-value sentinel is sufficient for current needs.

## Rollout

All three changes are local, low-risk, and independently testable. No feature flag needed. Land as one PR with three commits (one per fix) or three small PRs — implementer's choice.
