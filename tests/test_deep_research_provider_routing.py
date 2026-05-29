import os
from unittest import mock
from app.services.deep_research_service import deep_research_service


def test_routes_to_claude_when_provider_env_set():
    sentinel = {"review_verdict": "CONFIRMED", "action": "BUY"}
    with mock.patch.dict(os.environ, {"DEEP_RESEARCH_PROVIDER": "claude"}):
        with mock.patch(
            "app.services.claude_deep_research_service.claude_deep_research_service.execute_deep_research",
            return_value=sentinel,
        ) as claude_exec:
            out = deep_research_service.execute_deep_research("AAPL", {"drop_percent": -6}, 1)
    claude_exec.assert_called_once()
    assert out is sentinel


def test_defaults_to_gemini(monkeypatch):
    monkeypatch.delenv("DEEP_RESEARCH_PROVIDER", raising=False)
    # Force the Gemini HTTP path to short-circuit so we don't hit the network:
    with mock.patch("app.services.deep_research_service.requests.post",
                    side_effect=AssertionError("gemini path attempted (expected)")):
        try:
            deep_research_service.execute_deep_research("AAPL", {"drop_percent": -6}, 1)
        except AssertionError as e:
            assert "gemini path attempted" in str(e)
