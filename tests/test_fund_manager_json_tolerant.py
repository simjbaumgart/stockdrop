"""Tolerant PM JSON extraction.

Regression: the Fund Manager intermittently emits JSON with a single
structural defect (e.g. a trailing comma) that makes strict json.loads fail,
forcing a Gemini Flash repair pass that silently drops list items
(key_factors came back with 1 of 3 entries for FICO/PAYP/CELH).

_extract_json should tolerate the most common, value-preserving defect
(trailing commas) WITHOUT corrupting string contents, so the complete object
parses and all key_factors survive — no lossy repair needed.
"""
import json

from app.services.research_service import ResearchService


def _svc():
    return ResearchService.__new__(ResearchService)  # bypass __init__/network


def test_trailing_comma_in_list_is_tolerated():
    svc = _svc()
    text = (
        '{"action": "BUY", "conviction": "HIGH", '
        '"key_factors": ["margin expansion", "buyback", "oversold",]}'
    )
    out = svc._extract_json(text)
    assert out is not None
    assert out["action"] == "BUY"
    assert out["key_factors"] == ["margin expansion", "buyback", "oversold"]


def test_trailing_comma_in_object_is_tolerated():
    svc = _svc()
    text = '{"action": "AVOID", "conviction": "LOW", "reason": "weak guidance",}'
    out = svc._extract_json(text)
    assert out is not None
    assert out["conviction"] == "LOW"
    assert out["reason"] == "weak guidance"


def test_strings_containing_comma_bracket_are_not_corrupted():
    """The trailing-comma stripper must be string-aware: a ',]' or ',}' that
    lives INSIDE a string value must be preserved verbatim."""
    svc = _svc()
    reason = "Cut guidance, [per CFO], and missed,"
    text = json.dumps({"action": "AVOID", "reason": reason, "key_factors": ["a", "b"]})
    # Inject a real trailing comma into the list to force the tolerant path.
    text = text.replace('["a", "b"]', '["a", "b",]')
    out = svc._extract_json(text)
    assert out is not None
    assert out["reason"] == reason  # not mangled
    assert out["key_factors"] == ["a", "b"]


def test_well_formed_json_still_parses():
    svc = _svc()
    text = '{"action": "BUY", "conviction": "MODERATE", "key_factors": ["x"]}'
    out = svc._extract_json(text)
    assert out == {"action": "BUY", "conviction": "MODERATE", "key_factors": ["x"]}
