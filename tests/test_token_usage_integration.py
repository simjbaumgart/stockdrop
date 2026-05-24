# tests/test_token_usage_integration.py
import sqlite3
import threading


class _FakeUsage:
    def __init__(self, p, c):
        self.prompt_token_count = p
        self.candidates_token_count = c


class _FakeCandidate:
    finish_reason = 1


class _FakeResponse:
    def __init__(self, p, c):
        self.candidates = [_FakeCandidate()]
        self.text = "OK"
        self.usage_metadata = _FakeUsage(p, c)


class _FakeModels:
    def generate_content(self, model, contents, config):
        # Return varying counts so we can verify SUM math
        return _FakeResponse(1000, 500)


class _FakeGroundingClient:
    models = _FakeModels()


def test_three_grounded_calls_rollup_to_decision_points(temp_db):
    path, decision_id = temp_db
    from app.services.research_service import ResearchService
    from app.services.token_tracker import rollup_decision_totals
    from app.models.market_state import MarketState

    rs = ResearchService.__new__(ResearchService)
    rs.grounding_client = _FakeGroundingClient()
    rs.model = object()
    rs.lock = threading.Lock()

    state = MarketState(ticker="AAPL", date="2026-05-23", decision_id=decision_id)

    # Simulate 3 grounded agent calls
    for label in ["News Agent", "Bull Researcher", "Fund Manager"]:
        rs._call_agent("prompt", label, state=state)

    rollup_decision_totals(decision_id)

    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT agent_name FROM agent_token_usage ORDER BY id"
    ).fetchall()
    totals = conn.execute(
        "SELECT total_tokens_in, total_tokens_out, total_llm_calls "
        "FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()
    conn.close()

    assert [r[0] for r in rows] == ["sensor_news", "debate_bull", "pm"]
    # 3 calls × (1000 in, 500 out)
    assert totals == (3000, 1500, 3)
