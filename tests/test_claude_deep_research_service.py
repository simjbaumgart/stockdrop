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
