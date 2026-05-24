from app.services.token_pricing import compute_cost, GEMINI_PRICING


def test_known_model_computes_cost_per_million():
    # Stub a known model into the table with non-zero rates for the test.
    GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}  # USD per 1M
    try:
        # 1,000,000 input + 500,000 output -> 2.0 + 4.0 = 6.0
        assert compute_cost("__test_model__", 1_000_000, 500_000) == 6.0
        # 0 tokens -> 0 cost
        assert compute_cost("__test_model__", 0, 0) == 0.0
    finally:
        del GEMINI_PRICING["__test_model__"]


def test_unknown_model_returns_none():
    assert compute_cost("does-not-exist-model", 1_000_000, 1_000_000) is None


def test_zero_rates_compute_to_zero_not_none():
    # The shipped placeholders are all 0.0. Make sure that path returns 0.0,
    # not None — None is reserved for "model not in table".
    GEMINI_PRICING["__zero_model__"] = {"in": 0.0, "out": 0.0}
    try:
        assert compute_cost("__zero_model__", 1_000_000, 1_000_000) == 0.0
    finally:
        del GEMINI_PRICING["__zero_model__"]
