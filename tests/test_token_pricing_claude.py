from app.services.token_pricing import compute_cost


def test_claude_opus_cost_known_rates():
    # $5/1M in, $25/1M out
    cost = compute_cost("claude-opus-4-8", 1_000_000, 1_000_000)
    assert abs(cost - 30.0) < 1e-6


def test_unknown_model_returns_none():
    assert compute_cost("nope-model", 10, 10) is None
