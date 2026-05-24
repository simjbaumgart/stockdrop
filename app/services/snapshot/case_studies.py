"""Pick one candidate per case-study slot and draft a markdown stub.

The drafter intentionally uses only structured columns. Free-text LLM
fields are never accessed — even if they sneak into the input DataFrame
in the future, they won't appear in the draft.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def _is_missing(v: Any) -> bool:
    """True for None and any pandas/numpy NaN (including float, str, datetime)."""
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _clean(v: Any, default: str = "—") -> str:
    """Return v as a clean string, or `default` if it's missing/NaN."""
    if _is_missing(v):
        return default
    s = str(v).strip()
    return s if s and s.lower() != "nan" else default


def _row_to_dict(row: pd.Series, decision_row: Optional[pd.Series]) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "ticker": row.get("ticker"),
        "status": row.get("status"),
        "entry_date": row.get("entry_date"),
        "entry_price": row.get("entry_price"),
        "current_price": row.get("current_price"),
        "exit_date": row.get("exit_date"),
        "exit_price": row.get("exit_price"),
        "realized_pnl_pct": row.get("realized_pnl_pct"),
        "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
        "exit_reason": row.get("exit_reason"),
    }
    if decision_row is not None:
        d.update({
            "company_name": decision_row.get("company_name"),
            "sector": decision_row.get("sector"),
            "drop_percent": decision_row.get("drop_percent"),
            "recommendation": decision_row.get("recommendation"),
            "ai_score": decision_row.get("ai_score"),
            "deep_research_action": decision_row.get("deep_research_action"),
            "deep_research_score": decision_row.get("deep_research_score"),
            "entry_price_low": decision_row.get("entry_price_low"),
            "entry_price_high": decision_row.get("entry_price_high"),
            "stop_loss": decision_row.get("stop_loss"),
            "take_profit_1": decision_row.get("take_profit_1"),
            "take_profit_2": decision_row.get("take_profit_2"),
        })
    return d


def _join_one(position_row: pd.Series, decisions: pd.DataFrame) -> Dict[str, Any]:
    dp_id = position_row.get("decision_point_id")
    matches = decisions[decisions["id"] == dp_id] if not decisions.empty else pd.DataFrame()
    decision_row = matches.iloc[0] if not matches.empty else None
    return _row_to_dict(position_row, decision_row)


