"""Integration tests for news digest injection into research_service prompts."""

import json
from pathlib import Path

import pytest

from app.models.market_state import MarketState


@pytest.fixture
def archive_tree_with_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "true")
    digests_dir = tmp_path / "FT Archive" / "digests"
    digests_dir.mkdir(parents=True)
    digest = {
        "date": "2026-04-22",
        "source": "ft",
        "one_liner": "AI capex unease bleeds into credit.",
        "market_tape": "Risk-off tone as credit spreads widen.",
        "themes": [
            {
                "theme": "private_credit_strain",
                "sentiment": "bearish",
                "confidence": 0.8,
                "opinion_driven": False,
                "supporting_articles": ["x"],
                "one_liner": "Credit widening.",
            }
        ],
        "tickers_mentioned": {
            "NVDA": {"count": 1, "sentiment": "bearish", "articles": ["x"], "relevance_to_portfolio": "high"}
        },
        "macro_signals": [
            {"signal": "fed_hawkish_shift", "direction": "up_rates", "confidence": 0.6, "article": "x"}
        ],
        "risk_flags": [{"flag": "geopolitical_hormuz", "severity": "medium", "impacts": ["energy"]}],
        "flagged_critical": [],
    }
    (digests_dir / "2026-04-22.json").write_text(json.dumps(digest))
    (digests_dir / "2026-04-22.md").write_text(
        "# FT Digest — 2026-04-22\n_AI capex unease bleeds into credit._\n\n## Market tape\nRisk-off tone.\n"
    )
    return tmp_path


def _svc():
    from app.services.research_service import ResearchService
    return ResearchService.__new__(ResearchService)


def test_news_agent_block_has_header_and_content(archive_tree_with_digest):
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = _svc()._news_block_for(state, "news")
    assert "RELEVANT NEWS DIGEST" in block
    assert "AI capex unease" in block


def test_pm_block_has_only_compact(archive_tree_with_digest):
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = _svc()._news_block_for(state, "pm")
    assert "AI capex unease" in block
    assert "private_credit_strain" not in block


def test_bear_block_has_bearish_and_macro(archive_tree_with_digest):
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = _svc()._news_block_for(state, "bear")
    assert "private_credit_strain" in block
    assert "fed_hawkish_shift" in block
    assert "geopolitical_hormuz" in block


def test_risk_block_has_macro_not_themes(archive_tree_with_digest):
    state = MarketState(ticker="NVDA", date="2026-04-22")
    block = _svc()._news_block_for(state, "risk")
    assert "fed_hawkish_shift" in block
    assert "geopolitical_hormuz" in block
    assert "private_credit_strain" not in block


def test_transitive_consumers_get_empty(archive_tree_with_digest):
    state = MarketState(ticker="NVDA", date="2026-04-22")
    svc = _svc()
    assert svc._news_block_for(state, "technical") == ""
    assert svc._news_block_for(state, "bull") == ""
    assert svc._news_block_for(state, "deep_research") == ""
    assert svc._news_block_for(state, "seeking_alpha") == ""


def test_missing_digest_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    state = MarketState(ticker="NVDA", date="2026-04-22")
    assert _svc()._news_block_for(state, "news") == ""


def test_prompt_builders_compile_with_injection(archive_tree_with_digest):
    """The actual _create_*_prompt methods must return a string that includes the block."""
    from app.services.research_service import ResearchService

    svc = ResearchService.__new__(ResearchService)
    state = MarketState(ticker="NVDA", date="2026-04-22")
    state.reports = {"technical": "t", "news": "n", "market_sentiment": "s"}

    raw_data = {"news_items": [], "transcript_text": ""}
    # News agent
    p = svc._create_news_agent_prompt(state, raw_data, "-6.5%")
    assert "AI capex unease" in p
    # Competitive
    p = svc._create_competitive_agent_prompt(state, "-6.5%")
    assert "RELEVANT NEWS DIGEST" in p
    assert "private_credit_strain" in p
    # Bear
    p = svc._create_bear_prompt(state, "-6.5%")
    assert "private_credit_strain" in p
    # Risk
    p = svc._create_risk_agent_prompt(state, "-6.5%")
    assert "fed_hawkish_shift" in p
    # Sentiment
    p = svc._create_market_sentiment_prompt(state, raw_data)
    assert "MARKET TAPE" in p or "AI capex" in p
    # PM
    p = svc._create_fund_manager_prompt(state, [], [], "-6.5%")
    assert "AI capex unease" in p
    assert "private_credit_strain" not in p  # PM gets compact only

    # Bull must NOT have it (transitive)
    p = svc._create_bull_prompt(state, "-6.5%")
    assert "RELEVANT NEWS DIGEST" not in p
