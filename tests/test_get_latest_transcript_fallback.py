from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from app.services.stock_service import StockService


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import importlib, app.database as db
    importlib.reload(db)
    db.init_db()
    yield
    # tmp_path cleanup is automatic


@pytest.fixture
def svc():
    return StockService()


def _db_list(report_date: str, paragraphs):
    """Build a fake DefeatBeta get_transcripts_list() DataFrame with one row."""
    return pd.DataFrame([{
        "report_date": report_date,
        "transcripts": paragraphs,
    }])


def test_cache_hit_skips_all_external_calls(svc, monkeypatch):
    """If (symbol, latest known quarter) is already cached, return it directly."""
    from app.database import save_cached_transcript
    save_cached_transcript("AAPL", "2026Q1", "alpha_vantage",
                           "cached transcript text",
                           "2026-01-30")

    # Stub the helper that would derive the quarter — return the cached quarter
    with patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch("app.services.stock_service._DBTicker") as mock_db, \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript") as mock_av:
        result = svc.get_latest_transcript("AAPL")
    assert result["text"] == "cached transcript text"
    assert result["date"] == "2026-01-30"
    mock_db.assert_not_called()
    mock_av.assert_not_called()


def test_fresh_defeatbeta_returns_db_transcript(svc):
    """DefeatBeta has a transcript <75 days old — return it, never call AV."""
    today = datetime.utcnow().date()
    fresh_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    df = _db_list(fresh_date, [{"content": "DB paragraph 1"}, {"content": "DB paragraph 2"}])

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = df

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript") as mock_av:
        result = svc.get_latest_transcript("AAPL")
    assert "DB paragraph 1" in result["text"]
    assert result["date"] == fresh_date
    mock_av.assert_not_called()


def test_stale_defeatbeta_triggers_av(svc):
    """DefeatBeta's latest transcript is >75 days old — fall back to AV."""
    today = datetime.utcnow().date()
    stale_date = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    df = _db_list(stale_date, [{"content": "old DB paragraph"}])

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = df

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "AV fresh transcript", "report_date": None,
                                    "segment_count": 5, "rate_limited": False,
                                    "quota_exhausted": False}) as mock_av:
        result = svc.get_latest_transcript("AAPL")
    assert result["text"] == "AV fresh transcript"
    mock_av.assert_called_once_with("AAPL", "2026Q1")


def test_empty_defeatbeta_triggers_av(svc):
    """DefeatBeta returns empty DF — fall back to AV."""
    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = pd.DataFrame()

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "AV result", "report_date": None,
                                    "segment_count": 1, "rate_limited": False,
                                    "quota_exhausted": False}) as mock_av:
        result = svc.get_latest_transcript("AAPL")
    assert result["text"] == "AV result"
    mock_av.assert_called_once()


def test_av_quota_exhausted_keeps_stale_db(svc):
    """When AV is over its daily cap, return the stale DB transcript anyway."""
    today = datetime.utcnow().date()
    stale_date = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    df = _db_list(stale_date, [{"content": "stale DB paragraph"}])

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = df

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "", "report_date": None, "segment_count": 0,
                                    "rate_limited": False, "quota_exhausted": True}):
        result = svc.get_latest_transcript("AAPL")
    assert "stale DB paragraph" in result["text"]
    assert result["date"] == stale_date


def test_finnhub_returns_no_quarter_skips_av(svc):
    """If we can't derive a quarter, we cannot call AV — keep DB result (even if stale)."""
    today = datetime.utcnow().date()
    stale_date = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    df = _db_list(stale_date, [{"content": "stale DB"}])

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = df

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value=None), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript") as mock_av:
        result = svc.get_latest_transcript("AAPL")
    assert "stale DB" in result["text"]
    mock_av.assert_not_called()


def test_av_success_writes_to_cache(svc):
    """A successful AV fetch writes the transcript to the SQLite cache."""
    from app.database import get_cached_transcript
    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = pd.DataFrame()

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "AV new", "report_date": None,
                                    "segment_count": 1, "rate_limited": False,
                                    "quota_exhausted": False}):
        svc.get_latest_transcript("AAPL")

    row = get_cached_transcript("AAPL", "2026Q1")
    assert row is not None
    assert row["text"] == "AV new"
    assert row["source"] == "alpha_vantage"


def test_both_empty_returns_empty_dict(svc):
    """No DB data and AV also empty — return the standard empty dict."""
    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = pd.DataFrame()

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "", "report_date": None, "segment_count": 0,
                                    "rate_limited": False, "quota_exhausted": False}):
        result = svc.get_latest_transcript("AAPL")
    assert result == {"text": "", "date": None, "warning": ""}


def test_defeatbeta_exception_falls_through_to_av(svc):
    """If DefeatBeta raises, we still try AV (so a DefeatBeta outage doesn't kill transcripts)."""
    with patch("app.services.stock_service._DBTicker",
               side_effect=RuntimeError("DB outage")), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value="2026Q1"), \
         patch.object(svc.alpha_vantage_service, "get_earnings_call_transcript",
                      return_value={"text": "AV saved us", "report_date": None,
                                    "segment_count": 1, "rate_limited": False,
                                    "quota_exhausted": False}):
        result = svc.get_latest_transcript("AAPL")
    assert result["text"] == "AV saved us"
