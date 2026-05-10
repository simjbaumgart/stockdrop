# StockDrop Strategy Analysis — Playbook

> A re-runnable pipeline for evaluating the council's recommendations as a
> trading strategy: filter, simulate, optimize, compare exit rules.
>
> **Use this when more decisions have aged into the 1-week / 2-week / 4-week
> windows and you want to see whether the early findings hold up.**

## Quick start

```bash
# 1. Refresh the package (HTML report + every aggregation CSV/JSON + 22 PNGs)
./venv/bin/python scripts/analysis/build_package.py

# 2. Re-run the portfolio simulator with SPY benchmark, R/R cutoff sweep,
#    investment-size sweep, per-verdict breakdown, and drop-one-verdict.
./venv/bin/python scripts/analysis/portfolio_sim.py

# 3. Optimize the take-profit / stop-loss combination.
./venv/bin/python scripts/analysis/tp_sl_optimizer.py

# 4. Compare exit strategies (trailing, breakeven, time-decay, multi-tier TP).
./venv/bin/python scripts/analysis/exit_strategy_comparison.py
```

All four scripts share the same cohort loader and yfinance cache. They will
silently expand as more decisions age into the chosen horizon — no code
changes needed.

## Pipeline overview

```
                ┌────────────────────────────────────┐
                │  app/services/analytics/cohort.py  │   loads decision_points
                │  (filters TEST / underscore /       │   from subscribers.db,
                │   PBMRF; intent normalization)      │   normalizes verdicts
                └────────────────┬───────────────────┘
                                 │
                                 ▼
                ┌────────────────────────────────────┐
                │  app/services/analytics/            │   yfinance OHLC bars
                │  price_cache.py (parquet cache)     │   under data/price_cache/
                └────────────────┬───────────────────┘
                                 │
                                 ▼
                ┌────────────────────────────────────┐
                │  app/services/analytics/payload.py  │   compute_dataset()
                │  • outcomes (return_1w/2w/4w)       │   returns enriched df,
                │  • aggregations (winrate_by_*)       │   bars, spy_bars,
                │  • stats (pairwise, correlation)    │   payload dict
                │  • intervals (CIs / SEs)            │
                │  • time_series_by_group              │
                │  • SPY overlay                      │
                └────────────────┬───────────────────┘
                                 │
              ┌─────────┬────────┼─────────┬──────────────────┐
              ▼         ▼        ▼         ▼                  ▼
         build_     portfolio_  tp_sl_   exit_strategy_   deep_dive_
         package.py sim.py      opt.py   comparison.py    html.py
              │                                                │
              ▼                                                ▼
       docs/performance/                            docs/performance/
       <date>-package/                              <date>-deep-dive.html
         REPORT.md
         charts/*.png
         data/*.csv|.json
```

## What each script answers

### `build_package.py` — the headline report

Generates a complete analysis package under `docs/performance/<date>-package/`:

- `REPORT.md` — 240+ line written analysis with computed findings (10 sections covering verdict performance, significance, R/R correlation, recovery patterns, SPY benchmark, drop-size buckets, win/loss decomposition, cumulative P&L)
- `deep-dive.html` — interactive Chart.js report
- `charts/` — 22 static PNGs
- `data/` — 60+ CSVs and JSONs with every aggregation, statistical test, time-series, R/R analysis, and the full enriched cohort

**Run it first** — every other script can re-use the cached bars it produces.

### `portfolio_sim.py` — strategy simulator

Filter the cohort by R/R + intent, simulate buying €X at decision close, hold for N trading days, sell at close. Includes:

- **SPY paired benchmark** per trade (€X invested in SPY over the same window)
- **R/R cutoff sweep** (0.5 → 5.0 in 0.25 steps)
- **Investment-size sweep** (€100 → €10,000 per trade)
- **Per-verdict breakdown** at the chosen threshold
- **Drop-one-verdict sensitivity** + BUY-only variant
- **BUY-only R/R sweep**