def pick_candidates(
    decisions: pd.DataFrame, positions: pd.DataFrame
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Choose one row for each of: best, worst, avoided, open.

    Returns a dict with keys best/worst/avoided/open; each value is a
    flattened dict of fields suitable for the drafter, or None if no
    candidate exists for that slot.
    """
    out: Dict[str, Optional[Dict[str, Any]]] = {
        "best": None, "worst": None, "avoided": None, "open": None,
    }

    # Restrict best/worst/open candidates to positions whose decision is
    # in the in-window decisions DataFrame, so the case study's "setup"
    # section is populated. Falls back to the unfiltered pool only if no
    # in-window candidate exists for a slot.
    if not positions.empty:
        in_window_ids = set(decisions["id"]) if not decisions.empty else set()
        in_window_pos = positions[positions["decision_point_id"].isin(in_window_ids)]

        def _pick_best_worst(pool: pd.DataFrame):
            closed = pool[pool["status"] == "CLOSED"].dropna(subset=["realized_pnl_pct"])
            if closed.empty:
                return None, None
            return (
                closed.loc[closed["realized_pnl_pct"].idxmax()],
                closed.loc[closed["realized_pnl_pct"].idxmin()],
            )

        best, worst = _pick_best_worst(in_window_pos)
        if best is None:
            best, worst = _pick_best_worst(positions)
        if best is not None:
            out["best"] = _join_one(best, decisions)
            out["worst"] = _join_one(worst, decisions)

        def _pick_open(pool: pd.DataFrame):
            active = pool[pool["status"] == "ACTIVE"]
            if active.empty:
                return None
            if active["unrealized_pnl_pct"].notna().any():
                return active.loc[active["unrealized_pnl_pct"].idxmax()]
            return active.iloc[0]

        open_pick = _pick_open(in_window_pos)
        if open_pick is None:
            open_pick = _pick_open(positions)
        if open_pick is not None:
            out["open"] = _join_one(open_pick, decisions)

    if not decisions.empty:
        avoids = decisions[decisions["recommendation"] == "AVOID"]
        if not avoids.empty:
            # Prefer the one with the biggest drop (most dramatic "knife catch averted")
            pick = avoids.loc[avoids["drop_percent"].idxmin()]  # idxmin: most negative drop
            out["avoided"] = _row_to_dict(
                pd.Series({"ticker": pick.get("symbol"), "status": "N/A"}),
                pick,
            )

    return out


_SLOT_TITLES = {
    "best": "Best trade",
    "worst": "Worst trade",
    "avoided": "Correctly avoided",
    "open": "Still open",
}


def draft_case_study(slot: str, candidate: Optional[Dict[str, Any]]) -> str:
    """Return a markdown draft for the given slot. Hand-edit before commit."""
    title = _SLOT_TITLES.get(slot, slot.title())
    if candidate is None:
        return f"# {title}\n\nNo candidate available for this slot in the current window.\n"

    ticker = _clean(candidate.get("ticker"), _clean(candidate.get("company_name"), "?"))
    company = _clean(candidate.get("company_name"), ticker)
    sector = _clean(candidate.get("sector"))
    drop = candidate.get("drop_percent")
    rec = _clean(candidate.get("recommendation"))
    ai_score = candidate.get("ai_score")
    dr_action = _clean(candidate.get("deep_research_action"))
    dr_score = candidate.get("deep_research_score")

    entry_low = candidate.get("entry_price_low")
    entry_high = candidate.get("entry_price_high")
    stop = candidate.get("stop_loss")
    tp1 = candidate.get("take_profit_1")
    tp2 = candidate.get("take_profit_2")

    entry_date = _clean(candidate.get("entry_date"))
    entry_price = candidate.get("entry_price")
    exit_date_raw = candidate.get("exit_date")
    exit_date = _clean(exit_date_raw)
    exit_price = candidate.get("exit_price")
    realized = candidate.get("realized_pnl_pct")
    unrealized = candidate.get("unrealized_pnl_pct")
    cur_price = candidate.get("current_price")
    exit_reason = _clean(candidate.get("exit_reason"))

    def fmt(v, suffix=""):
        if _is_missing(v):
            return "—"
        if isinstance(v, float):
            return f"{v:.2f}{suffix}"
        return f"{v}{suffix}"

    lines = [
        f"# {title} — {ticker} ({company})",
        "",
        "## The setup",
        f"- **Sector:** {sector}",
        f"- **Drop that triggered the screener:** {fmt(drop, '%')}",
        "",
        "## The verdict",
        f"- **PM:** {rec} (score {fmt(ai_score)})",
        f"- **Deep Research:** {dr_action} (score {fmt(dr_score)})",
        "",
        "## The plan",
        f"- **Entry range:** {fmt(entry_low)} – {fmt(entry_high)}",
        f"- **Stop loss:** {fmt(stop)}",
        f"- **TP1 / TP2:** {fmt(tp1)} / {fmt(tp2)}",
        "",
        "## What happened",
        f"- **Entry date / price:** {entry_date} @ {fmt(entry_price)}",
    ]
    if not _is_missing(exit_date_raw):
        lines += [
            f"- **Exit date / price:** {exit_date} @ {fmt(exit_price)}",
            f"- **Realized P&L:** {fmt(realized, '%')}",
            f"- **Exit reason:** {exit_reason}",
        ]
    else:
        lines += [
            f"- **Current price:** {fmt(cur_price)}",
            f"- **Unrealized P&L:** {fmt(unrealized, '%')}",
            "- **Status:** still open",
        ]
    lines += [
        "",
        "## Takeaway",
        "_<one line on what this case illustrates — fill in before committing>_",
        "",
    ]
    return "\n".join(lines)
