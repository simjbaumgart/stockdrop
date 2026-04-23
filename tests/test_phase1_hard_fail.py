from unittest.mock import patch

from app.services.quality_control_service import QualityControlService
from app.services.research_service import ResearchService, _is_real_report


def test_short_threshold_is_200():
    reports = {"technical": "x" * 150}
    out = QualityControlService.validate_reports(reports, "TEST", ["technical"])
    # 150 chars should now be flagged as SHORT INPUT, matching _is_real_report.
    assert out["technical"].startswith("[SHORT INPUT DETECTED:")


def test_200_chars_passes():
    reports = {"technical": "x" * 250}
    out = QualityControlService.validate_reports(reports, "TEST", ["technical"])
    assert not out["technical"].startswith("[SHORT INPUT DETECTED:")


def test_bby_scenario_aborts_instead_of_high_conviction():
    """All 5 core agents return 70-char error stubs -> pipeline must abort,
    not produce a PM decision."""
    svc = ResearchService()
    # Force _call_agent to always return a short error stub.
    fake_stub = "[Error in Agent: Connection reset by peer after 3 retries]"
    assert len(fake_stub) < 200  # guard: must be shorter than the real threshold

    # Also stub seeking_alpha (it goes through a different service, not _call_agent).
    with patch.object(svc, "_call_agent", return_value=fake_stub), \
         patch.object(svc, "_check_and_increment_usage", return_value=True), \
         patch(
             "app.services.research_service.seeking_alpha_service.get_evidence",
             return_value=fake_stub,
         ), \
         patch(
             "app.services.research_service.seeking_alpha_service.get_counts",
             return_value={"total": 0, "analysis": 0, "news": 0, "pr": 0},
         ):
        result = svc.analyze_stock(
            "BBY",
            {"change_percent": -6.1, "price": 50.0, "volume": 1_000_000},
        )

    # Assert we produced an abstention, not a HIGH-conviction verdict.
    # _build_insufficient_data_response returns recommendation="PASS_INSUFFICIENT_DATA"
    # and conviction="NONE" (see research_service.py:1131,1136).
    assert result.get("recommendation") in (
        "ABSTAIN",
        "INSUFFICIENT_DATA",
        "PASS",
        "PASS_INSUFFICIENT_DATA",
    )
    # _build_insufficient_data_response sets conviction="NONE" (research_service.py ~L1165).
    # Asserting the exact value — "!= HIGH" would pass for {} or any error shape.
    assert result.get("conviction") == "NONE"


def test_is_real_report_rejects_short_stubs():
    assert not _is_real_report("[Error: short]")
    assert not _is_real_report("")
    assert not _is_real_report(None)
    assert _is_real_report("x" * 400)
    # Long-but-erroring string: rejected via marker check, NOT length check.
    assert not _is_real_report("[Error: " + "x" * 400 + "]")
