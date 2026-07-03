"""Calibration card injection (Option 1, Phase 2).

Loads the cached ``data/calibration_card.json`` and formats a short base-rate
block for the relevant slice of a candidate. Injected into the PM and DR
prompts behind the ``CALIBRATION_ENABLED`` feature flag so production behaviour
is unchanged until the A/B (Phase 3) says otherwise.

When the flag is off, or the card is missing, ``calibration_block`` returns an
empty string — callers insert it such that an empty string is a no-op, keeping
prompts byte-identical to today.
"""
import datetime
import json
import logging
import os
from typing import Optional

# The hand-pinned card — written ONLY by scripts/analysis/pin_calibration_card.py
# after a monthly audit. The nightly candidate card is console-only and is
# never read here (THREE_TIER_FEEDBACK_PROPOSAL.md, Tier 2).
_CARD_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "calibration_card_pinned.json")
)

# A pin older than this is treated as missing: stale base rates are worse
# than none. The nightly QC in main.run_outcome_marking alerts on this too.
STALE_PIN_MAX_DAYS = 45

# Drop types that are earnings-driven, for inferring is_earnings at DR time.
EARNINGS_DROP_TYPES = {"EARNINGS_MISS", "GUIDANCE_CUT"}


def is_enabled() -> bool:
    return os.getenv("CALIBRATION_ENABLED", "0") == "1"


_card_cache = {"mtime": None, "card": None}


def _load_card(path: Optional[str] = None) -> Optional[dict]:
    """mtime-cached read so a manual re-pin reaches a long-lived process.

    The path is resolved at CALL time (not as a bound default) so tests can
    monkeypatch _CARD_PATH.
    """
    path = path or _CARD_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if _card_cache["mtime"] != mtime:
        try:
            with open(path) as f:
                _card_cache["card"] = json.load(f)
        except (OSError, json.JSONDecodeError):
            _card_cache["card"] = None
        _card_cache["mtime"] = mtime
    return _card_cache["card"]


def reload_card() -> None:
    """Drop the cached card (kept for compatibility; mtime check usually suffices)."""
    _card_cache["mtime"] = None
    _card_cache["card"] = None


def _today() -> datetime.date:
    return datetime.date.today()


def pinned_card_age_days(today: Optional[datetime.date] = None) -> Optional[int]:
    """Days since the pinned card's audit stamp; None = missing/unstamped card."""
    card = _load_card()
    if not card or not card.get("audited_at"):
        return None
    try:
        audited = datetime.date.fromisoformat(card["audited_at"])
    except ValueError:
        return None
    return ((today or _today()) - audited).days


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
        logging.getLogger(__name__).warning(
            "[calibration] pinned card missing/unreadable — nothing to inject; "
            "run pin_calibration_card --approve after an audit"
        )
        return ""

    age = pinned_card_age_days()
    if age is None or age > STALE_PIN_MAX_DAYS:
        logging.getLogger(__name__).warning(
            "[calibration] pinned card is %s — refusing to inject; "
            "re-run the monthly audit and pin_calibration_card --approve",
            "unstamped" if age is None else f"{age}d old",
        )
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

    # Data, not instructions (Tier 2 rule): header + base-rate lines only.
    # Any change to this block's wording mid-shadow splits the A/B sample —
    # record the deploy date and filter eval_calibration_ab accordingly.
    header = f"HISTORICAL BASE RATES (4-week, n>={card.get('min_n')}, as of {card.get('as_of')}):"
    return "\n".join([header, *lines])
