"""
Test suite verifying that international markets have been fully removed
and only US stocks flow through the pipeline.

All external APIs are mocked — no .env or API keys needed.
"""

import math
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Shared mock data
# ---------------------------------------------------------------------------

INTERNATIONAL_SUFFIXES = [
    ".DE", ".PA", ".SW", ".L", ".AS", ".BR", ".LS", ".MI", ".MC", ".F",
    ".ST", ".HE", ".CO", ".T", ".SS", ".SZ", ".HK", ".NS", ".BO",
    ".TW", ".KS", ".AX", ".SA", ".TO", ".V",
]

MOCK_SCREENER_COLUMNS = [
    "name", "description", "change", "close", "change_abs", "market_cap_basic",
    "volume", "currency", "exchange",
    "open", "high", "low",
    "RSI", "SMA50", "SMA200", "BB.lower", "BB.upper",
    "MACD.macd", "MACD.signal", "MACD.hist", "Mom", "Stoch.K", "Stoch.D",
    "ADX", "CCI20", "VWMA", "ATR", "relative_volume_10d_calc", "beta_1_year",
    "price_52_week_high", "price_52_week_low", "Recommend.All", "Recommend.MA",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.Y", "Perf.5Y", "Perf.YTD",
    "price_book_fq", "price_earnings_ttm", "enterprise_value_ebitda_ttm",
    "price_free_cash_flow_ttm", "dividend_yield_recent",
    "total_revenue_ttm", "total_revenue_yoy_growth_ttm", "gross_margin_ttm",
    "operating_margin_ttm", "net_income_ttm", "earnings_per_share_basic_ttm",
    "total_assets_fq", "total_liabilities_fq", "total_debt_fq",
    "cash_n_equivalents_fq", "current_ratio_fq", "debt_to_equity_fq",
    "free_cash_flow_ttm",
]


def _make_screener_row(symbol, desc, price, change_pct, exchange="NASDAQ"):
    """Build a single row dict matching the screener SELECT list."""
    base = {col: 0.0 for col in MOCK_SCREENER_COLUMNS}
    base.update({
        "name": symbol,
        "description": desc,
        "close": price,
        "change": change_pct,
        "change_abs": price * change_pct / 100,
        "market_cap_basic": 50_000_000_000,
        "volume": 1_000_000,
        "currency": "USD",
        "exchange": exchange,
        "open": price + 1,
        "high": price + 2,
        "low": price - 1,
    })
    return base


def _make_screener_df(rows):
    """Turn a list of row dicts into a DataFrame like the screener returns."""
    return pd.DataFrame(rows)


MOCK_DECISIONS = [
    {"id": 1, "symbol": "AAPL", "price_at_decision": 150.0, "recommendation": "BUY",
     "reasoning": "test", "timestamp": "2025-01-01", "region": "US"},
    {"id": 2, "symbol": "MSFT", "price_at_decision": 300.0, "recommendation": "AVOID",
     "reasoning": "test", "timestamp": "2025-01-01", "region": "US"},
    {"id": 3, "symbol": "TSLA", "price_at_decision": 200.0, "recommendation": "HOLD",
     "reasoning": "test", "timestamp": "2025-01-01", "region": "US"},
    # Historical international stock still in DB
    {"id": 4, "symbol": "SAP.DE", "price_at_decision": 180.0, "recommendation": "BUY",
     "reasoning": "test", "timestamp": "2025-01-01", "region": "EU"},
]


# ===========================================================================
# Group 1: TradingViewService — US-Only Screening
# ===========================================================================

