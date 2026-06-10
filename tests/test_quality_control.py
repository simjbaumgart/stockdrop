"""Tests for QualityControlService report validation.

Regression: economics is a conditional agent — it's intentionally "" when
NEEDS_ECONOMICS: FALSE. The QC pass used to flag that empty string as
"suspiciously short" AND rewrite it to a [SHORT INPUT DETECTED] sentinel,
polluting a field the pipeline meant to leave blank.
"""
from app.services.quality_control_service import QualityControlService as QC


def test_empty_economics_is_not_flagged_or_rewritten():
    reports = {"economics": "", "technical": "x" * 300}
    out = QC.validate_reports(dict(reports), "ABVX", ["economics", "technical"])
    assert out["economics"] == "", "empty conditional economics must be left untouched"
    assert "SHORT INPUT DETECTED" not in out["economics"]


def test_missing_economics_none_is_not_flagged():
    reports = {"economics": None, "technical": "x" * 300}
    out = QC.validate_reports(dict(reports), "NU", ["economics", "technical"])
    assert out["economics"] is None


def test_short_nonempty_economics_is_still_flagged():
    # A conditional agent that DID run but produced a too-short report is a
    # real defect and must still be flagged.
    reports = {"economics": "too short"}
    out = QC.validate_reports(dict(reports), "MSTR", ["economics"])
    assert out["economics"].startswith("[SHORT INPUT DETECTED")


def test_short_required_section_still_flagged():
    reports = {"technical": "brief"}
    out = QC.validate_reports(dict(reports), "CVNA", ["technical"])
    assert out["technical"].startswith("[SHORT INPUT DETECTED")


def test_long_section_passes_through_unchanged():
    long_text = "y" * 250
    reports = {"news": long_text}
    out = QC.validate_reports(dict(reports), "PRAX", ["news"])
    assert out["news"] == long_text
