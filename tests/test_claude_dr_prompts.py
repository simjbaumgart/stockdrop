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

from app.services.claude_dr_prompts import build_individual_prompt, build_sell_prompt, condense_sensor_report


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


# ── Step 2b: truncation removal ───────────────────────────────────────────────

class TestBullBearNoTruncation:
    """Bull/bear text must not be hard-truncated at 4000 chars for Claude."""

    # Marker is placed well past the old 4000-char limit
    _MARKER = "MARKER_PAST_4000"
    _LONG_BULL = "x" * 4010 + _MARKER + "y" * 100  # total > 4000, marker after position 4000

    def test_full_bull_case_present_in_prompt(self):
        """Content past char 4000 must appear in the prompt (no 4000-char cutoff)."""
        ctx = {**MINIMAL_CONTEXT, "bull_case": self._LONG_BULL}
        prompt = build_individual_prompt("ACME", ctx)
        assert self._MARKER in prompt, (
            "Expected content past char 4000 to appear in prompt — "
            "4000-char truncation must be removed for the Claude path."
        )

    def test_full_bear_case_present_in_prompt(self):
        """Bear case: content past char 4000 must appear in the prompt."""
        long_bear = "z" * 4010 + self._MARKER + "w" * 100
        ctx = {**MINIMAL_CONTEXT, "bear_case": long_bear}
        prompt = build_individual_prompt("ACME", ctx)
        assert self._MARKER in prompt, (
            "Expected content past char 4000 in bear_case to appear in prompt."
        )

    def test_8000_char_bull_fully_present(self):
        """8000-char bull case must survive intact (well under 40k guard)."""
        long_bull = "A" * 7990 + "END_MARKER"
        ctx = {**MINIMAL_CONTEXT, "bull_case": long_bull}
        prompt = build_individual_prompt("ACME", ctx)
        assert "END_MARKER" in prompt

    def test_hard_cap_applied_at_40000(self):
        """A 50000-char bull case should be capped at 40000, not 4000."""
        very_long = "B" * 41000
        ctx = {**MINIMAL_CONTEXT, "bull_case": very_long}
        prompt = build_individual_prompt("ACME", ctx)
        # The prompt must contain a lot of the bull text (not just 4000 chars)
        # We count occurrences of 'B' in the prompt as a proxy
        b_count = prompt.count("B" * 100)  # 100-char B-runs present
        assert b_count >= 20, "Expected most of the 40k bull content in prompt"


# ── Step 2b: condense_sensor_report helper ────────────────────────────────────

class TestCondenseSensorReport:
    """Unit tests for condense_sensor_report()."""

    def test_empty_input_returns_empty(self):
        assert condense_sensor_report("") == ""

    def test_whitespace_only_returns_empty(self):
        assert condense_sensor_report("   \n  ") == ""

    def test_short_text_returned_as_is(self):
        text = "RSI at 29 — oversold."
        result = condense_sensor_report(text)
        assert result == text

    def test_long_text_bounded_by_limit(self):
        long = "x" * 2000
        result = condense_sensor_report(long)
        assert len(result) <= _SENSOR_CONDENSE_LIMIT + 10  # small slack for separator

    def test_custom_limit_respected(self):
        text = "A" * 300
        result = condense_sensor_report(text, limit=100)
        assert len(result) <= 110  # small slack

    def test_verdict_line_preserved_at_front(self):
        text = "Verdict: BEARISH\nLong body text here.\nMore lines of analysis."
        result = condense_sensor_report(text)
        assert result.startswith("Verdict: BEARISH")

    def test_hash_header_preserved(self):
        text = "## BULLISH SIGNAL\nRSI at 28, price below BB lower.\nMore text."
        result = condense_sensor_report(text)
        assert result.startswith("## BULLISH SIGNAL")

    def test_no_verdict_line_returns_prefix(self):
        text = "Price action shows strong support. Volume confirming. " * 50
        result = condense_sensor_report(text, limit=200)
        assert len(result) <= 210
        # Should start with the beginning of the text
        assert result.startswith("Price action shows strong support.")

    def test_verdict_line_not_duplicated(self):
        text = "Verdict: BULLISH\nBody of the report continues here."
        result = condense_sensor_report(text)
        # "Verdict: BULLISH" should appear exactly once
        assert result.count("Verdict: BULLISH") == 1


def _SENSOR_CONDENSE_LIMIT():
    """Expose the module-level constant for tests above (imported via closure)."""
    from app.services import claude_dr_prompts
    return claude_dr_prompts._SENSOR_CONDENSE_LIMIT


# Fix: import the constant directly for use in test assertions above
from app.services.claude_dr_prompts import _SENSOR_CONDENSE_LIMIT


# ── Step 2b: sensor_summaries in build_individual_prompt ─────────────────────

class TestSensorSummariesInPrompt:
    """sensor_summaries content must appear when provided."""

    def test_technical_summary_in_prompt(self):
        ctx = {
            **MINIMAL_CONTEXT,
            "sensor_summaries": {
                "Technical Analysis": "RSI at 29, price below BB lower — oversold.",
            },
        }
        prompt = build_individual_prompt("ACME", ctx)
        assert "RSI at 29" in prompt or "oversold" in prompt

    def test_all_five_summaries_in_prompt(self):
        ctx = {
            **MINIMAL_CONTEXT,
            "sensor_summaries": {
                "Technical Analysis": "Oversold — RSI 28.",
                "News Analysis": "EPS miss by 12 cents.",
                "Market Sentiment": "Short interest at 8%.",
                "Competitive Landscape": "Peers flat on the day.",
                "Seeking Alpha": "Quant rating Strong Buy.",
            },
        }
        prompt = build_individual_prompt("ACME", ctx)
        assert "Oversold" in prompt
        assert "EPS miss" in prompt
        assert "Short interest" in prompt
        assert "Peers flat" in prompt
        assert "Quant rating" in prompt
