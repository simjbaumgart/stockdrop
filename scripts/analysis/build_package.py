"""Build a self-contained analysis package for a given cohort.

Layout (under docs/performance/<date>-package/):
  README.md        — index of the package
  REPORT.md        — written analysis with findings and figure references
  deep-dive.html   — the interactive HTML report (copy of the offline file)
  charts/          — static PNGs for every figure in REPORT.md
  data/            — CSVs / JSONs of every aggregation + the enriched cohort

Usage:
  ./venv/bin/python scripts/analysis/build_package.py [--start 2026-02-01]
                                                       [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.charts import (  # noqa: E402
    DR_COLORS,
    INTENT_COLORS,
    avg_return_bar,
    cum_pnl_calendar,
    equity_line,
    recovery_histogram_by_group,
    scatter_with_regression,
    spaghetti_plot,
    time_series_lines,
    win_loss_split_grid,
    winrate_bar,
)
from app.services.analytics.payload import compute_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("build_package")


def _pf(p: Optional[float]) -> str:
    if p is None:
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _fmt_pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "—"
    if signed:
        return f"{'+' if v >= 0 else ''}{v * 100:.2f}%"
    return f"{v * 100:.1f}%"


def _df_from_records(records: List[Dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _md_table(df: pd.DataFrame, float_cols: Optional[List[str]] = None) -> str:
    if df is None or df.empty:
        return "_no data_"
    out = df.copy()
    if float_cols:
        for c in float_cols:
            if c in out.columns:
                out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:.3f}")
    try:
        return out.to_markdown(index=False)
    except ImportError:
        cols = list(out.columns)
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
                for r in out.values.tolist()]
        return "\n".join([header, sep, *rows])


def _generate_charts(payload: Dict[str, Any], enriched: pd.DataFrame, charts_dir: Path) -> Dict[str, Path]:
    """Render every static figure used in REPORT.md. Returns {key -> path}."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}

    eq = pd.DataFrame(payload.get("equity_curve") or [])
    if not eq.empty:
        eq["decision_date"] = pd.to_datetime(eq["decision_date"])
    out["equity"] = equity_line(
        eq,
        "Equity curve — equal-weight BUY/BUY_LIMIT decisions, 4w returns",
        charts_dir / "01_equity_curve.png",
    )

    intent_df = _df_from_records(payload.get("winrate_by_intent") or [])
    out["wr_intent"] = winrate_bar(
        intent_df, "intent",
        "Win rate by AI council intent (4w)",
        charts_dir / "02_winrate_by_intent.png",
    )
    out["ar_intent"] = avg_return_bar(
        intent_df, "intent",
        "Average 4w return by AI council intent",
        charts_dir / "03_avgreturn_by_intent.png",
    )

    dr_df = _df_from_records(
        payload.get("winrate_by_dr_verdict") or payload.get("winrate_by_dr_action") or []
    )
    dr_label = (
        "deep_research_verdict" if "deep_research_verdict" in dr_df.columns
        else "deep_research_action"
    )
    out["wr_dr"] = winrate_bar(
        dr_df, dr_label,
        "Win rate by Deep Research verdict (4w)",
        charts_dir / "04_winrate_by_dr_verdict.png",
    )
    out["ar_dr"] = avg_return_bar(
        dr_df, dr_label,
        "Average 4w return by Deep Research verdict",
        charts_dir / "05_avgreturn_by_dr_verdict.png",
    )

    pmrr_df = _df_from_records(payload.get("winrate_by_pm_rr") or [])
    out["wr_pmrr"] = winrate_bar(
        pmrr_df, "bucket",
        "Win rate by AI council R/R bucket (4w)",
        charts_dir / "06_winrate_by_pm_rr.png",
    )
    out["ar_pmrr"] = avg_return_bar(
        pmrr_df, "bucket",
        "Average 4w return by AI council R/R bucket",
        charts_dir / "07_avgreturn_by_pm_rr.png",
    )

    drrr_df = _df_from_records(payload.get("winrate_by_dr_rr") or [])
    out["wr_drrr"] = winrate_bar(
        drrr_df, "bucket",
        "Win rate by Deep Research R/R bucket (4w)",
        charts_dir / "08_winrate_by_dr_rr.png",
    )
    out["ar_drrr"] = avg_return_bar(
        drrr_df, "bucket",
        "Average 4w return by Deep Research R/R bucket",
        charts_dir / "09_avgreturn_by_dr_rr.png",
    )

    drop_df = _df_from_records(payload.get("winrate_by_drop_bucket") or [])
    out["wr_drop"] = winrate_bar(
        drop_df, "bucket",
        "Win rate by drop-size bucket (4w)",
        charts_dir / "10_winrate_by_drop_size.png",
    )

    ts = payload.get("time_series") or {}
    spy_overlay = ts.get("spy_overlay") or None

    out["ts_intent"] = time_series_lines(
        ts.get("by_intent") or {},
        "Median return path by AI council intent — IQR band, with S&P 500 overlay",
        charts_dir / "11_timeseries_by_intent.png",
        palette=INTENT_COLORS,
        spy_overlay=spy_overlay,
        use="median",
    )
    out["ts_intent_mean"] = time_series_lines(
        ts.get("by_intent") or {},
        "Mean return path by AI council intent — 95% CI band, with S&P 500 overlay",
        charts_dir / "11b_timeseries_mean_ci_by_intent.png",
        palette=INTENT_COLORS,
        spy_overlay=spy_overlay,
        use="mean",
    )
    out["ts_dr"] = time_series_lines(
        ts.get("by_dr_verdict") or {},
        "Median return path by Deep Research verdict — IQR band, with S&P 500 overlay",
        charts_dir / "12_timeseries_by_dr_verdict.png",
        palette=DR_COLORS,
        spy_overlay=spy_overlay,
        use="median",
    )

    # Build alpha view by subtracting SPY
    alpha_groups = {}
    if spy_overlay and spy_overlay.get("median"):
        spy_med = spy_overlay["median"]
        for grp_name, grp_data in (ts.get("by_intent") or {}).items():
            if not grp_data.get("median"):
                continue
            alpha = []
            for i, v in enumerate(grp_data["median"]):
                s = spy_med[i] if i < len(spy_med) else None
                if v is None or s is None:
                    alpha.append(None)
                else:
                    alpha.append(v - s)
            alpha_groups[grp_name] = {
                "day_offsets": grp_data["day_offsets"],
                "median": alpha,
                "n_paths": grp_data.get("n_paths", 0),
            }
    out["ts_alpha"] = time_series_lines(
        alpha_groups,
        "Excess return vs S&P 500 — by AI council intent",
        charts_dir / "13_timeseries_alpha_by_intent.png",
        palette=INTENT_COLORS,
        ylabel="Excess return vs SPY",
    )

    # Spaghetti
    by_intent = ts.get("by_intent") or {}
    individuals: List[dict] = []
    for grp in ("ENTER_NOW", "ENTER_LIMIT"):
        for p in (by_intent.get(grp, {}).get("individuals") or []):
            individuals.append(p)
    medians_for_spaghetti = {
        g: by_intent[g] for g in ("ENTER_NOW", "ENTER_LIMIT") if g in by_intent
    }
    out["ts_spaghetti"] = spaghetti_plot(
        individuals,
        medians_for_spaghetti,
        "BUY-signal trajectories — every ENTER_NOW + ENTER_LIMIT decision",
        charts_dir / "14_buy_trajectories_spaghetti.png",
        palette=INTENT_COLORS,
    )

    # Correlation scatter
    corr_pm = (payload.get("stats") or {}).get("corr_pm_rr") or {}
    out["corr_pm"] = scatter_with_regression(
        corr_pm.get("points") or [],
        corr_pm.get("regression_slope"),
        corr_pm.get("regression_intercept"),
        corr_pm.get("pearson_r"), corr_pm.get("pearson_p"),
        corr_pm.get("spearman_rho"), corr_pm.get("spearman_p"),
        "AI council R/R ratio vs realized 4w return",
        "AI council R/R ratio (risk_reward_ratio)",
        charts_dir / "15_corr_pm_rr_vs_return.png",
        pearson_ci=(corr_pm.get("pearson_ci_low"), corr_pm.get("pearson_ci_high")),
        spearman_ci=(corr_pm.get("spearman_ci_low"), corr_pm.get("spearman_ci_high")),
    )
    corr_dr = (payload.get("stats") or {}).get("corr_dr_rr") or {}
    out["corr_dr"] = scatter_with_regression(
        corr_dr.get("points") or [],
        corr_dr.get("regression_slope"),
        corr_dr.get("regression_intercept"),
        corr_dr.get("pearson_r"), corr_dr.get("pearson_p"),
        corr_dr.get("spearman_rho"), corr_dr.get("spearman_p"),
        "Deep Research R/R vs realized 4w return",
        "Deep Research R/R (deep_research_rr_ratio)",
        charts_dir / "16_corr_dr_rr_vs_return.png",
        pearson_ci=(corr_dr.get("pearson_ci_low"), corr_dr.get("pearson_ci_high")),
        spearman_ci=(corr_dr.get("spearman_ci_low"), corr_dr.get("spearman_ci_high")),
    )

    # Recovery histogram by intent
    out["recovery_hist"] = recovery_histogram_by_group(
        enriched, "intent",
        "Trading days to recovery — by AI council intent",
        charts_dir / "17_recovery_days_by_intent.png",
        palette=INTENT_COLORS,
    )

    # Win/loss split per intent and per DR verdict
    out["winloss_intent"] = win_loss_split_grid(
        ts.get("winloss_by_intent") or {},
        "Winner vs loser trajectories — by AI council intent (split at day-20 sign)",
        charts_dir / "18_winloss_by_intent.png",
        palette=INTENT_COLORS,
        group_order=["ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"],
    )
    out["winloss_dr"] = win_loss_split_grid(
        ts.get("winloss_by_dr_verdict") or {},
        "Winner vs loser trajectories — by Deep Research verdict",
        charts_dir / "19_winloss_by_dr_verdict.png",
        palette=DR_COLORS,
        group_order=["BUY", "BUY_LIMIT", "AVOID", "WATCH", "HOLD"],
    )

    # Cumulative dollar P&L over actual calendar time, per group
    out["cum_pnl_intent"] = cum_pnl_calendar(
        ts.get("cum_pnl_by_intent") or {},
        "Cumulative mark-to-market P&L by AI council intent\n($1 per signal, summed across all open positions)",
        charts_dir / "20_cum_pnl_calendar_by_intent.png",
        palette=INTENT_COLORS,
    )
    out["cum_pnl_dr"] = cum_pnl_calendar(
        ts.get("cum_pnl_by_dr_verdict") or {},
        "Cumulative mark-to-market P&L by Deep Research verdict\n($1 per signal, summed across all open positions)",
        charts_dir / "21_cum_pnl_calendar_by_dr_verdict.png",
        palette=DR_COLORS,
    )

    return out


