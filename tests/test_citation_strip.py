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


class TestNumericCitationMarkers:
    """Gemini also emits bare-number footnote markers like [1], [1.1], [2.3.4].
    These appeared in COR/ANET reports after the [Source N] strip shipped."""

    def test_strips_bare_single_digit(self):
        assert _strip_citations("strong setup [1] confirmed") == "strong setup confirmed"

    def test_strips_dotted_section_marker(self):
        assert _strip_citations("revenue beat [1.1] guidance") == "revenue beat guidance"

    def test_strips_multi_dotted_marker(self):
        assert _strip_citations("note [2.3.4] applies") == "note applies"

    def test_strips_cluster_of_numeric_markers(self):
        raw = "growth [1.1][1.2] across segments [2][3.1] confirmed"
        assert _strip_citations(raw) == "growth across segments confirmed"

    def test_strips_cite_prefix_marker(self):
        assert _strip_citations("strong [cite 4] confirmed") == "strong confirmed"
        assert _strip_citations("strong [cite:4] confirmed") == "strong confirmed"

    def test_numeric_marker_at_word_boundary(self):
        # ANET-style bleed: "growth[1.1]across" with no surrounding spaces.
        assert _strip_citations("growth[1.1]across") == "growth across"

    def test_does_not_strip_legitimate_bracketed_text(self):
        # Must NOT eat real bracketed content like [BUY], [N/A], [YoY 5%].
        assert _strip_citations("rating [BUY] confirmed") == "rating [BUY] confirmed"
        assert _strip_citations("value [N/A] today") == "value [N/A] today"
        assert _strip_citations("growth [YoY 5%] strong") == "growth [YoY 5%] strong"
        assert _strip_citations("range [low-high]") == "range [low-high]"

    def test_does_not_strip_dates_in_brackets(self):
        # E.g. ISO-style bracketed dates should survive.
        assert _strip_citations("filed [2026-05-06]") == "filed [2026-05-06]"

    def test_mixed_source_and_numeric_markers(self):
        raw = "revenue [Source 1] beat [1.2] guidance"
        assert _strip_citations(raw) == "revenue beat guidance"
