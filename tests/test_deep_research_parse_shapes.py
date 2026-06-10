"""Tests for _parse_output / _extract_output_texts shape-tolerance.

DR completed polls do NOT reliably carry results under 'outputs' (the
CBOE/SSRM/BLTE empty-outputs incidents). The parser must extract the
reviewer's JSON regardless of which common container the Interactions API
uses, and fall back to a loud diagnostic (None) only when nothing is
extractable.
"""
import json

from app.services.deep_research_service import DeepResearchService

VALID = {"review_verdict": "BUY", "action": "BUY", "conviction": "HIGH"}
VALID_JSON = json.dumps(VALID)


def _svc():
    return DeepResearchService.__new__(DeepResearchService)  # bypass __init__/network


def test_legacy_outputs_shape():
    svc = _svc()
    poll = {"status": "completed", "outputs": [{"text": VALID_JSON}]}
    assert svc._parse_output(poll) == VALID


def test_outputs_plain_string_items():
    svc = _svc()
    poll = {"status": "completed", "outputs": [VALID_JSON]}
    assert svc._parse_output(poll) == VALID


def test_response_generatecontent_shape():
    svc = _svc()
    poll = {
        "status": "completed",
        "response": {"candidates": [{"content": {"parts": [{"text": VALID_JSON}]}}]},
    }
    assert svc._parse_output(poll) == VALID


def test_top_level_candidates_shape():
    svc = _svc()
    poll = {
        "state": "COMPLETED",
        "candidates": [{"content": {"parts": [{"text": VALID_JSON}]}}],
    }
    assert svc._parse_output(poll) == VALID


def test_response_as_json_string():
    svc = _svc()
    poll = {"status": "completed", "response": VALID_JSON}
    assert svc._parse_output(poll) == VALID


def test_result_with_fenced_json():
    svc = _svc()
    poll = {"status": "completed", "result": f"```json\n{VALID_JSON}\n```"}
    assert svc._parse_output(poll) == VALID


def test_deep_walk_fallback_finds_nested_text():
    svc = _svc()
    poll = {"status": "completed", "weird": {"nested": {"blob": {"text": VALID_JSON}}}}
    assert svc._parse_output(poll) == VALID


def test_steps_shape_uses_model_output_not_user_input():
    """The real Interactions agent shape. The user_input step embeds the prompt
    (which contains the JSON output schema with braces) — the parser MUST return
    the model's answer, not mis-parse the prompt."""
    svc = _svc()
    poll = {
        "status": "completed",
        "usage": {"total_input_tokens": 275123, "total_output_tokens": 8051},
        "steps": [
            {"type": "user_input", "content": [
                {"text": 'Analyze BMNR. Return JSON like {"review_verdict": "X", "action": "Y"}'}
            ]},
            {"type": "tool_call", "content": [{"text": "searching the web..."}]},
            {"type": "agent_output", "content": [{"text": VALID_JSON}]},
        ],
    }
    assert svc._parse_output(poll) == VALID


def test_steps_shape_takes_last_model_step():
    svc = _svc()
    stale = json.dumps({"review_verdict": "OLD", "action": "WAIT"})
    poll = {
        "status": "completed",
        "steps": [
            {"type": "user_input", "content": [{"text": "prompt"}]},
            {"type": "agent_output", "content": [{"text": stale}]},
            {"type": "agent_output", "content": [{"text": VALID_JSON}]},
        ],
    }
    assert svc._parse_output(poll) == VALID


def test_steps_content_as_plain_string():
    svc = _svc()
    poll = {
        "status": "completed",
        "steps": [
            {"role": "user", "content": "prompt text"},
            {"role": "model", "content": VALID_JSON},
        ],
    }
    assert svc._parse_output(poll) == VALID


def test_empty_completion_returns_none_without_raising():
    svc = _svc()
    poll = {"status": "completed", "usageMetadata": {}}  # no text anywhere
    # Must return None (not raise) — the empty/unknown-shape diagnostic path.
    assert svc._parse_output(poll) is None


def test_non_dict_poll_returns_none():
    svc = _svc()
    assert svc._parse_output("garbage") is None
