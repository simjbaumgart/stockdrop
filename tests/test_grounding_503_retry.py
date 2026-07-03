"""Unit tests for the 503 retry-then-degrade policy in _call_grounded_model.

A 503 on the primary pro model (gemini-3.1-pro-preview) is transient overload,
so it must be retried on the SAME model up to MAX_GROUNDING_RETRIES BEFORE
degrading to GROUNDING_FALLBACK_MODEL. The degrade must happen exactly once.
"""
from unittest.mock import MagicMock

from app.services import research_service as rs
from app.services.research_service import (
    ResearchService,
    BudgetClock,
    GROUNDING_FALLBACK_MODEL,
    MAX_GROUNDING_RETRIES,
)

PRO_MODEL = "gemini-3.1-pro-preview"


class _Fake503(Exception):
    code = 503

    def __init__(self, msg="503 UNAVAILABLE"):
        super().__init__(msg)


def _fresh_clock() -> BudgetClock:
    """A BudgetClock with the full wall-clock budget ahead of it."""
    return BudgetClock()


def _success_response(text: str = "PRO OK"):
    """Minimal grounded-call response that _format_citations returns verbatim."""
    resp = MagicMock()
    candidate = MagicMock()
    candidate.finish_reason = 1  # STOP — not MAX_TOKENS (2) or FunctionCall (10)
    candidate.content.parts = [MagicMock(text=text)]
    candidate.grounding_metadata = None  # short-circuits citation formatting
    resp.candidates = [candidate]
    resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=20)
    return resp


def _make_service():
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()
    return svc


def _models_called(svc):
    """Ordered list of the `model=` arg passed to each generate_content call."""
    return [c.kwargs.get("model") for c in svc.grounding_client.models.generate_content.call_args_list]


def test_503_retries_same_pro_model_then_succeeds(monkeypatch):
    """Pro model 503s twice then succeeds: NO fallback, stays on the pro model."""
    monkeypatch.setattr(rs.time, "sleep", lambda *_a, **_k: None)

    svc = _make_service()
    svc.grounding_client.models.generate_content.side_effect = [
        _Fake503(),
        _Fake503(),
        _success_response("PRO OK"),
    ]

    result = svc._call_grounded_model(
        prompt="x",
        model_name=PRO_MODEL,
        agent_context="Bull Researcher",
        retry_count=0,
        budget_clock=_fresh_clock(),
    )

    assert "PRO OK" in result
    assert f"Model: {PRO_MODEL}" in result
    # 1 initial + 2 retries == 3 calls, ALL on the pro model — never degraded.
    called = _models_called(svc)
    assert called == [PRO_MODEL, PRO_MODEL, PRO_MODEL]
    assert GROUNDING_FALLBACK_MODEL not in called


def test_503_through_all_retries_degrades_once_to_fallback(monkeypatch):
    """Pro model 503s through every retry: degrade EXACTLY once to the fallback."""
    monkeypatch.setattr(rs.time, "sleep", lambda *_a, **_k: None)

    svc = _make_service()

    def _side_effect(*_a, **kwargs):
        if "3.1" in kwargs.get("model", ""):
            raise _Fake503()
        return _success_response("FALLBACK OK")

    svc.grounding_client.models.generate_content.side_effect = _side_effect

    result = svc._call_grounded_model(
        prompt="x",
        model_name=PRO_MODEL,
        agent_context="Bear Researcher",
        retry_count=0,
        budget_clock=_fresh_clock(),
    )

    assert "FALLBACK OK" in result
    assert f"Model: {GROUNDING_FALLBACK_MODEL}" in result

    called = _models_called(svc)
    # Pro retried 1 + MAX_GROUNDING_RETRIES times, THEN one degrade to fallback.
    assert called.count(PRO_MODEL) == MAX_GROUNDING_RETRIES + 1
    assert called.count(GROUNDING_FALLBACK_MODEL) == 1, "must degrade exactly once"
    # The degrade is the final call, after all pro retries are exhausted.
    assert called[-1] == GROUNDING_FALLBACK_MODEL
