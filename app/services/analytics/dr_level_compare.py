"""Buy/sell level comparison for the Claude vs Gemini deep-research dual-run.

Pure functions, no external dependencies.

Materiality thresholds
----------------------
A comparison is flagged ``material=True`` when ANY of the following fire:

  1. ``entry.midpoint_pct_delta > 3``  — the two models disagree on where to
     buy by more than 3% of the Gemini entry midpoint.

  2. ``stop_loss.pct_delta > 5``  — the two models place the protective stop
     more than 5% apart (material risk divergence).

  3. ``entry.overlap_fraction == 0``  — the two entry bands do not overlap at
     all (completely disjoint entry zones).

Missing / None data for a criterion → that criterion is skipped silently (it
never alone triggers materiality).

Anchored flag
-------------
Shadow comparisons (where Claude saw Gemini's already-refined levels) must be
tagged ``anchored=True`` so the result is never confused for a clean signal.
Live dual-run comparisons (Claude un-anchored) pass ``anchored=False`` (default).

band_overlap denominator
------------------------
The overlap fraction is divided by the **smaller** band width (not the larger,
and not the union). This means:

  * 0.0 → the bands are completely disjoint.
  * ≥1.0 → the smaller band is fully contained inside the larger band.

Using the smaller width makes the metric sensitive to whether the tighter
model is "inside" the looser one — e.g. if Gemini gives [40,60] and Claude
gives [44,46], the 2-unit overlap / 2-unit smaller-width = 1.0 means Claude
is fully within Gemini, which is the most useful thing to know.
"""
from __future__ import annotations

from typing import Any, Optional


# ─── pct_delta ────────────────────────────────────────────────────────────────

def pct_delta(a: Any, b: Any) -> Optional[float]:
    """Return ``|b - a| / |a| * 100`` as a percentage, or None on bad inputs.

    Returns None when:
      - either argument is None
      - either argument is not a real number
      - ``a`` is zero (would divide by zero)
    """
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if fa == 0.0:
        return None
    return abs(fb - fa) / abs(fa) * 100.0


# ─── band_overlap ─────────────────────────────────────────────────────────────

def band_overlap(a: Any, b: Any) -> float:
    """Overlap fraction for two ``[low, high]`` bands.

    Returns the length of the overlap divided by the **smaller** band width,
    so the result is in [0, ∞) where:

      0.0      → completely disjoint bands
      ≥ 1.0    → the smaller band is fully inside the larger band

    Returns 0.0 when:
      - either band is None / not a two-element sequence
      - either band has zero or negative width (degenerate)
      - the bands do not overlap
    """
    try:
        al, ah = float(a[0]), float(a[1])
        bl, bh = float(b[0]), float(b[1])
    except (TypeError, IndexError, ValueError):
        return 0.0

    # normalise reversed inputs
    if al > ah:
        al, ah = ah, al
    if bl > bh:
        bl, bh = bh, bl

    width_a = ah - al
    width_b = bh - bl
    smaller = min(width_a, width_b)
    if smaller <= 0.0:
        return 0.0

    overlap = max(0.0, min(ah, bh) - max(al, bl))
    return overlap / smaller


# ─── midpoint_pct_delta ───────────────────────────────────────────────────────

def midpoint_pct_delta(a: Any, b: Any) -> Optional[float]:
    """Percentage delta between the midpoints of two ``[low, high]`` bands.

    Returns None when either band is None/invalid or when the Gemini midpoint
    is zero (division not defined).
    """
    try:
        al, ah = float(a[0]), float(a[1])
        bl, bh = float(b[0]), float(b[1])
    except (TypeError, IndexError, ValueError):
        return None

    mid_a = (al + ah) / 2.0
    mid_b = (bl + bh) / 2.0
    return pct_delta(mid_a, mid_b)


# ─── detect_incoherence ───────────────────────────────────────────────────────

def detect_incoherence(levels: dict) -> list[str]:
    """Flag internally-incoherent levels on a SINGLE model's level dict.

    Checks:
      1. ``risk_reward_ratio`` is 0 or None when entry and stop are present
         (the math can't close with no reward).
      2. ``take_profit_1 <= entry_price_high``
         (TP at or below entry — trade has no upside).
      3. ``stop_loss >= entry_price_low``
         (stop not below entry — stop would fire immediately).

    Returns a list of human-readable strings, one per flagged condition.
    Returns [] for a coherent set or when required fields are absent.
    """
    flags: list[str] = []

    entry_low = _to_float(levels.get("entry_price_low"))
    entry_high = _to_float(levels.get("entry_price_high"))
    stop = _to_float(levels.get("stop_loss"))
    tp1 = _to_float(levels.get("take_profit_1"))
    rr = _to_float(levels.get("risk_reward_ratio"))

    # 1. R:R is zero or None when enough context is present
    if entry_low is not None and stop is not None:
        if rr is None or rr == 0.0:
            flags.append(
                "risk_reward_ratio is 0 or None despite entry/stop being set"
            )

    # 2. TP1 at or below entry_high
    if tp1 is not None and entry_high is not None:
        if tp1 <= entry_high:
            flags.append(
                f"take_profit_1 ({tp1}) is at or below entry_price_high ({entry_high})"
            )

    # 3. Stop at or above entry_low
    if stop is not None and entry_low is not None:
        if stop >= entry_low:
            flags.append(
                f"stop_loss ({stop}) is at or above entry_price_low ({entry_low}) — "
                "stop would fire at entry"
            )

    return flags


