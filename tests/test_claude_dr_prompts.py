"""Tests for app/services/claude_dr_prompts.py — Claude-native deep-research prompts.

Run (offline only):
    python3 -m pytest tests/test_claude_dr_prompts.py -v
"""
import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

MINIMAL_CONTEXT = {
    "pm_decision": {"verdict": "BUY", "reason": "looks good"},
    "bull_case": "Strong revenue growth and expanding margins.",
    "bear_case": "Debt load is high; macro headwinds.",
    "technical_data": {"rsi": 29, "price": 95.0, "bb_lower": 90.0},
    "drop_percent": -6.2,
    "raw_news": [
        {
            "datetime_str": "2026-05-29",
            "source": "Benzinga",
            "source_type": "WIRE",
            "headline": "ACME misses Q1 EPS by 12 cents",
            "content": "ACME Corporation reported Q1 results below analyst estimates.",
        }
    ],
    "transcript_summary": "CEO highlighted margin compression due to raw-material costs.",
    "transcript_date": "2026-05-01",
    "data_depth": {"news": {"total_count": 18}},
}

FULL_CONTEXT = {
    **MINIMAL_CONTEXT,
    "sensor_summaries": {
        "Technical Analysis": "RSI at 29 — oversold; BB lower touched.",
        "News Analysis": "EPS miss, guidance cut.",
    },
    "disagreement_points": [
        "Bull claims guidance cut is one-time; Bear says it signals structural margin erosion.",
        "Council disagrees on whether insider buying offsets the sell-off.",
    ],
}


# ── import the module under test ─────────────────────────────────────────────

from app.services.claude_dr_prompts import build_individual_prompt, build_sell_prompt


# ── test_build_individual_prompt ─────────────────────────────────────────────

