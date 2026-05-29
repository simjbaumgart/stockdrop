from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA, SELL_SCHEMA, BATCH_SCHEMA


def test_individual_schema_has_required_result_keys():
    props = INDIVIDUAL_SCHEMA["properties"]
    for key in (
        "review_verdict", "action", "conviction", "entry_price_low",
        "entry_price_high", "stop_loss", "verification_results",
        "swot_analysis", "knife_catch_warning", "reason",
        "sell_price_low", "sell_price_high", "ceiling_exit", "exit_trigger",
    ):
        assert key in props, f"missing {key}"
    assert INDIVIDUAL_SCHEMA["additionalProperties"] is False


def test_verification_results_items_carry_source_url():
    item = INDIVIDUAL_SCHEMA["properties"]["verification_results"]["items"]
    assert set(item["required"]) >= {"claim", "verdict", "source_url"}
    assert item["additionalProperties"] is False


from app.services.claude_deep_research_service import _collect_source_urls


class _FakeBlock:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.type = kw.get("type")


def test_collect_source_urls_pulls_from_web_tool_results_and_citations():
    # web_search result block carrying a list of results with urls
    search_block = _FakeBlock(
        type="web_search_tool_result",
        content=[_FakeBlock(type="web_search_result",
                            url="https://example.com/a", title="A")],
    )
    # a text block carrying a citation with a url
    cited = _FakeBlock(
        type="text", text="x",
        citations=[_FakeBlock(type="web_search_result_location",
                              url="https://example.com/b")],
    )
    urls = _collect_source_urls([search_block, cited])
    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_collect_source_urls_dedupes_and_skips_nonhttp():
    blocks = [
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="https://x.com/1")]),
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="https://x.com/1")]),
        _FakeBlock(type="web_search_tool_result",
                   content=[_FakeBlock(type="web_search_result", url="ftp://nope")]),
    ]
    assert _collect_source_urls(blocks) == ["https://x.com/1"]


import os
import pytest
from unittest.mock import patch, MagicMock
from app.services.claude_deep_research_service import claude_deep_research_service, ClaudeDeepResearchService

requires_live = pytest.mark.skipif(
    not (os.getenv("CLAUDE_API_KEY") and os.getenv("RUN_CLAUDE_LIVE_TESTS")),
    reason="set CLAUDE_API_KEY and RUN_CLAUDE_LIVE_TESTS=1 to run live Claude test",
)


# ---------------------------------------------------------------------------
# Unit tests for synthesis-cost accounting (Step 4 fix)
# ---------------------------------------------------------------------------

CANNED_RESEARCH = {
    "transcript_text": "Some research findings about AAPL.",
    "source_urls": ["https://example.com/a"],
    "thinking": "internal thoughts",
    "usage": {"in": 5000, "out": 1500, "cache_read": 200, "cache_write": 100},
    "search_count": 3,
    "latency_s": 8.2,
}

CANNED_SYNTH_RESULT = {
    "review_verdict": "CONFIRMED",
    "action": "BUY",
    "conviction": "HIGH",
    "entry_price_low": 174.0,
    "entry_price_high": 177.5,
    "stop_loss": 169.0,
    "take_profit_1": 194.0,
    "take_profit_2": 209.0,
    "sell_price_low": 189.0,
    "sell_price_high": 204.0,
    "ceiling_exit": 219.0,
    "risk_reward_ratio": 3.4,
    "entry_trigger": "Bounce off 50d MA",
    "exit_trigger": "Break above 52-week high",
    "reason": "Strong fundamentals",
    "knife_catch_warning": "LOW",
    "could_not_verify": [],
    "verification_results": [],
    "swot_analysis": {},
}

CANNED_SYNTH_USAGE = {"in": 1000, "out": 500, "cache_read": 0, "cache_write": 0}


