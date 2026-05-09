"""Build a single JSON-serializable payload describing cohort performance.

Used by the offline HTML report generator. Pure function — no caching,
no FastAPI dependency.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.services.analytics.aggregations import (
    equity_curve,
    time_to_recover_dist,
    winrate_by,
    winrate_by_bucket,
)
from app.services.analytics.cohort import load_cohort
from app.services.analytics.outcomes import HORIZON_DAYS, enrich_outcomes
from app.services.analytics.price_cache import prefetch

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
        out[grp] = {
            "day_offsets": list(range(max_len)),
            "median": [float(np.median(x)) if x else None for x in per_day],
            "q25": [float(np.percentile(x, 25)) if x else None for x in per_day],
            "q75": [float(np.percentile(x, 75)) if x else None for x in per_day],
            "count": [len(x) for x in per_day],
            "n_paths": len(paths),
        }
        if include_individuals:
            out[grp]["individuals"] = paths
    return out


def build_payload(start_date: str = "2026-02-01") -> Dict[str, Any]:
    """Compute every aggregation we render and return one JSON-friendly dict."""
    df = load_cohort(start_date=start_date)
    if df.empty:
        return {
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

    return {
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
        },
        "decisions": decisions,
    }
