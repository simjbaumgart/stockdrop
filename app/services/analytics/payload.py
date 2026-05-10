"""Build a single JSON-serializable payload describing cohort performance.

Used by the offline HTML report generator. Pure function — no caching,
no FastAPI dependency.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.services.analytics.aggregations import (
    equity_curve,
    time_to_recover_dist,
    winrate_by,
    winrate_by_bucket,
)
from app.services.analytics.cohort import load_cohort
from app.services.analytics.intervals import mean_ci
from app.services.analytics.outcomes import HORIZON_DAYS, enrich_outcomes
from app.services.analytics.price_cache import get_bars, prefetch
from app.services.analytics.stats import (
    correlation,
    pairwise_welch,
    recovery_stats,
    rr_by_group,
    top_rr_decisions,
)

logger = logging.getLogger(__name__)


def _df_records(df: pd.DataFrame, columns: Optional[List[str]] = None) -> List[dict]:
    if df is None or df.empty:
        return []
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    out = []
    for _, row in df.iterrows():
        rec = {}
        for k, v in row.items():
            if pd.isna(v):
                rec[str(k)] = None
            elif isinstance(v, pd.Timestamp):
                rec[str(k)] = v.strftime("%Y-%m-%d")
            elif hasattr(v, "item"):
                rec[str(k)] = v.item()
            else:
                rec[str(k)] = v
        out.append(rec)
    return out


def _time_series_by_group(
    cohort: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    group_col: str,
    max_days: int = 40,
    include_individuals: bool = False,
) -> Dict[str, Any]:
    """For each value of `group_col`, build the median post-decision return path.

    For every cohort row we read `max_days+1` daily closes from the cached bars,
    starting at the decision date, and turn them into pct-returns vs the
    decision price. We then aggregate (median, q25, q75) per day-offset within
    each group.
    """
    if cohort.empty:
        return {}

    paths_by_group: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for _, row in cohort.iterrows():
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        bars = bars_by_ticker.get(sym, pd.DataFrame())
        if bars is None or bars.empty:
            continue
        bars = bars.sort_index()
        forward = bars.loc[bars.index >= row["decision_date"]]
        if forward.empty:
            continue
        closes = forward["Close"].astype(float).iloc[: max_days + 1]
        if len(closes) < 2:
            continue
        try:
            decision_price = float(row.get("price_at_decision") or 0)
        except (TypeError, ValueError):
            continue
        if decision_price <= 0:
            continue

        rets = [(float(c) / decision_price - 1.0) for c in closes.tolist()]

        group_value = row.get(group_col)
        if group_value is None or (isinstance(group_value, float) and pd.isna(group_value)):
            continue

        paths_by_group[str(group_value)].append({
            "symbol": sym,
            "decision_date": row["decision_date"].strftime("%Y-%m-%d"),
            "returns": rets,
        })

    out: Dict[str, Any] = {}
    for grp, paths in paths_by_group.items():
        max_len = max(len(p["returns"]) for p in paths)
        per_day: List[List[float]] = [[] for _ in range(max_len)]
        for p in paths:
            for d, r in enumerate(p["returns"]):
                per_day[d].append(r)
        # Per-day mean / SE / 95% t-CI alongside median + quartiles
        means, ses, ci_lows, ci_highs = [], [], [], []
        for x in per_day:
            ci = mean_ci(x)
            means.append(ci["mean"])
            ses.append(ci["se"])
            ci_lows.append(ci["ci_low"])
            ci_highs.append(ci["ci_high"])

        out[grp] = {
            "day_offsets": list(range(max_len)),
            "median": [float(np.median(x)) if x else None for x in per_day],
            "q25": [float(np.percentile(x, 25)) if x else None for x in per_day],
            "q75": [float(np.percentile(x, 75)) if x else None for x in per_day],
            "mean": means,
            "se": ses,
            "ci_low": ci_lows,
            "ci_high": ci_highs,
            "count": [len(x) for x in per_day],
            "n_paths": len(paths),
        }
        if include_individuals:
            out[grp]["individuals"] = paths
    return out


def _winloss_split_by_group(
    cohort: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    group_col: str,
    horizon_label: str = "4w",
    horizon_day: int = 20,
    max_days: int = 40,
) -> Dict[str, Any]:
    """Split each group's per-day mean return path into winners vs losers.

    A row is classified by the sign of its return at `horizon_day` (or last
    available day if it isn't yet old enough). The output mirrors the shape of
    `_time_series_by_group` for each subgroup so the JS / matplotlib renderers
    can reuse the same chart code.

    Returns:
        {
            group_value: {
                "winners":  {day_offsets, mean, ci_low, ci_high, count, n_paths},
                "losers":   {...},
                "n_winners": int,
                "n_losers":  int,
                "horizon_label": str,
            },
            ...
        }
    """
    if cohort.empty:
        return {}

    paths_by_group: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for _, row in cohort.iterrows():
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        bars = bars_by_ticker.get(sym, pd.DataFrame())
        if bars is None or bars.empty:
            continue
        bars = bars.sort_index()
        forward = bars.loc[bars.index >= row["decision_date"]]
        if forward.empty:
            continue
        closes = forward["Close"].astype(float).iloc[: max_days + 1]
        if len(closes) < 2:
            continue
        try:
            decision_price = float(row.get("price_at_decision") or 0)
        except (TypeError, ValueError):
            continue
        if decision_price <= 0:
            continue
        rets = [(float(c) / decision_price - 1.0) for c in closes.tolist()]

        group_value = row.get(group_col)
        if group_value is None or (isinstance(group_value, float) and pd.isna(group_value)):
            continue

        # Classify by the sign of the return at horizon_day (or final available)
        idx = min(horizon_day, len(rets) - 1)
        final_ret = rets[idx]
        side = "winners" if final_ret > 0 else "losers"
        paths_by_group[(str(group_value), side)].append({
            "symbol": sym,
            "decision_date": row["decision_date"].strftime("%Y-%m-%d"),
            "final_ret": final_ret,
            "returns": rets,
        })

    out: Dict[str, Any] = {}
    groups = sorted({g for g, _ in paths_by_group.keys()})
    for grp in groups:
        out[grp] = {"horizon_label": horizon_label, "n_winners": 0, "n_losers": 0}
        for side in ("winners", "losers"):
            paths = paths_by_group.get((grp, side), [])
            if not paths:
                out[grp][side] = None
                continue
            max_len = max(len(p["returns"]) for p in paths)
            per_day = [[] for _ in range(max_len)]
            for p in paths:
                for d, r in enumerate(p["returns"]):
                    per_day[d].append(r)
            means, ci_lows, ci_highs = [], [], []
            for x in per_day:
                ci = mean_ci(x)
                means.append(ci["mean"])
                ci_lows.append(ci["ci_low"])
                ci_highs.append(ci["ci_high"])
            out[grp][side] = {
                "day_offsets": list(range(max_len)),
                "mean": means,
                "ci_low": ci_lows,
                "ci_high": ci_highs,
                "count": [len(x) for x in per_day],
                "n_paths": len(paths),
            }
            out[grp][f"n_{side}"] = len(paths)
    return out


def _cumulative_pnl_by_calendar(
    cohort: pd.DataFrame,
    bars_by_ticker: Dict[str, pd.DataFrame],
    group_col: str,
    initial_per_signal: float = 1.0,
) -> Dict[str, Any]:
    """Cumulative dollar P&L per group over actual calendar dates.

    For each row, a `initial_per_signal` long position is opened at the
    decision-date close and marked-to-market every subsequent trading day.
    The result is a per-group time series of total mark-to-market P&L,
    summed across all open positions on that calendar date.
    """
    if cohort.empty:
        return {}

    series_by_group: Dict[str, List[Tuple[pd.Timestamp, float]]] = defaultdict(list)
    counts_by_group: Dict[str, int] = defaultdict(int)

    for _, row in cohort.iterrows():
        grp = row.get(group_col)
        if grp is None or (isinstance(grp, float) and pd.isna(grp)):
            continue
        grp = str(grp)
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        bars = bars_by_ticker.get(sym, pd.DataFrame())
        if bars is None or bars.empty:
            continue
        bars = bars.sort_index()
        forward = bars.loc[bars.index >= row["decision_date"]]
        if forward.empty:
            continue
        try:
            entry = float(row.get("price_at_decision") or 0)
        except (TypeError, ValueError):
            continue
        if entry <= 0:
            continue
        counts_by_group[grp] += 1
        for ts, close in forward["Close"].astype(float).items():
            pnl = initial_per_signal * (float(close) / entry - 1.0)
            series_by_group[grp].append((pd.Timestamp(ts).normalize(), pnl))

    out: Dict[str, Any] = {}
    for grp, rows in series_by_group.items():
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["date", "pnl"])
        # Sum across all open positions per calendar date
        agg = df.groupby("date", as_index=False)["pnl"].sum().sort_values("date")
        out[grp] = {
            "dates": [d.strftime("%Y-%m-%d") for d in agg["date"]],
            "cumulative_pnl": [float(v) for v in agg["pnl"]],
            "n_signals": int(counts_by_group[grp]),
        }
    return out


def _spy_overlay(
    cohort: pd.DataFrame,
    spy_bars: pd.DataFrame,
    max_days: int = 40,
) -> Dict[str, Any]:
    """For each cohort decision date, build SPY's normalized return path; aggregate.

    Returns the same shape as a `_time_series_by_group` group entry so the JS can
    treat SPY as just another series.
    """
    if cohort.empty or spy_bars is None or spy_bars.empty:
        return {}
    spy_bars = spy_bars.sort_index()
    paths: List[List[float]] = []

    for decision_date in cohort["decision_date"].dropna().unique():
        forward = spy_bars.loc[spy_bars.index >= decision_date]
        if forward.empty:
            continue
        closes = forward["Close"].astype(float).iloc[: max_days + 1]
        if len(closes) < 2:
            continue
        base = float(closes.iloc[0])
        if base <= 0:
            continue
        # weight by number of cohort decisions on this date so the SPY median
        # matches the time-weighting of the per-intent medians
        weight = int((cohort["decision_date"] == decision_date).sum())
        rets = [(float(c) / base - 1.0) for c in closes.tolist()]
        for _ in range(weight):
            paths.append(rets)

    if not paths:
        return {}

    max_len = max(len(p) for p in paths)
    per_day: List[List[float]] = [[] for _ in range(max_len)]
    for p in paths:
        for d, r in enumerate(p):
            per_day[d].append(r)

    means, ses, ci_lows, ci_highs = [], [], [], []
    for x in per_day:
        ci = mean_ci(x)
        means.append(ci["mean"])
        ses.append(ci["se"])
        ci_lows.append(ci["ci_low"])
        ci_highs.append(ci["ci_high"])

    return {
        "day_offsets": list(range(max_len)),
        "median": [float(np.median(x)) if x else None for x in per_day],
        "q25": [float(np.percentile(x, 25)) if x else None for x in per_day],
        "q75": [float(np.percentile(x, 75)) if x else None for x in per_day],
        "mean": means,
        "se": ses,
        "ci_low": ci_lows,
        "ci_high": ci_highs,
        "count": [len(x) for x in per_day],
        "n_paths": len(paths),
    }


def _stats_records(stats_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert a stats DataFrame to list of dicts; bools survive _df_records cleanly."""
    return _df_records(stats_df)


def compute_dataset(start_date: str = "2026-02-01") -> Dict[str, Any]:
    """Build everything: enriched cohort, raw bars, SPY bars, and the JSON payload.

    Returns a dict with keys:
        - "enriched": pd.DataFrame with one row per decision plus all derived columns.
        - "bars": dict[ticker -> OHLC DataFrame].
        - "spy_bars": pd.DataFrame of SPY OHLC over the cohort window.
        - "payload": the JSON-serializable dict consumed by the HTML report.

    `build_payload` is a thin wrapper that returns just `payload` for back-compat.
    """
    df = load_cohort(start_date=start_date)
    if df.empty:
        empty_payload = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "cohort_size": 0,
            "cohort_start": start_date,
            "headline": {},
            "winrate_by_intent": [],
            "winrate_by_horizon": [],
            "winrate_by_drop_bucket": [],
            "winrate_by_dr_action": [],
            "winrate_by_gatekeeper": [],
            "winrate_by_sector": [],
            "equity_curve": [],
            "time_to_recover": [],
            "decisions": [],
        }
        return {
            "enriched": df,
            "bars": {},
            "spy_bars": pd.DataFrame(),
            "payload": empty_payload,
        }

    end = pd.Timestamp.now().normalize()
    bars = prefetch(
        df["symbol"].dropna().unique().tolist(),
        start=df["decision_date"].min(),
        end=end + pd.Timedelta(days=2),
    )
    enriched = enrich_outcomes(df, bars)

    buys = enriched[enriched["intent"].isin(["ENTER_NOW", "ENTER_LIMIT"])]
    buys_4w = buys.dropna(subset=["return_4w"])
    win_rate_buys = float((buys_4w["return_4w"] > 0).mean()) if not buys_4w.empty else None
    avg_return_buys = float(buys_4w["return_4w"].mean()) if not buys_4w.empty else None

    limits = enriched[enriched["intent"] == "ENTER_LIMIT"].copy()
    n_limits = len(limits)
    n_filled = (
        int(limits["limit_filled"].fillna(False).astype(bool).sum())
        if "limit_filled" in limits.columns else 0
    )
    fill_rate = (n_filled / n_limits) if n_limits else None
    avg_filled_4w = None
    if "return_filled_4w" in limits.columns:
        sub = limits.dropna(subset=["return_filled_4w"])
        if not sub.empty:
            avg_filled_4w = float(sub["return_filled_4w"].mean())

    rec_series = (
        enriched["days_to_recover"].dropna()
        if "days_to_recover" in enriched.columns else pd.Series(dtype=float)
    )
    median_days = float(rec_series.median()) if not rec_series.empty else None
    n_recovered = int(rec_series.size)

    horizon_rows = []
    for h in HORIZON_DAYS:
        agg = winrate_by(enriched, "intent", horizon=h)
        if not agg.empty:
            for _, r in agg.iterrows():
                horizon_rows.append({
                    "horizon": h,
                    "intent": r["intent"],
                    "n": int(r["count"]),
                    "win_rate": float(r["win_rate"]),
                    "avg_return": float(r["avg_return"]),
                })

    drop_bucket = winrate_by_bucket(
        enriched, "drop_percent",
        bins=[-100, -15, -8, -5, 0],
        labels=["<= -15%", "-15 to -8", "-8 to -5", "> -5%"],
        horizon="4w",
    )
    if not drop_bucket.empty:
        drop_bucket["bucket"] = drop_bucket["bucket"].astype(str)

    dr_action = pd.DataFrame()
    if "deep_research_action" in enriched.columns and enriched["deep_research_action"].notna().any():
        dr_action = winrate_by(enriched, "deep_research_action", horizon="4w")

    gatekeeper = pd.DataFrame()
    if "gatekeeper_tier" in enriched.columns and enriched["gatekeeper_tier"].notna().any():
        gatekeeper = winrate_by(enriched, "gatekeeper_tier", horizon="4w")

    sector = pd.DataFrame()
    if "sector" in enriched.columns and enriched["sector"].notna().any():
        sector = winrate_by(enriched, "sector", horizon="4w")

    # R/R buckets — same edges for PM and DR so the charts are directly comparable.
    # Distribution check (post-2026-02-01): PM 0.0-5.4 mean 0.9; DR 0.0-4.0 mean 1.5.
    rr_bins = [-0.001, 1.0, 2.0, 3.0, 100.0]
    rr_labels = ["<1", "1-2", "2-3", ">=3"]

    pm_rr = pd.DataFrame()
    if "risk_reward_ratio" in enriched.columns and enriched["risk_reward_ratio"].notna().any():
        pm_rr = winrate_by_bucket(
            enriched, "risk_reward_ratio",
            bins=rr_bins, labels=rr_labels, horizon="4w",
        )
        if not pm_rr.empty:
            pm_rr["bucket"] = pm_rr["bucket"].astype(str)

    dr_rr = pd.DataFrame()
    if (
        "deep_research_rr_ratio" in enriched.columns
        and enriched["deep_research_rr_ratio"].notna().any()
    ):
        dr_rr = winrate_by_bucket(
            enriched, "deep_research_rr_ratio",
            bins=rr_bins, labels=rr_labels, horizon="4w",
        )
        if not dr_rr.empty:
            dr_rr["bucket"] = dr_rr["bucket"].astype(str)

    # Also expose the DR-verdict view (column-distinct from DR action, even though
    # in current data they happen to mirror).
    dr_verdict = pd.DataFrame()
    if (
        "deep_research_verdict" in enriched.columns
        and enriched["deep_research_verdict"].notna().any()
    ):
        dr_verdict = winrate_by(enriched, "deep_research_verdict", horizon="4w")

    eq = equity_curve(buys, horizon="4w")

    # Time series since signal — median return path per group, plus individual
    # paths for buy signals so we can render spaghetti + median in JS.
    ts_by_intent = _time_series_by_group(
        enriched, bars, group_col="intent",
        max_days=40, include_individuals=True,
    )
    ts_by_dr_verdict = _time_series_by_group(
        enriched, bars, group_col="deep_research_verdict",
        max_days=40, include_individuals=False,
    )

    # Winner/loser split by intent (and DR verdict)
    winloss_by_intent = _winloss_split_by_group(
        enriched, bars, group_col="intent",
        horizon_label="20d", horizon_day=20, max_days=40,
    )
    winloss_by_dr_verdict = _winloss_split_by_group(
        enriched, bars, group_col="deep_research_verdict",
        horizon_label="20d", horizon_day=20, max_days=40,
    )

    # Cumulative dollar P&L per group over calendar time (mark-to-market)
    cum_pnl_by_intent = _cumulative_pnl_by_calendar(
        enriched, bars, group_col="intent", initial_per_signal=1.0,
    )
    cum_pnl_by_dr_verdict = _cumulative_pnl_by_calendar(
        enriched, bars, group_col="deep_research_verdict", initial_per_signal=1.0,
    )

    # SPY benchmark — fetch a single ticker spanning the cohort
    try:
        spy_bars = get_bars(
            "SPY",
            start=df["decision_date"].min(),
            end=end + pd.Timedelta(days=2),
        )
    except Exception as e:
        logger.warning("SPY fetch failed: %s", e)
        spy_bars = pd.DataFrame()
    spy_overlay = _spy_overlay(enriched, spy_bars, max_days=40)

    # Statistics — run at 1w / 2w / 4w so the small-n 4w results sit alongside
    # the larger-n short-horizon ones.
    HORIZONS = ("1w", "2w", "4w")
    stats_intent_by_h = {
        h: pairwise_welch(enriched, group_col="intent", value_col=f"return_{h}", min_n=5)
        for h in HORIZONS
    }
    stats_dr_verdict_by_h = {
        h: pairwise_welch(
            enriched, group_col="deep_research_verdict", value_col=f"return_{h}", min_n=3,
        )
        for h in HORIZONS
    }
    corr_pm_rr_by_h = {
        h: correlation(enriched, x_col="risk_reward_ratio", y_col=f"return_{h}")
        for h in HORIZONS
    }
    corr_dr_rr_by_h = {
        h: correlation(enriched, x_col="deep_research_rr_ratio", y_col=f"return_{h}")
        for h in HORIZONS
    }
    # Per-intent / per-DR-verdict aggregations at every horizon
    winrate_intent_by_h = {
        h: winrate_by(enriched, "intent", horizon=h) for h in HORIZONS
    }
    winrate_dr_verdict_by_h = {
        h: winrate_by(enriched, "deep_research_verdict", horizon=h) for h in HORIZONS
    }
    drop_bucket_by_h = {}
    for h in HORIZONS:
        b = winrate_by_bucket(
            enriched, "drop_percent",
            bins=[-100, -15, -8, -5, 0],
            labels=["<= -15%", "-15 to -8", "-8 to -5", "> -5%"],
            horizon=h,
        )
        if not b.empty:
            b["bucket"] = b["bucket"].astype(str)
        drop_bucket_by_h[h] = b
    pm_rr_bucket_by_h = {}
    dr_rr_bucket_by_h = {}
    for h in HORIZONS:
        bins = [-0.001, 1.0, 2.0, 3.0, 100.0]
        labels = ["<1", "1-2", "2-3", ">=3"]
        if "risk_reward_ratio" in enriched.columns and enriched["risk_reward_ratio"].notna().any():
            b = winrate_by_bucket(enriched, "risk_reward_ratio", bins=bins, labels=labels, horizon=h)
            if not b.empty:
                b["bucket"] = b["bucket"].astype(str)
            pm_rr_bucket_by_h[h] = b
        if "deep_research_rr_ratio" in enriched.columns and enriched["deep_research_rr_ratio"].notna().any():
            b = winrate_by_bucket(enriched, "deep_research_rr_ratio", bins=bins, labels=labels, horizon=h)
            if not b.empty:
                b["bucket"] = b["bucket"].astype(str)
            dr_rr_bucket_by_h[h] = b

    # Keep the 4w-named fields for back-compat / visual headline.
    stats_intent = stats_intent_by_h["4w"]
    stats_dr_verdict = stats_dr_verdict_by_h["4w"]
    corr_pm_rr = corr_pm_rr_by_h["4w"]
    corr_dr_rr = corr_dr_rr_by_h["4w"]
    rec_intent = recovery_stats(enriched, group_col="intent")
    rec_dr_verdict = recovery_stats(enriched, group_col="deep_research_verdict")

    # R/R distribution by verdict (categorical R/R correlation)
    pm_rr_by_intent = rr_by_group(enriched, "intent", "risk_reward_ratio", min_n=3)
    dr_rr_by_dr_verdict = rr_by_group(
        enriched, "deep_research_verdict", "deep_research_rr_ratio", min_n=2
    )
    pm_rr_by_dr_verdict = rr_by_group(
        enriched, "deep_research_verdict", "risk_reward_ratio", min_n=2
    )
    dr_rr_by_intent = rr_by_group(enriched, "intent", "deep_research_rr_ratio", min_n=2)

    # Top-N high-R/R decisions, surfaced as data tables
    top_pm_rr = top_rr_decisions(enriched, "risk_reward_ratio", n=25)
    top_dr_rr = top_rr_decisions(enriched, "deep_research_rr_ratio", n=25)

    rec_dist = time_to_recover_dist(enriched, max_days=40)
    rec_records = [{"days": int(idx), "count": int(val)} for idx, val in rec_dist.items()]

    decision_cols = [
        "id", "symbol", "decision_date", "intent", "recommendation", "drop_percent",
        "price_at_decision", "sector", "gatekeeper_tier",
        "deep_research_verdict", "deep_research_action",
        "risk_reward_ratio", "deep_research_rr_ratio",
        "return_1w", "return_2w", "return_4w", "return_8w",
        "max_roi_4w", "max_drawdown_4w",
        "limit_filled", "return_filled_4w",
        "recovered", "days_to_recover",
    ]
    decisions = _df_records(enriched, columns=decision_cols)

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cohort_size": int(len(enriched)),
        "cohort_start": start_date,
        "headline": {
            "win_rate_4w_buys": win_rate_buys,
            "avg_return_4w_buys": avg_return_buys,
            "n_buys_4w": int(len(buys_4w)),
            "buy_limit_count": n_limits,
            "buy_limit_filled": n_filled,
            "buy_limit_fill_rate": fill_rate,
            "buy_limit_avg_filled_4w": avg_filled_4w,
            "median_days_to_recover": median_days,
            "n_recovered": n_recovered,
        },
        "winrate_by_intent": _df_records(winrate_by(enriched, "intent", horizon="4w")),
        "winrate_by_horizon": horizon_rows,
        "winrate_by_drop_bucket": _df_records(drop_bucket),
        "winrate_by_dr_action": _df_records(dr_action),
        "winrate_by_dr_verdict": _df_records(dr_verdict),
        "winrate_by_gatekeeper": _df_records(gatekeeper),
        "winrate_by_sector": _df_records(sector),
        "winrate_by_pm_rr": _df_records(pm_rr),
        "winrate_by_dr_rr": _df_records(dr_rr),
        "equity_curve": _df_records(eq, columns=["decision_date", "equity", "n", "avg_return"]),
        "time_to_recover": rec_records,
        "time_series": {
            "max_days": 40,
            "by_intent": ts_by_intent,
            "by_dr_verdict": ts_by_dr_verdict,
            "spy_overlay": spy_overlay,
            "winloss_by_intent": winloss_by_intent,
            "winloss_by_dr_verdict": winloss_by_dr_verdict,
            "cum_pnl_by_intent": cum_pnl_by_intent,
            "cum_pnl_by_dr_verdict": cum_pnl_by_dr_verdict,
        },
        "stats": {
            "pairwise_intent": _stats_records(stats_intent),
            "pairwise_dr_verdict": _stats_records(stats_dr_verdict),
            "corr_pm_rr": corr_pm_rr,
            "corr_dr_rr": corr_dr_rr,
            "recovery_by_intent": _stats_records(rec_intent),
            "recovery_by_dr_verdict": _stats_records(rec_dr_verdict),
            "pm_rr_by_intent": pm_rr_by_intent,
            "dr_rr_by_dr_verdict": dr_rr_by_dr_verdict,
            "pm_rr_by_dr_verdict": pm_rr_by_dr_verdict,
            "dr_rr_by_intent": dr_rr_by_intent,
            "top_pm_rr": _stats_records(top_pm_rr),
            "top_dr_rr": _stats_records(top_dr_rr),
            # Multi-horizon: same family of analyses repeated at 1w / 2w / 4w
            "by_horizon": {
                h: {
                    "winrate_by_intent": _stats_records(winrate_intent_by_h[h]),
                    "winrate_by_dr_verdict": _stats_records(winrate_dr_verdict_by_h[h]),
                    "winrate_by_drop_bucket": _stats_records(drop_bucket_by_h[h]),
                    "winrate_by_pm_rr": _stats_records(pm_rr_bucket_by_h.get(h, pd.DataFrame())),
                    "winrate_by_dr_rr": _stats_records(dr_rr_bucket_by_h.get(h, pd.DataFrame())),
                    "pairwise_intent": _stats_records(stats_intent_by_h[h]),
                    "pairwise_dr_verdict": _stats_records(stats_dr_verdict_by_h[h]),
                    "corr_pm_rr": corr_pm_rr_by_h[h],
                    "corr_dr_rr": corr_dr_rr_by_h[h],
                }
                for h in HORIZONS
            },
        },
        "decisions": decisions,
    }
    return {
        "enriched": enriched,
        "bars": bars,
        "spy_bars": spy_bars,
        "payload": payload,
    }


def build_payload(start_date: str = "2026-02-01") -> Dict[str, Any]:
    """Back-compat wrapper: return just the JSON payload dict."""
    return compute_dataset(start_date=start_date)["payload"]
