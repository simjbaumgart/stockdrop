import json
from unittest.mock import patch

import pytest

from app.services.deep_research_service import (
    _strip_citations,
    _CITATION_STRIP_COUNTER,
    DeepResearchService,
)


class TestStripCitations:
    def test_simple_trailing_marker(self):
        assert _strip_citations("great news [Source 1]") == "great news"

    def test_mid_word_marker_collapses_cleanly(self):
        assert _strip_citations("signa [Source 1]ling") == "signaling"

    def test_multiple_markers(self):
        raw = "text [Source 1] more [Source 2] end"
        assert _strip_citations(raw) == "text more end"

    def test_no_markers_is_noop(self):
        raw = '{"action": "BUY", "reason": "clean"}'
        assert _strip_citations(raw) == raw

    def test_marker_with_multiple_digits(self):
        assert _strip_citations("x [Source 42] y") == "x y"

    def test_marker_with_internal_whitespace(self):
        assert _strip_citations("x [Source  3] y") == "x y"

    def test_counter_increments_only_on_change(self):
        before = _CITATION_STRIP_COUNTER["stripped"]
        _strip_citations("no markers here")
        assert _CITATION_STRIP_COUNTER["stripped"] == before
        _strip_citations("has [Source 1] marker")
        assert _CITATION_STRIP_COUNTER["stripped"] == before + 1

    def test_json_parseable_after_strip(self):
        raw = '{"action": "BUY", "reason": "strong setup [Source 1] confirmed"}'
        cleaned = _strip_citations(raw)
        parsed = json.loads(cleaned)
        assert parsed["reason"] == "strong setup confirmed"


class TestParserStripsCitations:
    def test_parse_output_handles_citation_markers(self):
        svc = DeepResearchService.__new__(DeepResearchService)
        poll = {
            "outputs": [
                {"text": '{"action": "BUY", "reason": "clean [Source 1] setup"}'},
            ]
        }
        with patch.object(svc, "_repair_json_using_flash", return_value=None):
            result = svc._parse_output(poll, schema_type="individual")
        assert result is not None
        assert result["reason"] == "clean setup"
        assert result["action"] == "BUY"
