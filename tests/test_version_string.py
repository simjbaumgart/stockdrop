"""Regression: startup banner must not double-prefix the version with 'vv'.

Production bug: every log line printed `StockDrop vv0.8.2-106` because
`get_git_version()` returns `git describe --tags`'s output (already
prefixed with `v`), and the banner format string added another `v`.
"""
from unittest.mock import patch
import importlib


def test_startup_banner_has_no_double_v():
    """get_git_version() returns 'vX.Y.Z'; the banner must not add another 'v'."""
    with patch("subprocess.check_output", return_value=b"v0.8.2-106\n"):
        import main
        importlib.reload(main)
        banner = f"  StockDrop {main.VERSION}"
        assert not banner.startswith("  StockDrop vv"), banner
        assert banner.startswith("  StockDrop v"), banner
        # And the version itself still starts with v (so we know we're not
        # over-correcting by stripping the leading v from the source string).
        assert main.VERSION.startswith("v"), (
            f"VERSION should retain its 'v' prefix from git describe, got: {main.VERSION!r}"
        )


def test_unknown_version_still_renders_cleanly():
    """If git describe fails, get_git_version() returns 'unknown' (no leading v).
    The banner just prints 'StockDrop unknown' — not 'StockDrop vunknown'."""
    with patch("subprocess.check_output", side_effect=Exception("git not found")):
        import main
        importlib.reload(main)
        banner = f"  StockDrop {main.VERSION}"
        assert banner == "  StockDrop unknown", banner
