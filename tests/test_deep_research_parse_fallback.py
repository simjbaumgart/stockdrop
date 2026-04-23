"""
Tests for Task 3 of pipeline-error-hardening: parse-failure fallback must
preserve the PM verdict instead of silently downgrading to AVOID.

Regression: 04-22 ADBE had PM produce BUY_LIMIT, Deep Research Flash-repair
timed out, and the raw fallback overrode everything to AVOID. Correct
behaviour is `review_verdict == 'PENDING_REVIEW'` + `action is None`, so
the PM verdict is preserved upstream and the row can be re-queued.
"""

from unittest.mock import patch

from app.services.deep_research_service import DeepResearchService


def test_parse_failure_returns_pending_review_not_avoid():
    """When Flash repair returns None, _parse_output must not silently
    downgrade the verdict to AVOID. It should return a PENDING_REVIEW
    sentinel with action=None so the PM verdict is preserved upstream."""
    svc = DeepResearchService.__new__(DeepResearchService)  # bypass __init__
    svc.api_key = "fake"

    poll_data = {
        "outputs": [
            {"text": "not valid json and not repairable either"}
        ]
    }

    with patch.object(svc, "_repair_json_using_flash", return_value=None):
        result = svc._parse_output(poll_data, schema_type="individual")

    assert result is not None
    assert result["review_verdict"] == "PENDING_REVIEW", (
        f"Expected PENDING_REVIEW, got {result['review_verdict']!r}"
    )
    assert result["action"] is None, (
        "action must be None so PM verdict is preserved"
    )
    assert "raw_report_full" in result


def test_parse_failure_fallback_preserves_result_schema_keys():
    """The fallback dict must still contain the keys downstream code
    reads (swot_analysis, verification_results, etc.) so _handle_completion
    doesn't KeyError."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    poll_data = {"outputs": [{"text": "unparseable garbage"}]}

    with patch.object(svc, "_repair_json_using_flash", return_value=None):
        result = svc._parse_output(poll_data, schema_type="individual")

    # These keys are all read by _handle_completion / _apply_trading_level_overrides
    expected_keys = {
        "review_verdict", "action", "conviction", "drop_type", "risk_level",
        "catalyst_type", "entry_price_low", "entry_price_high", "stop_loss",
        "take_profit_1", "take_profit_2", "upside_percent",
        "downside_risk_percent", "risk_reward_ratio", "pre_drop_price",
        "entry_trigger", "reassess_in_days", "global_market_analysis",
        "local_market_analysis", "swot_analysis", "verification_results",
        "council_blindspots", "knife_catch_warning", "reason",
        "raw_report_full",
    }
    missing = expected_keys - set(result.keys())
    assert not missing, f"Fallback dict dropped keys: {missing}"


def test_handle_completion_skips_trading_overrides_when_action_none():
    """When the parse fallback is hit (action=None), _handle_completion
    must NOT call _apply_trading_level_overrides — otherwise a bogus
    PENDING_REVIEW row would wipe the entry/stop/tp columns the PM set."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    pending_result = {
        "review_verdict": "PENDING_REVIEW",
        "action": None,
        "conviction": "LOW",
        "risk_level": "Unknown",
        "catalyst_type": "Parse Error",
        "knife_catch_warning": False,
        "swot_analysis": {},
        "verification_results": [],
        "council_blindspots": [],
        "reason": "parse failure",
        "global_market_analysis": "",
        "local_market_analysis": "",
    }

    task = {"symbol": "ADBE", "decision_id": 42}

    with patch(
        "app.database.update_deep_research_data",
        return_value=True,
    ), patch.object(
        svc, "_apply_trading_level_overrides"
    ) as mock_apply, patch.object(
        svc, "_print_deep_research_result"
    ), patch.object(
        svc, "_save_result_to_file"
    ):
        svc._handle_completion(task, pending_result)

    assert not mock_apply.called, (
        "PENDING_REVIEW (action=None) must NOT trigger trading-level overrides"
    )


def test_handle_completion_writes_pending_review_to_verdict_col_when_action_none():
    """When action is None, verdict_for_db should be 'PENDING_REVIEW'
    (not None, which could violate NOT NULL constraints / muddle reports)."""
    svc = DeepResearchService.__new__(DeepResearchService)
    svc.api_key = "fake"

    pending_result = {
        "review_verdict": "PENDING_REVIEW",
        "action": None,
        "conviction": "LOW",
        "risk_level": "Unknown",
        "catalyst_type": "Parse Error",
        "knife_catch_warning": False,
        "swot_analysis": {},
        "verification_results": [],
        "council_blindspots": [],
        "reason": "parse failure",
        "global_market_analysis": "",
        "local_market_analysis": "",
    }

    task = {"symbol": "ADBE", "decision_id": 42}

    captured = {}

    def fake_update(**kwargs):
        captured.update(kwargs)
        return True

    with patch(
        "app.database.update_deep_research_data",
        side_effect=fake_update,
    ), patch.object(
        svc, "_apply_trading_level_overrides"
    ), patch.object(
        svc, "_print_deep_research_result"
    ), patch.object(
        svc, "_save_result_to_file"
    ):
        svc._handle_completion(task, pending_result)

    assert captured.get("verdict") == "PENDING_REVIEW", (
        f"Expected verdict_for_db='PENDING_REVIEW', got {captured.get('verdict')!r}"
    )
    assert captured.get("action") is None
    assert captured.get("review_verdict") == "PENDING_REVIEW"