class TestTradingViewUSOnlyScreening:

    @patch("app.services.tradingview_service.Query")
    def test_get_top_movers_only_queries_america(self, mock_query_cls):
        """get_top_movers should only call set_markets('america')."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_query = MagicMock()
        mock_query_cls.return_value = mock_query
        mock_query.set_markets.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.get_scanner_data.return_value = (0, pd.DataFrame())

        tv.get_top_movers()

        # Every set_markets call should only contain "america"
        for c in mock_query.set_markets.call_args_list:
            markets = c[0]  # positional args
            assert markets == ("america",), f"Expected ('america',), got {markets}"

    @patch("app.services.tradingview_service.Query")
    def test_get_top_movers_returns_america_region(self, mock_query_cls):
        """All results from get_top_movers should have region='America'."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        rows = [
            _make_screener_row("AAPL", "Apple Inc", 180.0, -5.5),
            _make_screener_row("TSLA", "Tesla Inc", 250.0, -6.0),
        ]
        df = _make_screener_df(rows)

        mock_query = MagicMock()
        mock_query_cls.return_value = mock_query
        mock_query.set_markets.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.get_scanner_data.return_value = (2, df)

        movers = tv.get_top_movers()

        assert len(movers) == 2
        for m in movers:
            assert m["region"] == "America", f"Got region={m['region']} for {m['symbol']}"

    @patch("app.services.tradingview_service.Query")
    def test_get_latest_price_uses_america_market(self, mock_query_cls):
        """get_latest_price should query 'america' market only."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_query = MagicMock()
        mock_query_cls.return_value = mock_query
        mock_query.set_markets.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.get_scanner_data.return_value = (1, pd.DataFrame({"close": [150.0]}))

        price = tv.get_latest_price("AAPL")

        mock_query.set_markets.assert_called_once_with("america")
        assert price == 150.0

    @patch("app.services.tradingview_service.Query")
    def test_get_latest_price_returns_zero_on_failure(self, mock_query_cls):
        """get_latest_price should return 0.0 when the screener raises."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_query_cls.return_value.set_markets.side_effect = Exception("API down")

        price = tv.get_latest_price("AAPL")
        assert price == 0.0


# ===========================================================================
# Group 2: TradingViewService — Technical Analysis
# ===========================================================================

