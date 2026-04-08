import unittest
import pandas as pd
from datetime import datetime, timedelta

# Import the shared helper from generate_report_v2
from scripts.generate_report_v2 import calculate_change, get_price_on_date


class TestCalculateChange(unittest.TestCase):
    def test_positive_return(self):
        self.assertAlmostEqual(calculate_change(100.0, 110.0), 0.10, places=4)

    def test_negative_return(self):
        self.assertAlmostEqual(calculate_change(100.0, 90.0), -0.10, places=4)

    def test_zero_start_price(self):
        self.assertIsNone(calculate_change(0, 110.0))

    def test_none_inputs(self):
        self.assertIsNone(calculate_change(None, 110.0))
        self.assertIsNone(calculate_change(100.0, None))


class TestGetPriceOnDate(unittest.TestCase):
    def setUp(self):
        """Create a simple price DataFrame for testing."""
        dates = pd.date_range("2026-03-01", periods=30, freq="B")  # Business days
        self.price_data = pd.DataFrame(
            {"AAPL": [150.0 + i for i in range(30)]},
            index=dates,
        )

    def test_exact_date_match(self):
        # First business day of March 2026
        price = get_price_on_date(self.price_data, "AAPL", "2026-03-02")
        self.assertIsNotNone(price)

    def test_weekend_falls_to_next_trading_day(self):
        # Saturday 2026-03-07 should fall forward to Monday 2026-03-09
        price_sat = get_price_on_date(self.price_data, "AAPL", "2026-03-07")
        price_mon = get_price_on_date(self.price_data, "AAPL", "2026-03-09")
        self.assertEqual(price_sat, price_mon)

    def test_unknown_ticker_returns_none(self):
        price = get_price_on_date(self.price_data, "ZZZZ", "2026-03-02")
        self.assertIsNone(price)

    def test_date_beyond_range_returns_none(self):
        price = get_price_on_date(self.price_data, "AAPL", "2027-01-01")
        self.assertIsNone(price)


class TestMultiHorizonOffsets(unittest.TestCase):
    """Verify that the 7/14/28-day offsets produce correct target dates."""

    def test_horizon_offsets(self):
        decision_date = datetime(2026, 3, 2)
        self.assertEqual(
            (decision_date + timedelta(days=7)).strftime("%Y-%m-%d"), "2026-03-09"
        )
        self.assertEqual(
            (decision_date + timedelta(days=14)).strftime("%Y-%m-%d"), "2026-03-16"
        )
        self.assertEqual(
            (decision_date + timedelta(days=28)).strftime("%Y-%m-%d"), "2026-03-30"
        )


if __name__ == "__main__":
    unittest.main()
