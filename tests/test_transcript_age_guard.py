"""Tests for the transcript age guard in get_latest_transcript.

Regression (IESC 2026-06-10): DefeatBeta returned a February 2009 earnings
call, the company-name guard passed (same company), and the News Agent
summarized a 17-year-old transcript as the "Extended Transcript Summary" —
the evidence checklist then counted Transcript: Yes. Transcripts older than
MAX_TRANSCRIPT_AGE_DAYS must be rejected in the fetch path (returned as
"No Transcript") instead of being used as a last-resort fallback.
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.services.stock_service as ss
from app.services.stock_service import StockService, MAX_TRANSCRIPT_AGE_DAYS


def _fake_db_ticker(report_date: str, content: str = "Welcome to the IES Holdings earnings call."):
    """Build a DefeatBeta Ticker stand-in returning one transcript row."""

    class FakeTranscripts:
        def get_transcripts_list(self):
            return pd.DataFrame(
                [{"report_date": report_date, "transcripts": [{"content": content}]}]
            )

    class FakeTicker:
        def __init__(self, symbol):
            pass

        def earning_call_transcripts(self):
            return FakeTranscripts()

    return FakeTicker


def _svc(monkeypatch):
    svc = StockService.__new__(StockService)  # bypass __init__/network
    # No quarter resolvable -> cache/AV fallbacks are skipped, exercising the
    # "return whatever DefeatBeta had" path where the stale leak lived.
    monkeypatch.setattr(
        svc, "_finnhub_latest_quarter_for", lambda symbol: None, raising=False
    )
    return svc


def test_ancient_transcript_rejected(monkeypatch):
    monkeypatch.setattr(ss, "_DBTicker", _fake_db_ticker("2009-02-15"))
    svc = _svc(monkeypatch)
    result = svc.get_latest_transcript("IESC")
    assert result["text"] == ""
    assert result["date"] is None


def test_just_over_max_age_rejected(monkeypatch):
    too_old = (datetime.utcnow() - timedelta(days=MAX_TRANSCRIPT_AGE_DAYS + 5)).strftime("%Y-%m-%d")
    monkeypatch.setattr(ss, "_DBTicker", _fake_db_ticker(too_old))
    svc = _svc(monkeypatch)
    result = svc.get_latest_transcript("IESC")
    assert result["text"] == ""


def test_fresh_transcript_returned(monkeypatch):
    fresh = (datetime.utcnow() - timedelta(days=10)).strftime("%Y-%m-%d")
    monkeypatch.setattr(ss, "_DBTicker", _fake_db_ticker(fresh))
    svc = _svc(monkeypatch)
    result = svc.get_latest_transcript("IESC")
    assert "earnings call" in result["text"]
    assert result["date"] == fresh


def test_stale_but_not_ancient_still_falls_back(monkeypatch):
    """Between STALE_TRANSCRIPT_DAYS and MAX_TRANSCRIPT_AGE_DAYS the transcript
    is stale (triggers AV fallback) but still usable as a last resort."""
    assert ss.STALE_TRANSCRIPT_DAYS < MAX_TRANSCRIPT_AGE_DAYS
    midway = (datetime.utcnow() - timedelta(days=ss.STALE_TRANSCRIPT_DAYS + 10)).strftime("%Y-%m-%d")
    monkeypatch.setattr(ss, "_DBTicker", _fake_db_ticker(midway))
    svc = _svc(monkeypatch)
    result = svc.get_latest_transcript("IESC")
    assert "earnings call" in result["text"]
