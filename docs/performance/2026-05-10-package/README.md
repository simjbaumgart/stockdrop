# Performance analysis package — 2026-02-01

Generated 2026-05-10 12:35. Cohort size: **359 decisions**.

## Files

- [`REPORT.md`](REPORT.md) — full written analysis with findings.
- [`deep-dive.html`](deep-dive.html) — interactive Chart.js report (open in a browser).

### Charts

- `charts/01_equity_curve.png`
- `charts/02_winrate_by_intent.png`
- `charts/03_avgreturn_by_intent.png`
- `charts/04_winrate_by_dr_verdict.png`
- `charts/05_avgreturn_by_dr_verdict.png`
- `charts/06_winrate_by_pm_rr.png`
- `charts/07_avgreturn_by_pm_rr.png`
- `charts/08_winrate_by_dr_rr.png`
- `charts/09_avgreturn_by_dr_rr.png`
- `charts/10_winrate_by_drop_size.png`
- `charts/11_timeseries_by_intent.png`
- `charts/11b_timeseries_mean_ci_by_intent.png`
- `charts/12_timeseries_by_dr_verdict.png`
- `charts/13_timeseries_alpha_by_intent.png`
- `charts/14_buy_trajectories_spaghetti.png`
- `charts/15_corr_pm_rr_vs_return.png`
- `charts/16_corr_dr_rr_vs_return.png`
- `charts/17_recovery_days_by_intent.png`
- `charts/18_winloss_by_intent.png`
- `charts/19_winloss_by_dr_verdict.png`
- `charts/20_cum_pnl_calendar_by_intent.png`
- `charts/21_cum_pnl_calendar_by_dr_verdict.png`
- `charts/22_rr_box_pm_by_intent.png`
- `charts/23_rr_box_dr_by_verdict.png`
- `charts/24_winrate_by_intent_multi.png`
- `charts/25_avgreturn_by_intent_multi.png`
- `charts/26_winrate_by_drop_multi.png`
- `charts/27_winrate_by_pm_rr_multi.png`
- `charts/28_winrate_by_dr_rr_multi.png`

### Data

- `data/cohort_enriched.csv`
- `data/corr_dr_rr_1w.json`
- `data/corr_dr_rr_2w.json`
- `data/corr_dr_rr_4w.json`
- `data/corr_pm_rr_1w.json`
- `data/corr_pm_rr_2w.json`
- `data/corr_pm_rr_4w.json`
- `data/cum_pnl_by_dr_verdict.json`
- `data/cum_pnl_by_intent.json`
- `data/equity_curve.csv`
- `data/full_payload.json`
- `data/pairwise_dr_verdict_1w.csv`
- `data/pairwise_dr_verdict_2w.csv`
- `data/pairwise_dr_verdict_4w.csv`
- `data/pairwise_intent_1w.csv`
- `data/pairwise_intent_2w.csv`
- `data/pairwise_intent_4w.csv`
- `data/spy_bars.csv`
- `data/stats_corr_dr_rr.json`
- `data/stats_corr_pm_rr.json`
- `data/stats_dr_rr_by_dr_verdict.json`
- `data/stats_dr_rr_by_dr_verdict_pairwise.csv`
- `data/stats_dr_rr_by_dr_verdict_per_group.csv`
- `data/stats_dr_rr_by_intent.json`
- `data/stats_dr_rr_by_intent_pairwise.csv`
- `data/stats_dr_rr_by_intent_per_group.csv`
- `data/stats_pairwise_dr_verdict.csv`
- `data/stats_pairwise_intent.csv`
- `data/stats_pm_rr_by_dr_verdict.json`
- `data/stats_pm_rr_by_dr_verdict_pairwise.csv`
- `data/stats_pm_rr_by_dr_verdict_per_group.csv`
- `data/stats_pm_rr_by_intent.json`
- `data/stats_pm_rr_by_intent_pairwise.csv`
- `data/stats_pm_rr_by_intent_per_group.csv`
- `data/stats_recovery_by_dr_verdict.csv`
- `data/stats_recovery_by_intent.csv`
- `data/time_series.json`
- `data/time_series_individuals.json`
- `data/time_to_recover.csv`
- `data/top_dr_rr.csv`
- `data/top_pm_rr.csv`
- `data/winloss_by_dr_verdict.json`
- `data/winloss_by_intent.json`
- `data/winrate_by_dr_action.csv`
- `data/winrate_by_dr_rr.csv`
- `data/winrate_by_dr_rr_1w.csv`
- `data/winrate_by_dr_rr_2w.csv`
- `data/winrate_by_dr_rr_4w.csv`
- `data/winrate_by_dr_verdict.csv`
- `data/winrate_by_dr_verdict_1w.csv`
- `data/winrate_by_dr_verdict_2w.csv`
- `data/winrate_by_dr_verdict_4w.csv`
- `data/winrate_by_drop_bucket.csv`
- `data/winrate_by_drop_bucket_1w.csv`
- `data/winrate_by_drop_bucket_2w.csv`
- `data/winrate_by_drop_bucket_4w.csv`
- `data/winrate_by_gatekeeper.csv`
- `data/winrate_by_horizon.csv`
- `data/winrate_by_intent.csv`
- `data/winrate_by_intent_1w.csv`
- `data/winrate_by_intent_2w.csv`
- `data/winrate_by_intent_4w.csv`
- `data/winrate_by_pm_rr.csv`
- `data/winrate_by_pm_rr_1w.csv`
- `data/winrate_by_pm_rr_2w.csv`
- `data/winrate_by_pm_rr_4w.csv`
- `data/winrate_by_sector.csv`

### Regenerate

```
./venv/bin/python scripts/analysis/build_package.py
```
