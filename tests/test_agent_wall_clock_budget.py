import time
from unittest.mock import MagicMock

from app.services.research_service import ResearchService, BudgetClock, AGENT_WALL_CLOCK_BUDGET_SEC


def _expired_clock() -> BudgetClock:
    """Return a BudgetClock whose deadline is already in the past.

    Both _last_now and _last_monotonic are set to the current moment so that
    the tick() call inside _call_grounded_model does NOT trigger sleep
    detection (wall and monotonic advance together from that reference point).
    Only the deadline is in the past.
    """
    now = time.time()
    bc = BudgetClock(now=now)
    bc.deadline = now - 1  # expired
    return bc


def _short_clock(seconds: float = 3) -> BudgetClock:
    """Return a BudgetClock with a very short deadline from now."""
    bc = BudgetClock.__new__(BudgetClock)
    bc.deadline = time.time() + seconds
    bc._last_now = time.time()
    bc._last_monotonic = time.monotonic()
    return bc


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
        budget_clock=_expired_clock(),  # already expired
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
        budget_clock=_short_clock(3),
    )
    elapsed = time.time() - start
    assert isinstance(result, str)
    assert result.startswith("[Error")
    # If the backoff-skip gate were missing, we'd burn 2+4 = 6s sleeping.
    # Assert we bailed faster than that.
    assert elapsed < 5, f"expected mid-flight bail, took {elapsed:.1f}s"


def test_fallback_path_inherits_budget_clock():
    """The gemini-3.1-pro → gemini-3-pro-preview 503 fallback resets
    retry_count=0 but MUST pass the BudgetClock through. Lock that
    inheritance in so a refactor can't drop it."""
    svc = ResearchService.__new__(ResearchService)
    svc.api_key = "fake"
    svc.grounding_client = MagicMock()

    class Fake503(Exception):
        code = 503

    svc.grounding_client.models.generate_content.side_effect = Fake503("503 UNAVAILABLE")

    start = time.time()
    # Already-expired budget. If the fallback path ignores the BudgetClock,
    # the recursive call would create a fresh 600s clock and loop again.
    result = svc._call_grounded_model(
        prompt="x",
        model_name="gemini-3.1-pro-preview",
        agent_context="Fallback Agent",
        retry_count=0,
        budget_clock=_expired_clock(),
    )
    assert isinstance(result, str)
    assert result.startswith("[Error")
    assert (time.time() - start) < 5
