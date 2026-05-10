"""Correlate Seeking Alpha rankings with the StockDrop cohort's realized returns.

Source file: SA_Quant_Ranked_Clean.csv (~3,958 stocks ranked by SA Quant).
Joins on symbol with our cohort's `return_1w / 2w / 4w` columns and tests
whether any SA-derived signal predicts our subsequent realized performance.

Signals tested:
  • SA Rank (1 = best, lower = better)
  • SA Quant Rating (1.0 .. 5.0 numeric, parsed from "Rating: Strong Buy4.99")
  • SA Analyst Rating (same parse, separate column)
  • Wall Street Rating (same parse)
  • Past 6-month performance (their own historical proxy)

Outputs:
  • Pearson + Spearman correlations at 1w / 2w / 4w horizons
  • Quartile bucketing: avg return per SA-Rank quartile
  • Quant-score bucketing: <3 / 3-3.5 / 3.5-4 / 4-4.5 / 4.5+
  • Scatter chart: SA Rank vs return_1w with OLS line
  • Merged ledger CSV
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats as scipy_stats  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.payload import compute_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sa_corr")

DEFAULT_SA_PATH = Path(
    "/Users/simonbaumgart/Documents/Claude/Projects/"
    "Investment Ideas and Portfolio/SA_Quant_Ranked_Clean.csv"
)


def parse_rating(s):
    """Extract numeric rating tail from strings like 'Rating: Strong Buy4.99'."""
    if pd.isna(s):
        return np.nan
    m = re.search(r"([0-9]+\.?[0-9]*)\s*$", str(s))
    return float(m.group(1)) if m else np.nan


def correlate(merged: pd.DataFrame, x_col: str, label: str, horizons=("1w", "2w", "4w")) -> list:
    rows = []
    for h in horizons:
        ycol = f"return_{h}"
        sub = merged.dropna(subset=[x_col, ycol])
        n = len(sub)
        if n < 5:
            rows.append({"signal": label, "horizon": h, "n": n,
                         "pearson_r": None, "pearson_p": None,
                         "spearman_rho": None, "spearman_p": None})
            continue
        pr = scipy_stats.pearsonr(sub[x_col], sub[ycol])
        sp = scipy_stats.spearmanr(sub[x_col], sub[ycol])
        rows.append({
            "signal": label, "horizon": h, "n": n,
            "pearson_r": float(pr.statistic),
            "pearson_p": float(pr.pvalue),
            "spearman_rho": float(sp.statistic),
            "spearman_p": float(sp.pvalue),
        })
    return rows


def render_scatter(merged: pd.DataFrame, x_col: str, y_col: str,
                   x_label: str, y_label: str, title: str, out_path: Path) -> Path:
    sub = merged.dropna(subset=[x_col, y_col])
    if len(sub) < 5:
        return out_path
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.scatter(sub[x_col], sub[y_col] * 100, s=22, alpha=0.55,
               color="#3b82f6", edgecolor="none")
    if sub[x_col].std() > 0:
        slope, intercept = np.polyfit(sub[x_col], sub[y_col], 1)
        xs = np.linspace(sub[x_col].min(), sub[x_col].max(), 50)
        ax.plot(xs, (slope * xs + intercept) * 100, color="#fbbf24",
                linewidth=2, label=f"OLS fit  slope={slope:+.4f}")
    pr = scipy_stats.pearsonr(sub[x_col], sub[y_col])
    sp = scipy_stats.spearmanr(sub[x_col], sub[y_col])
    annot = (f"n={len(sub)}\n"
             f"Pearson r={pr.statistic:+.3f} (p={pr.pvalue:.3f})\n"
             f"Spearman ρ={sp.statistic:+.3f} (p={sp.pvalue:.3f})")
    ax.text(0.02, 0.98, annot, transform=ax.transAxes, va="top",
            fontsize=9, family="monospace",
            bbox=dict(facecolor="white", alpha=0.9, edgecolor="#cbd5e1"))
    ax.axhline(0, color="black", linewidth=0.4, linestyle=":")
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.3)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sa-file", default=str(DEFAULT_SA_PATH))
    parser.add_argument("--start", default="2026-02-01")
    args = parser.parse_args()

    sa_path = Path(args.sa_file)
    if not sa_path.exists():
        logger.error("SA file not found: %s", sa_path)
        sys.exit(1)

    logger.info("Loading SA rankings from %s", sa_path)
    sa = pd.read_csv(sa_path)
    sa["quant_score"] = sa["Quant Rating"].apply(parse_rating)
    sa["sa_score"]    = sa["SA Analyst Ratings"].apply(parse_rating)
    sa["ws_score"]    = sa["Wall Street Ratings"].apply(parse_rating)
    sa["perf_6m"]     = pd.to_numeric(sa["6M Perf"], errors="coerce")
    sa["sym"]         = sa["Symbol"].astype(str).str.upper().str.strip()

    logger.info("Loading cohort...")
    ds = compute_dataset(start_date=args.start)
    df = ds["enriched"].copy()
    df["sym"] = df["symbol"].astype(str).str.upper().str.strip()

    merged = df.merge(
        sa[["sym", "Rank", "quant_score", "sa_score", "ws_score", "perf_6m"]],
        on="sym", how="inner",
    )
    n_total = len(merged)
    n_1w = merged["return_1w"].notna().sum()
    n_4w = merged["return_4w"].notna().sum()
    print("=" * 96)
    print(f"  COHORT × SA QUANT — overlap: {n_total} symbols")
    print(f"  with return_1w: {n_1w}  |  with return_4w: {n_4w}")
    print("=" * 96)
    print()

    # Correlations
    all_rows = []
    for x, label in [
        ("Rank",         "SA Rank (lower=better)"),
        ("quant_score",  "SA Quant numeric"),
        ("sa_score",     "SA Analyst Rating"),
        ("ws_score",     "Wall Street Rating"),
        ("perf_6m",      "Past 6M performance"),
    ]:
        all_rows.extend(correlate(merged, x, label))
    corr_df = pd.DataFrame(all_rows)
    print("CORRELATIONS — Pearson + Spearman with realized return at each horizon")
    print("-" * 96)
    print(f"  {'signal':<28s} {'horizon':<8s} {'n':>4s} "
          f"{'Pearson r':>11s} {'p':>7s} {'Spearman ρ':>12s} {'p':>7s}")
    for _, r in corr_df.iterrows():
        if r["pearson_r"] is None:
            print(f"  {r['signal']:<28s} {r['horizon']:<8s} {int(r['n']):>4d}  too few")
            continue
        flag = "  ✓" if min(r["pearson_p"] or 1, r["spearman_p"] or 1) < 0.05 else ""
        print(f"  {r['signal']:<28s} {r['horizon']:<8s} {int(r['n']):>4d} "
              f"{r['pearson_r']:>+10.3f} {r['pearson_p']:>7.3f} "
              f"{r['spearman_rho']:>+11.3f} {r['spearman_p']:>7.3f}{flag}")

    # Quartile bucketing on SA Rank
    print()
    print("=" * 96)
    print("RETURN BY SA RANK QUARTILE (1w realized return)")
    print("=" * 96)
    m = merged.dropna(subset=["return_1w", "Rank"]).copy()
    if not m.empty:
        m["rank_bucket"] = pd.qcut(
            m["Rank"], 4,
            labels=["Q1 best (low rank)", "Q2", "Q3", "Q4 worst (high rank)"],
            duplicates="drop",
        )
        agg = m.groupby("rank_bucket", observed=True).agg(
            n=("return_1w", "size"),
            win_rate=("return_1w", lambda x: (x > 0).mean()),
            mean_return=("return_1w", "mean"),
            median_return=("return_1w", "median"),
            std_return=("return_1w", "std"),
        ).reset_index()
        for _, r in agg.iterrows():
            print(f"  {str(r['rank_bucket']):<25s} n={int(r['n']):3d}  "
                  f"win_rate={r['win_rate']:.1%}  mean={r['mean_return']*100:+.2f}%  "
                  f"median={r['median_return']*100:+.2f}%  std={r['std_return']*100:.2f}%")

    # Quant-score bucketing
    print()
    print("=" * 96)
    print("RETURN BY SA QUANT SCORE BAND (1w realized return)")
    print("=" * 96)
    m["qs_bucket"] = pd.cut(
        m["quant_score"],
        bins=[0, 3.0, 3.5, 4.0, 4.5, 5.001],
        labels=["<3.0 Sell/Hold", "3.0–3.5", "3.5–4.0 Buy", "4.0–4.5", "4.5+ Strong Buy"],
    )
    agg2 = m.groupby("qs_bucket", observed=True).agg(
        n=("return_1w", "size"),
        win_rate=("return_1w", lambda x: (x > 0).mean()),
        mean_return=("return_1w", "mean"),
        median_return=("return_1w", "median"),
    ).reset_index()
    for _, r in agg2.iterrows():
        print(f"  {str(r['qs_bucket']):<22s} n={int(r['n']):3d}  "
              f"win_rate={r['win_rate']:.1%}  mean={r['mean_return']*100:+.2f}%  "
              f"median={r['median_return']*100:+.2f}%")

    # Cross with our PM intent
    print()
    print("=" * 96)
    print("RETURN BY SA QUANT × PM INTENT (1w)")
    print("=" * 96)
    m["qs_simple"] = pd.cut(
        m["quant_score"], bins=[0, 3.5, 5.001],
        labels=["SA: Sell/Hold (<3.5)", "SA: Buy (>=3.5)"],
    )
    cross = m.groupby(["intent", "qs_simple"], observed=True).agg(
        n=("return_1w", "size"),
        mean_return=("return_1w", "mean"),
        win_rate=("return_1w", lambda x: (x > 0).mean()),
    ).reset_index()
    for _, r in cross.iterrows():
        print(f"  {str(r['intent']):<14s} × {str(r['qs_simple']):<24s} "
              f"n={int(r['n']):3d}  mean={r['mean_return']*100:+.2f}%  "
              f"win_rate={r['win_rate']:.1%}")

    # Save artifacts
    out_dir = REPO_ROOT / "docs" / "performance" / f"{datetime.now():%Y-%m-%d}-package"
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_path = out_dir / "data" / "sa_ranking_merged.csv"
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    keep = ["sym", "decision_date", "intent", "recommendation", "risk_reward_ratio",
            "Rank", "quant_score", "sa_score", "ws_score", "perf_6m",
            "return_1w", "return_2w", "return_4w"]
    merged[[c for c in keep if c in merged.columns]].to_csv(merged_path, index=False)

    corr_path = out_dir / "data" / "sa_ranking_correlations.csv"
    corr_df.to_csv(corr_path, index=False)

    chart_dir = out_dir / "charts"
    render_scatter(
        merged, "Rank", "return_1w",
        "SA Rank (1 = best)", "1-week return (%)",
        "SA Rank vs realized 1-week return",
        chart_dir / "30_sa_rank_vs_return_1w.png",
    )
    render_scatter(
        merged, "ws_score", "return_1w",
        "Wall Street Rating (1=Sell, 5=Strong Buy)", "1-week return (%)",
        "Wall Street Rating vs realized 1-week return",
        chart_dir / "31_wall_street_rating_vs_return_1w.png",
    )
    render_scatter(
        merged, "quant_score", "return_1w",
        "SA Quant Rating (1=Sell, 5=Strong Buy)", "1-week return (%)",
        "SA Quant Rating vs realized 1-week return",
        chart_dir / "32_sa_quant_score_vs_return_1w.png",
    )
    render_scatter(
        merged, "perf_6m", "return_1w",
        "Past 6-month performance (decimal)", "1-week return (%)",
        "Past 6M performance vs realized 1-week return",
        chart_dir / "33_perf_6m_vs_return_1w.png",
    )

    print()
    print(f"Saved merged ledger to {merged_path}")
    print(f"Saved correlation table to {corr_path}")
    print(f"Saved scatter charts to {chart_dir}/30..33")


if __name__ == "__main__":
    main()