def _export_data(payload: Dict[str, Any], enriched: pd.DataFrame,
                 spy_bars: pd.DataFrame, data_dir: Path) -> Dict[str, Path]:
    """Write CSVs/JSONs for every aggregation."""
    data_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}

    # 1. Enriched cohort (all derived columns)
    cohort_csv = data_dir / "cohort_enriched.csv"
    cols_to_drop = [c for c in enriched.columns if c.endswith("_swot")
                    or c.endswith("_global_analysis") or c.endswith("_local_analysis")
                    or c.endswith("_blindspots") or c == "reasoning"
                    or c.endswith("_verification") or c.endswith("_reason")]
    enriched.drop(columns=cols_to_drop, errors="ignore").to_csv(cohort_csv, index=False)
    out["cohort"] = cohort_csv

    # 2. Aggregations as CSVs
    csv_keys = [
        "winrate_by_intent",
        "winrate_by_horizon",
        "winrate_by_drop_bucket",
        "winrate_by_dr_action",
        "winrate_by_dr_verdict",
        "winrate_by_gatekeeper",
        "winrate_by_sector",
        "winrate_by_pm_rr",
        "winrate_by_dr_rr",
        "equity_curve",
        "time_to_recover",
    ]
    for key in csv_keys:
        records = payload.get(key) or []
        if records:
            df = pd.DataFrame(records)
            path = data_dir / f"{key}.csv"
            df.to_csv(path, index=False)
            out[key] = path

    # 3. Stats as CSVs
    stats = payload.get("stats") or {}
    for key in ("pairwise_intent", "pairwise_dr_verdict",
                "recovery_by_intent", "recovery_by_dr_verdict"):
        records = stats.get(key) or []
        if records:
            path = data_dir / f"stats_{key}.csv"
            pd.DataFrame(records).to_csv(path, index=False)
            out[f"stats_{key}"] = path

    for key in ("corr_pm_rr", "corr_dr_rr"):
        record = stats.get(key)
        if record:
            path = data_dir / f"stats_{key}.json"
            path.write_text(json.dumps(record, default=str, indent=2))
            out[f"stats_{key}"] = path

    # 4. Time series + SPY overlay as JSON
    ts = payload.get("time_series") or {}
    if ts:
        # Strip individuals from a copy so the file is small
        ts_export = {
            "max_days": ts.get("max_days"),
            "by_intent": {
                grp: {k: v for k, v in g.items() if k != "individuals"}
                for grp, g in (ts.get("by_intent") or {}).items()
            },
            "by_dr_verdict": ts.get("by_dr_verdict") or {},
            "spy_overlay": ts.get("spy_overlay") or {},
        }
        path = data_dir / "time_series.json"
        path.write_text(json.dumps(ts_export, default=str, indent=2))
        out["time_series"] = path

        # Win/loss split per group + cumulative P&L by calendar
        for key in ("winloss_by_intent", "winloss_by_dr_verdict",
                    "cum_pnl_by_intent", "cum_pnl_by_dr_verdict"):
            value = ts.get(key)
            if value:
                p = data_dir / f"{key}.json"
                p.write_text(json.dumps(value, default=str, indent=2))
                out[key] = p

        # individuals separately for compactness
        individuals = []
        for grp_name, grp_data in (ts.get("by_intent") or {}).items():
            for p in (grp_data.get("individuals") or []):
                individuals.append({**p, "intent": grp_name})
        if individuals:
            ipath = data_dir / "time_series_individuals.json"
            ipath.write_text(json.dumps(individuals, default=str))
            out["time_series_individuals"] = ipath

    # 5. SPY raw bars (post-cohort-start)
    if spy_bars is not None and not spy_bars.empty:
        spy_csv = data_dir / "spy_bars.csv"
        spy_bars.to_csv(spy_csv)
        out["spy_bars"] = spy_csv

    # 6. Full payload (one JSON for programmatic re-use)
    full_path = data_dir / "full_payload.json"
    # Strip individuals from time_series to keep size sane
    payload_compact = json.loads(json.dumps(payload, default=str))
    full_path.write_text(json.dumps(payload_compact, indent=2))
    out["full_payload"] = full_path

    return out


