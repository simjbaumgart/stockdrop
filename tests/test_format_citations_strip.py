"""Regression: _format_citations must strip [Source N] from the inline body
before appending the Sources appendix, so stored agent reports don't carry
citation markers into downstream consumers (DB, dashboard, PM prompt)."""
from unittest.mock import MagicMock
from app.services.research_service import ResearchService


def _make_response(text: str, supports, chunks):
    response = MagicMock()
    candidate = MagicMock()
    part = MagicMock()
    part.text = text
    candidate.content.parts = [part]
    candidate.grounding_metadata.grounding_supports = supports
    candidate.grounding_metadata.grounding_chunks = chunks
    response.candidates = [candidate]
    return response


def _support(end_index, chunk_indices):
    s = MagicMock()
    s.segment.end_index = end_index
    s.grounding_chunk_indices = chunk_indices
    return s


def _chunk(title):
    c = MagicMock()
    c.web.title = title
    return c


def test_format_citations_strips_markers_from_body():
    text = "Stock dropped on weak guidance and competitive pressure."
    # end_index after "guidance" (position 30) and after "pressure" (position ~56).
    # Sorted descending so injection doesn't shift earlier indices.
    supports = [_support(30, [0]), _support(56, [1])]
    chunks = [_chunk("Reuters earnings report"), _chunk("Bloomberg sector note")]
    response = _make_response(text, supports, chunks)

    agent = ResearchService.__new__(ResearchService)  # bypass __init__
    out = agent._format_citations(response)

    # Split body from appendix
    assert "### Sources:" in out, "Sources appendix must be appended"
    body, appendix = out.split("### Sources:", 1)

    # Body must NOT contain inline markers
    assert "[Source 1]" not in body, f"body should be stripped, got: {body!r}"
    assert "[Source 2]" not in body, f"body should be stripped, got: {body!r}"

    # Appendix titles preserved
    assert "Reuters earnings report" in appendix
    assert "Bloomberg sector note" in appendix
