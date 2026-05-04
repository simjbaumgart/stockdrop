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

    def test_strips_marker_in_middle_of_word(self):
        # Old behavior joined letters across the marker ('signaling').
        # New behavior preserves a space because we cannot tell joined-vs-separated
        # from the raw text alone, and word-boundary preservation is the higher
        # priority (see CAR 'Massivestructuralunwind' production failure).
        assert _strip_citations("signa [Source 1]ling") == "signa ling"

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


class TestCitationStripSpacing:
    """Regression tests for the 'Massivestructuralunwind' spacing collapse."""

    def test_marker_between_words_without_trailing_space(self):
        assert _strip_citations("Massive [Source 1]structural") == "Massive structural"

    def test_consecutive_marker_cluster(self):
        raw = "Phy [Source 6][Source 1]sical impact [Source 10][Source 11][Source 4] expected"
        assert _strip_citations(raw) == "Phy sical impact expected"

    def test_no_double_space_after_strip(self):
        out = _strip_citations("word [Source 1] word")
        assert "  " not in out
        assert out == "word word"

    def test_strip_at_sentence_join(self):
        raw = "Massive [Source 1]structural [Source 2]unwind: [Source 3]The [Source 4]stock"
        assert _strip_citations(raw) == "Massive structural unwind: The stock"

    def test_no_leading_or_trailing_space(self):
        assert _strip_citations("[Source 1] hello") == "hello"
        assert _strip_citations("hello [Source 1]") == "hello"
