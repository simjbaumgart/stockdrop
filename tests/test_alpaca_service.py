# tests/test_alpaca_service.py
"""Unit tests for AlpacaService symbol translation and snapshot round-trip."""

from unittest.mock import MagicMock, patch

import pytest


# --- Translation helper tests -------------------------------------------------

class TestSymbolTranslation:
    def test_to_alpaca_replaces_single_hyphen(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("BRK-B") == "BRK.B"

    def test_to_alpaca_passthrough_for_plain_symbol(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("AAPL") == "AAPL"

    def test_to_alpaca_replaces_all_hyphens(self):
        """Defensive: if a weird ticker ever has two dashes, translate both."""
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._to_alpaca_symbol("FOO-BAR-BAZ") == "FOO.BAR.BAZ"

    def test_from_alpaca_replaces_dot(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._from_alpaca_symbol("BRK.B") == "BRK-B"

    def test_from_alpaca_passthrough_for_plain_symbol(self):
        from app.services.alpaca_service import AlpacaService
        assert AlpacaService._from_alpaca_symbol("AAPL") == "AAPL"


class TestGetSnapshotsSymbolMapping:
    """Alpaca returns snapshots keyed by dot-form (BRK.B). Callers pass and
    expect hyphen-form (BRK-B). Verify both directions are translated."""

    def _make_service_with_mock_client(self):
        from app.services.alpaca_service import AlpacaService
        svc = AlpacaService.__new__(AlpacaService)  # bypass __init__ / env loading
        svc.stock_client = MagicMock()
        svc.option_client = MagicMock()
        return svc

    def test_request_symbols_are_translated_to_alpaca_form(self):
        svc = self._make_service_with_mock_client()
        svc.stock_client.get_stock_snapshot.return_value = {}

        svc.get_snapshots(["AAPL", "BRK-B"])

        # Inspect the StockSnapshotRequest that was built.
        call_args, _ = svc.stock_client.get_stock_snapshot.call_args
        request = call_args[0]
        assert list(request.symbol_or_symbols) == ["AAPL", "BRK.B"]

    def test_response_keys_are_translated_back_to_caller_form(self):
        svc = self._make_service_with_mock_client()
        aapl_snap = MagicMock(name="AAPL_snapshot")
        brk_snap = MagicMock(name="BRK_snapshot")
        svc.stock_client.get_stock_snapshot.return_value = {
            "AAPL": aapl_snap,
            "BRK.B": brk_snap,
        }

        result = svc.get_snapshots(["AAPL", "BRK-B"])

        assert set(result.keys()) == {"AAPL", "BRK-B"}
        assert result["BRK-B"] is brk_snap
        assert result["AAPL"] is aapl_snap

    def test_no_client_returns_empty(self):
        from app.services.alpaca_service import AlpacaService
        svc = AlpacaService.__new__(AlpacaService)
        svc.stock_client = None
        svc.option_client = None
        assert svc.get_snapshots(["BRK-B"]) == {}

    def test_api_exception_returns_empty(self):
        svc = self._make_service_with_mock_client()
        svc.stock_client.get_stock_snapshot.side_effect = RuntimeError("boom")
        assert svc.get_snapshots(["BRK-B"]) == {}