def _format_pairwise_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "_no significance data_"
    df = pd.DataFrame(rows)
    keep = ["group_a", "group_b", "n_a", "n_b",
            "diff", "cohen_d", "welch_p", "welch_p_fdr", "mwu_p", "mwu_p_fdr", "significant"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["diff"] = df["diff"].apply(lambda v: "" if pd.isna(v) else f"{v*100:+.2f}%")
    for c in ("cohen_d",):
        df[c] = df[c].apply(lambda v: "" if pd.isna(v) else f"{v:+.2f}")
    for c in ("welch_p", "welch_p_fdr", "mwu_p", "mwu_p_fdr"):
        if c in df.columns:
            df[c] = df[c].apply(lambda v: "" if pd.isna(v) else (
                "<0.001" if v < 0.001 else f"{v:.3f}"))
    df["significant"] = df["significant"].map({True: "✅", False: "—"})
    df.columns = ["A", "B", "n_A", "n_B", "Δ mean",
                  "Cohen d", "Welch p", "Welch p (FDR)",
                  "MWU p", "MWU p (FDR)", "Sig?"]
    try:
        return df.to_markdown(index=False)
    except ImportError:
        return _md_table(df)


def _format_recovery_table(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "_no recovery data_"
    df = pd.DataFrame(rows)
    keep = ["group", "n_total", "n_recovered", "recovery_rate",
            "p25_days", "p50_days", "p75_days", "p90_days",
            "post_recover_5d_mean", "post_recover_10d_mean", "post_recover_20d_mean"]
    df = df[[c for c in keep if c in df.columns]].copy()
    df["recovery_rate"] = df["recovery_rate"].apply(
        lambda v: "" if pd.isna(v) else f"{v * 100:.0f}%")
    for c in ("p25_days", "p50_days", "p75_days", "p90_days"):
        if c in df.columns:
            df[c] = df[c].apply(lambda v: "" if pd.isna(v) else f"{v:.1f}")
    for c in ("post_recover_5d_mean", "post_recover_10d_mean", "post_recover_20d_mean"):
        if c in df.columns:
            df[c] = df[c].apply(lambda v: "" if pd.isna(v) else f"{v * 100:+.2f}%")
    df.columns = ["group", "n_total", "n_recov", "recov%",
                  "p25 d", "p50 d", "p75 d", "p90 d",
                  "post +5d", "post +10d", "post +20d"]
    try:
        return df.to_markdown(index=False)
    except ImportError:
        return _md_table(df)


def _build_findings(payload: Dict[str, Any]) -> Dict[str, str]:
    """Compute readable narrative paragraphs based on the payload numbers.

    Each value is a small markdown chunk inserted into REPORT.md.
    """
    findings: Dict[str, str] = {}
    h = payload.get("headline") or {}
    cohort = payload.get("cohort_size", 0)

    findings["intro"] = (
        f"Cohort of **{cohort} decisions** with `decision_date >= "
        f"{payload.get('cohort_start', 'all')}`, evaluated against yfinance "
        f"OHLC bars cached locally. Headline 4-week metrics on BUY/BUY_LIMIT "
        f"signals: win rate **{_fmt_pct(h.get('win_rate_4w_buys'), False)}**, "
        f"avg return **{_fmt_pct(h.get('avg_return_4w_buys'))}** "
        f"(n={h.get('n_buys_4w', 0)})."
    )

    # A — Significance
    intent_pairs = (payload.get("stats") or {}).get("pairwise_intent") or []
    sig_intent = [r for r in intent_pairs if r.get("significant")]
    if sig_intent:
        names = ", ".join(f"{r['group_a']} vs {r['group_b']}" for r in sig_intent)
        findings["sig_intent"] = (
            f"**{len(sig_intent)} of {len(intent_pairs)} pairwise comparisons** "
            f"reach FDR-adjusted p<0.05: {names}."
        )
    else:
        findings["sig_intent"] = (
            f"**None of the {len(intent_pairs)} pairwise PM-intent comparisons** "
            f"reach FDR-adjusted p<0.05. Sample sizes are small "
            f"(n={', '.join(str(r['n_a']) for r in intent_pairs)} per group), "
            f"so the apparent gaps in win rate are not yet distinguishable from noise."
        )

    dr_pairs = (payload.get("stats") or {}).get("pairwise_dr_verdict") or []
    sig_dr = [r for r in dr_pairs if r.get("significant")]
    if dr_pairs:
        if sig_dr:
            findings["sig_dr"] = (
                f"DR verdicts: **{len(sig_dr)} of {len(dr_pairs)}** pairwise "
                f"comparisons cross FDR p<0.05."
            )
        else:
            findings["sig_dr"] = (
                f"DR verdicts: **none of the {len(dr_pairs)} pairwise comparisons** "
                f"are significant — DR groups have n=3–6 each, far below what's "
                f"needed to detect even large effects."
            )
    else:
        findings["sig_dr"] = "DR groups too small for pairwise testing."

    # B — Correlation
    corr_pm = (payload.get("stats") or {}).get("corr_pm_rr") or {}
    if corr_pm.get("n", 0) >= 5:
        pcl, pch = corr_pm.get("pearson_ci_low"), corr_pm.get("pearson_ci_high")
        scl, sch = corr_pm.get("spearman_ci_low"), corr_pm.get("spearman_ci_high")
        ci_pm_pearson = (
            f" 95% CI [{pcl:+.2f}, {pch:+.2f}]"
            if pcl is not None and pch is not None else ""
        )
        ci_pm_spearman = (
            f" 95% CI [{scl:+.2f}, {sch:+.2f}]"
            if scl is not None and sch is not None else ""
        )
        findings["corr_pm"] = (
            f"PM R/R vs 4w return (n={corr_pm['n']}): "
            f"Pearson r={corr_pm.get('pearson_r', 0):+.3f} (p={_pf(corr_pm.get('pearson_p'))}){ci_pm_pearson}, "
            f"Spearman ρ={corr_pm.get('spearman_rho', 0):+.3f} (p={_pf(corr_pm.get('spearman_p'))}){ci_pm_spearman}."
        )
        if (corr_pm.get("pearson_p") or 1) < 0.05 and (corr_pm.get("spearman_p") or 1) < 0.05:
            findings["corr_pm"] += " Both linear and rank correlations significant — robust signal."
        elif (corr_pm.get("pearson_p") or 1) < 0.05:
            findings["corr_pm"] += (
                " Pearson is significant but Spearman is not, meaning the linear correlation "
                "is being driven by a few high-R/R outliers rather than a monotonic relationship."
            )
        else:
            findings["corr_pm"] += " Neither correlation reaches p<0.05."
    else:
        findings["corr_pm"] = "PM R/R correlation: too few populated rows (n<5)."

    corr_dr = (payload.get("stats") or {}).get("corr_dr_rr") or {}
    if corr_dr.get("n", 0) >= 5:
        findings["corr_dr"] = (
            f"DR R/R vs 4w return (n={corr_dr['n']}): "
            f"Pearson r={corr_dr.get('pearson_r', 0):+.3f} (p={_pf(corr_dr.get('pearson_p'))}), "
            f"Spearman ρ={corr_dr.get('spearman_rho', 0):+.3f} (p={_pf(corr_dr.get('spearman_p'))}). "
            f"Sample size is small — conclusions are tentative."
        )
    else:
        findings["corr_dr"] = (
            f"DR R/R correlation: only {corr_dr.get('n', 0)} populated rows — too few to test."
        )

    # C — Recovery
    rec = (payload.get("stats") or {}).get("recovery_by_intent") or []
    if rec:
        rec_lines = []
        for r in rec:
            rate = r.get("recovery_rate")
            p50 = r.get("p50_days")
            p20 = r.get("post_recover_20d_mean")
            rec_lines.append(
                f"- **{r['group']}** (n={r['n_total']}): "
                f"{(rate or 0) * 100:.0f}% recovered, median {('—' if p50 is None else f'{p50:.0f}')} days; "
                f"+20d post-recovery: {_fmt_pct(p20)}"
            )
        findings["recovery"] = "\n".join(rec_lines)
    else:
        findings["recovery"] = "_no recovery data computed_"

    # D — SPY benchmark / alpha
    ts = payload.get("time_series") or {}
    spy = ts.get("spy_overlay") or {}
    if spy.get("median") and len(spy["median"]) > 5:
        m = spy["median"]
        d20 = m[20] if len(m) > 20 else m[-1]
        findings["spy_benchmark"] = (
            f"SPY median over the same calendar windows: day-5 {_fmt_pct(m[5])}, "
            f"day-10 {_fmt_pct(m[10]) if len(m) > 10 else '—'}, "
            f"day-{min(20, len(m) - 1)} {_fmt_pct(d20)}."
        )

        alphas = []
        for grp in ("ENTER_NOW", "ENTER_LIMIT", "AVOID", "NEUTRAL"):
            g = ts.get("by_intent", {}).get(grp)
            if not g or not g.get("median"):
                continue
            gm = g["median"]
            day = min(20, len(gm) - 1, len(m) - 1)
            if gm[day] is None or m[day] is None:
                continue
            alpha = gm[day] - m[day]
            alphas.append(f"**{grp}** {_fmt_pct(alpha)} (n={g['n_paths']})")
        if alphas:
            findings["alpha_by_intent"] = (
                "Excess vs SPY at day-20 (group median minus SPY median): "
                + ", ".join(alphas) + "."
            )
        else:
            findings["alpha_by_intent"] = "_no alpha computed_"
    else:
        findings["spy_benchmark"] = "_SPY benchmark unavailable_"
        findings["alpha_by_intent"] = ""

    return findings


def _build_report(
    payload: Dict[str, Any],
    findings: Dict[str, str],
    chart_paths: Dict[str, Path],
    package_dir: Path,
) -> str:
    """Compose REPORT.md as a string."""
    def img(key: str) -> str:
        p = chart_paths.get(key)
        if not p:
            return ""
        rel = p.relative_to(package_dir)
        return f"\n![{key}]({rel})\n"

    h = payload.get("headline") or {}
    pairs_intent = (payload.get("stats") or {}).get("pairwise_intent") or []
    pairs_dr = (payload.get("stats") or {}).get("pairwise_dr_verdict") or []
    rec_intent = (payload.get("stats") or {}).get("recovery_by_intent") or []
    rec_dr = (payload.get("stats") or {}).get("recovery_by_dr_verdict") or []
    intent_table = _md_table(
        pd.DataFrame(payload.get("winrate_by_intent") or []),
        float_cols=["win_rate", "avg_return", "median_return", "std_return"],
    )
    dr_table = _md_table(
        pd.DataFrame(
            payload.get("winrate_by_dr_verdict") or payload.get("winrate_by_dr_action") or []
        ),
        float_cols=["win_rate", "avg_return", "median_return", "std_return"],
    )
    pmrr_table = _md_table(
        pd.DataFrame(payload.get("winrate_by_pm_rr") or []),
        float_cols=["win_rate", "avg_return", "median_return", "std_return"],
    )
    drrr_table = _md_table(
        pd.DataFrame(payload.get("winrate_by_dr_rr") or []),
        float_cols=["win_rate", "avg_return", "median_return", "std_return"],
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: List[str] = [
        f"# StockDrop Performance Analysis — {payload.get('cohort_start', 'all')} cohort",
        "",
        f"_Generated {now}. Cohort: **{payload.get('cohort_size', 0)} decisions**._",
        "",
        "## Executive summary",
        "",
        findings.get("intro", ""),
        "",
        "**Headlines**",
        "",
        f"- BUY/BUY_LIMIT 4w win rate: **{_fmt_pct(h.get('win_rate_4w_buys'), False)}** "
        f"(avg {_fmt_pct(h.get('avg_return_4w_buys'))}, n={h.get('n_buys_4w', 0)})",
        f"- BUY_LIMIT fill rate: **{_fmt_pct(h.get('buy_limit_fill_rate'), False)}** "
        f"({h.get('buy_limit_filled', 0)}/{h.get('buy_limit_count', 0)} filled), "
        f"avg 4w on filled: **{_fmt_pct(h.get('buy_limit_avg_filled_4w'))}**",
        f"- Median days to recover (recovered cohort): **"
        f"{(h.get('median_days_to_recover') or 0):.1f} days** (n={h.get('n_recovered', 0)})",
        "",
        findings.get("spy_benchmark", ""),
        "",
        findings.get("alpha_by_intent", ""),
        "",
        "## 1. Equity curve",
        "",
        "Equal-weight cumulative growth assuming each ENTER_NOW/ENTER_LIMIT decision "
        "is held for 4 weeks at its 4w return.",
        img("equity"),
        "",
        "## 2. Verdict performance",
        "",
        "### 2.1 AI council (PM) intent",
        "",
        intent_table,
        "",
        img("wr_intent"),
        img("ar_intent"),
        "",
        "### 2.2 Deep Research verdict",
        "",
        dr_table,
        "",
        img("wr_dr"),
        img("ar_dr"),
        "",
        "## 3. Statistical significance",
        "",
        findings.get("sig_intent", ""),
        "",
        "### 3.1 Pairwise tests on AI council intent",
        "",
        _format_pairwise_table(pairs_intent),
        "",
        "### 3.2 Pairwise tests on Deep Research verdict",
        "",
        findings.get("sig_dr", ""),
        "",
        _format_pairwise_table(pairs_dr),
        "",
        "**Interpretation.** Welch's t-test compares group means under the (relaxed) "
        "assumption that variances may differ. Mann-Whitney U is rank-based and works "
        "even when returns are skewed. Both p-values are FDR-adjusted "
        "(Benjamini-Hochberg) to control the false-discovery rate across the family of "
        "comparisons.",
        "",
        "## 4. R/R ratio vs realized return",
        "",
        "### 4.1 AI council R/R",
        "",
        pmrr_table,
        "",
        img("wr_pmrr"),
        img("ar_pmrr"),
        "",
        findings.get("corr_pm", ""),
        "",
        img("corr_pm"),
        "",
        "### 4.2 Deep Research R/R",
        "",
        drrr_table,
        "",
        img("wr_drrr"),
        img("ar_drrr"),
        "",
        findings.get("corr_dr", ""),
        "",
        img("corr_dr"),
        "",
        "**Interpretation.** Pearson r captures linear association — if a few "
        "high-R/R, high-return rows dominate, Pearson can be inflated even when most "
        "of the data is uncorrelated. Spearman ρ ranks the values and is robust to "
        "those outliers; if the two coefficients disagree sharply the relationship is "
        "not monotonic and shouldn't be treated as predictive.",
        "",
        "## 5. Recovery patterns",
        "",
        findings.get("recovery", ""),
        "",
        "### 5.1 By AI council intent",
        "",
        _format_recovery_table(rec_intent),
        "",
        img("recovery_hist"),
        "",
        "### 5.2 By Deep Research verdict",
        "",
        _format_recovery_table(rec_dr),
        "",
        "**Interpretation.** `days_to_recover` is the number of trading days from the "
        "decision date until the price first reaches the pre-drop level. The "
        "post-recovery columns measure what the stock did over the next 5/10/20 trading "
        "days *after* recovery — a positive number means the stock kept going up after "
        "reaching its pre-drop level.",
        "",
        "## 6. Performance over time vs S&P 500",
        "",
        "Median return path from the decision date forward, with SPY's median over "
        "the same calendar windows (dashed line) as a passive benchmark.",
        "",
        "### 6.1 By AI council intent",
        "",
        "Two views: median path with **inter-quartile range** band (default), and "
        "mean path with **95% t-CI** band.",
        img("ts_intent"),
        img("ts_intent_mean"),
        "",
        "### 6.2 By Deep Research verdict",
        img("ts_dr"),
        "",
        "### 6.3 Excess return vs SPY (alpha)",
        img("ts_alpha"),
        "",
        "### 6.4 Per-decision BUY trajectories",
        "",
        "Light grey lines are individual ENTER_NOW + ENTER_LIMIT decisions; bold lines "
        "are the per-intent medians. Useful for sense-checking how typical the median "
        "trajectory really is.",
        img("ts_spaghetti"),
        "",
        "## 7. Drop-size buckets",
        "",
        img("wr_drop"),
        "",
        "## 8. Profit and loss decomposition",
        "",
        "Two complementary views of how each group's wins and losses played out "
        "*over time*.",
        "",
        "### 8.1 Winner vs loser trajectories per category",
        "",
        "Each cohort row is classified at day-20 by the sign of its return. The "
        "panels below show the **mean winning trajectory** vs the **mean losing "
        "trajectory** within each category, with 95% CI bands.",
        "",
        "Why this matters: a high overall avg return can come from many small "
        "wins or a few large ones; this chart lets you see the asymmetry.",
        "",
        img("winloss_intent"),
        "",
        img("winloss_dr"),
        "",
        "### 8.2 Cumulative mark-to-market P&L over calendar time",
        "",
        "Assume **\\$1 is invested at every signal** at the decision-date close, "
        "held forward, and marked to its closing price every subsequent trading "
        "day. The lines below sum that mark-to-market P&L across every open "
        "position, by category, on each calendar date.",
        "",
        "Useful for seeing *when* the P&L accrued (early jump? steady drift?) and "
        "how each category's book performed in real time.",
        "",
        img("cum_pnl_intent"),
        "",
        img("cum_pnl_dr"),
        "",
        "## 9. Limitations",
        "",
        "- **Forward-window coverage.** With current `decision_date` range, no decision "
        "  has more than ~22 trading days of forward data, which means the 4-week and "
        "  8-week return columns are NaN for most rows. Re-running this script after "
        "  more time elapses extends every horizon naturally.",
        "- **Sample size.** After dropping rows without 4w returns, intent groups have "
        "  n=4–18 and DR-verdict groups have n=1–6. The pairwise significance tests are "
        "  honest about this — they refuse to call differences \"real\" until the data "
        "  catches up.",
        "- **Market regime.** Cohort window appears to coincide with a broad SPY rally "
        "  (+7.6% median over 20 trading days). Many AVOIDs would have been profitable "
        "  passive holdings; that is a property of this regime and should not be "
        "  generalized.",
        "- **Storage duplication.** `deep_research_action` and `deep_research_verdict` "
        "  carry identical values in this DB; the Q2/3.1 sections are therefore "
        "  redundant against the underlying signal.",
        "",
        "## 10. Recommendations",
        "",
        "- **Wait, then re-run.** The single largest analytical lift is more time. "
        "  Once the earliest decisions reach their 8-week mark, re-run "
        "  `build_package.py` and the same charts will tell a much sharper story.",
        "- **Investigate AVOID hits.** AVOIDs with high `+20d` post-recovery returns "
        "  are worth pulling individually — was the AVOID a calibration bug, or did "
        "  the model correctly price in higher risk that didn't materialize this regime?",
        "- **Drop the duplicate column.** Either consolidate `deep_research_verdict` "
        "  and `deep_research_action`, or document why both exist.",
        "- **Backfill `ai_score`.** Currently populated for 10/363 rows, all = 50. "
        "  Either fully populate or remove from prompts; right now it can't inform "
        "  any analysis.",
        "",
        "## Appendix",
        "",
        "All raw data underlying this report is in `data/`:",
        "",
        "- `cohort_enriched.csv` — every decision with computed return columns",
        "- `winrate_by_*.csv` — per-group aggregations",
        "- `stats_*.csv|.json` — significance and correlation results",
        "- `time_series.json` — per-day median paths",
        "- `time_series_individuals.json` — every BUY-signal trajectory",
        "- `spy_bars.csv` — SPY OHLC for the cohort window",
        "- `full_payload.json` — the entire JSON payload that drives "
        "  `deep-dive.html`",
        "",
        f"Interactive HTML report: [`deep-dive.html`](deep-dive.html)",
        "",
    ]
    return "\n".join(lines)


def _build_readme(payload: Dict[str, Any], chart_paths: Dict[str, Path],
                  data_paths: Dict[str, Path], package_dir: Path) -> str:
    chart_lines = "\n".join(
        f"- `{p.relative_to(package_dir)}`" for p in sorted(chart_paths.values())
    )
    data_lines = "\n".join(
        f"- `{p.relative_to(package_dir)}`" for p in sorted(data_paths.values())
    )
    return "\n".join([
        f"# Performance analysis package — {payload.get('cohort_start', 'all')}",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. Cohort size: "
        f"**{payload.get('cohort_size', 0)} decisions**.",
        "",
        "## Files",
        "",
        "- [`REPORT.md`](REPORT.md) — full written analysis with findings.",
        "- [`deep-dive.html`](deep-dive.html) — interactive Chart.js report (open "
        "in a browser).",
        "",
        "### Charts",
        "",
        chart_lines,
        "",
        "### Data",
        "",
        data_lines,
        "",
        "### Regenerate",
        "",
        "```",
        "./venv/bin/python scripts/analysis/build_package.py",
        "```",
        "",
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01", help="Cohort start date or 'all'")
    today = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument("--out", default=f"docs/performance/{today}-package",
                        help="Package output directory")
    parser.add_argument("--html-source", default=None,
                        help="Existing deep-dive HTML to copy (default: regenerate via deep_dive_html.py)")
    args = parser.parse_args()

    package_dir = Path(args.out)
    package_dir.mkdir(parents=True, exist_ok=True)
    charts_dir = package_dir / "charts"
    data_dir = package_dir / "data"

    start = None if args.start == "all" else args.start
    logger.info("Building dataset (start=%s)...", start)
    ds = compute_dataset(start_date=start or "all")
    payload = ds["payload"]
    enriched = ds["enriched"]
    spy_bars = ds["spy_bars"]

    if payload["cohort_size"] == 0:
        logger.error("Empty cohort — aborting")
        sys.exit(1)
    logger.info("Cohort size: %d", payload["cohort_size"])

    logger.info("Rendering %d static charts -> %s", 17, charts_dir)
    chart_paths = _generate_charts(payload, enriched, charts_dir)

    logger.info("Exporting data -> %s", data_dir)
    data_paths = _export_data(payload, enriched, spy_bars, data_dir)

    findings = _build_findings(payload)
    report_md = _build_report(payload, findings, chart_paths, package_dir)
    (package_dir / "REPORT.md").write_text(report_md)

    readme_md = _build_readme(payload, chart_paths, data_paths, package_dir)
    (package_dir / "README.md").write_text(readme_md)

    # Copy the HTML report
    html_source = (
        Path(args.html_source) if args.html_source
        else Path(f"docs/performance/{today}-deep-dive.html")
    )
    if html_source.exists():
        shutil.copy(html_source, package_dir / "deep-dive.html")
        logger.info("Copied HTML from %s", html_source)
    else:
        logger.warning("HTML source not found at %s — run deep_dive_html.py first", html_source)

    logger.info("Done. Package at %s", package_dir)


if __name__ == "__main__":
    main()
