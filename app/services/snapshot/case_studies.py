"""Pick one candidate per case-study slot and draft a markdown stub.

The drafter intentionally uses only structured columns. Free-text LLM
fields are never accessed — even if they sneak into the input DataFrame
in the future, they won't appear in the draft.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


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

    if not positions.empty:
        closed = positions[positions["status"] == "CLOSED"].dropna(subset=["realized_pnl_pct"])
        if not closed.empty:
            best = closed.loc[closed["realized_pnl_pct"].idxmax()]
            worst = closed.loc[closed["realized_pnl_pct"].idxmin()]
            out["best"] = _join_one(best, decisions)
            out["worst"] = _join_one(worst, decisions)

        active = positions[positions["status"] == "ACTIVE"]
        if not active.empty:
            # Pick the one with the highest unrealized P&L, or the most recent entry as tiebreaker.
            if active["unrealized_pnl_pct"].notna().any():
                pick = active.loc[active["unrealized_pnl_pct"].idxmax()]
            else:
                pick = active.iloc[0]
            out["open"] = _join_one(pick, decisions)

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

    ticker = candidate.get("ticker") or candidate.get("company_name") or "?"
    company = candidate.get("company_name") or ticker
    sector = candidate.get("sector") or "—"
    drop = candidate.get("drop_percent")
    rec = candidate.get("recommendation") or "—"
    ai_score = candidate.get("ai_score")
    dr_action = candidate.get("deep_research_action") or "—"
    dr_score = candidate.get("deep_research_score")

    entry_low = candidate.get("entry_price_low")
    entry_high = candidate.get("entry_price_high")
    stop = candidate.get("stop_loss")
    tp1 = candidate.get("take_profit_1")
    tp2 = candidate.get("take_profit_2")

    entry_date = candidate.get("entry_date") or "—"
    entry_price = candidate.get("entry_price")
    exit_date = candidate.get("exit_date")
    exit_price = candidate.get("exit_price")
    realized = candidate.get("realized_pnl_pct")
    unrealized = candidate.get("unrealized_pnl_pct")
    cur_price = candidate.get("current_price")
    exit_reason = candidate.get("exit_reason") or "—"

    def fmt(v, suffix=""):
        if v is None or (isinstance(v, float) and pd.isna(v)):
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
    if exit_date:
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
