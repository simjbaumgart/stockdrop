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


from app.utils.earnings_consistency import ConsistencyResult


def test_earnings_consistency_flags_avoid_even_without_downgrade(monkeypatch):
    from app.services import research_service as rs

    svc = rs.ResearchService()

    # Simulate an inconsistent narrative.
    monkeypatch.setattr(
        rs,
        "check_narrative_consistency",
        lambda **kw: ConsistencyResult(
            inconsistent=True,
            flag="EARNINGS_NARRATIVE_INCONSISTENT",
            reason="reasoning narrates beat but surprise_pct=-3.2",
        ),
    )
    # downgrade_action of AVOID returns AVOID (no-op) — we still want a flag visible.
    final = {
        "action": "AVOID",
        "conviction": "MEDIUM",
        "reason": "Earnings beat strongly.",
    }
    out = svc._apply_earnings_consistency(final, ticker="TOST", earnings_facts={"surprise_pct": -3.2})

    # Action stays AVOID, but the row should be visibly flagged.
    assert out["earnings_narrative_flag"] == "EARNINGS_NARRATIVE_INCONSISTENT"
    assert out["reason"].startswith("[FLAGGED]")
    # Conviction should drop one tier on a flag, even if action is unchanged.
    assert out["conviction"] == "LOW"


def test_earnings_consistency_is_idempotent(monkeypatch):
    """Calling _apply_earnings_consistency twice on the same dict must not
    drop conviction twice or duplicate the [FLAG] key_factors entry."""
    from app.services import research_service as rs

    svc = rs.ResearchService()
    monkeypatch.setattr(
        rs,
        "check_narrative_consistency",
        lambda **kw: ConsistencyResult(
            inconsistent=True,
            flag="EARNINGS_NARRATIVE_INCONSISTENT",
            reason="reasoning narrates beat but surprise_pct=-3.2",
        ),
    )
    final = {
        "action": "BUY",
        "conviction": "HIGH",
        "reason": "Earnings beat.",
        "key_factors": [],
    }
    once = svc._apply_earnings_consistency(final, ticker="TOST", earnings_facts={"surprise_pct": -3.2})
    twice = svc._apply_earnings_consistency(once, ticker="TOST", earnings_facts={"surprise_pct": -3.2})

    # Conviction dropped once (HIGH -> MEDIUM), not twice.
    assert twice["conviction"] == "MEDIUM"
    # Only one [FLAG] entry in key_factors.
    flag_entries = [kf for kf in twice["key_factors"] if str(kf).startswith("[FLAG]")]
    assert len(flag_entries) == 1
    # Reason still prefixed [FLAGGED] exactly once.
    assert twice["reason"].count("[FLAGGED]") == 1
