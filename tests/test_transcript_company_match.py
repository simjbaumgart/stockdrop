"""Regression: DefeatBeta returns wrong-company transcripts for ambiguous
tickers (e.g. ticker 'L' returns Loblaw Companies instead of Loews
Corporation). Before trusting the transcript, the company name embedded
in the text must roughly match the expected company name."""
from unittest.mock import patch, MagicMock
import pandas as pd
from app.services.stock_service import StockService


def _make_df(company_in_text: str):
    """Build a fake DefeatBeta DataFrame whose transcript paragraphs
    mention `company_in_text`."""
    return pd.DataFrame([{
        "report_date": "2026-04-15",
        "transcripts": [
            {"content": f"Welcome to the {company_in_text} earnings call. ..."},
            {"content": "We had a strong quarter."},
        ],
    }])


def test_rejects_mismatched_company_transcript():
    """Ticker L expects Loews; DefeatBeta returns Loblaw → reject (empty text)."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Loblaw Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker), \
         patch.object(svc, "_finnhub_latest_quarter_for", return_value=None):
        result = svc.get_latest_transcript("L", company_name="Loews Corporation")

    # On mismatch: db_text is cleared, fallback to AV is attempted, but with
    # quarter=None and no AV path, the result should be the empty fallback.
    assert result.get("text", "") == "", (
        f"expected empty result on company mismatch, got text length "
        f"{len(result.get('text', ''))}"
    )


def test_accepts_matching_company_transcript():
    """Ticker LOW expects Lowe's; DefeatBeta returns Lowe's → accept."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Lowe's Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker):
        result = svc.get_latest_transcript("LOW", company_name="Lowe's Companies")

    assert result["text"] != ""
    assert "Lowe" in result["text"]


def test_no_company_name_skips_validation():
    """Backward compat: if caller passes no company_name, no validation runs
    and any transcript is accepted."""
    svc = StockService()

    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Loblaw Companies")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker):
        result = svc.get_latest_transcript("L")  # no company_name

    assert result["text"] != "", "should accept any transcript when no expected name given"


def test_matches_first_token_only():
    """Match should pass when the first significant token of the expected
    name appears in the transcript text, even if the suffix differs."""
    svc = StockService()

    # Expected: "Apple Inc.", transcript says "Apple"
    fake_ticker = MagicMock()
    fake_ticker.earning_call_transcripts.return_value.get_transcripts_list.return_value = (
        _make_df("Apple")
    )

    with patch("app.services.stock_service._DBTicker", return_value=fake_ticker):
        result = svc.get_latest_transcript("AAPL", company_name="Apple Inc.")

    assert result["text"] != "", "first-token match should accept"
