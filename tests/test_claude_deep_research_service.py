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


from app.services.claude_deep_research_service import (
    _deglooglify, _collect_source_urls,
)


def test_deglooglify_replaces_google_search_references():
    src = "Verify their key claims using fresh Google Search data."
    out = _deglooglify(src)
    assert "Google Search" not in out
    assert "web search" in out


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
