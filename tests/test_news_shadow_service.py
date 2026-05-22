import pytest

from app import database


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_file))
    database.init_db()
    return str(db_file)


def _sample_record(symbol="AAPL"):
    return {
        "symbol": symbol,
        "decision_date": "2026-05-22",
        "production_model": "gemini-3.5-flash-preview",
        "production_report": "Production report. NEEDS_ECONOMICS: TRUE",
        "production_tokens_in": 1000,
        "production_tokens_out": 400,
        "production_latency_ms": 5200,
        "production_needs_economics": True,
        "shadow_model": "gemini-3-flash-preview",
        "shadow_report": "Shadow report. NEEDS_ECONOMICS: FALSE",
        "shadow_tokens_in": 1010,
        "shadow_tokens_out": 380,
        "shadow_latency_ms": 6100,
        "shadow_needs_economics": False,
        "shadow_error": None,
    }


def test_count_starts_at_zero(temp_db):
    assert database.count_news_shadow_runs() == 0


def test_insert_and_count(temp_db):
    database.insert_news_shadow_run(1, _sample_record())
    assert database.count_news_shadow_runs() == 1


def test_errored_shadow_does_not_count(temp_db):
    rec = _sample_record()
    rec["shadow_report"] = None
    rec["shadow_error"] = "timeout"
    database.insert_news_shadow_run(2, rec)
    assert database.count_news_shadow_runs() == 0


def test_get_returns_inserted_rows(temp_db):
    database.insert_news_shadow_run(1, _sample_record("AAPL"))
    database.insert_news_shadow_run(2, _sample_record("MSFT"))
    rows = database.get_news_shadow_runs()
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}
    assert rows[0]["decision_point_id"] == 1
