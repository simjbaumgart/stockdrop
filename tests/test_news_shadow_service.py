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


from app.services import news_shadow_service as nss


def test_extract_needs_economics_true():
    assert nss.extract_needs_economics("blah\nNEEDS_ECONOMICS: TRUE") is True


def test_extract_needs_economics_false():
    assert nss.extract_needs_economics("blah\nNEEDS_ECONOMICS: FALSE") is False
    assert nss.extract_needs_economics("") is False
    assert nss.extract_needs_economics(None) is False


def test_models_differ():
    assert nss.PRODUCTION_NEWS_MODEL != nss.SHADOW_NEWS_MODEL


def test_is_shadow_active_under_target(monkeypatch):
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", lambda: 5)
    assert nss.is_shadow_active() is True


def test_is_shadow_active_at_target(monkeypatch):
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", lambda: 20)
    assert nss.is_shadow_active() is False


def test_is_shadow_active_swallows_errors(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(nss.database, "count_news_shadow_runs", boom)
    assert nss.is_shadow_active() is False


def test_run_shadow_call_passes_shadow_model():
    captured = {}

    def fake_call(prompt, model_name, agent_context, metrics_sink):
        captured["model"] = model_name
        captured["prompt"] = prompt
        metrics_sink["model"] = model_name
        metrics_sink["tokens_in"] = 100
        metrics_sink["tokens_out"] = 50
        return "shadow output"

    result = nss.run_shadow_call(fake_call, "the prompt")
    assert captured["model"] == nss.SHADOW_NEWS_MODEL
    assert captured["prompt"] == "the prompt"
    assert result["report"] == "shadow output"
    assert result["metrics"]["tokens_in"] == 100
    assert "latency_ms" in result["metrics"]


def test_build_shadow_record_with_success():
    prod_metrics = {"model": "gemini-3.5-flash-preview",
                    "tokens_in": 900, "tokens_out": 300, "latency_ms": 4000}
    shadow_result = {"report": "Shadow. NEEDS_ECONOMICS: TRUE",
                     "metrics": {"tokens_in": 950, "tokens_out": 310, "latency_ms": 5000}}
    rec = nss.build_shadow_record("AAPL", "2026-05-22",
                                  "Prod. NEEDS_ECONOMICS: FALSE",
                                  prod_metrics, shadow_result)
    assert rec["symbol"] == "AAPL"
    assert rec["production_needs_economics"] is False
    assert rec["shadow_needs_economics"] is True
    assert rec["shadow_tokens_in"] == 950
    assert rec["shadow_error"] is None


def test_build_shadow_record_with_failure():
    prod_metrics = {"model": "gemini-3.5-flash-preview",
                    "tokens_in": 900, "tokens_out": 300, "latency_ms": 4000}
    rec = nss.build_shadow_record("AAPL", "2026-05-22",
                                  "Prod report", prod_metrics, None)
    assert rec["shadow_report"] is None
    assert rec["shadow_error"] is not None
    assert rec["shadow_needs_economics"] is None
