"""Regression: PM emitted zero-width entry bands (ESAB 87.7-87.7,
FTAI 213.47-213.47) which rendered degenerate zones and fed a
meaningless R/R checkmark into the trade report."""
from app.utils.stop_loss_guard import repair_entry_band


def test_zero_width_band_repaired_to_1pct():
    assert repair_entry_band(87.7, 87.7) == (86.82, 88.58)


def test_inverted_band_repaired_around_midpoint():
    low, high = repair_entry_band(90.0, 88.0)
    assert low < 89.0 < high


def test_valid_band_untouched():
    assert repair_entry_band(85.0, 88.0) is None


def test_non_numeric_and_missing_are_noop():
    assert repair_entry_band(None, 88.0) is None
    assert repair_entry_band("N/A", "N/A") is None


def test_non_positive_levels_are_noop():
    # zero/negative levels are handled by the existing zero-level guards
    assert repair_entry_band(0.0, 0.0) is None
