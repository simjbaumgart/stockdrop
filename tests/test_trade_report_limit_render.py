"""Regression: UMC/KXIAY rendered the council's BUY_LIMIT rec with the DR's
overridden-AVOID levels as a live limit price."""
from scripts.core.generate_trade_report import _build_limit_str


def test_dr_avoid_override_blanks_limit():
    # UMC: PM BUY_LIMIT, DR overrode to AVOID with informational levels
    assert _build_limit_str("BUY_LIMIT", "AVOID", 7.40, 11.53) == "-"


def test_dr_sell_override_blanks_limit():
    assert _build_limit_str("BUY_LIMIT", "SELL", 40.18, 42.21) == "-"


def test_dr_confirmed_buy_limit_renders_band():
    assert _build_limit_str("BUY_LIMIT", "BUY_LIMIT", 40.18, 42.21) == "40.18-42.21"


def test_no_dr_verdict_renders_band():
    assert _build_limit_str("BUY_LIMIT", None, 40.18, 42.21) == "40.18-42.21"


def test_non_buy_limit_rec_is_blank():
    assert _build_limit_str("AVOID", None, 87.7, 87.7) == "-"


def test_single_sided_levels():
    assert _build_limit_str("BUY_LIMIT", "", 7.40, None) == "7.40"
    assert _build_limit_str("BUY_LIMIT", "", None, 11.53) == "11.53"
