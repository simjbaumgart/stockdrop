"""Unit tests for PM verdict console-block formatters.

These cover the new R/R highlight block and the External Ratings block that
get printed alongside each PM decision.
"""
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.pm_verdict_formatters import (
    rating_label,
    format_rr_block,
    format_ratings_block,
)


# ---------------------------- rating_label ----------------------------

def test_rating_label_bands():
    assert rating_label(4.99) == "Strong Buy"
    assert rating_label(4.50) == "Strong Buy"
    assert rating_label(4.49) == "Buy"
    assert rating_label(3.50) == "Buy"
    assert rating_label(3.49) == "Hold"
    assert rating_label(2.50) == "Hold"
    assert rating_label(2.49) == "Sell"
    assert rating_label(1.50) == "Sell"
    assert rating_label(1.49) == "Strong Sell"
    assert rating_label(None) == "n/a"


# ---------------------------- format_rr_block ----------------------------

def test_rr_block_strong_gets_green_check():
    out = format_rr_block(upside=12.5, downside=6.4, rr=2.0)
    assert "RISK / REWARD" in out
    assert "R/R: 2.0x" in out
    assert "✅" in out
    assert "+12.5%" in out
    assert "−6.4%" in out  # unicode minus


def test_rr_block_marginal_gets_warning():
    out = format_rr_block(upside=9.0, downside=6.0, rr=1.5)
    assert "⚠️" in out
    assert "✅" not in out
    assert "❌" not in out


def test_rr_block_weak_gets_red_x():
    out = format_rr_block(upside=4.0, downside=4.0, rr=1.0)
    assert "❌" in out


def test_rr_block_handles_none_rr():
    out = format_rr_block(upside=None, downside=None, rr=None)
    assert "n/a" in out
    # No glyph when we can't compute.
    assert "✅" not in out and "⚠️" not in out and "❌" not in out


# ---------------------------- format_ratings_block ----------------------------

def _full_ratings(rank=312, total=3958):
    return {
        "sa_quant_rating": 4.62,
        "sa_authors_rating": 3.80,
        "wall_street_rating": 4.10,
        "sa_rank": rank,
        "total_ranked": total,
        "available": True,
    }


def test_ratings_block_with_full_data():
    out = format_ratings_block(_full_ratings())
    assert "EXTERNAL RATINGS" in out
    assert "informational" in out  # reminder that this isn't shown to agents
    assert "4.62" in out
    assert "Strong Buy" in out  # 4.62 >= 4.5
    assert "3.80" in out
    assert "Buy" in out         # 3.80 in [3.5, 4.5)
    assert "4.10" in out
    assert "#312" in out
    assert "3,958" in out


def test_ratings_block_ticker_miss_collapses_to_one_line():
    miss = {
        "sa_quant_rating": None, "sa_authors_rating": None,
        "wall_street_rating": None, "sa_rank": None, "total_ranked": 3958,
        "available": True,
    }
    out = format_ratings_block(miss)
    assert "n/a" in out
    assert "ticker not in SA_Quant_Ranked_Clean.csv" in out
    # Should NOT render the bordered block when ticker missing.
    assert "EXTERNAL RATINGS" not in out


def test_ratings_block_csv_unavailable():
    unavail = {
        "sa_quant_rating": None, "sa_authors_rating": None,
        "wall_street_rating": None, "sa_rank": None, "total_ranked": None,
        "available": False,
    }
    out = format_ratings_block(unavail)
    assert "unavailable" in out
    assert "snapshot CSV missing or unreadable" in out
    assert "EXTERNAL RATINGS" not in out
