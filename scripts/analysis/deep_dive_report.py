"""Generate the StockDrop performance deep-dive report.

Usage:
    python scripts/analysis/deep_dive_report.py [--start 2026-02-01] [--out PATH]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.analytics.aggregations import (  # noqa: E402
    equity_curve,
    time_to_recover_dist,
    winrate_by,
    winrate_by_bucket,
)
from app.services.analytics.charts import (  # noqa: E402
    avg_return_bar,
    equity_line,
    hist_bar,
    winrate_bar,
)
from app.services.analytics.cohort import load_cohort  # noqa: E402
from app.services.analytics.outcomes import HORIZON_DAYS, enrich_outcomes  # noqa: E402
from app.services.analytics.price_cache import prefetch  # noqa: E402
from app.services.analytics.report import (  # noqa: E402
    Section,
    df_to_md,
    img_link,
    render_report,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("deep_dive")


def _build_sections(df: pd.DataFrame, charts_dir: Path, report_dir: Path):
    sections = []

    # Headline equity curve
    eq = equity_curve(df[df["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])], horizon="4w")
    eq_chart = equity_line(
        eq,
        "Equity curve — equal-weight all BUY/BUY_LIMIT decisions (4w returns)",
        charts_dir / "headline_equity_curve.png",
    )
    sections.append(
        Section(
            "Headline equity curve",
            img_link(eq_chart, report_dir)
            + "\n\n_Equal-weight cumulative growth assuming each ENTER_NOW/ENTER_LIMIT "
              "decision is held for 4 weeks._",
        )
    )

    # Q1 — Verdict accuracy at all horizons
    verdict_aggs = {h: winrate_by(df, "intent", horizon=h) for h in HORIZON_DAYS}
    chart_path = winrate_bar(
        verdict_aggs["4w"],
        "intent",
        "Win rate by PM intent (4w horizon)",
        charts_dir / "q1_winrate_by_intent_4w.png",
    )
    avg_chart = avg_return_bar(
        verdict_aggs["4w"],
        "intent",
        "Avg 4w return by intent",
        charts_dir / "q1_avg_return_by_intent_4w.png",
    )
    pivot_rows = []
    for h, agg in verdict_aggs.items():
        if not agg.empty:
            for _, r in agg.iterrows():
                pivot_rows.append({
                    "horizon": h,
                    "intent": r["intent"],
                    "n": r["count"],
                    "win_rate": r["win_rate"],
                    "avg_return": r["avg_return"],
                })
    body = [
        "**Verdict win-rate at 4 weeks:**",
        "",
        df_to_md(verdict_aggs["4w"]),
        "",
        img_link(chart_path, report_dir),
        "",
        img_link(avg_chart, report_dir),
        "",
        "**At each horizon (1w / 2w / 4w / 8w):**",
        "",
    ]
    if pivot_rows:
        body.append(df_to_md(pd.DataFrame(pivot_rows)))
    sections.append(Section("Q1 — Verdict accuracy", "\n".join(body)))

    # Q2 — Deep Research override value
    if "deep_research_action" in df.columns and df["deep_research_action"].notna().any():
        dr_agg = winrate_by(df, "deep_research_action", horizon="4w")
        c1 = winrate_bar(
            dr_agg, "deep_research_action",
            "Win rate by DR action (4w)",
            charts_dir / "q2_winrate_by_dr_action.png",
        )
        c2 = avg_return_bar(
            dr_agg, "deep_research_action",
            "Avg 4w return by DR action",
            charts_dir / "q2_avg_return_by_dr_action.png",
        )
        body = [
            "**Outcome by Deep Research action:**", "",
            df_to_md(dr_agg), "",
            img_link(c1, report_dir), "",
            img_link(c2, report_dir),
        ]
    else:
        body = ["_no DR action data in cohort_"]
    sections.append(Section("Q2 — Deep Research override value", "\n".join(body)))

    # Q3 — Per-agent / signal strength
    body = []
    if "deep_research_verdict" in df.columns and df["deep_research_verdict"].notna().any():
        dv_agg = winrate_by(df, "deep_research_verdict", horizon="4w")
        c = winrate_bar(
            dv_agg, "deep_research_verdict",
            "Win rate by DR verdict (4w)",
            charts_dir / "q3_winrate_by_dr_verdict.png",
        )
        body += ["**By DR verdict:**", "", df_to_md(dv_agg), "", img_link(c, report_dir), ""]
    if "ai_score" in df.columns and df["ai_score"].notna().any():
        # ai_score is stored on a 0-100 scale in the DB
        ai_agg = winrate_by_bucket(
            df, "ai_score",
            bins=[-0.001, 40, 60, 80, 100.001],
            labels=["<40", "40-60", "60-80", ">80"],
            horizon="4w",
        )
        c = winrate_bar(
            ai_agg, "bucket",
            "Win rate by AI score bucket (4w)",
            charts_dir / "q3_winrate_by_ai_score.png",
        )
        body += ["**By AI score bucket:**", "", df_to_md(ai_agg), "", img_link(c, report_dir), ""]
    if not body:
        body = [
            "_per-agent breakdown requires per-agent score columns; current cohort exposes "
            "only DR verdict and ai_score._"
        ]
    sections.append(Section("Q3 — Per-agent signal strength", "\n".join(body)))

    # Q4 — Gatekeeper
    if "gatekeeper_tier" in df.columns and df["gatekeeper_tier"].notna().any():
        g_agg = winrate_by(df, "gatekeeper_tier", horizon="4w")
        c = winrate_bar(
            g_agg, "gatekeeper_tier",
            "Win rate by gatekeeper tier (4w)",
            charts_dir / "q4_winrate_by_gatekeeper.png",
        )
        body = ["**By gatekeeper tier:**", "", df_to_md(g_agg), "", img_link(c, report_dir)]
    else:
        body = ["_no gatekeeper_tier data in cohort_"]
    sections.append(Section("Q4 — Gatekeeper calibration", "\n".join(body)))

    # Q5 — Sector / regime
    if "sector" in df.columns and df["sector"].notna().any():
        s_agg = winrate_by(df, "sector", horizon="4w")
        c = winrate_bar(
            s_agg, "sector",
            "Win rate by sector (4w)",
            charts_dir / "q5_winrate_by_sector.png",
        )
        body = ["**By sector (4w):**", "", df_to_md(s_agg), "", img_link(c, report_dir)]
    else:
        body = ["_no sector data in cohort_"]
    sections.append(Section("Q5 — Regime / sector conditioning", "\n".join(body)))

    # Q6 — BUY_LIMIT execution
    body_lines = []
    if "limit_filled" in df.columns:
        limits = df[df["intent"] == "ENTER_LIMIT"].copy()
        n_total = len(limits)
        n_filled = int(limits["limit_filled"].fillna(False).sum())
        pct = (n_filled / n_total * 100.0) if n_total else 0.0
        body_lines.append(
            f"**BUY_LIMIT decisions:** {n_total}; filled within 4w: **{n_filled}** "
            f"({pct:.1f}%)"
        )
        body_lines.append("")
        if n_filled > 0 and "return_filled_4w" in limits.columns:
            sub = limits.dropna(subset=["return_filled_4w"])
            if not sub.empty:
                body_lines += [
                    f"**Of filled BUY_LIMITs, avg 4w return at fill price:** "
                    f"{sub['return_filled_4w'].mean():.2%}",
                    f"**Median:** {sub['return_filled_4w'].median():.2%}",
                    f"**Win rate:** {(sub['return_filled_4w'] > 0).mean():.0%}",
                    "",
                ]
    if not body_lines:
        body_lines = ["_no BUY_LIMIT rows or fill simulation produced no data_"]
    sections.append(Section("Q6 — BUY_LIMIT execution", "\n".join(body_lines)))

    # Q7 — Drop-size sweet spot
    drop_agg = winrate_by_bucket(
        df,
        "drop_percent",
        bins=[-100, -15, -8, -5, 0],
        labels=["<= -15%", "-15 to -8", "-8 to -5", "> -5%"],
        horizon="4w",
    )
    c = winrate_bar(
        drop_agg, "bucket",
        "Win rate by drop size (4w)",
        charts_dir / "q7_winrate_by_drop_size.png",
    )
    body = ["**By drop-% bucket (4w):**", "", df_to_md(drop_agg), "", img_link(c, report_dir)]
    sections.append(Section("Q7 — Drop-size sweet spot", "\n".join(body)))

    # Q8 — Time-to-recovery
    rec = time_to_recover_dist(df, max_days=40)
    if not rec.empty:
        c = hist_bar(
            rec,
            "Days to recovery (capped at 40)",
            "trading days",
            charts_dir / "q8_time_to_recover.png",
        )
        median_days = df["days_to_recover"].median()
        body = [
            f"**Recovered decisions:** {int(rec.sum())}",
            f"**Median days to recover:** {median_days:.1f}",
            "",
            img_link(c, report_dir),
        ]
    else:
        body = [
            "_no recovery data — likely all decisions still inside their 8-week window "
            "or pre_drop_price unknown._"
        ]
    sections.append(Section("Q8 — Time-to-recovery distribution", "\n".join(body)))

    return sections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-02-01",
                        help="Cohort start date (or 'all')")
    today = datetime.now().strftime("%Y-%m-%d")
    parser.add_argument("--out", default=f"docs/performance/{today}-deep-dive.md")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit cohort size for fast iteration")
    parser.add_argument("--skip-appendix", action="store_true",
                        help="Skip the full-history sensitivity appendix")
    args = parser.parse_args()

    start = None if args.start == "all" else args.start
    logger.info("Loading cohort (start=%s)...", start)
    df = load_cohort(start_date=start)
    if args.limit:
        df = df.head(args.limit)
    logger.info("Cohort size: %d", len(df))
    if df.empty:
        logger.error("Empty cohort — aborting")
        sys.exit(1)

    end = pd.Timestamp.now().normalize()
    span_start = df["decision_date"].min()
    logger.info(
        "Prefetching bars for %d unique tickers (%s -> %s)...",
        df["symbol"].nunique(), span_start.date(), end.date(),
    )
    bars = prefetch(
        df["symbol"].dropna().unique().tolist(),
        start=span_start,
        end=end + pd.Timedelta(days=2),
    )

    logger.info("Computing outcomes...")
    enriched = enrich_outcomes(df, bars)

    out_path = Path(args.out)
    charts_dir = out_path.parent / "charts" / out_path.stem
    cohort_label = f"cohort >= {start}" if start else "full history"

    logger.info("Building sections + charts at %s ...", charts_dir)
    sections = _build_sections(enriched, charts_dir=charts_dir, report_dir=out_path.parent)

    appendix = []
    if start is not None and not args.skip_appendix:
        full = load_cohort(start_date=None)
        if args.limit:
            full = full.head(args.limit)
        if not full.empty:
            full_bars = prefetch(
                full["symbol"].dropna().unique().tolist(),
                start=full["decision_date"].min(),
                end=end + pd.Timedelta(days=2),
            )
            full_enriched = enrich_outcomes(full, full_bars)
            full_agg = winrate_by(full_enriched, "intent", horizon="4w")
            appendix.append(
                Section(
                    "Sensitivity: full-history Q1 (verdict accuracy 4w)",
                    df_to_md(full_agg),
                )
            )

    logger.info("Rendering report -> %s", out_path)
    render_report(out_path, cohort_label, len(enriched), sections, appendix=appendix)
    logger.info("Done.")


if __name__ == "__main__":
    main()
