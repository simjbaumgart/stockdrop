# Performance Analysis — Stats, R/R Correlation, Recovery, SPY Overlay

> Extends the focused HTML report with quantitative significance, continuous-variable
> correlation, recovery-pattern descriptive stats, and an S&P 500 benchmark overlay.

## Goals

A. **Significance tests** — Are the return-distribution differences between verdict groups (PM intent, DR verdict) statistically significant or just sample-size noise?

B. **R/R correlation** — Is there a continuous-variable relationship between the council's R/R rating and the realized 4-week return? Both PM and DR R/R, both Pearson and Spearman.

C. **Recovery time distribution** — For each verdict group, how many trading days until pre-drop level is reached, and what does the price do after recovery (5/10/20 days post-recovery)?

D. **S&P 500 baseline** — Overlay SPY's return path (over the same calendar windows as each cohort decision) on the time-series charts. Recompute "alpha" — group return minus same-window SPY return — to disentangle "the council picks winners" from "the market just rallied."

## Architecture

New shared module `app/services/analytics/stats.py` with pure functions:

- `pairwise_welch(df, group_col, value_col, min_n=5)` → DataFrame: every pair of groups, Welch t-test (unequal variance), Mann-Whitney U as a non-parametric backup, Cohen's d effect size, FDR-adjusted p-values (Benjamini-Hochberg) since we run many comparisons.
- `correlation(df, x_col, y_col)` → dict: Pearson r/p, Spearman rho/p, n. Add scatter coords for plotting.
- `recovery_stats(df, group_col)` → DataFrame: per-group n, % recovered, p25/p50/p75/p90 of `days_to_recover`, plus post-recovery returns at +5/+10/+20 days.

Extension to `app/services/analytics/outcomes.py`:

- Add post-recovery return columns to each enriched row: `post_recover_5d`, `post_recover_10d`, `post_recover_20d` — pct change from recovery-day close to N trading days later. NaN if not recovered or insufficient bars.

Extension to `app/services/analytics/payload.py`:

- New SPY path computation: for each cohort decision, fetch SPY OHLC from `decision_date` forward (use existing `price_cache.get_bars`), normalize to that date, build a per-day median across the cohort. Expose under `time_series.spy_overlay`.
- Compute and expose the **alpha** versions: each group's median minus SPY median, day-by-day. Lets the chart switch between absolute return and excess return.
- Add `stats.pairwise_intent`, `stats.pairwise_dr_verdict`, `stats.corr_pm_rr`, `stats.corr_dr_rr`, `stats.recovery_by_intent` to the payload.

HTML rewrite (`scripts/analysis/deep_dive_html.py`):

- New section **"Statistical significance"** — two pairwise tables (intent, DR verdict) showing each group pair, n1/n2, Welch p, Mann-Whitney p, Cohen's d, FDR-adjusted p, and a green/red badge for significant.
- New section **"R/R vs realized return"** — two scatter plots (PM R/R, DR R/R) with regression lines drawn in JS. Pearson + Spearman shown above each plot.
- New section **"Recovery patterns"** — table of recovery percentiles per intent + post-recovery returns. Plus a small histogram/box-plot view of `days_to_recover` per intent.
- **SPY overlay** — add a dashed gray line ("S&P 500") to the by-intent and by-DR-verdict time-series charts. Toggle button: "Show absolute return" / "Show alpha vs SPY".

## Data sources

- All from yfinance, cached via existing `app/services/analytics/price_cache.py` parquet store.
- SPY prefetched once across full cohort span. ~80 KB cache file.

## Tests

- `tests/test_analytics_stats.py` — synthetic frames covering pairwise_welch (clear-difference and no-difference cases), correlation (perfect/zero/negative), recovery_stats (mix of recovered and not).

## Out of scope

- Multi-factor regressions (sector × intent × R/R interactions) — flag if useful after seeing the descriptive stats.
- Survival analysis (Kaplan-Meier) on time-to-recovery — overkill at current n.
- Confidence-interval bands on time-series charts — already have q25/q75 in payload, can add later if useful.
