"""Phase 3 hygiene tests: financial-table stripping, SA grades lookup
normalization, and the daily macro snapshot cache."""

import json
import os
from types import SimpleNamespace

import pytest

from app.services.seeking_alpha_service import strip_financial_tables
from app.services.sa_grades_service import SAGradesService


BALANCE_SHEET = """Q1 results were strong, management raised guidance.

Operating lease right-of-use assets 1,033 1,019
Total current assets 5,210 4,987
Goodwill 2,401 2,401
Property and equipment, net 1,872 1,790
Total liabilities 8,665 8,212
Accrued expenses and other 644 702

The CEO said the demand environment remains robust."""


def test_balance_sheet_block_collapsed():
    out = strip_financial_tables(BALANCE_SHEET)
    assert "[financial statement table omitted]" in out
    assert "Operating lease right-of-use assets" not in out
    # Prose survives.
    assert "management raised guidance" in out
    assert "demand environment remains robust" in out


def test_prose_with_numbers_untouched():
    prose = ("Revenue grew 12% to $1,033 million compared to $980 million in the "
             "prior year, while operating margin expanded to 23.5% on cost cuts.\n"
             "The company repurchased 1,200,000 shares for $150 million in Q1.")
    assert strip_financial_tables(prose) == prose


def test_short_table_runs_kept():
    # Fewer than min_run table lines: leave them (could be a small data callout).
    text = "Header\nRevenue 1,033 1,019\nEPS 2.10 1.95\nFooter prose here."
    assert strip_financial_tables(text) == text


# ---------------------------------------------------------------------------
# SA grades lookup normalization
# ---------------------------------------------------------------------------

@pytest.fixture()
def grades_csv(tmp_path):
    p = tmp_path / "grades.csv"
    p.write_text(
        "Rank,Symbol,Company Name,Quant Rating,SA Analyst Ratings,Wall Street Ratings\n"
        "1,BRK-B,Berkshire,Rating: Buy4.10,Rating: Buy3.90,Rating: Buy4.00\n"
        "2,APA,APA Corp,Rating: Buy3.47,Rating: Hold3.10,Rating: Buy3.80\n"
    )
    return str(p)


def test_dot_dash_share_class_normalization(grades_csv):
    svc = SAGradesService(csv_path=grades_csv)
    # CSV stores BRK-B; pipeline may ask for BRK.B.
    assert svc.lookup("BRK.B")["sa_quant_rating"] == 4.10
    assert svc.lookup("BRK-B")["sa_quant_rating"] == 4.10
    assert svc.lookup("APA")["sa_quant_rating"] == 3.47


def test_miss_returns_available_nulls(grades_csv):
    svc = SAGradesService(csv_path=grades_csv)
    r = svc.lookup("NOPE")
    assert r["available"] is True
    assert r["sa_quant_rating"] is None


# ---------------------------------------------------------------------------
# Daily macro snapshot cache
# ---------------------------------------------------------------------------

def test_macro_snapshot_cached_once_per_day(tmp_path, monkeypatch):
    import app.services.research_service as rs

    monkeypatch.setattr(rs, "_MACRO_SNAPSHOT_DIR", str(tmp_path))
    svc = rs.ResearchService.__new__(rs.ResearchService)

    calls = []

    def fake_call_agent(prompt, agent_name, state=None, metrics_sink=None):
        calls.append(agent_name)
        return "X" * 300  # passes _is_real_report

    monkeypatch.setattr(svc, "_call_agent", fake_call_agent)
    monkeypatch.setattr(rs.fred_service, "get_macro_data", lambda: {"cpi": 3.1})

    state = SimpleNamespace(ticker="AAA", date="2026-06-10", volatility_regime=None)
    snap1 = svc._get_or_build_macro_snapshot(state, {"news_items": []})
    assert snap1["market_sentiment"].startswith("X")
    assert snap1["economics"].startswith("X")
    assert calls == ["Market Sentiment Agent", "Economics Agent"]

    # Second stock, same day: served from cache, no further LLM calls.
    state2 = SimpleNamespace(ticker="BBB", date="2026-06-10", volatility_regime=None)
    snap2 = svc._get_or_build_macro_snapshot(state2, {"news_items": []})
    assert snap2 == snap1
    assert calls == ["Market Sentiment Agent", "Economics Agent"]
    assert os.path.exists(os.path.join(str(tmp_path), "2026-06-10.json"))


def test_macro_snapshot_failed_build_not_cached(tmp_path, monkeypatch):
    import app.services.research_service as rs

    monkeypatch.setattr(rs, "_MACRO_SNAPSHOT_DIR", str(tmp_path))
    svc = rs.ResearchService.__new__(rs.ResearchService)
    monkeypatch.setattr(svc, "_call_agent",
                        lambda *a, **k: "[Error in Market Sentiment Agent: boom]")

    state = SimpleNamespace(ticker="AAA", date="2026-06-10", volatility_regime=None)
    assert svc._get_or_build_macro_snapshot(state, {"news_items": []}) is None
    assert not os.path.exists(os.path.join(str(tmp_path), "2026-06-10.json"))
