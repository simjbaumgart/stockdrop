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
from app.services.claude_deep_research_service import claude_deep_research_service

requires_live = pytest.mark.skipif(
    not (os.getenv("CLAUDE_API_KEY") and os.getenv("RUN_CLAUDE_LIVE_TESTS")),
    reason="set CLAUDE_API_KEY and RUN_CLAUDE_LIVE_TESTS=1 to run live Claude test",
)


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
