"""Deterministic post-PM decision gates.

Applied after the Fund Manager verdict is parsed and before persistence.
Converts the statistically verified leaks from prompt_vs_outcome_analysis
(2026-06-10, 681 decisions Apr 9 - Jun 10, 7-day marks) into hard rules
instead of prompt instructions:

  * Gate 1 (DROP_TYPE_GATE): buys on EARNINGS_MISS / COMPANY_SPECIFIC /
    ANALYST_DOWNGRADE drops won 37-39% vs 52% for SECTOR_ROTATION /
    MACRO_SELLOFF. Downgrade those buys to WATCH. Deep Research can lift the
    WATCH back to BUY_LIMIT, but only with a NAMED_EVENT positive catalyst.
  * Gate 2 (SA_QUANT_GATE): SA quant rating < 2.5 decisions won 31% with a
    median of -3.47%. Missing rating does NOT block (coverage ~39%).
  * Gate 3 (RISK_KNIFE_GATE): explicit falling-knife verdicts from the Risk
    agent were ignored by the PM; those buys averaged -2.48%. BUY downgrades
    to BUY_LIMIT, or to WATCH when PM conviction is LOW. Until the structured
    risk verdict (Phase 2) lands, an interim regex catches the explicit
    verdict subset (~11% of reports) that was predictive.
  * Gate 5 (NEWS_SENTIMENT_GATE): bearish-news buys won 39% vs 54% for
    bullish-news buys. A buy on BEARISH news sentiment needs a named,
    verifiable catalyst from the News agent; otherwise downgrade to WATCH.
  * Gate 6 (UNCONFIRMED_DROP_GATE): BUY on a drop whose reason the News
    agent explicitly could not confirm is demoted to BUY_LIMIT.

The PM's original action is preserved (`pre_gate_action`) so gated-vs-kept
performance is a free ongoing A/B — see scripts/analysis/gate_baseline_check.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Drop types with no historical edge on buys. SECTOR_ROTATION, MACRO_SELLOFF,
# TECHNICAL_BREAKDOWN and UNKNOWN pass through.
GATED_DROP_TYPES = {"EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE"}

SA_QUANT_FLOOR = 2.5

_BUY_ACTIONS = {"BUY", "BUY_LIMIT"}

# Action severity ladder: a fired gate can only move the action rightward.
_ACTION_RANK = {"BUY": 0, "BUY_LIMIT": 1, "WATCH": 2}

# Interim falling-knife detector on the free-text risk report. Matches the
# explicit-verdict subset ("Verdict: YES", "verdict — this is a falling
# knife") that was predictive; deliberately narrow to avoid false fires on
# generic knife discussion. Superseded by the structured `falling_knife`
# field once Phase 2 parse success exceeds 90% over a rolling 50 decisions.
_KNIFE_RE = re.compile(r"verdict[^a-zA-Z]{0,5}(yes|.{0,30}falling knife)", re.IGNORECASE)


@dataclass
class GateResult:
    final_action: str          # possibly downgraded
    pre_gate_action: str       # PM's original action
    gates_fired: List[str] = field(default_factory=list)   # e.g. ["DROP_TYPE_GATE"]
    gate_reasons: List[str] = field(default_factory=list)  # human-readable, for dashboard/email


def risk_report_flags_knife(risk_report: Optional[str]) -> bool:
    """True when the Risk agent's free-text report carries an explicit falling-knife verdict."""
    if not risk_report:
        return False
    return bool(_KNIFE_RE.search(risk_report))


def apply_decision_gates(
    action: Optional[str],
    drop_type: Optional[str],
    conviction: Optional[str],
    sa_quant_rating: Optional[float],
    risk_report: Optional[str] = None,
    risk_falling_knife: Optional[str] = None,
    news_sentiment: Optional[str] = None,
    news_named_catalyst: Optional[str] = None,
    news_drop_reason_confirmed: Optional[bool] = None,
) -> GateResult:
    """Run all deterministic gates against a finalized PM decision.

    Non-buy actions (WATCH/AVOID/None) pass through untouched. Gates evaluate
    against the PM's original action; when several fire, the most restrictive
    downgrade wins and every fired gate is recorded.

    `risk_falling_knife` is the structured Phase 2 verdict ("YES"/"NO"); when
    absent, the interim regex on `risk_report` is used instead.
    """
    pre_gate = (action or "").strip().upper()
    result = GateResult(final_action=pre_gate, pre_gate_action=pre_gate)

    if pre_gate not in _BUY_ACTIONS:
        return result

    targets: List[str] = []

    drop_type_norm = (drop_type or "").strip().upper()
    if drop_type_norm in GATED_DROP_TYPES:
        targets.append("WATCH")
        result.gates_fired.append("DROP_TYPE_GATE")
        result.gate_reasons.append(
            f"{drop_type_norm} buys have no historical edge (37-39% win at 7d)"
        )

    if sa_quant_rating is not None and sa_quant_rating < SA_QUANT_FLOOR:
        targets.append("WATCH")
        result.gates_fired.append("SA_QUANT_GATE")
        result.gate_reasons.append(
            f"SA quant rating {sa_quant_rating:.2f} < {SA_QUANT_FLOOR} (31% win, median -3.47%)"
        )

    knife = (
        (risk_falling_knife or "").strip().upper() == "YES"
        if risk_falling_knife is not None
        else risk_report_flags_knife(risk_report)
    )
    if knife and pre_gate == "BUY":
        low_conviction = (conviction or "").strip().upper() == "LOW"
        targets.append("WATCH" if low_conviction else "BUY_LIMIT")
        result.gates_fired.append("RISK_KNIFE_GATE")
        result.gate_reasons.append(
            "Risk agent flags a falling knife"
            + (" and PM conviction is LOW" if low_conviction else "")
            + " (knife-flagged buys averaged -2.48%)"
        )

    if (news_sentiment or "").strip().upper() == "BEARISH" and not (news_named_catalyst or "").strip():
        targets.append("WATCH")
        result.gates_fired.append("NEWS_SENTIMENT_GATE")
        result.gate_reasons.append(
            "Bearish news flow with no named catalyst (bearish-news buys won 39% vs 54%)"
        )

    # Gate 6: the News agent explicitly could NOT confirm why the stock
    # dropped (drop_reason_confirmed=False, PTC 2026-06-11 went BUY anyway).
    # An immediate BUY on an unexplained drop becomes a limit order; None
    # (unparsed verdict) never fires.
    if news_drop_reason_confirmed is False and pre_gate == "BUY":
        targets.append("BUY_LIMIT")
        result.gates_fired.append("UNCONFIRMED_DROP_GATE")
        result.gate_reasons.append(
            "News agent could not confirm the drop reason — no immediate entry"
        )

    if targets:
        result.final_action = max(targets, key=lambda a: _ACTION_RANK[a])
        logger.info(
            "[DecisionGate] %s -> %s (%s)",
            pre_gate, result.final_action, ", ".join(result.gates_fired),
        )

    return result
