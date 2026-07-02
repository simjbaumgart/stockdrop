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
    to WATCH. SUSPENDED as of 2026-07-02: the structured `falling_knife`
    verdict collapsed to 97% YES (256/264 since Jun 11) — zero information —
    and this gate downgraded ALL 17 June PM BUYs into BUY_LIMIT, the desk's
    worst bucket (1/9 win at 14d). Re-enable via RISK_KNIFE_GATE_ENABLED once
    the recalibrated Risk prompt (two-of-three conjunction test, 2026-07-02)
    restores variance: trailing-50 YES-rate must fall below
    KNIFE_YES_RATE_CEILING. Downgrade target changed BUY_LIMIT -> WATCH:
    BUY_LIMIT is empirically the worst action (medExc -1.9), so downgrading
    into it was anti-defensive.
  * Gate 5 (NEWS_SENTIMENT_GATE): bearish-news buys won 39% vs 54% for
    bullish-news buys. A buy on BEARISH news sentiment needs a named,
    verifiable catalyst from the News agent; otherwise downgrade to WATCH.
  * Gate 6 (UNCONFIRMED_DROP_GATE): BUY on a drop whose reason the News
    agent explicitly could not confirm is demoted to WATCH (was BUY_LIMIT
    until 2026-07-02 — see Gate 3 note on why BUY_LIMIT is not a safe
    downgrade target).

The PM's original action is preserved (`pre_gate_action`) so gated-vs-kept
performance is a free ongoing A/B — see scripts/analysis/gate_baseline_check.py.

Degeneracy monitoring: run_nightly_degeneracy_check (called from
main.run_outcome_marking) persists auto-suspensions to
data/gate_suspensions.json when a gate's input signal exceeds its
GATE_DEGENERACY_CEILINGS share over the trailing 50 decisions — the
generalization of the falling-knife collapse. Suspension is fail-open
(gate skipped, PM action kept) and reverses automatically once the
signal regains variance.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Drop types with no historical edge on buys. SECTOR_ROTATION, MACRO_SELLOFF,
# TECHNICAL_BREAKDOWN and UNKNOWN pass through.
GATED_DROP_TYPES = {"EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE"}

SA_QUANT_FLOOR = 2.5

# RISK_KNIFE_GATE kill switch — see module docstring. The structured
# falling_knife verdict ran 97% YES since 2026-06-11 (no information); the
# gate converted the entire June buy book into BUY_LIMIT (1/9 win at 14d).
# Flip back to True only when the trailing-50 YES-rate is below the ceiling.
RISK_KNIFE_GATE_ENABLED = False
KNIFE_YES_RATE_CEILING = 0.60

# --- Degeneracy monitoring (THREE_TIER_FEEDBACK_PROPOSAL.md, Tier 1) ---
# Trailing share of decisions carrying each gate's triggering signal. If a
# signal stops discriminating (the falling-knife precedent: 97% YES), its
# gate is auto-suspended via data/gate_suspensions.json rather than left to
# downgrade the whole book. SA_QUANT_GATE has no ceiling: an external numeric
# rating cannot mode-collapse; UNCONFIRMED_DROP_GATE's input is not persisted.
GATE_DEGENERACY_CEILINGS = {
    "DROP_TYPE_GATE": 0.80,       # share classified into GATED_DROP_TYPES
    "NEWS_SENTIMENT_GATE": 0.80,  # share of BEARISH news sentiment
    "RISK_KNIFE_GATE": KNIFE_YES_RATE_CEILING,
}
DEGENERACY_MIN_SIGNALS = 30  # below this trailing sample, never suspend

_SUSPENSIONS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "gate_suspensions.json")
)
_suspensions_cache = {"mtime": None, "suspended": frozenset()}

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


