"""Regression: on IAG the Competitive Landscape agent analyzed International
Consolidated Airlines (LSE: IAG) instead of IAMGOLD (NYSE: IAG)."""
from app.services.research_service import _entity_guard_report

AIRLINE_REPORT = (
    "## 1. Top Competitors & Performance\n"
    "International Consolidated Airlines Group's main peers are Lufthansa "
    "and Air France-KLM on the North Atlantic corridor. The 70% fuel hedge "
    "position protects margins...\n"
)


def test_wrong_entity_report_discarded():
    out = _entity_guard_report(AIRLINE_REPORT, "IAMGOLD Corporation", "Competitive Landscape Agent")
    assert "WRONG ENTITY" in out
    assert "IAMGOLD" in out


def test_matching_report_passes_through():
    report = "IAMGOLD Corporation is a mid-tier gold producer. " + AIRLINE_REPORT
    out = _entity_guard_report(report, "IAMGOLD Corporation", "Competitive Landscape Agent")
    assert out == report


def test_missing_company_name_is_noop():
    assert _entity_guard_report(AIRLINE_REPORT, "", "X") == AIRLINE_REPORT


def test_none_report_is_noop():
    assert _entity_guard_report(None, "IAMGOLD Corporation", "X") is None
