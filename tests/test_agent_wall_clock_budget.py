import time
from unittest.mock import MagicMock

from app.services.research_service import ResearchService


def test_call_grounded_model_respects_wall_clock_budget():
    """Even if retry_count would allow another attempt, an expired wall-clock
    budget must stop the loop. Simulates the QXO/PB 17h stall."""
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()

    def boom(*a, **kw):
        raise ConnectionResetError("simulated 503")

    svc.grounding_client.models.generate_content.side_effect = boom

    start = time.time()
    result = svc._call_grounded_model(
        prompt="x",
        model_name="gemini-3-flash-preview",
        agent_context="Test Agent",
        retry_count=0,
        budget_deadline=start - 1,  # already expired
    )
    assert isinstance(result, str)
    assert (
        "budget" in result.lower()
        or "[Error" in result
        or "[Grounding Error" in result
    )
    assert (time.time() - start) < 5, "must not stall when budget is exhausted"