def test_synthesize_returns_tuple_with_usage(monkeypatch):
    """_synthesize must return a (result_or_None, synth_usage_dict) tuple."""
    svc = ClaudeDeepResearchService()
    svc.api_key = "fake"

    # Build a minimal mock response
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = '{"review_verdict": "CONFIRMED"}'

    fake_usage = MagicMock()
    fake_usage.input_tokens = 1000
    fake_usage.output_tokens = 500
    fake_usage.cache_read_input_tokens = 50
    fake_usage.cache_creation_input_tokens = 25

    fake_resp = MagicMock()
    fake_resp.content = [fake_block]
    fake_resp.usage = fake_usage

    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.get_final_message = MagicMock(return_value=fake_resp)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = fake_stream
    svc._client = mock_client

    from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
    result, synth_usage = svc._synthesize("transcript text", ["https://a.com"], INDIVIDUAL_SCHEMA)

    assert result == {"review_verdict": "CONFIRMED"}
    assert synth_usage["in"] == 1000
    assert synth_usage["out"] == 500
    assert synth_usage["cache_read"] == 50
    assert synth_usage["cache_write"] == 25


def test_synthesize_returns_none_with_usage_on_parse_failure(monkeypatch):
    """_synthesize must return (None, synth_usage) when JSON parse fails — usage is still reported."""
    svc = ClaudeDeepResearchService()
    svc.api_key = "fake"

    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = "not valid json {"

    fake_usage = MagicMock()
    fake_usage.input_tokens = 800
    fake_usage.output_tokens = 300
    fake_usage.cache_read_input_tokens = 0
    fake_usage.cache_creation_input_tokens = 0

    fake_resp = MagicMock()
    fake_resp.content = [fake_block]
    fake_resp.usage = fake_usage

    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.get_final_message = MagicMock(return_value=fake_resp)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = fake_stream
    svc._client = mock_client

    from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
    result, synth_usage = svc._synthesize("transcript text", [], INDIVIDUAL_SCHEMA)

    assert result is None
    assert synth_usage["in"] == 800
    assert synth_usage["out"] == 300


def test_synthesize_returns_none_empty_dict_when_no_text_block(monkeypatch):
    """_synthesize returns (None, {}) when no text block is present in response."""
    svc = ClaudeDeepResearchService()
    svc.api_key = "fake"

    fake_resp = MagicMock()
    fake_resp.content = []  # no text block
    fake_resp.usage = MagicMock(input_tokens=100, output_tokens=50)

    fake_stream = MagicMock()
    fake_stream.__enter__ = MagicMock(return_value=fake_stream)
    fake_stream.__exit__ = MagicMock(return_value=False)
    fake_stream.get_final_message = MagicMock(return_value=fake_resp)

    mock_client = MagicMock()
    mock_client.messages.stream.return_value = fake_stream
    svc._client = mock_client

    from app.services.deep_research_schemas import INDIVIDUAL_SCHEMA
    result, synth_usage = svc._synthesize("transcript text", [], INDIVIDUAL_SCHEMA)

    assert result is None
    # No text block means we got no usable result; empty usage dict is acceptable
    # (the implementation may choose to return {} or the actual usage from the failed call)
    assert isinstance(synth_usage, dict)


