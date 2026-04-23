import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import news_digest_service as nds

FIXTURES = Path(__file__).parent / "fixtures" / "news"


@pytest.fixture
def archive_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "true")
    (tmp_path / "FT Archive" / "daily").mkdir(parents=True)
    (tmp_path / "Finimize Archive" / "daily").mkdir(parents=True)
    (tmp_path / "Finimize Archive" / "weekly").mkdir(parents=True)
    (tmp_path / "FT Archive" / "daily" / "2026-04-22.md").write_text(
        (FIXTURES / "ft_2026-04-22.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "Finimize Archive" / "daily" / "2026-04-22.md").write_text(
        (FIXTURES / "finimize_2026-04-22.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


def _fake_digest(source: str, date: str) -> dict:
    return {
        "date": date,
        "source": source,
        "generated_at": "2026-04-22T07:12:00Z",
        "model": "gemini-3.1-pro-thinking",
        "one_liner": "AI capex unease crosses into credit.",
        "market_tape": "Risk-off tone as credit spreads widen; oil vol tied to Trump feed.",
        "themes": [
            {
                "theme": "private_credit_strain",
                "sentiment": "bearish",
                "confidence": 0.8,
                "opinion_driven": False,
                "supporting_articles": ["47606fe2-108e-4a71-ba2b-9e1b779edda8"],
                "one_liner": "Private credit spreads widening.",
            },
            {
                "theme": "ai_capex_surge",
                "sentiment": "bullish",
                "confidence": 0.6,
                "opinion_driven": False,
                "supporting_articles": ["87ea0ced-bf3c-4822-8dda-437241570ded"],
                "one_liner": "Bezos AI lab near $38bn valuation.",
            },
        ],
        "tickers_mentioned": {
            "NVDA": {
                "count": 2,
                "sentiment": "bearish",
                "articles": ["x"],
                "relevance_to_portfolio": "high",
            }
        },
        "macro_signals": [
            {"signal": "fed_hawkish_shift", "direction": "up_rates", "confidence": 0.6, "article": "x"}
        ],
        "risk_flags": [
            {"flag": "geopolitical_hormuz", "severity": "medium", "impacts": ["energy", "safe_havens"]}
        ],
        "flagged_critical": [
            {
                "ticker": "AAPL",
                "headline": "Guidance cut",
                "uuid": "deadbeef",
                "reason": "earnings_guidance_cut",
            }
        ],
    }


# --- bail conditions ------------------------------------------------------


def test_bails_when_raw_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWS_ARCHIVE_ROOT", str(tmp_path))
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


def test_bails_when_disabled(archive_tree, monkeypatch):
    monkeypatch.setenv("NEWS_DIGEST_ENABLED", "false")
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


def test_bails_when_raw_empty(archive_tree):
    (archive_tree / "FT Archive" / "daily" / "2026-04-22.md").write_text("")
    assert nds.ensure_daily_digest("ft", "2026-04-22") is None


# --- generation + idempotency --------------------------------------------


def test_ensure_is_idempotent(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.return_value = json.dumps(_fake_digest("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
        nds.ensure_daily_digest("ft", "2026-04-22")
    assert mock.call_count == 1


def test_writes_json_and_md(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.return_value = json.dumps(_fake_digest("ft", "2026-04-22"))
        result = nds.ensure_daily_digest("ft", "2026-04-22")
    assert result["one_liner"].startswith("AI capex")
    j = archive_tree / "FT Archive" / "digests" / "2026-04-22.json"
    m = archive_tree / "FT Archive" / "digests" / "2026-04-22.md"
    assert j.exists() and m.exists()
    assert "private_credit_strain" in m.read_text()


def test_handles_json_fence(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.return_value = "```json\n" + json.dumps(_fake_digest("ft", "2026-04-22")) + "\n```"
        result = nds.ensure_daily_digest("ft", "2026-04-22")
    assert result is not None


def test_appends_flagged_critical(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.return_value = json.dumps(_fake_digest("ft", "2026-04-22"))
        nds.ensure_daily_digest("ft", "2026-04-22")
    flagged = archive_tree / "flagged_for_portfolio_desk.json"
    entries = json.loads(flagged.read_text())
    assert any(e["ticker"] == "AAPL" and e["source"] == "ft" for e in entries)


def test_ensure_news_digests_for_today_both_sources(archive_tree):
    call_count = {"n": 0}

    def _fake(prompt):
        call_count["n"] += 1
        # derive source from prompt
        src = "ft" if "FT articles" in prompt else "finimize"
        return json.dumps(_fake_digest(src, "2026-04-22"))

    with patch.object(nds, "_call_thinking_model", side_effect=_fake):
        nds.ensure_news_digests_for_today("2026-04-22")
    assert call_count["n"] == 2
    assert (archive_tree / "FT Archive" / "digests" / "2026-04-22.json").exists()
    assert (archive_tree / "Finimize Archive" / "digests" / "2026-04-22.json").exists()


# --- weekly (FT only, pulls last 3 Finimize weekly rollups) --------------


def test_ft_weekly_pulls_last_3_finimize_rollups(archive_tree):
    iso_week = "2026-W17"  # week of 2026-04-20 (Mon) to 2026-04-24 (Fri)

    # Seed FT daily digests for Mon-Fri of this week
    for d in ["2026-04-20", "2026-04-21", "2026-04-22", "2026-04-23", "2026-04-24"]:
        p = archive_tree / "FT Archive" / "digests" / f"{d}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# FT Digest — {d}\n_Test daily {d}_\n")

    # Seed scheduler-written Finimize weekly rollups for the 3 prior weeks
    prior_weeks = ["2026-W16", "2026-W15", "2026-W14"]
    for pw in prior_weeks:
        p = archive_tree / "Finimize Archive" / "weekly" / f"{pw}.md"
        p.write_text(f"# Finimize weekly {pw}\nThemes for {pw}...\n")

    captured_prompt = {"text": ""}

    def _capture(prompt):
        captured_prompt["text"] = prompt
        return "# FT Weekly 2026-W17\nDirection of the tape...\n"

    with patch.object(nds, "_call_thinking_model", side_effect=_capture):
        result = nds.ensure_ft_weekly_digest(iso_week)

    assert result is not None
    assert result["inputs"]["finimize_weekly_count"] == 3
    assert set(result["inputs"]["finimize_weeks_used"]) == set(prior_weeks)
    # Every prior Finimize weekly must be in the prompt context
    for pw in prior_weeks:
        assert pw in captured_prompt["text"]
    # The 3-week count is explicit in the inputs summary
    assert result["inputs"]["ft_daily_count"] == 5

    out = archive_tree / "FT Archive" / "digests" / "weekly" / "2026-W17.md"
    assert out.exists()
    assert "Direction of the tape" in out.read_text()


def test_ft_weekly_idempotent(archive_tree):
    iso_week = "2026-W17"
    out = archive_tree / "FT Archive" / "digests" / "weekly" / f"{iso_week}.md"
    out.parent.mkdir(parents=True)
    out.write_text("existing weekly")
    with patch.object(nds, "_call_thinking_model") as mock:
        result = nds.ensure_ft_weekly_digest(iso_week)
    assert result["skipped"] is True
    mock.assert_not_called()


def test_ft_weekly_tolerates_missing_finimize_rollups(archive_tree):
    iso_week = "2026-W17"
    # FT dailies present, no Finimize weeklies
    for d in ["2026-04-20", "2026-04-21", "2026-04-22"]:
        p = archive_tree / "FT Archive" / "digests" / f"{d}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# FT {d}\n")
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.return_value = "# weekly output\n"
        result = nds.ensure_ft_weekly_digest(iso_week)
    assert result is not None
    assert result["inputs"]["finimize_weekly_count"] == 0


def test_ft_weekly_bails_when_no_ft_dailies(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        result = nds.ensure_ft_weekly_digest("2026-W17")
    assert result is None


def test_prior_iso_weeks_wraps_year_boundary():
    # Week 2 of 2026 -> prior weeks should include 2025 weeks
    prev = nds._prior_iso_weeks("2026-W02", 3)
    assert len(prev) == 3
    # Most recent first
    assert prev[0] == "2026-W01"
    # Then 2025 weeks
    assert any(w.startswith("2025-") for w in prev[1:])


# --- format_for_agent slicing --------------------------------------------


def _seed_both_daily_digests(archive_tree):
    with patch.object(nds, "_call_thinking_model") as mock:
        mock.side_effect = [
            json.dumps(_fake_digest("ft", "2026-04-22")),
            json.dumps(_fake_digest("finimize", "2026-04-22")),
        ]
        nds.ensure_daily_digest("ft", "2026-04-22")
        nds.ensure_daily_digest("finimize", "2026-04-22")


def test_news_agent_gets_full(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("news", "2026-04-22", "NVDA")
    assert "private_credit_strain" in block
    assert "FT daily digest" in block
    assert "Finimize daily digest" in block


def test_pm_gets_compact_only(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("pm", "2026-04-22", "NVDA")
    assert "AI capex unease" in block
    # Compact must NOT include theme details
    assert "private_credit_strain" not in block


def test_technical_gets_empty(archive_tree):
    _seed_both_daily_digests(archive_tree)
    assert nds.format_for_agent("technical", "2026-04-22", "NVDA") == ""


def test_bull_gets_empty_transitive(archive_tree):
    _seed_both_daily_digests(archive_tree)
    assert nds.format_for_agent("bull", "2026-04-22", "NVDA") == ""


def test_deep_research_gets_empty_transitive(archive_tree):
    _seed_both_daily_digests(archive_tree)
    assert nds.format_for_agent("deep_research", "2026-04-22", "NVDA") == ""


def test_bear_gets_bearish_bundle(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("bear", "2026-04-22", "NVDA")
    assert "private_credit_strain" in block   # bearish theme
    assert "ai_capex_surge" not in block      # bullish filtered out
    assert "geopolitical_hormuz" in block     # risk flag
    assert "fed_hawkish_shift" in block       # macro signal


def test_risk_gets_macro_risk_only(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("risk", "2026-04-22", "NVDA")
    assert "fed_hawkish_shift" in block
    assert "geopolitical_hormuz" in block
    # Risk should NOT receive full theme detail
    assert "private_credit_strain" not in block


def test_sentiment_includes_themes_and_macro_not_tickers(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("market_sentiment", "2026-04-22", "NVDA")
    assert "MARKET TAPE" in block
    assert "private_credit_strain" in block
    assert "fed_hawkish_shift" in block
    assert "AAPL" not in block  # no flagged_critical
    assert "NVDA" not in block  # no tickers_mentioned


def test_competitive_includes_tickers_and_risk(archive_tree):
    _seed_both_daily_digests(archive_tree)
    block = nds.format_for_agent("competitive", "2026-04-22", "NVDA")
    assert "private_credit_strain" in block
    assert "NVDA" in block  # direct ticker match
    assert "geopolitical_hormuz" in block


def test_missing_digest_returns_empty(archive_tree):
    assert nds.format_for_agent("news", "2026-04-22", "NVDA") == ""


def test_format_for_agent_includes_finimize_weekly_when_mapped(archive_tree, monkeypatch):
    # Seed a Finimize weekly rollup and re-map PM to consume it for this test.
    iso_week = nds._iso_week_for("2026-04-22")
    p = archive_tree / "Finimize Archive" / "weekly" / f"{iso_week}.md"
    p.write_text("# Finimize weekly rollup\nAccumulating AI thesis...\n")
    _seed_both_daily_digests(archive_tree)

    # Patch the map to route a Finimize weekly slice into PM for this test
    from app.services import news_digest_schema as sch
    original = sch.AGENT_SLICE_MAP["pm"]["finimize_weekly"]
    monkeypatch.setitem(sch.AGENT_SLICE_MAP["pm"], "finimize_weekly", "weekly_full")
    try:
        block = nds.format_for_agent("pm", "2026-04-22", "NVDA")
        assert "Finimize weekly rollup" in block
        assert "Accumulating AI thesis" in block
    finally:
        monkeypatch.setitem(sch.AGENT_SLICE_MAP["pm"], "finimize_weekly", original)
