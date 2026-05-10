# Strategy Analysis Checkpoint — 2026-05-10

> **Reference snapshot.** Pin this file when re-running the analysis later
> so you can compare findings against a known baseline. Re-running the
> pipeline against a larger cohort should EXTEND, not contradict, these
> numbers — if the conclusions flip, the small-n run was probably noisy.

## Cohort at this checkpoint

| Property | Value |
|---|---|
| Source DB | `subscribers.db` |
| Cohort window | `decision_date >= 2026-02-01` |
| Earliest decision in DB | 2026-04-09 |
| Latest decision in DB | 2026-05-08 |
| Total decisions after filters | **361** |
| Symbols excluded | `TEST`, `TEST_*`, `T8_*`, `PBMRF` (penny outlier) |
| Decisions with completed `return_1w` | **272** (75% of cohort) |
| Decisions with completed `return_2w` | **184** (51%) |
| Decisions with completed `return_4w` | **39** (11%) |
| Decisions with completed `return_8w` | **0** |

**Per-intent sample size at 1w:** AVOID 156, NEUTRAL 56, ENTER_NOW 33, ENTER_LIMIT 27.

## Headline findings as of 2026-05-10

### 1. SPY paired benchmark, 1-week hold

For each of the 35 trades passing R/R > 1.5 at €750/trade:

| | Strategy | SPY (same dates+sizes) | Alpha |
|---|---|---|---|
| Total deployed | €26,250 | €26,250 | — |
| Net P&L | **€+432.58** | **€+296.68** | **€+135.90** |
| ROI | +1.65% | +1.13% | **+0.52 pp** |

### 2. Verdict-conditional alpha (key actionable insight)

Adding the verdict filter on top of R/R **triples** alpha:

| Strategy | n | Net | Alpha vs SPY |
|---|---|---|---|
| R/R > 1.5 (any verdict) | 35 | €+433 | +0.52 pp |
| **R/R > 1.5 + BUY-only** | 18 | €+438 | **+1.73 pp** |
| R/R > 2.0 (any verdict) | 15 | €+379 | +1.50 pp |
| **R/R > 2.0 + BUY-only** | 11 | €+378 | **+2.71 pp** |

Per-verdict contribution at R/R > 1.5:

| Verdict | n | Alpha € | Alpha % | Sign |
|---|---|---|---|---|
| ENTER_NOW | 15 | €+93 | +0.83 pp | ✅ |
| ENTER_LIMIT | 3 | €+141 | +6.25 pp | ✅✅ (small n) |
| AVOID | 8 | **€−62** | **−1.03 pp** | ❌ drag |
| NEUTRAL | 9 | **€−36** | **−0.54 pp** | ❌ drag |

### 3. Optimal R/R cutoff (BUY-only)

| R/R > | n | ROI | Alpha % | Win % |
|---|---|---|---|---|
| 1.5 | 18 | +3.24% | +1.73 pp | 56% |
| **2.0** | **11** | **+4.58%** | **+2.71 pp** | **64%** |
| 2.25 | 7 | +5.73% | +3.96 pp | 71% |
| 2.5 | 3 | +7.86% | +6.18 pp | 67% (n=3, small) |

### 4. PM R/R correlation with realized return

| Horizon | n | Pearson r (p) | Spearman ρ (p) |
|---|---|---|---|
| 1w | 246 | **+0.000 (p=1.0)** | +0.030 (p=0.65) |
| 2w | 164 | +0.116 (p=0.14) | +0.111 (p=0.16) |
| 4w | 35 | +0.377 (p=0.03) | +0.090 (p=0.61) |

**The 4w "significant" Pearson is a small-sample artifact** — at 1w with n=246, the correlation is exactly zero. The PM's R/R rating is not a continuous predictor of return; the verdict-conditional analysis is what matters.

### 5. R/R verdict consistency (high confidence)

PM assigns systematically higher R/R to its own buy verdicts (n=329):

| Intent | Mean R/R | 95% CI |
|---|---|---|
| AVOID | 0.66 | [0.57, 0.75] |
| NEUTRAL | 0.89 | [0.72, 1.05] |
| ENTER_LIMIT | 1.14 | [0.95, 1.33] |
| **ENTER_NOW** | **1.47** | **[1.22, 1.73]** |