def _to_float(v: Any) -> Optional[float]:
    """Coerce to float; return None on failure."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─── compare_levels ───────────────────────────────────────────────────────────

def compare_levels(
    gem: dict,
    claude: dict,
    anchored: bool = False,
) -> dict:
    """Compare all trading levels between Gemini and Claude deep-research outputs.

    Args:
        gem:      Gemini level dict (keys: entry_price_low, entry_price_high,
                  stop_loss, take_profit_1, take_profit_2, sell_price_low,
                  sell_price_high, ceiling_exit, risk_reward_ratio,
                  entry_trigger, exit_trigger)
        claude:   Claude level dict (same keys)
        anchored: True when comparison is from a shadow run where Claude saw
                  Gemini's levels first — contaminates the level diff signal.
                  Set False (default) for clean live dual-runs only.

    Returns a dict with the following structure:

        entry:
            gem: [low, high]
            claude: [low, high]
            overlap_fraction: float
            midpoint_pct_delta: float | None

        stop_loss / take_profit_1 / take_profit_2 /
        sell_price_low / sell_price_high / ceiling_exit:
            gem: float | None
            claude: float | None
            pct_delta: float | None

        risk_reward_ratio:
            gem: float | None
            claude: float | None
            abs_delta: float | None

        triggers:
            entry:
                gem: str | None
                claude: str | None
                both_present: bool
            exit:
                gem: str | None
                claude: str | None
                both_present: bool

        incoherence:
            gem:    list[str]   — from detect_incoherence on the Gemini dict
            claude: list[str]   — from detect_incoherence on the Claude dict

        material: bool
        material_reasons: list[str]
        anchored: bool   — echoes the input flag
    """
    gem = gem or {}
    claude = claude or {}

    # ── entry band ────────────────────────────────────────────────────────────
    gem_entry_lo = _to_float(gem.get("entry_price_low"))
    gem_entry_hi = _to_float(gem.get("entry_price_high"))
    cl_entry_lo = _to_float(claude.get("entry_price_low"))
    cl_entry_hi = _to_float(claude.get("entry_price_high"))

    gem_band = [gem_entry_lo, gem_entry_hi] if (gem_entry_lo is not None and gem_entry_hi is not None) else None
    cl_band = [cl_entry_lo, cl_entry_hi] if (cl_entry_lo is not None and cl_entry_hi is not None) else None

    overlap_frac = band_overlap(gem_band, cl_band) if (gem_band and cl_band) else 0.0
    mp_delta = midpoint_pct_delta(gem_band, cl_band) if (gem_band and cl_band) else None

    entry_block = {
        "gem": gem_band,
        "claude": cl_band,
        "overlap_fraction": overlap_frac,
        "midpoint_pct_delta": mp_delta,
    }

    # ── scalar fields ─────────────────────────────────────────────────────────
    scalar_fields = [
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "sell_price_low",
        "sell_price_high",
        "ceiling_exit",
    ]
    scalar_blocks: dict = {}
    for field in scalar_fields:
        gv = _to_float(gem.get(field))
        cv = _to_float(claude.get(field))
        scalar_blocks[field] = {
            "gem": gv,
            "claude": cv,
            "pct_delta": pct_delta(gv, cv),
        }

    # ── risk_reward_ratio ─────────────────────────────────────────────────────
    gem_rr = _to_float(gem.get("risk_reward_ratio"))
    cl_rr = _to_float(claude.get("risk_reward_ratio"))
    if gem_rr is not None and cl_rr is not None:
        abs_delta: Optional[float] = abs(cl_rr - gem_rr)
    else:
        abs_delta = None
    rr_block = {"gem": gem_rr, "claude": cl_rr, "abs_delta": abs_delta}

    # ── triggers ──────────────────────────────────────────────────────────────
    def _trig(gem_key: str, cl_key: str) -> dict:
        gv = gem.get(gem_key) or None
        cv = claude.get(cl_key) or None
        return {
            "gem": gv,
            "claude": cv,
            "both_present": bool(gv and cv),
        }

    triggers_block = {
        "entry": _trig("entry_trigger", "entry_trigger"),
        "exit": _trig("exit_trigger", "exit_trigger"),
    }

    # ── incoherence ───────────────────────────────────────────────────────────
    incoherence_block = {
        "gem": detect_incoherence(gem),
        "claude": detect_incoherence(claude),
    }

    # ── materiality ───────────────────────────────────────────────────────────
    material_reasons: list[str] = []

    # Criterion 1: entry midpoint delta > 3%
    if mp_delta is not None and mp_delta > 3.0:
        material_reasons.append(
            f"entry midpoint diverges by {mp_delta:.2f}% (threshold: 3%)"
        )

    # Criterion 2: stop loss pct_delta > 5%
    stop_pd = scalar_blocks["stop_loss"]["pct_delta"]
    if stop_pd is not None and stop_pd > 5.0:
        material_reasons.append(
            f"stop_loss diverges by {stop_pd:.2f}% (threshold: 5%)"
        )

    # Criterion 3: disjoint entry bands (and both bands were present)
    if gem_band is not None and cl_band is not None and overlap_frac == 0.0:
        material_reasons.append(
            "entry bands are completely disjoint (overlap_fraction == 0)"
        )

    return {
        "entry": entry_block,
        **scalar_blocks,
        "risk_reward_ratio": rr_block,
        "triggers": triggers_block,
        "incoherence": incoherence_block,
        "material": bool(material_reasons),
        "material_reasons": material_reasons,
        "anchored": anchored,
    }
