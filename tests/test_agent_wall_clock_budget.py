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


def test_call_grounded_model_bails_mid_flight_when_budget_crosses():
    """Deadline stamped normally; retryable failures would loop several
    times, but the wall-clock budget expires mid-flight. The method must
    stop retrying (including skipping the exponential backoff sleep) and
    return an error stub well before MAX_GROUNDING_RETRIES exhausts
    their sleeps."""
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()
    svc.grounding_client.models.generate_content.side_effect = \
        ConnectionResetError("simulated 503")

    start = time.time()
    # 3s budget — enough to take one attempt, but the backoff-skip branch
    # must prevent a 2s then 4s sleep from running.
    result = svc._call_grounded_model(
        prompt="x",
        model_name="gemini-3-flash-preview",
        agent_context="MidFlight Agent",
        retry_count=0,
        budget_deadline=start + 3,
    )
    elapsed = time.time() - start
    assert isinstance(result, str)
    assert result.startswith("[Error")
    # If the backoff-skip gate were missing, we'd burn 2+4 = 6s sleeping.
    # Assert we bailed faster than that.
    assert elapsed < 5, f"expected mid-flight bail, took {elapsed:.1f}s"


def test_fallback_path_inherits_budget_deadline():
    """The gemini-3.1-pro → gemini-3-pro-preview 503 fallback at L1364
    resets retry_count=0 but MUST pass budget_deadline through. Lock
    that inheritance in so a refactor can't drop it."""
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()

    class Fake503(Exception):
        code = 503

    svc.grounding_client.models.generate_content.side_effect = Fake503("503 UNAVAILABLE")

    start = time.time()
    # Already-expired budget. If the fallback path ignores budget_deadline,
    # the recursive call would re-stamp a fresh 600s budget and loop again.
    result = svc._call_grounded_model(
        prompt="x",
        model_name="gemini-3.1-pro-preview",
        agent_context="Fallback Agent",
        retry_count=0,
        budget_deadline=start - 1,
    )
    assert isinstance(result, str)
    assert result.startswith("[Error")
    assert (time.time() - start) < 5