**ANOVA F=22.3, p<1e-12; Kruskal-Wallis p<1e-12.** All 6 pairwise comparisons FDR-significant. The PM is internally consistent — it just isn't externally predictive at the magnitude level.

### 6. Optimal TP/SL (n=18 BUY-only at R/R > 1.5)

| Configuration | Net P&L | ROI | Win % |
|---|---|---|---|
| Baseline (no TP/SL) | €+437.51 | +3.24% | 55.6% |
| **HARD TP=21%, SL=9.5%** | **€+521.74** | **+3.86%** | 55.6% |
| ORACLE upper bound | €+646.69 | +4.79% | 83.3% |

The hard-TP captures **81% of the theoretical max alpha** (€522/€647). The 9.5% SL never triggered in this cohort.

### 7. Exit strategy ranking (n=18)

| Rank | Strategy | Net |
|---|---|---|
| 1 | **HARD TP=21% / SL=9.5%** | **€+522** |
| 2 | MULTI-TIER TP (TP1=10%, TP2=21%) | €+446 |
| 3 | BREAKEVEN-TRAIL (trigger=15%) | €+441 |
| 4 | BASELINE (hold to day-5 close) | €+438 |
| 5 | TIME-DECAY (day=3, threshold=−2%) | €+364 |
| 6 | TRAILING STOP (trail=6%) | €+348 |

**Counter-intuitive:** trailing stops and time-decay HURT returns in this rally regime. Patience pays. Hard TP only beats baseline because of 2 names (TEAM, DOCN) that intraday-spiked above +21% on day 5 then closed lower.

### 8. SPY market regime context

SPY median over the same calendar windows: day-5 +1.61%, day-10 +3.26%, day-20 +7.60%. The cohort was a **broad rally** — every strategy had tailwinds. A different regime (sideways or down) is the true test.

## Data ledger at this checkpoint

All artifacts shipped under `2026-05-10-package/`:

| Type | Count |
|---|---|
| Static charts (PNG) | 23 |
| Per-aggregation CSVs | ~50 |
| Stats JSON files | 6 |
| Per-trade ledger CSVs | 4 |
| Sweep CSVs (R/R, investment, TP/SL, exit strategies) | 6 |
| Total files in package | 80+ |

## Open questions to resolve as data grows

These are the things that **we cannot conclude from n=18** but should clarify with n ≥ 100:

1. **Is the BUY-only alpha real or regime-driven?** Out-of-sample backtest required. Hold out latest 30% of trades, optimize on the older 70%, evaluate on hold-out.

2. **Does the optimal TP=21% replicate?** Almost certainly settles lower (10–15%) once outliers stop dominating.

3. **Does the SL=9.5% threshold ever trigger?** A 0/18 hit rate suggests it's untested. First regime shift should validate or invalidate.

4. **Are AVOID and NEUTRAL really negative-alpha?** The drag is small in absolute terms (€62 + €36 = €98). Could plausibly flip to neutral at higher n.

5. **Does ENTER_LIMIT (n=3) hold up?** That subgroup has +6.25 pp alpha — almost certainly noise at this n.

6. **Are the pairwise verdict differences in *return* statistically significant?** Currently all 0/3 cells are above FDR p=0.05 even at 1w (n=18 in the smallest cell). Need n ≥ 30 per group.

## Re-run checklist

When re-running this analysis (recommended cadence: every 2 weeks):

1. ✅ Refresh yfinance bars cache (`build_package.py` does this automatically)
2. ✅ Verify cohort size has grown — diff against the count in this checkpoint
3. ✅ Re-run `portfolio_sim.py` — does the BUY-only alpha at R/R > 1.5 hold?
4. ✅ Re-run `tp_sl_optimizer.py` — does the optimal TP/SL shift, or does 21%/9.5% replicate?
5. ✅ Re-run `exit_strategy_comparison.py` — does the strategy ranking change?
6. ✅ Compare every "key result" table above against the new run; flag any reversals
7. ✅ Save the new package as `<date>-package/` and write a new checkpoint MD

If conclusions reverse, that's the strongest signal — it means the original
finding was driven by the small-n outliers (TEAM, DOCN, PBMRF when it was
included). If conclusions hold, that's evidence the strategy is real.
