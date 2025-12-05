import unittest
from datetime import datetime, timedelta
from app.services.performance_service import performance_service

class TestPerformanceService(unittest.TestCase):
    def test_analyze_historical_trade_valid(self):
        # Test with a known stock and past date (e.g., AAPL one month ago)
        symbol = "AAPL"
        buy_date = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        
        result = performance_service.analyze_historical_trade(symbol, buy_date)
        
        self.assertNotIn("error", result)
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["buy_date"], buy_date)
        self.assertTrue(len(result["daily_data"]) > 0)
        self.assertTrue(len(result["metrics"]) > 0)
        
        # Check specific metric existence
        metrics_dict = {m['days']: m for m in result['metrics']}
        self.assertIn(7, metrics_dict)
        self.assertIn(31, metrics_dict)
        
        # Verify calculation logic (roughly)
        # Initial value should be close to invesment
        first_day = result["daily_data"][0]
        self.assertAlmostEqual(first_day["value"], 1000.0, delta=1.0)
        
    def test_analyze_historical_trade_future_date(self):
        symbol = "AAPL"
        future_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        result = performance_service.analyze_historical_trade(symbol, future_date)
        self.assertIn("error", result)

if __name__ == '__main__':
    unittest.main()
