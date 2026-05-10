"""Regression: building the PM prompt with earnings_facts that has
surprise_pct=None (estimate was 0 or missing) must NOT raise a TypeError
on the f-string format."""
from app.services.research_service import ResearchService
from app.models.market_state import MarketState


def _make_state(earnings_facts):
    state = MarketState(ticker="TST", date="2026-05-08")
    state.reports = {
        "technical": "stub", "news": "stub", "market_sentiment": "stub",
        "competitive": "stub", "seeking_alpha": "stub",
        "bull": "stub", "bear": "stub", "risk": "stub",
    }
    state.earnings_facts = earnings_facts
    state.gatekeeper_tier = None
    return state


def _build_prompt(state):
    rs = ResearchService.__new__(ResearchService)
    return rs._create_fund_manager_prompt(state, [], [], "-7%")


def test_prompt_with_full_earnings_facts():
    state = _make_state({
        "reported_eps": 0.27, "consensus_eps": 0.20, "surprise_pct": 35.0,
        "fiscal_quarter": "2026Q1", "period": "2026-03-31",
        "source": "finnhub", "fetched_at": "2026-05-08T16:30Z",
    })
    out = _build_prompt(state)
    assert "EARNINGS_FACTS" in out
    assert "+35.0% (BEAT)" in out
    assert "2026Q1" in out


def test_prompt_with_negative_surprise():
    state = _make_state({
        "reported_eps": 0.10, "consensus_eps": 0.20, "surprise_pct": -50.0,
        "fiscal_quarter": "2026Q1", "period": "2026-03-31",
        "source": "finnhub", "fetched_at": "2026-05-08T16:30Z",
    })
    out = _build_prompt(state)
    assert "-50.0% (MISS)" in out


def test_prompt_with_none_surprise_does_not_crash():
    """Regression: when Finnhub estimate is 0 (e.g. early-stage company),
    surprise_pct comes back as None. The f-string must not crash."""
    state = _make_state({
        "reported_eps": 0.05, "consensus_eps": 0.0, "surprise_pct": None,
        "fiscal_quarter": "2026Q1", "period": "2026-03-31",
        "source": "finnhub", "fetched_at": "2026-05-08T16:30Z",
    })
    out = _build_prompt(state)  # must not raise TypeError
    assert "EARNINGS_FACTS" in out
    assert "Surprise: N/A" in out


def test_prompt_with_no_earnings_facts():
    state = _make_state(None)
    out = _build_prompt(state)
    assert "no recent reported quarter available" in out


def test_prompt_with_missing_reported_eps():
    state = _make_state({"reported_eps": None})
    out = _build_prompt(state)
    assert "no recent reported quarter available" in out
