import pytest
from unittest.mock import patch
from app.services.tv_exchange_resolver import (
    resolve_tv_exchange,
    clear_cache,
    TA_UNAVAILABLE_SENTINEL,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class TestResolveTvExchange:
    def test_nasdaq_ticker_from_probe(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "NASDAQ"
            result = resolve_tv_exchange("AAPL")
        assert result == ("NASDAQ", "america")

    def test_nyse_ticker_from_probe(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "NYSE"
            result = resolve_tv_exchange("JPM")
        assert result == ("NYSE", "america")

    def test_otc_pink_sheet_ticker(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "OTC"
            result = resolve_tv_exchange("MBGYY")
        assert result == ("OTC", "america")

    def test_unresolvable_returns_none(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists", return_value=False):
            result = resolve_tv_exchange("XXXXX")
        assert result is None

    def test_cache_hit_skips_probe(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            probe.side_effect = lambda sym, ex: ex == "NYSE"
            resolve_tv_exchange("JPM")
            call_count_first = probe.call_count
            resolve_tv_exchange("JPM")
            assert probe.call_count == call_count_first

    def test_explicit_inputs_bypass_resolver(self):
        with patch("app.services.tv_exchange_resolver._tv_symbol_exists") as probe:
            result = resolve_tv_exchange(
                "AAPL", known_exchange="NASDAQ", known_screener="america"
            )
            assert result == ("NASDAQ", "america")
            assert probe.call_count == 0


class TestSentinel:
    def test_sentinel_shape(self):
        assert TA_UNAVAILABLE_SENTINEL == {"ta_unavailable": True}


class TestGetTechnicalIndicatorsDelegates:
    def test_indicators_resolves_missing_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NYSE", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.return_value.indicators = {
                    "close": 10.0, "SMA200": 9.0, "RSI": 50.0,
                    "BB.lower": 8.0, "BB.upper": 12.0, "volume": 100,
                }
                tradingview_service.get_technical_indicators("JPM")
                res.assert_called_once_with(
                    "JPM", known_exchange=None, known_screener=None,
                )
                _, kwargs = handler_cls.call_args
                assert kwargs["exchange"] == "NYSE"

    def test_indicators_skips_resolver_when_exchange_supplied(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NASDAQ", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.return_value.indicators = {
                    "close": 10.0, "SMA200": 9.0, "RSI": 50.0,
                    "BB.lower": 8.0, "BB.upper": 12.0, "volume": 100,
                }
                tradingview_service.get_technical_indicators(
                    "AAPL", exchange="NASDAQ", screener="america",
                )
                res.assert_called_once_with(
                    "AAPL", known_exchange="NASDAQ", known_screener="america",
                )


class TestGetTechnicalAnalysisDelegates:
    def test_analysis_resolves_missing_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("OTC", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                handler.get_analysis.side_effect = Exception("no data for OTC")
                result = tradingview_service.get_technical_analysis("MBGYY")
                assert result == {"ta_unavailable": True}

    def test_analysis_accepts_known_exchange(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange") as res:
            res.return_value = ("NYSE", "america")
            with patch("app.services.tradingview_service.TA_Handler") as handler_cls:
                handler = handler_cls.return_value
                mock_analysis = handler.get_analysis.return_value
                mock_analysis.summary = {"RECOMMENDATION": "BUY"}
                mock_analysis.oscillators = {}
                mock_analysis.moving_averages = {}
                mock_analysis.indicators = {"close": 10.0}
                result = tradingview_service.get_technical_analysis(
                    "JPM", exchange="NYSE", screener="america",
                )
                assert result["summary"]["RECOMMENDATION"] == "BUY"
                res.assert_called_once_with(
                    "JPM", known_exchange="NYSE", known_screener="america",
                )

    def test_analysis_unresolvable_returns_empty(self):
        from app.services.tradingview_service import tradingview_service
        with patch("app.services.tradingview_service.resolve_tv_exchange", return_value=None):
            result = tradingview_service.get_technical_analysis("XXXXX")
            assert result == {}
