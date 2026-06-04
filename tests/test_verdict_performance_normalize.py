"""Regression tests for normalize_to_intent — robustness to non-string inputs.

The live decision_points DB has NULL cells in the verdict columns, which arrive
as float NaN via pandas. normalize_to_intent must treat those as "no verdict"
rather than crashing on `.strip()` (NaN is truthy, so `nan or ""` returns nan).
"""

import math

from scripts.analysis.verdict_performance import normalize_to_intent


def test_normalize_handles_nan_float():
    assert normalize_to_intent(float("nan")) == ""


def test_normalize_handles_none():
    assert normalize_to_intent(None) == ""


def test_normalize_handles_empty_string():
    assert normalize_to_intent("") == ""


def test_normalize_still_maps_known_strings():
    assert normalize_to_intent("BUY") == "ENTER_NOW"
    assert normalize_to_intent("BUY_LIMIT") == "ENTER_LIMIT"
    assert normalize_to_intent("PASS") == "AVOID"
    assert normalize_to_intent("HOLD") == "NEUTRAL"
