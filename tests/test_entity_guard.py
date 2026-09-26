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


def test_error_stub_passes_through_unguarded():
    # A failed-agent stub must NOT be rewritten into the WRONG ENTITY marker,
    # or the Phase 1 retry loop can no longer see the failure.
    stub = "[Error in Competitive Landscape Agent: 503 UNAVAILABLE]"
    out = _entity_guard_report(stub, "IAMGOLD Corporation", "Competitive Landscape Agent")
    assert out == stub


def test_late_company_mention_passes_full_text_match():
    # LLM prose may name the company late (e.g. after a long market-context
    # or competitor-table section) — the guard matches the whole report.
    report = ("Market backdrop paragraph. " * 80) + "IAMGOLD Corporation remains the focus."
    out = _entity_guard_report(report, "IAMGOLD Corporation", "Competitive Landscape Agent")
    assert out == report


def test_discard_marker_counts_as_failed_report():
    # The marker must register as failed so the Phase-1 retry loop
    # re-attempts the agent and the liveness count excludes the discard.
    from app.services.research_service import _is_real_report

    marker = _entity_guard_report(
        AIRLINE_REPORT, "IAMGOLD Corporation", "Competitive Landscape Agent"
    )
    assert marker.startswith("[REPORT DISCARDED")
    assert _is_real_report(marker) is False
