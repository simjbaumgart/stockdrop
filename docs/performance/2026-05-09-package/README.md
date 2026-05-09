# Performance analysis package — 2026-02-01

Generated 2026-05-09 20:40. Cohort size: **372 decisions**.

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

### Data

- `data/cohort_enriched.csv`
- `data/equity_curve.csv`
- `data/full_payload.json`
- `data/spy_bars.csv`
- `data/stats_corr_dr_rr.json`
- `data/stats_corr_pm_rr.json`
- `data/stats_pairwise_dr_verdict.csv`
- `data/stats_pairwise_intent.csv`
- `data/stats_recovery_by_dr_verdict.csv`
- `data/stats_recovery_by_intent.csv`
- `data/time_series.json`
- `data/time_series_individuals.json`
- `data/time_to_recover.csv`
- `data/winrate_by_dr_action.csv`
- `data/winrate_by_dr_rr.csv`
- `data/winrate_by_dr_verdict.csv`
- `data/winrate_by_drop_bucket.csv`
- `data/winrate_by_gatekeeper.csv`
- `data/winrate_by_horizon.csv`
- `data/winrate_by_intent.csv`
- `data/winrate_by_pm_rr.csv`
- `data/winrate_by_sector.csv`

### Regenerate

```
./venv/bin/python scripts/analysis/build_package.py
```
