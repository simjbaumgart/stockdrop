# tests/test_decision_id_threading.py
from app.models.market_state import MarketState


def test_market_state_carries_decision_id():
    s = MarketState(ticker="AAPL", date="2026-05-23", decision_id=42)
    assert s.decision_id == 42


def test_market_state_decision_id_optional():
    s = MarketState(ticker="AAPL", date="2026-05-23")
    assert s.decision_id is None