def _load_suspensions() -> frozenset:
    """Gates auto-suspended by the nightly degeneracy check (mtime-cached)."""
    try:
        mtime = os.path.getmtime(_SUSPENSIONS_PATH)
    except OSError:
        return frozenset()
    if _suspensions_cache["mtime"] != mtime:
        try:
            with open(_SUSPENSIONS_PATH) as f:
                _suspensions_cache["suspended"] = frozenset(json.load(f).get("suspended", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            _suspensions_cache["suspended"] = frozenset()
        _suspensions_cache["mtime"] = mtime
    return _suspensions_cache["suspended"]


def check_gate_degeneracy(rates: dict) -> List[str]:
    """Gates whose input signal has stopped discriminating, given trailing rates
    from app.database.get_recent_signal_rates. Pure — no I/O."""
    out: List[str] = []
    n = rates.get("n") or 0
    if n >= DEGENERACY_MIN_SIGNALS:
        if rates.get("drop_type_gated_rate", 0.0) > GATE_DEGENERACY_CEILINGS["DROP_TYPE_GATE"]:
            out.append("DROP_TYPE_GATE")
        if rates.get("news_bearish_rate", 0.0) > GATE_DEGENERACY_CEILINGS["NEWS_SENTIMENT_GATE"]:
            out.append("NEWS_SENTIMENT_GATE")
    if (rates.get("knife_n") or 0) >= DEGENERACY_MIN_SIGNALS:
        if rates.get("knife_yes_rate", 0.0) > GATE_DEGENERACY_CEILINGS["RISK_KNIFE_GATE"]:
            out.append("RISK_KNIFE_GATE")
    return out


def run_nightly_degeneracy_check(limit: int = 50) -> List[str]:
    """Recompute trailing signal rates, persist the suspension set, and return
    the NEWLY suspended gates (for the caller's QC alert). Also logs the knife
    YES-rate every night — it is the re-enable condition for RISK_KNIFE_GATE."""
    from app.database import get_recent_signal_rates

    rates = get_recent_signal_rates(limit, tuple(sorted(GATED_DROP_TYPES)))
    degenerate = check_gate_degeneracy(rates)
    previously = set(_load_suspensions())

    os.makedirs(os.path.dirname(_SUSPENSIONS_PATH), exist_ok=True)
    with open(_SUSPENSIONS_PATH, "w") as f:
        json.dump({
            "suspended": sorted(degenerate),
            "rates": rates,
            "as_of": datetime.date.today().isoformat(),
        }, f, indent=2)
    _suspensions_cache["mtime"] = None  # force re-read

    logger.info(
        "[DecisionGate] degeneracy check: n=%s knife_yes=%.0f%% (ceiling %.0f%%) "
        "drop_type=%.0f%% bearish=%.0f%% -> suspended=%s",
        rates.get("n"), 100 * rates.get("knife_yes_rate", 0.0),
        100 * KNIFE_YES_RATE_CEILING, 100 * rates.get("drop_type_gated_rate", 0.0),
        100 * rates.get("news_bearish_rate", 0.0), sorted(degenerate) or "none",
    )
    newly = sorted(set(degenerate) - previously)
    for gate in newly:
        logger.error("[QC ALERT] %s input degenerate over trailing %s decisions — gate auto-suspended",
                     gate, rates.get("n"))
    return newly


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

    suspended = _load_suspensions()

    def _active(gate: str) -> bool:
        if gate in suspended:
            logger.warning("[DecisionGate] %s suspended (degenerate input) — skipping", gate)
            return False
        return True

    if pre_gate not in _BUY_ACTIONS:
        return result

    targets: List[str] = []

    drop_type_norm = (drop_type or "").strip().upper()
    if drop_type_norm in GATED_DROP_TYPES and _active("DROP_TYPE_GATE"):
        targets.append("WATCH")
        result.gates_fired.append("DROP_TYPE_GATE")
        result.gate_reasons.append(
            f"{drop_type_norm} buys have no historical edge (37-39% win at 7d)"
        )

    if sa_quant_rating is not None and sa_quant_rating < SA_QUANT_FLOOR and _active("SA_QUANT_GATE"):
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
    if RISK_KNIFE_GATE_ENABLED and knife and pre_gate == "BUY" and _active("RISK_KNIFE_GATE"):
        targets.append("WATCH")
        result.gates_fired.append("RISK_KNIFE_GATE")
        result.gate_reasons.append(
            "Risk agent flags a falling knife (knife-flagged buys: 12% win at 14d, median -4.65%)"
        )

    if (news_sentiment or "").strip().upper() == "BEARISH" and not (news_named_catalyst or "").strip() and _active("NEWS_SENTIMENT_GATE"):
        targets.append("WATCH")
        result.gates_fired.append("NEWS_SENTIMENT_GATE")
        result.gate_reasons.append(
            "Bearish news flow with no named catalyst (bearish-news buys won 39% vs 54%)"
        )

    # Gate 6: the News agent explicitly could NOT confirm why the stock
    # dropped (drop_reason_confirmed=False, PTC 2026-06-11 went BUY anyway).
    # An immediate BUY on an unexplained drop becomes a limit order; None
    # (unparsed verdict) never fires.
    if news_drop_reason_confirmed is False and pre_gate == "BUY" and _active("UNCONFIRMED_DROP_GATE"):
        targets.append("WATCH")
        result.gates_fired.append("UNCONFIRMED_DROP_GATE")
        result.gate_reasons.append(
            "News agent could not confirm the drop reason — no entry until confirmed"
        )

    if targets:
        result.final_action = max(targets, key=lambda a: _ACTION_RANK[a])
        logger.info(
            "[DecisionGate] %s -> %s (%s)",
            pre_gate, result.final_action, ", ".join(result.gates_fired),
        )

    return result