Output: per-trade ledger CSV plus 5 sweep CSVs under `data/`.

```bash
# Optional flags:
./venv/bin/python scripts/analysis/portfolio_sim.py \
    --rr-min 2.0 \
    --horizon 1w \
    --investment 1500 \
    --intent-only      # restrict to BUY verdicts (ENTER_NOW + ENTER_LIMIT)
```

### `tp_sl_optimizer.py` — take-profit / stop-loss grid

Walks each trade's daily OHLC. At each (TP, SL) pair:
- TP fires when High ≥ entry × (1 + TP)
- SL fires when Low ≤ entry × (1 − SL)
- If both same day → conservative: SL fires first
- Otherwise hold to day-N close (TIMEOUT)

Sweeps TP × SL grid (default 1–25% × 1–15% in 0.5% steps = 1,421 combos).
Outputs:
- Top 10 (TP, SL) combinations by total net P&L
- **Break-even map**: smallest TP at each SL where total net ≥ €0
- Per-trade outcomes at the optimum
- Heatmap PNG

### `exit_strategy_comparison.py` — beyond hard TP/SL

Compares 6 exit policies head-to-head on the same trade list:

| Strategy | What it does |
|---|---|
| BASELINE | No TP/SL — exit at day-N close |
| HARD TP/SL | Fixed prices set at entry |
| TRAILING STOP | Track running peak high; exit when low ≤ peak × (1 − trail%) |
| BREAKEVEN-TRAIL | Initial SL; once high ≥ entry × (1+trigger), permanently lift SL to max(entry, peak × (1 − 3%)) |
| TIME-DECAY | If close at day N < threshold, exit then |
| MULTI-TIER TP | TP1 closes 50% at small profit; remainder targets TP2 with SL backstop |
| ORACLE | Hindsight — exit at the max close achieved (upper bound) |

Each parametric strategy sweeps a small grid and reports its best parameter set.

## Where to look in the data

| Question | File |
|---|---|
| "How did each verdict group perform?" | `data/winrate_by_intent.csv` (and `_1w.csv`, `_2w.csv`, `_4w.csv`) |
| "Did a verdict difference reach significance?" | `data/pairwise_intent_<horizon>.csv` |
| "Does R/R correlate with realized return?" | `data/corr_pm_rr_<horizon>.json` and `data/stats_pm_rr_by_intent.json` |
| "Which decisions had high R/R?" | `data/top_pm_rr.csv` |
| "How did the strategy do vs SPY?" | `data/portfolio_sim_*.csv` |
| "What R/R cutoff is best?" | `data/sweep_rr_*.csv` |
| "What TP/SL combo is best?" | `data/sweep_tp_sl_*.csv` |
| "How much does verdict help on top of R/R?" | `data/per_verdict_*.csv` and `data/drop_one_verdict_*.csv` |
| "Is a smarter exit rule better than hard TP/SL?" | `data/exit_strategy_summary_*.csv` |

## How to interpret a re-run

When you re-run the whole pipeline with a larger cohort (more decisions
aged in), here's what to compare against the **2026-05-10 baseline checkpoint**:

### Significance tests
| 2026-05-10 (n=18) | Re-run (target) |
|---|---|
| No pairwise FDR-significant | At least 1 cell at p<0.05 if real |
| Welch p ≥ 0.27 in best cell | < 0.05 with n ≥ 50/group |

### Correlations (PM R/R vs return)
| 2026-05-10 (n=246 at 1w) | Re-run (target) |
|---|---|
| Pearson r=+0.000 (p=1.0) | Should stay near zero — confirms R/R is not a continuous predictor |
| Spearman ρ=+0.030 (p=0.65) | Same |
| 4w cell (n=35): Pearson +0.38 (p=0.03) | Should regress to zero — was a small-sample artifact |

