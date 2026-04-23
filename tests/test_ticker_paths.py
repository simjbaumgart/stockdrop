from app.utils.ticker_paths import safe_ticker_path


def test_plain_ticker_unchanged():
    assert safe_ticker_path("AAPL") == "AAPL"


def test_slash_replaced_with_underscore():
    assert safe_ticker_path("QXO/PB") == "QXO_PB"


def test_backslash_replaced():
    assert safe_ticker_path("FOO\\BAR") == "FOO_BAR"


def test_path_separator_collisions_stripped():
    # os.sep is '/' on posix, '\\' on win — both must be handled.
    assert "/" not in safe_ticker_path("A/B/C")
    assert "\\" not in safe_ticker_path("A\\B\\C")


def test_none_or_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        safe_ticker_path("")
    with pytest.raises(ValueError):
        safe_ticker_path(None)  # type: ignore[arg-type]


def test_dots_and_dashes_preserved():
    # BRK.B, BRK-B etc. are valid file-name characters and common ticker styles.
    assert safe_ticker_path("BRK.B") == "BRK.B"
    assert safe_ticker_path("BRK-B") == "BRK-B"


import os
import tempfile
from unittest.mock import patch


def test_safe_ticker_path_produces_writable_filename(tmp_path):
    """Sanity-check: the sanitized name is usable as a single path component."""
    from app.utils.ticker_paths import safe_ticker_path

    sanitized = safe_ticker_path("QXO/PB")
    target = tmp_path / f"{sanitized}_2026-04-22_council1.json"
    target.write_text("{}")
    assert target.exists()
    # And it's a single file, not a nested directory.
    assert target.parent == tmp_path
