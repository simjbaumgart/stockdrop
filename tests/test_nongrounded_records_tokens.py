# tests/test_nongrounded_records_tokens.py
import sqlite3


class _FakeUsage:
    prompt_token_count = 11
    candidates_token_count = 22


class _FakeResponse:
    text = "FAKE"
    usage_metadata = _FakeUsage()


class _FakeOldModel:
    model_name = "models/gemini-3.1-pro-preview"
    def generate_content(self, prompt, request_options=None):
        return _FakeResponse()


def test_nongrounded_path_records_tokens(temp_db, monkeypatch):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    from app.models.market_state import MarketState
    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = None       # forces non-grounded path
    rs.model = _FakeOldModel()
    rs.lock = __import__("threading").Lock()
    # Patch time.sleep so the 2s rate-limit buffer doesn't slow the test.
    monkeypatch.setattr("app.services.research_service.time.sleep", lambda s: None)

    state = MarketState(ticker="AAPL", date="2026-05-23", decision_id=decision_id)
    rs._call_agent("prompt", "Fund Manager", state=state)

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT agent_name, model, tokens_in, tokens_out FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("pm", "gemini-3.1-pro-preview", 11, 22)
