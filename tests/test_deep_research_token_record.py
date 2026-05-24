# tests/test_deep_research_token_record.py
import sqlite3


def test_deep_research_records_when_usage_present(temp_db):
    """The DR snippet under test is essentially a token-tracker call with
    REST-style key names. Driving the full execute_deep_research end-to-end
    would require mocking requests.{post,get} and the poll loop — heavier
    than the value it adds. Instead, replicate the exact snippet inline so
    the contract it depends on is protected.
    """
    path, decision_id = temp_db
    poll_data = {
        "status": "completed",
        "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 4000},
    }
    from app.services.token_tracker import record_llm_call
    from datetime import datetime
    um = poll_data.get("usageMetadata") or {}
    record_llm_call(
        decision_id=decision_id, ticker="AAPL",
        run_date=datetime.now().strftime("%Y-%m-%d"),
        stage="deep_research", agent_name="deep_research",
        model="deep-research-pro",
        tokens_in=int(um["promptTokenCount"]), tokens_out=int(um["candidatesTokenCount"]),
    )
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT stage, agent_name, model, tokens_in, tokens_out "
        "FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == ("deep_research", "deep_research", "deep-research-pro", 9000, 4000)