### Verdict alpha vs SPY (key result)
| 2026-05-10 (1w, R/R > 1.5) | Re-run (target) |
|---|---|
| All-verdicts alpha: +0.52 pp (€+136) | Track this — does it stay positive as n grows? |
| BUY-only alpha: +1.73 pp (€+234) | Most likely to hold up if signal is real |
| AVOID alpha: −1.03 pp | Should stay ≤ 0 — confirms AVOID drag |

### Optimal R/R cutoff
| 2026-05-10 | Re-run (target) |
|---|---|
| Best ROI: R/R > 2.0 (n=15, +3.37%) | Cell n grows to 50+; ROI may drop closer to alpha |
| Best alpha %: R/R > 2.25 (n=10, +1.61 pp) | If R/R has signal, this cell stays positive |

### Optimal TP/SL
| 2026-05-10 (n=18) | Re-run (target) |
|---|---|
| TP=21%, SL=9.5% (€+522, +3.86% ROI) | The 21% TP only fires on TEAM and DOCN. With more trades, the optimal TP likely settles lower (10–15%). |
| 0 trades hit SL | More volatile names will hit SL — first regime shift will surface this |

### Exit strategy ranking
| 2026-05-10 ranking | Re-run (target) |
|---|---|
| Hard TP > Multi-tier > Baseline > Trailing | Trailing should improve in a choppier regime; multi-tier should stay consistent. Watch for ranking flips. |

## What to add when you have more data

Once n at 1w is ≥ 100 trades:

1. **Out-of-sample test.** Hold the most recent 30% of decisions out of all parameter optimization. Run TP/SL optimizer on the older 70%, then evaluate on the held-out 30%. If the optimal parameters still beat baseline on the unseen data, the signal is real.

2. **Walk-forward.** For each calendar week, optimize TP/SL on all earlier weeks, apply on this week. If the resulting equity curve is monotonic up, the strategy generalizes.

3. **Regime conditioning.** Re-run with cohort split by SPY trend (above vs below 50-day MA). If alpha is much smaller in bear-trend windows, the strategy is just market-rally beta in disguise.

4. **Vol-adjusted sizing.** Replace the fixed €750/trade with a per-trade size proportional to inverse 30-day stock volatility. Reduces concentration in high-vol names that dominate aggregate variance.

5. **Multi-day TP/SL.** Currently SL/TP are pct-of-entry. Try percentage-of-yesterday's-close (which would let SL widen as the trade matures), or ATR-based stops.

## Related files

- **Today's full report:** [`2026-05-10-package/REPORT.md`](2026-05-10-package/REPORT.md)
- **Interactive HTML:** [`2026-05-10-package/deep-dive.html`](2026-05-10-package/deep-dive.html)
- **Reference checkpoint (this run):** [`2026-05-10-checkpoint.md`](2026-05-10-checkpoint.md)
- **Spec:** [`docs/superpowers/specs/2026-05-08-performance-analysis-design.md`](../superpowers/specs/2026-05-08-performance-analysis-design.md)

## Configuration knobs

If something needs to change permanently, here's where:

| Change | File |
|---|---|
| Default cohort start date | `app/services/analytics/payload.py` (constant `start_date="2026-02-01"`) |
| TEST/excluded symbol filter | `app/services/analytics/cohort.py` (`EXCLUDED_SYMBOLS`, `_is_test_symbol`) |
| Horizon definitions (1w / 2w / 4w / 8w) | `app/services/analytics/outcomes.py` (`HORIZON_DAYS`) |
| Drop-size bucket boundaries | `app/services/analytics/payload.py` (`bins=[-100, -15, -8, -5, 0]`) |
| R/R bucket boundaries | `app/services/analytics/payload.py` (`rr_bins`) |
| SPY benchmark ticker | `app/services/analytics/payload.py` (`get_bars("SPY", ...)`) |
| Default investment / costs / horizon | argparse defaults in each script |

## Tests

Run the analytics test suite to confirm nothing broke after a code change:

```bash
./venv/bin/python -m pytest tests/test_analytics_*.py -q
```

33 tests covering cohort loader, intervals (Wilson, t, Pearson, Spearman),
outcomes, aggregations, and stats functions.
