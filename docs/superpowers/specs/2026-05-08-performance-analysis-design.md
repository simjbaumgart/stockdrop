# Performance Analysis — Design Spec

**Date:** 2026-05-08
**Status:** Approved (auto mode)

## Goal

Build a comprehensive view of the StockDrop tool's recommendation track record, in two phases:

1. **Phase 1 — Diagnostic deep-dive.** A re-runnable analysis that answers eight specific questions and produces a static markdown report with embedded charts.
2. **Phase 2 — Persistent dashboard.** Once Phase 1 surfaces the most useful views, bake them into a new `/insights` web page plus a small scoreboard tile on the existing dashboard.

Both phases share the same `app/services/analytics/` module so the report and the dashboard can never disagree.

## The eight questions (Phase 1 deliverable)

1. **Verdict accuracy** — Win-rate and avg ROI per PM verdict (BUY / BUY_LIMIT / WAIT / PASS) at horizons {1w, 2w, 4w, 8w}.
2. **Deep Research override value** — When DR overrides PM, does the override beat the original? Quantify lift/harm.
3. **Per-agent signal strength** — Phase-1 sensors (Technical, News, Sentiment, Competitive, Seeking Alpha) and Phase-2 debaters (Bull/Bear/Risk): which correlate with outcome?
4. **Gatekeeper calibration** — Are Bollinger %B < 0.50 and SPY/SMA200 thresholds optimal? What lies just above/below the cut?
5. **Regime conditioning** — Performance by SPY regime (bull/bear/chop) and by sector.
6. **BUY_LIMIT execution** — Of BUY_LIMITs, what fraction filled within their entry range? Performance of filled vs unfilled.
7. **Drop-size sweet spot** — Best-performing drop-% bucket (-5 to -8, -8 to -15, <-15).
8. **Time-to-recovery distribution** — How long do winners take to recover? Informs the right horizon for the dashboard scoreboard.

## Cohort & data scope

- **Headline cohort:** decisions with `timestamp >= 2026-02-01`. Stable post-DR-rollout window.
- **Sensitivity appendix:** same metrics computed against the full DB history.
- **Price data:** yfinance OHLC backfilled for each `(ticker, decision_date)` and cached locally so re-runs don't re-hit the API.

## Architecture

```
app/services/analytics/
  __init__.py
  price_cache.py      # yfinance backfill + parquet cache at data/price_cache/<TICKER>.parquet
  cohort.py           # load decision_points, filter to cohort, normalize columns
  outcomes.py         # compute return at horizons, max ROI, max drawdown, time-to-recovery
  aggregations.py     # slice by verdict/agent/drop bucket/regime; win-rate + avg ROI tables
  charts.py           # matplotlib PNG renderers (one function per chart)
  report.py           # markdown rendering, stitches tables + charts together

scripts/analysis/deep_dive_report.py   # orchestrator: runs everything, writes the report
docs/performance/YYYY-MM-DD-deep-dive.md  # output

tests/test_analytics_outcomes.py
tests/test_analytics_cohort.py
tests/test_analytics_aggregations.py
```

**Key principle:** every aggregation/chart used in the report must be importable as a function from `app/services/analytics/`. Phase 2 routes call those same functions.

## Outcome metrics

For each decision, given the cached daily OHLC bars from `decision_date` forward, compute:

- `return_1w` / `return_2w` / `return_4w` / `return_8w` — close-on-decision-date to close-N-trading-days-later.
  - For decisions with insufficient bars (e.g. recent), the column is `NaN`. Aggregations exclude NaN.
- `max_roi_4w` / `max_roi_8w` — peak high in the window vs decision price.
- `max_drawdown_4w` — worst low in the window vs decision price.
- `recovered` — boolean: did the price reach the pre-drop level within the window?
- `days_to_recover` — trading days until pre-drop level reached, or NaN if not recovered.
- `intent` — reuse `performance_service.normalize_to_intent(recommendation)`.
- `dr_action` — value of `deep_research_action` column (override / confirm / etc.).

## BUY_LIMIT fill simulation

A BUY_LIMIT fills if any low in the 4-week window from the decision touches the entry range `[entry_price_low, entry_price_high]`. Filled rows then use the entry midpoint as the cost basis for return computation. Unfilled rows are tracked separately.

## Aggregation primitives

Each takes the enriched cohort DataFrame and returns a small DataFrame:

- `winrate_by(df, group_col, horizon)` — count, win-rate, avg ROI, median ROI, std.
- `winrate_by_bucket(df, value_col, bins, horizon)` — same but for continuous columns (drop_percent, ai_score).
- `equity_curve(df, horizon)` — cumulative ROI assuming equal-weight allocation per BUY recommendation, indexed by decision date.
- `time_to_recover_dist(df)` — histogram values.

## Phase 2 (designed, deferred)

After Phase 1, build:

- `app/routers/insights.py` and `templates/insights.html` consuming the same analytics functions.
- Scoreboard tile component on `dashboard.html`: open-position P&L, 30-day win-rate, headline DR-override accuracy.
- Background refresh every 60 minutes (extend the existing trade-report cron).

Phase 2 plan is written separately after Phase 1 lands and we know which views are actually informative.

## Testing

- Unit tests on `outcomes.py` and `aggregations.py` with synthetic price frames so we know the math is right (this is the only thing that has to be correct).
- `cohort.py` unit-tested with a tiny in-memory SQLite copy.
- `price_cache.py` exercised in an integration test that hits yfinance for a single ticker.
- `charts.py` and `report.py` tested only by running the orchestrator end-to-end and eyeballing the output.

## Out of scope

- Backtesting "what if we'd run a different gatekeeper threshold" — the deep-dive surfaces the data; the harness is a separate project (already in CLAUDE.md backlog).
- Live position tracking via broker API — Phase 2 can use existing `decision_tracking` table.
- Risk-adjusted metrics beyond max drawdown (Sharpe, Sortino) — add to Phase 2 if useful.
