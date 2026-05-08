"""Build the dashboard summary payload from analytics primitives, with TTL cache."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

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

_CACHE: Dict[str, Any] = {"payload": None, "built_at": 0.0}
_CACHE_TTL_SECONDS = 60 * 60  # 1 hour
_LOCK = threading.Lock()


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
            elif isinstance(v, (pd.Timestamp,)):
                rec[str(k)] = v.strftime("%Y-%m-%d")
            elif hasattr(v, "item"):
                rec[str(k)] = v.item()
            else:
                rec[str(k)] = v
        out.append(rec)
    return out


def _build_payload(start_date: str = "2026-02-01") -> Dict[str, Any]:
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
            "equity_curve": [],
            "time_to_recover": [],
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
    n_filled = int(limits["limit_filled"].fillna(False).sum()) if "limit_filled" in limits.columns else 0
    fill_rate = (n_filled / n_limits) if n_limits else None
    avg_filled_4w = None
    if "return_filled_4w" in limits.columns:
        sub = limits.dropna(subset=["return_filled_4w"])
        if not sub.empty:
            avg_filled_4w = float(sub["return_filled_4w"].mean())

    rec_series = enriched["days_to_recover"].dropna() if "days_to_recover" in enriched.columns else pd.Series(dtype=float)
    median_days = float(rec_series.median()) if not rec_series.empty else None
    n_recovered = int(rec_series.size)

    winrate_intent_4w = winrate_by(enriched, "intent", horizon="4w")

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

    eq = equity_curve(buys, horizon="4w")

    rec_dist = time_to_recover_dist(enriched, max_days=40)
    rec_records = [{"days": int(idx), "count": int(val)} for idx, val in rec_dist.items()]

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
        "winrate_by_intent": _df_records(winrate_intent_4w),
        "winrate_by_horizon": horizon_rows,
        "winrate_by_drop_bucket": _df_records(drop_bucket),
        "winrate_by_dr_action": _df_records(dr_action),
        "winrate_by_gatekeeper": _df_records(gatekeeper),
        "equity_curve": _df_records(eq, columns=["decision_date", "equity", "n", "avg_return"]),
        "time_to_recover": rec_records,
    }


def summary_json(refresh: bool = False, start_date: str = "2026-02-01") -> Dict[str, Any]:
    """Return cached summary payload; rebuild if stale or refresh=True."""
    now = time.time()
    with _LOCK:
        cached = _CACHE.get("payload")
        if (
            not refresh
            and cached is not None
            and (now - _CACHE.get("built_at", 0)) < _CACHE_TTL_SECONDS
            and _CACHE.get("start_date") == start_date
        ):
            return cached

    logger.info("Rebuilding insights summary (start_date=%s, refresh=%s)", start_date, refresh)
    payload = _build_payload(start_date=start_date)

    with _LOCK:
        _CACHE["payload"] = payload
        _CACHE["built_at"] = time.time()
        _CACHE["start_date"] = start_date
    return payload
