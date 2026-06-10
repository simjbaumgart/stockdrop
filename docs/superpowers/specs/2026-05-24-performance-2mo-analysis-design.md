# Performance 2-Month Analysis — Design

**Date:** 2026-05-24
**Status:** Approved (plan B)
**Deliverable:** `notebooks/performance_2mo.ipynb`

## Goal

Extract the most and best insights from the last 60 days of StockDrop recommendations. Pure analysis, no changes to live evaluation code. The notebook is the artifact; it should be re-runnable on any future date by changing one constant.

## Scope

**In scope:**
- Decisions where `decision_points.date >= today − 60d` (since 2026-03-24)
- All `decision_tracking` rows for those decision_ids
- yfinance batch fetch for involved tickers + SPY benchmark
- Statistical analysis + visualization in a Jupyter notebook

**Out of scope:**
- Changes to `performance_service.py`, `tracking_service.py`, or the `/performance` dashboard
- Backfilling missing historical decisions
- Cron/automation — this is a one-shot analytical artifact (we may harden later)

## Data preparation

A single cell at the top of the notebook produces one tidy DataFrame, `decisions_df`, used by every downstream section.

**Steps:**
1. Connect to `subscribers.db`, pull `decision_points` where date >= cutoff.
2. Pull `decision_tracking` for those `decision_id`s.
3. Batch yfinance: `yf.download(tickers + ["SPY"], start=cutoff−5d, end=today+1d)` to cover entry-day prices and benchmark.
4. Per decision compute:
   - Returns at horizons: d1, d3, d7, d14, d30, current
   - SPY-relative alpha at each horizon (decision return − SPY return over same window)
   - MFE (max favorable excursion), MAE (max adverse excursion), max drawdown — from `decision_tracking` snapshots where available, else fill from yfinance daily closes
5. Tag each row with: `intent` via `normalize_to_intent`, DR verdict, DR conviction, gatekeeper Bollinger %B, drop magnitude on decision day.
6. Cache the prepared DataFrame to a pickle for re-runs.

## Notebook sections

| # | Section | Outputs |
|---|---|---|
| 0 | Sample overview | N, intent breakdown, tracking-coverage histogram, decision-date density |
| 1 | Headline performance per intent | Mean/median/std/win-rate per intent at d7/d30/current, SPY-relative alpha, bootstrap 95% CIs |
| 2 | Statistical tests | Mann-Whitney U (BUY vs AVOID), one-sample sign test (BUY > 0), test on alpha > 0, KS test on distributions, Cohen's d with CI |
| 3 | Return distributions | Violin + strip plot per intent at multiple horizons |
| 4 | Time evolution | Mean-return-vs-days-since-decision per intent with CI band; equal-weight BUY-portfolio cumulative curve vs SPY |
| 5 | Deep Research signal | DR conviction ↔ realized return (Spearman); PM-vs-DR override resolution; entry-zone hit rate |
| 6 | Subgroup breakdowns | Bollinger %B tier × outcome; drop magnitude × outcome |
| 7 | Calibration | Reliability diagram by conviction tier; Brier score for BUY-vs-AVOID classifier |
| 8 | Risk metrics | MFE/MAE per intent; Sharpe-like ratio; drawdown distribution |
| 9 | Takeaways | Plain-language summary of what the 60-day window actually says |

## Statistical choices (deliberate)

- **Non-parametric tests** (Mann-Whitney, sign test, KS) over t-tests. Returns are skewed; n is small.
- **Bootstrap CIs** over parametric. Same reason.
- **Effect sizes alongside p-values.** With this sample size, p-values are weak signal; Cohen's d + CIs communicate uncertainty honestly.
- **No multiple-comparison correction by default.** Flag in takeaways if subgroup tests proliferate.
- **Robust to small n.** Every cell handles `n < 5` per group gracefully (returns "insufficient sample" rather than erroring).

## Data quality / failure modes

Confirmed by inspection of `subscribers.db` on 2026-05-24:

- **N=560 decisions in window** (BUY=69, BUY_LIMIT=65, AVOID=288, WATCH=83, PASS_INSUFFICIENT_DATA=55).
- **`decision_tracking` is empty for this window (0 rows).** All price history comes from yfinance. MFE/MAE computed from daily OHLC highs/lows over each decision's holding period, not from tracking snapshots.
- **`sector` populated for only 8/560 → dropped from section 6.** Section 6 uses `drop_type` (530/560) and `gatekeeper_tier` (337/560) instead.
- **`deep_research_conviction` populated for 89/560.** Section 5 operates on this subset and reports n in the header.
- **`deep_research_action` populated for 89/560.** PM-vs-DR override analysis uses this subset.
- **Ticker not on yfinance:** drop with a warning rather than crash.
- **Future horizon not yet realized:** d30 horizon for a decision made 10 days ago is `NaN`, not zero. Time-evolution chart truncates accordingly.

## Libraries

pandas, numpy, scipy.stats, matplotlib, seaborn, yfinance. All already in `requirements.txt`. No new deps.

## Acceptance

- Notebook runs end-to-end without errors against current `subscribers.db`.
- Every section either produces output or reports "insufficient sample" — no silent skips.
- Section 9 contains 3–7 concrete observations grounded in the numbers above, not hedged generalities.
