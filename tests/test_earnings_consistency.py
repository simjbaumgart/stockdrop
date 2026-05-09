"""Catch the TOST-style failure: PM narrates 'missed estimates' while
Finnhub reports a positive surprise %. Downgrade the verdict by one tier."""
import pytest

from app.utils.earnings_consistency import (
    check_narrative_consistency,
    downgrade_action,
)


def test_beat_narrative_with_negative_surprise_is_inconsistent():
    out = check_narrative_consistency(
        reasoning="The company beat earnings on strong margin expansion.",
        surprise_pct=-12.0,
    )
    assert out.inconsistent is True
    assert out.flag == "EARNINGS_NARRATIVE_INCONSISTENT"
    assert "beat" in out.reason.lower()


def test_miss_narrative_with_positive_surprise_is_inconsistent():
    out = check_narrative_consistency(
        reasoning="Toast missed estimates this quarter, dragging the stock down.",
        surprise_pct=35.0,
    )
    assert out.inconsistent is True
    assert out.flag == "EARNINGS_NARRATIVE_INCONSISTENT"


def test_consistent_beat_passes():
    out = check_narrative_consistency(
        reasoning="Strong earnings beat across the board.",
        surprise_pct=8.0,
    )
    assert out.inconsistent is False


def test_consistent_miss_passes():
    out = check_narrative_consistency(
        reasoning="Disappointing miss; revenue weak too.",
        surprise_pct=-5.0,
    )
    assert out.inconsistent is False


def test_neutral_narrative_passes():
    out = check_narrative_consistency(
        reasoning="Results were in line with expectations.",
        surprise_pct=0.5,
    )
    assert out.inconsistent is False


def test_skipped_when_no_surprise_data():
    out = check_narrative_consistency(
        reasoning="The company beat on earnings.",
        surprise_pct=None,
    )
    assert out.inconsistent is False
    assert out.reason == "no_surprise_data"


def test_word_boundary_avoids_false_positive_on_unbeatable():
    # 'unbeatable' contains 'beat' as a substring — must not match
    out = check_narrative_consistency(
        reasoning="The product is unbeatable in its category.",
        surprise_pct=-10.0,
    )
    assert out.inconsistent is False


def test_downgrade_buy_to_buy_limit():
    assert downgrade_action("BUY") == "BUY_LIMIT"


def test_downgrade_buy_limit_to_watch():
    assert downgrade_action("BUY_LIMIT") == "WATCH"


def test_downgrade_wait_for_stab_unchanged():
    assert downgrade_action("WAIT_FOR_STAB") == "WAIT_FOR_STAB"


def test_downgrade_avoid_unchanged():
    assert downgrade_action("AVOID") == "AVOID"


def test_downgrade_unknown_unchanged():
    assert downgrade_action("HOLD") == "HOLD"