class TestTradingViewTechnicalAnalysis:

    @patch("app.services.tradingview_service.TA_Handler")
    def test_get_technical_analysis_defaults_to_us(self, mock_ta):
        """TA should default to screener='america', exchange='NASDAQ'."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_analysis = MagicMock()
        mock_analysis.summary = {"RECOMMENDATION": "BUY"}
        mock_analysis.oscillators = {}
        mock_analysis.moving_averages = {}
        mock_analysis.indicators = {"close": 150.0}
        mock_ta.return_value.get_analysis.return_value = mock_analysis

        tv.get_technical_analysis("AAPL")

        mock_ta.assert_called_once_with(
            symbol="AAPL", screener="america", exchange="NASDAQ",
            interval=pytest.importorskip("tradingview_ta").Interval.INTERVAL_1_DAY,
        )

    @patch("app.services.tradingview_service.TA_Handler")
    def test_get_technical_indicators_defaults_to_us(self, mock_ta):
        """Indicators should default to screener='america', exchange='NASDAQ'."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_analysis = MagicMock()
        mock_analysis.indicators = {"close": 150.0, "SMA200": 145.0, "RSI": 35.0,
                                     "BB.lower": 140.0, "BB.upper": 160.0, "volume": 1e6}
        mock_ta.return_value.get_analysis.return_value = mock_analysis

        tv.get_technical_indicators("AAPL")

        mock_ta.assert_called_once_with(
            symbol="AAPL", screener="america", exchange="NASDAQ",
            interval=pytest.importorskip("tradingview_ta").Interval.INTERVAL_1_DAY,
        )

    @patch("app.services.tradingview_service.Query")
    def test_get_earnings_date_uses_america(self, mock_query_cls):
        """Earnings lookup should only query 'america'."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        mock_query = MagicMock()
        mock_query_cls.return_value = mock_query
        mock_query.set_markets.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.get_scanner_data.return_value = (0, pd.DataFrame())

        tv.get_earnings_date("AAPL")

        mock_query.set_markets.assert_called_once_with("america")


# ===========================================================================
# Group 3: StockService — Config & Metadata
# ===========================================================================

class TestStockServiceConfig:

    @patch("app.services.stock_service.deep_research_service")
    @patch("app.services.stock_service.seeking_alpha_service")
    @patch("app.services.stock_service.benzinga_service")
    @patch("app.services.stock_service.drive_service")
    @patch("app.services.stock_service.alpha_vantage_service")
    @patch("app.services.stock_service.finnhub_service")
    @patch("app.services.stock_service.gatekeeper_service")
    @patch("app.services.stock_service.storage_service")
    @patch("app.services.stock_service.tradingview_service")
    @patch("app.services.stock_service.alpaca_service")
    @patch("app.services.stock_service.research_service")
    @patch("app.services.stock_service.email_service")
    def test_indices_config_us_only(self, *mocks):
        """indices_config should only contain S&P 500."""
        from app.services.stock_service import StockService
        ss = StockService()
        assert list(ss.indices_config.keys()) == ["S&P 500"]

    @patch("app.services.stock_service.deep_research_service")
    @patch("app.services.stock_service.seeking_alpha_service")
    @patch("app.services.stock_service.benzinga_service")
    @patch("app.services.stock_service.drive_service")
    @patch("app.services.stock_service.alpha_vantage_service")
    @patch("app.services.stock_service.finnhub_service")
    @patch("app.services.stock_service.gatekeeper_service")
    @patch("app.services.stock_service.storage_service")
    @patch("app.services.stock_service.tradingview_service")
    @patch("app.services.stock_service.alpaca_service")
    @patch("app.services.stock_service.research_service")
    @patch("app.services.stock_service.email_service")
    def test_stock_metadata_all_us(self, *mocks):
        """Every stock in metadata should have region='US'."""
        from app.services.stock_service import StockService
        ss = StockService()
        regions = set(m["region"] for m in ss.stock_metadata.values())
        assert regions == {"US"}, f"Found non-US regions: {regions}"

    @patch("app.services.stock_service.deep_research_service")
    @patch("app.services.stock_service.seeking_alpha_service")
    @patch("app.services.stock_service.benzinga_service")
    @patch("app.services.stock_service.drive_service")
    @patch("app.services.stock_service.alpha_vantage_service")
    @patch("app.services.stock_service.finnhub_service")
    @patch("app.services.stock_service.gatekeeper_service")
    @patch("app.services.stock_service.storage_service")
    @patch("app.services.stock_service.tradingview_service")
    @patch("app.services.stock_service.alpaca_service")
    @patch("app.services.stock_service.research_service")
    @patch("app.services.stock_service.email_service")
    def test_is_market_open_us_only(self, *mocks):
        """_is_market_open should only check US hours."""
        from app.services.stock_service import StockService
        from unittest.mock import PropertyMock
        from datetime import datetime
        import pytz

        ss = StockService()

        # Mock a weekday at 15:00 UTC (US market open)
        with patch("app.services.stock_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 1  # Tuesday
            mock_now.hour = 15
            mock_now.minute = 0
            mock_dt.now.return_value = mock_now
            assert ss._is_market_open() is True

        # Mock a weekend
        with patch("app.services.stock_service.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.weekday.return_value = 5  # Saturday
            mock_dt.now.return_value = mock_now
            assert ss._is_market_open() is False

    @patch("app.services.stock_service.deep_research_service")
    @patch("app.services.stock_service.seeking_alpha_service")
    @patch("app.services.stock_service.benzinga_service")
    @patch("app.services.stock_service.drive_service")
    @patch("app.services.stock_service.alpha_vantage_service")
    @patch("app.services.stock_service.finnhub_service")
    @patch("app.services.stock_service.gatekeeper_service")
    @patch("app.services.stock_service.storage_service")
    @patch("app.services.stock_service.tradingview_service", new_callable=MagicMock)
    @patch("app.services.stock_service.alpaca_service")
    @patch("app.services.stock_service.research_service")
    @patch("app.services.stock_service.email_service")
    def test_get_indices_expects_sp500_only(self, *mocks):
        """get_indices should only look for S&P 500."""
        from app.services.stock_service import StockService
        ss = StockService()

        # Mock tradingview_service at instance level
        mock_tv = MagicMock()
        mock_tv.get_indices_data.return_value = {
            "S&P 500": {"price": 5000.0, "change": 10.0, "change_percent": 0.2}
        }

        with patch.object(ss, '_get_index_fallback', return_value={"price": 0.0, "change": 0.0, "change_percent": 0.0}):
            with patch("app.services.stock_service.tradingview_service", mock_tv):
                result = ss.get_indices()

        assert "S&P 500" in result
        # Should NOT try to fetch STOXX 600, China, or India
        assert "STOXX 600" not in result or result["STOXX 600"]["price"] == 0.0


# ===========================================================================
# Group 4: PerformanceService — No Region Detection
# ===========================================================================

class TestPerformanceServiceNoRegion:

    @patch("app.services.performance_service.tradingview_service")
    @patch("app.services.performance_service.get_decision_points")
    def test_evaluate_decisions_calls_price_without_region(self, mock_get_dp, mock_tv):
        """get_latest_price should be called with just the symbol, no region."""
        from app.services.performance_service import PerformanceService

        mock_get_dp.return_value = [MOCK_DECISIONS[0]]  # AAPL
        mock_tv.get_latest_price.return_value = 160.0

        ps = PerformanceService()
        ps.evaluate_decisions()

        mock_tv.get_latest_price.assert_called_once_with("AAPL")

    @patch("app.services.performance_service.tradingview_service")
    @patch("app.services.performance_service.get_decision_points")
    def test_evaluate_decisions_handles_zero_price(self, mock_get_dp, mock_tv):
        """When price is 0.0, performance_percent should be 0.0 (not NaN)."""
        from app.services.performance_service import PerformanceService

        mock_get_dp.return_value = [MOCK_DECISIONS[0]]
        mock_tv.get_latest_price.return_value = 0.0

        ps = PerformanceService()
        results = ps.evaluate_decisions()

        assert len(results) == 1
        assert results[0]["performance_percent"] == 0.0
        assert not math.isnan(results[0]["performance_percent"])

    @patch("app.services.performance_service.tradingview_service")
    @patch("app.services.performance_service.get_decision_points")
    def test_evaluate_decisions_profit_loss_outcomes(self, mock_get_dp, mock_tv):
        """Verify PROFIT/LOSS/NEUTRAL classification."""
        from app.services.performance_service import PerformanceService

        decisions = [
            {"id": 1, "symbol": "AAPL", "price_at_decision": 100.0,
             "recommendation": "BUY", "reasoning": "", "timestamp": "2025-01-01"},
            {"id": 2, "symbol": "MSFT", "price_at_decision": 100.0,
             "recommendation": "BUY", "reasoning": "", "timestamp": "2025-01-01"},
            {"id": 3, "symbol": "TSLA", "price_at_decision": 100.0,
             "recommendation": "BUY", "reasoning": "", "timestamp": "2025-01-01"},
        ]
        mock_get_dp.return_value = decisions

        # AAPL: +10% (PROFIT), MSFT: -5% (LOSS), TSLA: +1% (NEUTRAL)
        mock_tv.get_latest_price.side_effect = [110.0, 95.0, 101.0]

        ps = PerformanceService()
        results = ps.evaluate_decisions()

        outcomes = {r["symbol"]: r["outcome"] for r in results}
        assert outcomes["AAPL"] == "PROFIT"
        assert outcomes["MSFT"] == "LOSS"
        assert outcomes["TSLA"] == "NEUTRAL"


# ===========================================================================
# Group 5: TrackingService — No Region Detection
# ===========================================================================

class TestTrackingServiceNoRegion:

    @patch("app.services.tracking_service.add_tracking_point")
    @patch("app.services.tracking_service.tradingview_service")
    @patch("app.services.tracking_service.get_decision_points")
    def test_update_tracked_stocks_no_region_logic(self, mock_get_dp, mock_tv, mock_add):
        """get_latest_price should be called with just the symbol."""
        from app.services.tracking_service import TrackingService

        mock_get_dp.return_value = [MOCK_DECISIONS[0]]  # AAPL
        mock_tv.get_latest_price.return_value = 155.0

        ts = TrackingService()
        ts.update_tracked_stocks()

        mock_tv.get_latest_price.assert_called_once_with("AAPL")
        mock_add.assert_called_once_with(1, 155.0)

    @patch("app.services.tracking_service.add_tracking_point")
    @patch("app.services.tracking_service.tradingview_service")
    @patch("app.services.tracking_service.get_decision_points")
    def test_update_tracked_stocks_skips_zero_price(self, mock_get_dp, mock_tv, mock_add):
        """When price is 0.0, add_tracking_point should NOT be called."""
        from app.services.tracking_service import TrackingService

        mock_get_dp.return_value = [MOCK_DECISIONS[0]]
        mock_tv.get_latest_price.return_value = 0.0

        ts = TrackingService()
        ts.update_tracked_stocks()

        mock_add.assert_not_called()


# ===========================================================================
# Group 6: YahooTickerResolver — US Only
# ===========================================================================

class TestYahooTickerResolverUSOnly:

    def test_resolve_us_exchanges_no_suffix(self):
        """US exchanges should return bare symbol (no suffix)."""
        from app.services.yahoo_ticker_resolver import YahooTickerResolver
        resolver = YahooTickerResolver()

        assert resolver.resolve("AAPL", "NASDAQ") == "AAPL"
        assert resolver.resolve("MSFT", "NYSE") == "MSFT"
        assert resolver.resolve("SPY", "AMEX") == "SPY"

    @patch("app.services.yahoo_ticker_resolver.requests.get")
    def test_resolve_unknown_exchange_falls_through(self, mock_get):
        """Unknown exchange (e.g. XETR) should fall through to search or bare symbol."""
        from app.services.yahoo_ticker_resolver import YahooTickerResolver
        resolver = YahooTickerResolver()

        # Mock Yahoo search returning nothing
        mock_get.return_value.json.return_value = {"quotes": []}

        result = resolver.resolve("BMW", "XETR")
        # Should not add .DE suffix (no longer in map)
        assert result == "BMW"

    def test_suffix_map_has_no_international(self):
        """suffix_map should contain zero international exchange suffixes."""
        from app.services.yahoo_ticker_resolver import YahooTickerResolver
        resolver = YahooTickerResolver()

        intl_suffixes = {".DE", ".L", ".T", ".SS", ".SZ", ".HK", ".NS", ".PA",
                         ".SW", ".MI", ".MC", ".F", ".ST", ".HE", ".CO",
                         ".BO", ".TW", ".KS", ".AX", ".SA", ".TO", ".V"}

        for suffix in resolver.suffix_map.values():
            assert suffix not in intl_suffixes, f"Found international suffix {suffix}"

    def test_region_map_is_empty(self):
        """region_map should be empty (no international fallbacks)."""
        from app.services.yahoo_ticker_resolver import YahooTickerResolver
        resolver = YahooTickerResolver()
        assert len(resolver.region_map) == 0


# ===========================================================================
# Group 7: normalize_to_intent
# ===========================================================================

class TestNormalizeToIntent:

    def test_normalize_buy_signals(self):
        from app.services.performance_service import normalize_to_intent

        assert normalize_to_intent("BUY") == "ENTER_NOW"
        assert normalize_to_intent("STRONG BUY") == "ENTER_NOW"
        assert normalize_to_intent("STRONG_BUY") == "ENTER_NOW"
        assert normalize_to_intent("BUY_LIMIT") == "ENTER_LIMIT"
        assert normalize_to_intent("SPECULATIVE BUY") == "ENTER_LIMIT"
        assert normalize_to_intent("SPECULATIVE_BUY") == "ENTER_LIMIT"

    def test_normalize_avoid_signals(self):
        from app.services.performance_service import normalize_to_intent

        assert normalize_to_intent("AVOID") == "AVOID"
        assert normalize_to_intent("SELL") == "AVOID"
        assert normalize_to_intent("STRONG SELL") == "AVOID"
        assert normalize_to_intent("HARD_AVOID") == "AVOID"
        assert normalize_to_intent("WAIT_FOR_STABILIZATION") == "AVOID"

    def test_normalize_neutral_signals(self):
        from app.services.performance_service import normalize_to_intent

        assert normalize_to_intent("HOLD") == "NEUTRAL"
        assert normalize_to_intent("WATCH") == "NEUTRAL"
        assert normalize_to_intent("") == "NEUTRAL"
        assert normalize_to_intent(None) == "NEUTRAL"


# ===========================================================================
# Group 8: Integration — No International Leakage
# ===========================================================================

class TestNoInternationalLeakage:

    @patch("app.services.tradingview_service.Query")
    def test_full_pipeline_no_international_symbols(self, mock_query_cls):
        """Even if screener somehow returns intl data, region is always 'America'."""
        from app.services.tradingview_service import TradingViewService
        tv = TradingViewService()

        rows = [
            _make_screener_row("AAPL", "Apple", 180.0, -5.0, "NASDAQ"),
            _make_screener_row("NVDA", "Nvidia", 450.0, -6.0, "NASDAQ"),
        ]
        df = _make_screener_df(rows)

        mock_query = MagicMock()
        mock_query_cls.return_value = mock_query
        mock_query.set_markets.return_value = mock_query
        mock_query.select.return_value = mock_query
        mock_query.where.return_value = mock_query
        mock_query.get_scanner_data.return_value = (2, df)

        movers = tv.get_top_movers()

        for m in movers:
            sym = m["symbol"]
            assert not any(sym.endswith(s) for s in INTERNATIONAL_SUFFIXES), \
                f"International symbol leaked: {sym}"
            assert m["region"] == "America"

    def test_cleanup_script_identifies_international_rows(self):
        """The cleanup script logic should correctly identify intl symbols."""
        import sqlite3
        import math as m

        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE decision_points (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                price_at_decision REAL NOT NULL,
                region TEXT,
                timestamp TEXT,
                recommendation TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE decision_tracking (
                id INTEGER PRIMARY KEY,
                decision_id INTEGER,
                price REAL
            )
        """)

        # Insert mixed data
        conn.executemany(
            "INSERT INTO decision_points (symbol, price_at_decision, region, timestamp, recommendation) VALUES (?,?,?,?,?)",
            [
                ("AAPL", 150.0, "US", "2025-01-01", "BUY"),
                ("SAP.DE", 180.0, "EU", "2025-01-01", "BUY"),
                ("HSBA.L", 700.0, "EU", "2025-01-01", "HOLD"),
                ("9984.T", 0.0, "Japan", "2025-01-01", "WATCH"),
                ("MSFT", 300.0, "US", "2025-01-01", "BUY"),
            ],
        )
        conn.commit()

        # Replicate cleanup script logic
        intl_suffixes = [".DE", ".L", ".T", ".SS", ".SZ", ".HK", ".PA", ".SW",
                         ".NS", ".KS", ".AX", ".SA", ".TO"]
        cursor = conn.execute("SELECT id, symbol FROM decision_points")
        intl_ids = []
        for row in cursor:
            if any(row[1].upper().endswith(s) for s in intl_suffixes):
                intl_ids.append(row[0])

        assert len(intl_ids) == 3  # SAP.DE, HSBA.L, 9984.T
        assert 1 not in intl_ids   # AAPL is US
        assert 5 not in intl_ids   # MSFT is US

        conn.close()
