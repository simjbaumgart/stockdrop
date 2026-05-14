"""Phase 1 must abort when source material is too thin, even if all five
sensor agents returned text (FJIKY 2026-05-14)."""

import os
import sys

os.environ.setdefault("DB_PATH", "test_phase1_depth.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.research_service import (
    ResearchService,
    MIN_SA_ITEMS_FOR_DECISION,
    MIN_TICKER_NEWS_FOR_DECISION,
)


def test_source_depth_check_aborts_when_sa_and_news_both_thin():
    svc = ResearchService()
    raw_data = {
        "news_items": [{"headline": "x"}] * (MIN_TICKER_NEWS_FOR_DECISION - 1),
        "seeking_alpha_local_counts": {"analysis": 0, "news": 0, "press_releases": 0},
    }
    aborted, reason = svc._source_depth_insufficient(raw_data)
    assert aborted is True
    assert "thin" in reason.lower() or "insufficient" in reason.lower()


def test_source_depth_passes_when_news_is_sufficient():
    svc = ResearchService()
    raw_data = {
        "news_items": [{"headline": "x"}] * (MIN_TICKER_NEWS_FOR_DECISION + 5),
        "seeking_alpha_local_counts": {"analysis": 0, "news": 0, "press_releases": 0},
    }
    aborted, _ = svc._source_depth_insufficient(raw_data)
    assert aborted is False


def test_source_depth_passes_when_sa_has_items():
    svc = ResearchService()
    raw_data = {
        "news_items": [{"headline": "x"}] * 2,
        "seeking_alpha_local_counts": {
            "analysis": MIN_SA_ITEMS_FOR_DECISION,
            "news": 0,
            "press_releases": 0,
        },
    }
    aborted, _ = svc._source_depth_insufficient(raw_data)
    assert aborted is False


def test_source_depth_passes_when_only_press_releases_present():
    """Regression: covers the seeking_alpha_service 'pr' -> 'press_releases'
    key translation. If the translation breaks, this test fails because
    sa_items would sum to 0 and trigger the abort."""
    svc = ResearchService()
    raw_data = {
        "news_items": [{"headline": "x"}] * 2,
        "seeking_alpha_local_counts": {
            "analysis": 0,
            "news": 0,
            "press_releases": MIN_SA_ITEMS_FOR_DECISION,
        },
    }
    aborted, _ = svc._source_depth_insufficient(raw_data)
    assert aborted is False
