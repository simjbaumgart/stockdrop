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
