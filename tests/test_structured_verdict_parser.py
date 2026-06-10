"""Unit tests for the shared STRUCTURED_VERDICT parser (Phase 2)."""

import json
import os

import pytest

from app.services.research_service import (
    STRUCTURED_VERDICT_MARKER,
    _extract_structured_verdict,
)


REPORT_PROSE = "Risk Assessment\nLong prose analysis here...\n"


def test_valid_json_block():
    report = REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n{{"falling_knife": "YES", "top_risk": "Guidance cut"}}'
    assert _extract_structured_verdict(report, "risk") == {
        "falling_knife": "YES", "top_risk": "Guidance cut",
    }


def test_markdown_fenced_json():
    report = REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n```json\n{{"sentiment": "BEARISH", "drop_reason_confirmed": true, "named_catalyst": null}}\n```'
    assert _extract_structured_verdict(report, "news") == {
        "sentiment": "BEARISH", "drop_reason_confirmed": True, "named_catalyst": None,
    }


def test_json_with_trailing_prose():
    report = (REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n{{"signal": "PULLBACK", "support_held": true}}\n'
              "I hope this helps with your analysis!")
    assert _extract_structured_verdict(report, "technical") == {
        "signal": "PULLBACK", "support_held": True,
    }


def test_trailing_comma_tolerated():
    report = REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n{{"falling_knife": "NO", "top_risk": "None",}}'
    assert _extract_structured_verdict(report, "risk") == {
        "falling_knife": "NO", "top_risk": "None",
    }


def test_last_marker_wins():
    report = (f'{STRUCTURED_VERDICT_MARKER}\n{{"falling_knife": "NO"}}\nmore prose\n'
              f'{STRUCTURED_VERDICT_MARKER}\n{{"falling_knife": "YES"}}')
    assert _extract_structured_verdict(report, "risk") == {"falling_knife": "YES"}


def test_missing_block_returns_none():
    assert _extract_structured_verdict(REPORT_PROSE, "risk") is None
    assert _extract_structured_verdict("", "risk") is None
    assert _extract_structured_verdict(None, "risk") is None


def test_malformed_json_returns_none_and_logs(tmp_path, monkeypatch):
    import app.services.research_service as rs
    monkeypatch.setattr(rs, "_PARSER_FAILURE_DIR", str(tmp_path))
    report = REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n{{"falling_knife": YES no quotes}}'
    assert _extract_structured_verdict(report, "risk") is None
    logged = list(tmp_path.iterdir())
    assert len(logged) == 1 and "structured_risk_" in logged[0].name


def test_non_dict_json_returns_none(tmp_path, monkeypatch):
    import app.services.research_service as rs
    monkeypatch.setattr(rs, "_PARSER_FAILURE_DIR", str(tmp_path))
    report = REPORT_PROSE + f'{STRUCTURED_VERDICT_MARKER}\n["not", "a", "dict"]'
    assert _extract_structured_verdict(report, "risk") is None
