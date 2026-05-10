"""Unit tests for app/services/sa_grades_service.py."""
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sa_grades_service import SAGradesService, parse_rating


# ---------------------------- parse_rating ----------------------------

def test_parse_rating_extracts_numeric_tail():
    assert parse_rating("Rating: Strong Buy4.99") == 4.99
    assert parse_rating("Rating: Buy3.88") == 3.88
    assert parse_rating("Rating: Hold2.5") == 2.5


def test_parse_rating_handles_missing_or_malformed():
    assert parse_rating("") is None
    assert parse_rating(None) is None
    assert parse_rating("not a rating") is None
    # NaN-like values via pandas come through as None
    import math
    assert parse_rating(math.nan) is None


# ---------------------------- SAGradesService ----------------------------

@pytest.fixture
def csv_with_three_rows(tmp_path: Path) -> Path:
    csv = tmp_path / "sa.csv"
    csv.write_text(textwrap.dedent("""\
        Rank,Symbol,Company Name,Quant Rating,SA Analyst Ratings,Wall Street Ratings
        1,MU,"Micron Technology, Inc.",Rating: Strong Buy4.99,Rating: Buy3.88,Rating: Strong Buy4.54
        312,AAPL,"Apple Inc.",Rating: Buy4.10,Rating: Buy3.80,Rating: Buy4.10
        500,XYZ,"Broken Co",,Rating: Buy3.20,Rating: Hold3.00
    """))
    return csv


def test_lookup_hit_returns_parsed_floats(csv_with_three_rows):
    svc = SAGradesService(csv_path=str(csv_with_three_rows))
    res = svc.lookup("MU")
    assert res["available"] is True
    assert res["sa_quant_rating"] == 4.99
    assert res["sa_authors_rating"] == 3.88
    assert res["wall_street_rating"] == 4.54
    assert res["sa_rank"] == 1
    assert res["total_ranked"] == 3


def test_lookup_is_case_insensitive_and_strips_whitespace(csv_with_three_rows):
    svc = SAGradesService(csv_path=str(csv_with_three_rows))
    assert svc.lookup("aapl")["sa_rank"] == 312
    assert svc.lookup("  AAPL  ")["sa_rank"] == 312


def test_lookup_miss_returns_none_fields_but_available_true(csv_with_three_rows):
    svc = SAGradesService(csv_path=str(csv_with_three_rows))
    res = svc.lookup("NOTREAL")
    assert res["available"] is True
    assert res["sa_quant_rating"] is None
    assert res["sa_authors_rating"] is None
    assert res["wall_street_rating"] is None
    assert res["sa_rank"] is None
    assert res["total_ranked"] == 3


def test_malformed_rating_field_yields_none_but_others_parse(csv_with_three_rows):
    svc = SAGradesService(csv_path=str(csv_with_three_rows))
    res = svc.lookup("XYZ")
    assert res["sa_quant_rating"] is None
    assert res["sa_authors_rating"] == 3.20
    assert res["wall_street_rating"] == 3.00
    assert res["sa_rank"] == 500


def test_missing_csv_returns_unavailable_no_exception(tmp_path: Path):
    svc = SAGradesService(csv_path=str(tmp_path / "does_not_exist.csv"))
    res = svc.lookup("MU")
    assert res["available"] is False
    assert res["sa_quant_rating"] is None
    assert res["sa_rank"] is None
    assert res["total_ranked"] is None


def test_lookup_loads_csv_only_once(csv_with_three_rows, monkeypatch):
    svc = SAGradesService(csv_path=str(csv_with_three_rows))
    svc.lookup("MU")
    # Trigger a second lookup; touching the file should not be required again.
    csv_with_three_rows.unlink()
    res = svc.lookup("AAPL")
    assert res["sa_rank"] == 312


def test_env_var_override(monkeypatch, csv_with_three_rows):
    monkeypatch.setenv("SA_GRADES_CSV_PATH", str(csv_with_three_rows))
    svc = SAGradesService()
    assert svc.lookup("MU")["sa_rank"] == 1
