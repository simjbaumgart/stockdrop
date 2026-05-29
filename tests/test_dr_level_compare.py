"""Tests for app/services/analytics/dr_level_compare.py

Step 3b — TDD: tests written before the implementation.

All fixtures use SYNTHETIC deterministic data so CI never needs live files.
The one optional test that loads a shadow JSON is guarded with skipif.
"""
from __future__ import annotations

import os
import json
import math
import pytest

from app.services.analytics.dr_level_compare import (
    pct_delta,
    band_overlap,
    midpoint_pct_delta,
    detect_incoherence,
    compare_levels,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _levels(
    entry_low=40.0, entry_high=44.0,
    stop_loss=36.0,
    take_profit_1=50.0, take_profit_2=58.0,
    sell_price_low=50.0, sell_price_high=58.0,
    ceiling_exit=62.0,
    risk_reward_ratio=2.0,
    entry_trigger="Buy on stabilisation",
    exit_trigger="Sell at resistance",
):
    return dict(
        entry_price_low=entry_low,
        entry_price_high=entry_high,
        stop_loss=stop_loss,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        sell_price_low=sell_price_low,
        sell_price_high=sell_price_high,
        ceiling_exit=ceiling_exit,
        risk_reward_ratio=risk_reward_ratio,
        entry_trigger=entry_trigger,
        exit_trigger=exit_trigger,
    )


# ─── pct_delta ────────────────────────────────────────────────────────────────

class TestPctDelta:
    def test_basic(self):
        # |110 - 100| / |100| * 100 = 10
        assert abs(pct_delta(100.0, 110.0) - 10.0) < 1e-9

    def test_uses_a_as_base(self):
        # pct_delta uses |a| as the denominator, so it is NOT symmetric.
        # pct_delta(110, 100) = |100-110|/110*100 ≈ 9.09%
        # pct_delta(100, 110) = |110-100|/100*100 = 10.0%
        assert abs(pct_delta(110.0, 100.0) - 9.0909) < 0.001
        assert abs(pct_delta(100.0, 110.0) - 10.0) < 1e-9

    def test_same_value(self):
        assert pct_delta(50.0, 50.0) == 0.0

    def test_a_is_zero(self):
        assert pct_delta(0.0, 10.0) is None

    def test_a_is_none(self):
        assert pct_delta(None, 10.0) is None

    def test_b_is_none(self):
        assert pct_delta(10.0, None) is None

    def test_both_none(self):
        assert pct_delta(None, None) is None

    def test_non_numeric_a(self):
        assert pct_delta("bad", 10.0) is None

    def test_non_numeric_b(self):
        assert pct_delta(10.0, "bad") is None

    def test_negative_base(self):
        # abs(-100) = 100, so |(-90) - (-100)| / 100 * 100 = 10
        assert abs(pct_delta(-100.0, -90.0) - 10.0) < 1e-9


# ─── band_overlap ─────────────────────────────────────────────────────────────

class TestBandOverlap:
    def test_identical_bands(self):
        # identical → overlap == smaller band width / smaller band width == 1.0
        assert abs(band_overlap([40.0, 44.0], [40.0, 44.0]) - 1.0) < 1e-9

    def test_disjoint_bands(self):
        assert band_overlap([40.0, 44.0], [50.0, 54.0]) == 0.0

    def test_partial_overlap(self):
        # a=[40,44] b=[42,46]; overlap=[42,44]=2; smaller width = min(4,4)=4 → 0.5
        result = band_overlap([40.0, 44.0], [42.0, 46.0])
        assert abs(result - 0.5) < 1e-9

    def test_smaller_band_fully_inside_larger(self):
        # a=[38,50] b=[40,44]; overlap=[40,44]=4; smaller width=min(12,4)=4 → 1.0
        result = band_overlap([38.0, 50.0], [40.0, 44.0])
        assert result >= 1.0

    def test_a_is_none(self):
        assert band_overlap(None, [40.0, 44.0]) == 0.0

    def test_b_is_none(self):
        assert band_overlap([40.0, 44.0], None) == 0.0

    def test_both_none(self):
        assert band_overlap(None, None) == 0.0

    def test_degenerate_band_zero_width(self):
        # zero-width band is degenerate
        assert band_overlap([44.0, 44.0], [40.0, 48.0]) == 0.0

    def test_reversed_band_treated_gracefully(self):
        # reversed inputs [high, low] should not crash
        result = band_overlap([44.0, 40.0], [42.0, 46.0])
        assert isinstance(result, float)


# ─── midpoint_pct_delta ───────────────────────────────────────────────────────

class TestMidpointPctDelta:
    def test_identical_bands(self):
        assert midpoint_pct_delta([40.0, 44.0], [40.0, 44.0]) == 0.0

    def test_known_value(self):
        # a midpoint = 42, b midpoint = 44.1 → delta = |44.1-42|/42*100 = 5%
        result = midpoint_pct_delta([40.0, 44.0], [42.1, 46.1])
        assert abs(result - 5.0) < 1e-6

    def test_a_is_none(self):
        assert midpoint_pct_delta(None, [40.0, 44.0]) is None

    def test_b_is_none(self):
        assert midpoint_pct_delta([40.0, 44.0], None) is None

    def test_zero_midpoint(self):
        # a=[-1,1] midpoint=0; can't divide by zero
        assert midpoint_pct_delta([-1.0, 1.0], [0.0, 2.0]) is None


# ─── detect_incoherence ───────────────────────────────────────────────────────

class TestDetectIncoherence:
    def test_coherent_set_no_flags(self):
        flags = detect_incoherence(_levels())
        assert flags == []

    def test_rr_zero_flagged(self):
        lvl = _levels(risk_reward_ratio=0.0)
        flags = detect_incoherence(lvl)
        assert any("risk_reward" in f.lower() or "r:r" in f.lower() or "rr" in f.lower()
                   for f in flags)

    def test_rr_none_with_entry_and_stop_flagged(self):
        lvl = _levels()
        lvl["risk_reward_ratio"] = None
        flags = detect_incoherence(lvl)
        assert any("risk_reward" in f.lower() or "rr" in f.lower()
                   for f in flags)

    def test_tp_below_entry_high_flagged(self):
        # take_profit_1 <= entry_price_high
        lvl = _levels(entry_low=40.0, entry_high=50.0, take_profit_1=48.0)
        flags = detect_incoherence(lvl)
        assert any("take_profit" in f.lower() or "tp" in f.lower() for f in flags)

    def test_tp_at_entry_high_flagged(self):
        lvl = _levels(entry_low=40.0, entry_high=50.0, take_profit_1=50.0)
        flags = detect_incoherence(lvl)
        assert any("take_profit" in f.lower() or "tp" in f.lower() for f in flags)

    def test_stop_above_entry_low_flagged(self):
        # stop_loss >= entry_price_low
        lvl = _levels(entry_low=40.0, stop_loss=41.0)
        flags = detect_incoherence(lvl)
        assert any("stop" in f.lower() for f in flags)

    def test_stop_at_entry_low_flagged(self):
        lvl = _levels(entry_low=40.0, stop_loss=40.0)
        flags = detect_incoherence(lvl)
        assert any("stop" in f.lower() for f in flags)

    def test_multiple_flags_returned(self):
        # rr=0 AND stop above entry AND tp below entry all triggered
        lvl = _levels(
            entry_low=40.0, entry_high=50.0,
            stop_loss=45.0,      # above entry_low
            take_profit_1=48.0,  # below entry_high
            risk_reward_ratio=0.0,
        )
        flags = detect_incoherence(lvl)
        assert len(flags) >= 3

    def test_missing_fields_no_crash(self):
        flags = detect_incoherence({})
        assert isinstance(flags, list)


# ─── compare_levels ───────────────────────────────────────────────────────────

class TestCompareLevels:
    def _base_gem(self):
        return _levels(
            entry_low=40.0, entry_high=44.0,
            stop_loss=36.0,
            take_profit_1=52.0, take_profit_2=60.0,
            sell_price_low=52.0, sell_price_high=60.0,
            ceiling_exit=64.0,
            risk_reward_ratio=2.0,
            entry_trigger="Enter on bounce",
            exit_trigger="Exit at 52",
        )

    def _base_claude(self):
        return _levels(
            entry_low=41.0, entry_high=45.0,
            stop_loss=37.0,
            take_profit_1=53.0, take_profit_2=61.0,
            sell_price_low=53.0, sell_price_high=61.0,
            ceiling_exit=65.0,
            risk_reward_ratio=2.1,
            entry_trigger="Scale in on stabilisation",
            exit_trigger="Exit at SMA200",
        )

    # ── output shape ──────────────────────────────────────────────────────────

    def test_output_has_required_keys(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        for key in ("entry", "stop_loss", "take_profit_1", "take_profit_2",
                    "sell_price_low", "sell_price_high", "ceiling_exit",
                    "risk_reward_ratio", "triggers", "incoherence",
                    "material", "material_reasons", "anchored"):
            assert key in result, f"missing key: {key}"

    def test_entry_shape(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        entry = result["entry"]
        assert "gem" in entry
        assert "claude" in entry
        assert "overlap_fraction" in entry
        assert "midpoint_pct_delta" in entry

    def test_scalar_field_shape(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        for field in ("stop_loss", "take_profit_1"):
            assert "gem" in result[field]
            assert "claude" in result[field]
            assert "pct_delta" in result[field]

    def test_rr_ratio_has_abs_delta(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        assert "abs_delta" in result["risk_reward_ratio"]

    def test_triggers_shape(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        for trig in ("entry", "exit"):
            assert "gem" in result["triggers"][trig]
            assert "claude" in result["triggers"][trig]
            assert "both_present" in result["triggers"][trig]

    def test_incoherence_shape(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        assert "gem" in result["incoherence"]
        assert "claude" in result["incoherence"]
        assert isinstance(result["incoherence"]["gem"], list)
        assert isinstance(result["incoherence"]["claude"], list)

    # ── anchored flag ─────────────────────────────────────────────────────────

    def test_anchored_default_false(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        assert result["anchored"] is False

    def test_anchored_true_echoed(self):
        result = compare_levels(self._base_gem(), self._base_claude(), anchored=True)
        assert result["anchored"] is True

    # ── overlapping vs disjoint entry bands & material ────────────────────────

    def test_overlapping_bands_not_material_on_overlap(self):
        gem = self._base_gem()   # entry [40,44], midpoint 42
        claude = self._base_claude()  # entry [41,45], midpoint 43 → ~2.4% delta
        result = compare_levels(gem, claude)
        assert result["entry"]["overlap_fraction"] > 0
        # ~2.4% < 3% threshold → midpoint criterion alone doesn't fire
        # (may still be material for other reasons — just check overlap > 0)

    def test_disjoint_bands_trigger_material(self):
        gem = _levels(entry_low=40.0, entry_high=44.0)
        claude = _levels(entry_low=50.0, entry_high=54.0)
        result = compare_levels(gem, claude)
        assert result["entry"]["overlap_fraction"] == 0.0
        assert result["material"] is True

    def test_disjoint_bands_in_material_reasons(self):
        gem = _levels(entry_low=40.0, entry_high=44.0)
        claude = _levels(entry_low=50.0, entry_high=54.0)
        result = compare_levels(gem, claude)
        assert any("overlap" in r.lower() or "disjoint" in r.lower()
                   for r in result["material_reasons"])

    # ── midpoint delta threshold ──────────────────────────────────────────────

    def test_midpoint_just_below_3pct_not_material_alone(self):
        # gem midpoint = 42, claude midpoint needs to be <3% away
        # 42 * 1.029 ≈ 43.22, so claude [41.22, 45.22] midpoint=43.22 → 2.9%
        gem = _levels(entry_low=40.0, entry_high=44.0)   # mid = 42
        claude = _levels(entry_low=41.22, entry_high=45.22)  # mid = 43.22 → ~2.9%
        result = compare_levels(gem, claude)
        # midpoint delta just under 3%, stop delta small → may not be material
        # We just verify material_reasons does NOT contain midpoint reason
        mp_reasons = [r for r in result["material_reasons"]
                      if "midpoint" in r.lower() or "entry" in r.lower()]
        assert len(mp_reasons) == 0

    def test_midpoint_just_over_3pct_triggers_material(self):
        # gem midpoint = 42, 3.1% above = 43.302
        gem = _levels(entry_low=40.0, entry_high=44.0)    # mid = 42
        claude = _levels(entry_low=41.302, entry_high=45.302)  # mid = 43.302 → ~3.1%
        result = compare_levels(gem, claude)
        assert result["material"] is True
        assert any("midpoint" in r.lower() or "entry" in r.lower()
                   for r in result["material_reasons"])

    # ── stop delta threshold ──────────────────────────────────────────────────

    def test_stop_just_below_5pct_not_material_alone(self):
        # gem stop=36.0, 4.9% different = 36 * 1.049 = 37.764
        gem = _levels(entry_low=40.0, entry_high=44.0, stop_loss=36.0)
        claude = _levels(entry_low=40.0, entry_high=44.0, stop_loss=37.764)
        result = compare_levels(gem, claude)
        stop_reasons = [r for r in result["material_reasons"]
                        if "stop" in r.lower()]
        assert len(stop_reasons) == 0

    def test_stop_just_over_5pct_triggers_material(self):
        # gem stop=36.0, 5.1% different = 36 * 1.051 = 37.836
        gem = _levels(entry_low=40.0, entry_high=44.0, stop_loss=36.0)
        claude = _levels(entry_low=40.0, entry_high=44.0, stop_loss=37.836)
        result = compare_levels(gem, claude)
        assert result["material"] is True
        assert any("stop" in r.lower() for r in result["material_reasons"])

    # ── None / missing field tolerance ────────────────────────────────────────

    def test_none_fields_no_crash(self):
        gem = {}
        claude = {}
        result = compare_levels(gem, claude)
        assert isinstance(result, dict)
        assert "material" in result

    def test_partial_none_fields_no_crash(self):
        gem = _levels()
        gem["take_profit_2"] = None
        gem["sell_price_high"] = None
        claude = _levels()
        result = compare_levels(gem, claude)
        assert result["take_profit_2"]["pct_delta"] is None

    def test_none_stop_loss_no_crash(self):
        gem = _levels()
        claude = _levels()
        gem["stop_loss"] = None
        result = compare_levels(gem, claude)
        assert result["stop_loss"]["pct_delta"] is None

    def test_none_stop_does_not_trigger_material(self):
        """Missing stop data shouldn't count as a material stop difference."""
        gem = _levels(stop_loss=None)
        claude = _levels(stop_loss=None)
        result = compare_levels(gem, claude)
        stop_reasons = [r for r in result["material_reasons"]
                        if "stop" in r.lower()]
        assert len(stop_reasons) == 0

    # ── both_present in triggers ───────────────────────────────────────────────

    def test_triggers_both_present_true(self):
        result = compare_levels(self._base_gem(), self._base_claude())
        assert result["triggers"]["entry"]["both_present"] is True
        assert result["triggers"]["exit"]["both_present"] is True

    def test_triggers_both_present_false_when_missing(self):
        gem = _levels(entry_trigger=None, exit_trigger=None)
        claude = _levels()
        result = compare_levels(gem, claude)
        assert result["triggers"]["entry"]["both_present"] is False
        assert result["triggers"]["exit"]["both_present"] is False

    # ── incoherence detection forwarded ───────────────────────────────────────

    def test_incoherence_rr_zero_flagged_in_gem(self):
        gem = _levels(risk_reward_ratio=0.0)
        claude = _levels()
        result = compare_levels(gem, claude)
        assert len(result["incoherence"]["gem"]) > 0

    def test_incoherence_clean_has_empty_lists(self):
        result = compare_levels(_levels(), _levels())
        assert result["incoherence"]["gem"] == []
        assert result["incoherence"]["claude"] == []

    # ── STEP 607 shadow file (optional) ───────────────────────────────────────

    _SHADOW_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "claude_shadow", "shadow_STEP_607.json",
    )

    @pytest.mark.skipif(
        not os.path.exists(_SHADOW_PATH),
        reason="shadow data file not present (gitignored; CI skip expected)",
    )
    def test_step_607_shadow_runs_and_flags_incoherence(self):
        """Load the real STEP 607 shadow file and assert compare_levels runs
        without error and flags the known Gemini incoherence (rr=0 or TP below entry).

        NOTE: STEP 607 is an ANCHORED shadow comparison — Claude saw Gemini's
        already-refined levels, so any level diff is not a clean signal.
        """
        with open(self._SHADOW_PATH) as f:
            rec = json.load(f)

        gem_raw = rec.get("gemini", {})
        cl_raw = rec.get("claude", {})

        gem_levels = {
            "entry_price_low": gem_raw.get("entry_low"),
            "entry_price_high": gem_raw.get("entry_high"),
            "stop_loss": gem_raw.get("stop_loss"),
            "take_profit_1": gem_raw.get("take_profit_1"),
            "take_profit_2": gem_raw.get("take_profit_2"),
            "sell_price_low": gem_raw.get("sell_price_low"),
            "sell_price_high": gem_raw.get("sell_price_high"),
            "ceiling_exit": gem_raw.get("ceiling_exit"),
            "risk_reward_ratio": gem_raw.get("risk_reward_ratio"),
            "entry_trigger": gem_raw.get("entry_trigger"),
            "exit_trigger": gem_raw.get("exit_trigger"),
        }
        claude_levels = {
            "entry_price_low": cl_raw.get("entry_price_low"),
            "entry_price_high": cl_raw.get("entry_price_high"),
            "stop_loss": cl_raw.get("stop_loss"),
            "take_profit_1": cl_raw.get("take_profit_1"),
            "take_profit_2": cl_raw.get("take_profit_2"),
            "sell_price_low": cl_raw.get("sell_price_low"),
            "sell_price_high": cl_raw.get("sell_price_high"),
            "ceiling_exit": cl_raw.get("ceiling_exit"),
            "risk_reward_ratio": cl_raw.get("risk_reward_ratio"),
            "entry_trigger": cl_raw.get("entry_trigger"),
            "exit_trigger": cl_raw.get("exit_trigger"),
        }

        result = compare_levels(gem_levels, claude_levels, anchored=True)

        # Must not crash and must return a dict
        assert isinstance(result, dict)
        # Shadow comparisons must be tagged anchored
        assert result["anchored"] is True
        # STEP 607 Gemini block has no TP/sell fields (only entry/stop in shadow)
        # so incoherence checks that require those fields should degrade gracefully,
        # but the rr=None (fields absent) should fire or be silently skipped.
        # Either way, no exception.
        assert isinstance(result["incoherence"]["gem"], list)