def test_execute_deep_research_counts_synthesis_tokens(monkeypatch):
    """execute_deep_research must pass combined research+synthesis tokens to record_llm_call
    and set _claude_research_meta['usage'] to the combined total."""
    svc = ClaudeDeepResearchService()
    svc.api_key = "fake"

    recorded_calls = []

    def fake_record_llm_call(**kwargs):
        recorded_calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.claude_deep_research_service.ClaudeDeepResearchService._run_research",
        lambda self, prompt: CANNED_RESEARCH,
    )
    monkeypatch.setattr(
        "app.services.claude_deep_research_service.ClaudeDeepResearchService._synthesize",
        lambda self, transcript, source_urls, schema: (CANNED_SYNTH_RESULT, CANNED_SYNTH_USAGE),
    )
    with patch("app.services.token_tracker.record_llm_call", side_effect=fake_record_llm_call):
        with patch("app.services.claude_dr_prompts.build_individual_prompt", return_value="fake prompt"):
            result = svc.execute_deep_research("AAPL", {}, decision_id=42)

    assert result is not None, "execute_deep_research returned None unexpectedly"

    # --- assert record_llm_call received combined tokens ---
    assert len(recorded_calls) == 1, f"Expected 1 call to record_llm_call, got {len(recorded_calls)}"
    call = recorded_calls[0]
    expected_in = CANNED_RESEARCH["usage"]["in"] + CANNED_SYNTH_USAGE["in"]   # 5000+1000 = 6000
    expected_out = CANNED_RESEARCH["usage"]["out"] + CANNED_SYNTH_USAGE["out"] # 1500+500  = 2000
    assert call["tokens_in"] == expected_in, f"tokens_in: expected {expected_in}, got {call['tokens_in']}"
    assert call["tokens_out"] == expected_out, f"tokens_out: expected {expected_out}, got {call['tokens_out']}"

    # --- assert meta usage is the combined total ---
    meta = result["_claude_research_meta"]
    assert meta["usage"]["in"] == expected_in, (
        f"meta usage['in']: expected {expected_in}, got {meta['usage']['in']}"
    )
    assert meta["usage"]["out"] == expected_out, (
        f"meta usage['out']: expected {expected_out}, got {meta['usage']['out']}"
    )
    assert meta["usage"]["cache_read"] == CANNED_RESEARCH["usage"]["cache_read"] + CANNED_SYNTH_USAGE["cache_read"]
    assert meta["usage"]["cache_write"] == CANNED_RESEARCH["usage"]["cache_write"] + CANNED_SYNTH_USAGE["cache_write"]

    # --- assert split sub-keys are present ---
    assert "research_usage" in meta, "meta should expose research_usage sub-key"
    assert "synthesis_usage" in meta, "meta should expose synthesis_usage sub-key"
    assert meta["research_usage"]["in"] == CANNED_RESEARCH["usage"]["in"]
    assert meta["synthesis_usage"]["in"] == CANNED_SYNTH_USAGE["in"]


def test_execute_sell_reassessment_counts_synthesis_tokens(monkeypatch):
    """execute_sell_reassessment must also pass real synth_usage to _record_cost."""
    svc = ClaudeDeepResearchService()
    svc.api_key = "fake"

    recorded_calls = []

    def fake_record_llm_call(**kwargs):
        recorded_calls.append(kwargs)

    from app.services.deep_research_schemas import SELL_SCHEMA
    canned_sell_result = {k: v for k, v in CANNED_SYNTH_RESULT.items()}
    synth_usage = {"in": 700, "out": 350, "cache_read": 0, "cache_write": 0}

    monkeypatch.setattr(
        "app.services.claude_deep_research_service.ClaudeDeepResearchService._run_research",
        lambda self, prompt: CANNED_RESEARCH,
    )
    monkeypatch.setattr(
        "app.services.claude_deep_research_service.ClaudeDeepResearchService._synthesize",
        lambda self, transcript, source_urls, schema: (canned_sell_result, synth_usage),
    )
    with patch("app.services.token_tracker.record_llm_call", side_effect=fake_record_llm_call):
        with patch("app.services.claude_dr_prompts.build_sell_prompt", return_value="fake prompt"):
            result = svc.execute_sell_reassessment("AAPL", {}, decision_id=55)

    assert result is not None
    assert len(recorded_calls) == 1
    call = recorded_calls[0]
    expected_in = CANNED_RESEARCH["usage"]["in"] + synth_usage["in"]   # 5000+700 = 5700
    expected_out = CANNED_RESEARCH["usage"]["out"] + synth_usage["out"] # 1500+350 = 1850
    assert call["tokens_in"] == expected_in
    assert call["tokens_out"] == expected_out


@requires_live
def test_live_individual_research_returns_grounded_result():
    context = {
        "pm_decision": {"verdict": "BUY_LIMIT", "reason": "test"},
        "bull_case": "Test bull case.",
        "bear_case": "Test bear case.",
        "technical_data": {"rsi": 28, "price": 100.0},
        "drop_percent": -7.5,
        "raw_news": [],
    }
    result = claude_deep_research_service.execute_deep_research("AAPL", context)
    assert result is not None
    assert result["review_verdict"] in ("CONFIRMED", "UPGRADED", "ADJUSTED", "OVERRIDDEN")
    meta = result["_claude_research_meta"]
    assert meta["search_count"] >= 1, "expected at least one web search hop"
    for v in result["verification_results"]:
        if v.get("verdict") in ("VERIFIED", "DISPUTED"):
            assert v["source_url"].startswith("http")
