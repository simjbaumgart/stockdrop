"""Tests for the NaN guard in the trade report.

Regression: thin-volume / OTC tickers (PTAIY, DQJCY, CHGCY, MOH, MLSPF, CYATY)
rendered "Performance +nan%" / "Price +7d = nan" when the +7d close lookup hit
a present-but-empty bar (NaN, not KeyError). Because bool(float('nan')) is True,
the `if price:` guards passed and nan reached the formatted table — and the Alpha
column inherited it.
"""
import math

from scripts.core.generate_trade_report import _finite_or_none


def test_nan_becomes_none():
    assert _finite_or_none(float("nan")) is None


def test_none_stays_none():
    assert _finite_or_none(None) is None


def test_valid_float_passes_through():
    assert _finite_or_none(42.5) == 42.5


def test_numeric_string_coerced():
    assert _finite_or_none("12.25") == 12.25


def test_garbage_is_none():
    assert _finite_or_none("n/a") is None


def test_guard_renders_dash_not_nan():
    """The exact format pattern from the report: a None price must render '-'
    rather than a formatted nan."""
    target_price = _finite_or_none(float("nan"))  # missing +7d bar
    price_at_decision = 100.0
    perf_pct = 0.0
    rendered = f"{perf_pct:+.2f}%" if price_at_decision and target_price else "-"
    assert rendered == "-"


def test_guard_with_real_nan_would_have_leaked_without_fix():
    """Demonstrates the original bug: a raw nan is truthy, so the un-guarded
    expression produces '+nan%'. The fix funnels through _finite_or_none."""
    raw = float("nan")
    leaked = f"{raw:+.2f}%" if (100.0 and raw) else "-"
    assert leaked == "+nan%"  # the bug, pre-fix
    fixed = f"{raw:+.2f}%" if (100.0 and _finite_or_none(raw)) else "-"
    assert fixed == "-"  # post-fix
