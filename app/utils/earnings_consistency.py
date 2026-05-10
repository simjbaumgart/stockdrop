"""Deterministic post-PM check: did the PM narrate the earnings event
consistently with the actual surprise sign?

Catches cases like the TOST 2026-05 incident where the PM described a
beat as a miss (or vice versa). When inconsistent, the caller should
attach an EARNINGS_NARRATIVE_INCONSISTENT flag and downgrade the verdict
by one tier (BUY -> BUY_LIMIT, BUY_LIMIT -> WATCH).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Word-boundary patterns — 'unbeatable' must not match 'beat'.
_BEAT_RE = re.compile(r"\b(beat|beats|beating|outperformed|topped\s+(?:estimates|consensus|expectations))\b", re.IGNORECASE)
_MISS_RE = re.compile(r"\b(miss(?:ed|es|ing)?|underperformed|fell\s+short|below\s+(?:estimates|consensus|expectations))\b", re.IGNORECASE)


@dataclass
class ConsistencyResult:
    inconsistent: bool
    flag: Optional[str]
    reason: str


def check_narrative_consistency(
    *, reasoning: Optional[str], surprise_pct: Optional[float]
) -> ConsistencyResult:
    """Compare PM narrative ('beat' vs 'miss') against the sign of surprise_pct.

    Returns inconsistent=True when the narrative claims a beat but the surprise
    is negative, or claims a miss but the surprise is positive.
    """
    if surprise_pct is None:
        return ConsistencyResult(False, None, "no_surprise_data")
    if not reasoning:
        return ConsistencyResult(False, None, "no_reasoning")

    has_beat = bool(_BEAT_RE.search(reasoning))
    has_miss = bool(_MISS_RE.search(reasoning))

    # If the narrative talks about both, treat as ambiguous and pass.
    if has_beat and has_miss:
        return ConsistencyResult(False, None, "ambiguous_narrative")

    if has_beat and surprise_pct < 0:
        return ConsistencyResult(
            True,
            "EARNINGS_NARRATIVE_INCONSISTENT",
            f"reasoning narrates beat but surprise_pct={surprise_pct:+.1f}",
        )
    if has_miss and surprise_pct > 0:
        return ConsistencyResult(
            True,
            "EARNINGS_NARRATIVE_INCONSISTENT",
            f"reasoning narrates miss but surprise_pct={surprise_pct:+.1f}",
        )

    return ConsistencyResult(False, None, "consistent_or_neutral")


# One-tier downgrade ladder. BUY is the most aggressive; AVOID is bottom.
_DOWNGRADE = {
    "BUY": "BUY_LIMIT",
    "BUY_LIMIT": "WATCH",
}


def downgrade_action(action: str) -> str:
    """Return the next-lower tier, or the input if no downgrade applies."""
    if not action:
        return action
    return _DOWNGRADE.get(action.upper().strip(), action)
