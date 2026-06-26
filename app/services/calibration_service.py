"""Calibration card injection (Option 1, Phase 2).

Loads the cached ``data/calibration_card.json`` and formats a short base-rate
block for the relevant slice of a candidate. Injected into the PM and DR
prompts behind the ``CALIBRATION_ENABLED`` feature flag so production behaviour
is unchanged until the A/B (Phase 3) says otherwise.

When the flag is off, or the card is missing, ``calibration_block`` returns an
empty string — callers insert it such that an empty string is a no-op, keeping
prompts byte-identical to today.
"""
import functools
import json
import os
from typing import Optional

_CARD_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "calibration_card.json")
)

# Drop types that are earnings-driven, for inferring is_earnings at DR time.
EARNINGS_DROP_TYPES = {"EARNINGS_MISS", "GUIDANCE_CUT"}


def is_enabled() -> bool:
    return os.getenv("CALIBRATION_ENABLED", "0") == "1"


@functools.lru_cache(maxsize=1)
def _load_card(path: str = _CARD_PATH) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def reload_card() -> None:
    """Drop the cached card (call after a rebuild within a long-lived process)."""
    _load_card.cache_clear()


def calibration_block(drop_type: Optional[str] = None,
                      is_earnings: Optional[bool] = None,
                      force: bool = False) -> str:
    """Return a short base-rate block for the relevant slice, or '' if disabled
    / card missing / no bucket met the min-n threshold.

    ``force=True`` bypasses the CALIBRATION_ENABLED gate — used by the Stage-1
    shadow run to build the card-ON treatment prompt while production stays OFF.

    Kept to a handful of lines so prompt cost stays negligible.
    """
    if not force and not is_enabled():
        return ""
    card = _load_card()
    if not card:
        return ""

    lines = []
    dt = (card.get("by_drop_type") or {}).get(drop_type) if drop_type else None
    if dt and dt.get("recovery_rate_4w") is not None:
        lines.append(
            f"- This drop_type ({drop_type}): {dt['recovery_rate_4w']:.0%} recovered, "
            f"mean {dt['mean_ret_4w']:+.1%} (n={dt['n']})"
        )

    if is_earnings is not None:
        ek = "earnings" if is_earnings else "non_earnings"
        ev = (card.get("by_earnings") or {}).get(ek)
        if ev and ev.get("recovery_rate_4w") is not None:
            lines.append(
                f"- {ek} drops overall: {ev['recovery_rate_4w']:.0%} recovered, "
                f"mean {ev['mean_ret_4w']:+.1%} (n={ev['n']})"
            )

    if not lines:
        return ""

    header = f"HISTORICAL BASE RATES (4-week, n>={card.get('min_n')}, as of {card.get('as_of')}):"
    footer = ("Weigh these base rates; do not let a compelling narrative override "
              "a poor base rate.")
    return "\n".join([header, *lines, footer])
