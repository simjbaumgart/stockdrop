"""The PM prompt's DIVIDEND_FACTS block: ground truth + ex-date capture rule."""
from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _make_state(dividend_facts, date="2026-06-09"):
    state = MarketState(ticker="BAP", date=date)
    state.reports = {
        "technical": "stub", "news": "stub", "market_sentiment": "stub",
        "competitive": "stub", "seeking_alpha": "stub",
        "bull": "stub", "bear": "stub", "risk": "stub",
    }
    state.earnings_facts = None
    state.dividend_facts = dividend_facts
    state.gatekeeper_tier = None
    return state


def _build_prompt(state):
    rs = ResearchService.__new__(ResearchService)
    return rs._create_fund_manager_prompt(state, [], [], "-7%")


def test_past_ex_date_marks_capture_invalid():
    # Today 2026-06-09 is AFTER the ex-date 2026-05-18 (the BAP case).
    state = _make_state({
        "ex_dividend_date": "2026-05-18", "pay_date": "2026-06-12",
        "amount": 1.23, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "2026-05-18" in out
    assert "INVALID" in out
    assert "PAST THE EX-DIVIDEND DATE" in out


def test_future_ex_date_marks_capture_valid():
    state = _make_state({
        "ex_dividend_date": "2026-06-20", "pay_date": "2026-07-01",
        "amount": 1.23, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "2026-06-20" in out
    assert "INVALID" not in out
    assert "would be entitled" in out


def test_no_dividend_facts_omits_block():
    state = _make_state(None)
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" not in out


def test_amount_unknown_renders_gracefully():
    state = _make_state({
        "ex_dividend_date": "2026-05-18", "pay_date": None,
        "amount": None, "source": "yfinance", "fetched_at": "2026-06-09T12:00Z",
    })
    out = _build_prompt(state)
    assert "DIVIDEND_FACTS" in out
    assert "unknown" in out
