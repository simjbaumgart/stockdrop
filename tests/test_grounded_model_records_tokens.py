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