class TestBuildIndividualPrompt:
    """Tests for build_individual_prompt(symbol, context)."""

    def test_no_error_minimal_context(self):
        """Builds without error when optional keys are absent."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert isinstance(prompt, str)
        assert len(prompt) > 200

    def test_no_error_full_context(self):
        """Builds without error when optional sensor_summaries and disagreement_points present."""
        prompt = build_individual_prompt("ACME", FULL_CONTEXT)
        assert isinstance(prompt, str)

    def test_contains_primary_source_directives(self):
        """Must direct Claude to search SEC EDGAR, Form 4, and other primary sources."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "EDGAR" in prompt, "Expected SEC EDGAR reference"
        assert "Form 4" in prompt, "Expected Form 4 (insider trades) reference"
        # Check there's an explicit search directive
        lower = prompt.lower()
        assert "search" in lower, "Expected a search directive"

    def test_names_all_five_council_agents(self):
        """Must name all five council agents so Claude targets gaps."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        # Exact agent names (case-insensitive)
        for agent in (
            "Technical",
            "News",
            "Seeking Alpha",
        ):
            assert agent.lower() in prompt.lower(), f"Expected agent '{agent}' named in prompt"
        # Sentiment / Market Sentiment — accept either form
        assert (
            "sentiment" in prompt.lower() or "market sentiment" in prompt.lower()
        ), "Expected 'Sentiment' or 'Market Sentiment' agent named in prompt"
        # Competitive / Competitive Landscape — accept either form
        assert (
            "competitive" in prompt.lower()
        ), "Expected 'Competitive' agent named in prompt"

    def test_no_gemini_framing(self):
        """Must NOT contain Gemini-specific language."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "Google Search" not in prompt, "Should not contain 'Google Search'"
        assert "not available via web search" not in prompt.lower(), (
            "Should not contain 'not available via web search'"
        )

    def test_omits_google_search_in_full_context_too(self):
        prompt = build_individual_prompt("ACME", FULL_CONTEXT)
        assert "Google Search" not in prompt

    def test_contains_elm_partners_or_priced_in_reminder(self):
        """Must preserve the 'priced in' / Elm Partners humility reminder."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        lower = prompt.lower()
        assert "priced in" in lower or "elm" in lower or "priced-in" in lower, (
            "Expected Elm Partners / 'priced in' humility reminder"
        )

    def test_knife_catch_warning_required(self):
        """Must require knife_catch_warning output."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "knife_catch_warning" in prompt

    def test_council_blindspots_required(self):
        """Must require council_blindspots output."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "council_blindspots" in prompt

    def test_external_driver_dominance_check(self):
        """Must include the external-driver-dominance check (sector/commodity/rate/FX)."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        lower = prompt.lower()
        assert (
            "sector" in lower or "commodity" in lower or "rate" in lower or "fx" in lower
        ), "Expected external driver (sector/commodity/rate/FX) dominance check"

    def test_could_not_verify_instruction(self):
        """Must instruct Claude to emit a could_not_verify list."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "could_not_verify" in prompt

    def test_dated_event_timeline_required(self):
        """Must require a dated event timeline output."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        lower = prompt.lower()
        assert "timeline" in lower or "dated" in lower, (
            "Expected a dated event timeline directive"
        )

    def test_bull_bear_disagreement_as_priority_when_absent(self):
        """When disagreement_points absent, should still direct Claude to resolve bull/bear contradictions."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        lower = prompt.lower()
        assert (
            "bull" in lower and "bear" in lower
        ), "Expected bull/bear contradiction resolution directive"

    def test_disagreement_points_included_when_present(self):
        """When disagreement_points present, they must appear in the prompt."""
        prompt = build_individual_prompt("ACME", FULL_CONTEXT)
        # At least one of the disagreement point substrings should appear
        assert "guidance cut" in prompt or "one-time" in prompt or "insider buying" in prompt, (
            "Expected explicit disagreement_points content in prompt"
        )

    def test_sensor_summaries_included_when_present(self):
        """When sensor_summaries present, they must appear in the prompt."""
        prompt = build_individual_prompt("ACME", FULL_CONTEXT)
        assert "RSI at 29" in prompt or "oversold" in prompt, (
            "Expected sensor_summaries content in prompt"
        )

    def test_sensor_summaries_graceful_when_absent(self):
        """When sensor_summaries absent, prompt still names the five agents."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "Technical" in prompt  # falls back to naming agents

    def test_pm_decision_included(self):
        """PM decision must appear in the prompt."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "BUY" in prompt  # the verdict from the minimal context

    def test_drop_percent_included(self):
        """Drop percent must appear in the prompt."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "6.2" in prompt or "6.20" in prompt

    def test_bull_case_included(self):
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "expanding margins" in prompt

    def test_bear_case_included(self):
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "Debt load" in prompt

    def test_symbol_included(self):
        prompt = build_individual_prompt("AAPL", MINIMAL_CONTEXT)
        assert "AAPL" in prompt

    def test_news_articles_included(self):
        """Council news articles should appear as supplementary evidence."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "ACME misses Q1 EPS" in prompt or "Q1 EPS" in prompt

    def test_web_fetch_mentioned(self):
        """Should mention web_fetch as available tool for reaching paywalled pages."""
        prompt = build_individual_prompt("ACME", MINIMAL_CONTEXT)
        assert "web_fetch" in prompt or "web fetch" in prompt.lower()


# ── test_build_sell_prompt ────────────────────────────────────────────────────

class TestBuildSellPrompt:
    """Basic sanity checks for build_sell_prompt — scope intentionally narrow."""

    SELL_CONTEXT = {
        "original_decision": {
            "entry_price_low": 90.0,
            "entry_price_high": 95.0,
            "stop_loss": 85.0,
            "sell_price_low": 105.0,
            "sell_price_high": 115.0,
            "ceiling_exit": 120.0,
            "reason": "Oversold bounce on earnings miss.",
        },
        "current_price": 108.0,
        "performance_since_entry": "+15%",
        "technical_data": {"rsi": 65},
        "sensor_reports": {"technical": "RSI rising, price at BB upper."},
        "raw_news": [],
    }

    def test_builds_without_error(self):
        prompt = build_sell_prompt("ACME", self.SELL_CONTEXT)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_no_google_search(self):
        prompt = build_sell_prompt("ACME", self.SELL_CONTEXT)
        assert "Google Search" not in prompt

    def test_symbol_in_prompt(self):
        prompt = build_sell_prompt("ACME", self.SELL_CONTEXT)
        assert "ACME" in prompt

    def test_entry_prices_in_prompt(self):
        prompt = build_sell_prompt("ACME", self.SELL_CONTEXT)
        assert "90" in prompt or "95" in prompt


# ── schema tests ─────────────────────────────────────────────────────────────

class TestIndividualSchemaCouldNotVerify:
    """Schema extension: could_not_verify field added to INDIVIDUAL_SCHEMA."""

    from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA as _SCHEMA

    def test_could_not_verify_in_properties(self):
        from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
        assert "could_not_verify" in INDIVIDUAL_SCHEMA["properties"], (
            "could_not_verify must be a property in INDIVIDUAL_SCHEMA"
        )

    def test_could_not_verify_is_array_of_strings(self):
        from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
        prop = INDIVIDUAL_SCHEMA["properties"]["could_not_verify"]
        assert prop["type"] == "array"
        assert prop["items"]["type"] == "string"

    def test_could_not_verify_in_required(self):
        from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
        assert "could_not_verify" in INDIVIDUAL_SCHEMA["required"], (
            "could_not_verify must be in the required list of INDIVIDUAL_SCHEMA"
        )

    def test_sample_dict_with_all_required_keys_passes_structural_check(self):
        """Verify that a sample dict covers all required INDIVIDUAL_SCHEMA fields."""
        from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
        sample = {
            "review_verdict": "CONFIRMED",
            "action": "BUY",
            "conviction": "HIGH",
            "drop_type": "EARNINGS_MISS",
            "risk_level": "Medium",
            "catalyst_type": "Temporary",
            "entry_price_low": 90.0,
            "entry_price_high": 95.0,
            "stop_loss": 85.0,
            "take_profit_1": 105.0,
            "upside_percent": 15.0,
            "downside_risk_percent": 5.0,
            "risk_reward_ratio": 3.0,
            "entry_trigger": "Price bounces off BB lower with volume.",
            "reassess_in_days": 5,
            "sell_price_low": 105.0,
            "sell_price_high": 115.0,
            "ceiling_exit": 120.0,
            "exit_trigger": "RSI > 70 and price at $115.",
            "global_market_analysis": "Rates stable; macro benign.",
            "local_market_analysis": "Sector in rotation; commodity stable.",
            "swot_analysis": {
                "strengths": ["s1"],
                "weaknesses": ["w1"],
                "opportunities": ["o1"],
                "threats": ["t1"],
            },
            "verification_results": [
                {"claim": "EPS miss", "verdict": "VERIFIED", "source_url": "https://example.com"}
            ],
            "council_blindspots": ["Missed insider sell."],
            "knife_catch_warning": False,
            "reason": "Oversold bounce with improving fundamentals.",
            "could_not_verify": ["Could not confirm CFO departure rumor."],
        }
        required = INDIVIDUAL_SCHEMA["required"]
        missing = [k for k in required if k not in sample]
        assert not missing, f"Sample dict missing required keys: {missing}"
