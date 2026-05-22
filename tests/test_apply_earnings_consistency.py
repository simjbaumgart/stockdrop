"""Conviction-downgrade behavior of the earnings-narrative consistency flag.

Regression: TTWO 2026-05-22 — PM emitted conviction 'MODERATE', which was
absent from the conviction ladder, so the downgrade silently no-opped.
"""

import os
import sys

os.environ.setdefault("DB_PATH", "test_aec.db")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.research_service import ResearchService

# Positive surprise paired with a "missed estimates" narrative — inconsistent.
INCONSISTENT_FACTS = {"surprise_pct": 38.6}


def _decision(conviction):
    return {
        "action": "WATCH",
        "conviction": conviction,
        "reason": "The company missed estimates this quarter, dragging the stock down.",
    }


def test_moderate_conviction_downgrades_to_low():
    svc = ResearchService()
    out = svc._apply_earnings_consistency(_decision("MODERATE"), "TTWO", INCONSISTENT_FACTS)
    assert out["conviction"] == "LOW"


def test_high_conviction_downgrades_to_medium():
    svc = ResearchService()
    out = svc._apply_earnings_consistency(_decision("HIGH"), "KWHIY", INCONSISTENT_FACTS)
    assert out["conviction"] == "MEDIUM"


def test_low_conviction_downgrades_to_none():
    svc = ResearchService()
    out = svc._apply_earnings_consistency(_decision("LOW"), "KWHIY", INCONSISTENT_FACTS)
    assert out["conviction"] == "NONE"


def test_flag_prefixes_reason():
    svc = ResearchService()
    out = svc._apply_earnings_consistency(_decision("MODERATE"), "TTWO", INCONSISTENT_FACTS)
    assert out["reason"].startswith("[FLAGGED]")
