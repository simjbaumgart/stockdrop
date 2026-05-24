# tests/test_grounded_model_records_tokens.py
import sqlite3


class _FakeUsage:
    prompt_token_count = 1234
    candidates_token_count = 567


class _FakeCandidate:
    finish_reason = 1  # STOP — not a function-call retry


class _FakeResponse:
    candidates = [_FakeCandidate()]
    text = "FAKE OUTPUT"
    usage_metadata = _FakeUsage()


class _FakeModels:
    def generate_content(self, model, contents, config):
        return _FakeResponse()


class _FakeGroundingClient:
    models = _FakeModels()


def test_grounded_model_records_token_row(temp_db):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    rs = ResearchService.__new__(ResearchService)  # bypass __init__
    rs.grounding_client = _FakeGroundingClient()
    rs.model = object()  # truthy so the function doesn't short-circuit
    rs.lock = __import__("threading").Lock()

    tracker_context = {
        "decision_id": decision_id, "ticker": "AAPL", "run_date": "2026-05-23",
        "stage": "sensor", "agent_name": "sensor_news",
    }
    rs._call_grounded_model(
        "prompt", model_name="gemini-3-flash-preview", agent_context="News Agent",
        tracker_context=tracker_context,
    )
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT agent_name, model, tokens_in, tokens_out FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("sensor_news", "gemini-3-flash-preview", 1234, 567)


class _RetryingFakeModels:
    """First call returns finish_reason=10 (FunctionCall, triggers retry).
    Second call returns finish_reason=1 (success). Allows pinning that
    only the final successful attempt is recorded — failed-grounding
    retry attempts must NOT write a row.
    """
    def __init__(self):
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        if self.calls == 1:
            r = _FakeResponse()
            r.candidates = [type("C", (), {"finish_reason": 10})()]
            return r
        return _FakeResponse()  # finish_reason=1, success


class _RetryingFakeGroundingClient:
    def __init__(self):
        self.models = _RetryingFakeModels()


def test_function_call_retry_does_not_double_record(temp_db):
    """The recording block lives AFTER the FunctionCall (finish_reason==10)
    early-return retry path, so failed-grounding attempts never reach it.
    Only the final successful attempt records exactly one row.
    """
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = _RetryingFakeGroundingClient()
    rs.model = object()
    rs.lock = __import__("threading").Lock()

    tracker_context = {
        "decision_id": decision_id, "ticker": "AAPL", "run_date": "2026-05-23",
        "stage": "sensor", "agent_name": "sensor_news",
    }
    rs._call_grounded_model(
        "prompt", model_name="gemini-3-flash-preview", agent_context="News Agent",
        tracker_context=tracker_context,
    )

    import sqlite3
    conn = sqlite3.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM agent_token_usage").fetchone()[0]
    conn.close()
    # Two SDK calls happened (one FunctionCall + one success), but only the
    # success path reaches the recording block. Otherwise the count would be 2.
    assert count == 1
    # Sanity-check the fake actually retried
    assert rs.grounding_client.models.calls == 2


def test_tracker_context_none_does_not_record(temp_db):
    """When tracker_context is None (unmapped agent, missing decision_id,
    direct unit-test invocation), the call succeeds but no row is written.
    Protects the silent-skip contract.
    """
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = _FakeGroundingClient()
    rs.model = object()
    rs.lock = __import__("threading").Lock()

    result = rs._call_grounded_model(
        "prompt", model_name="gemini-3-flash-preview", agent_context="News Agent",
        tracker_context=None,   # explicitly skip recording
    )

    # The call still succeeds and returns some output...
    assert result is not None
    # ...but no row was written to agent_token_usage.
    import sqlite3
    conn = sqlite3.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM agent_token_usage").fetchone()[0]
    conn.close()
    assert count == 0
