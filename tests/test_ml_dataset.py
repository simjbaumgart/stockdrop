"""Phase-0 dataset contract tests (drop-type classifier).

Unit tests import the builder module; artifact tests validate the parquet
and skip if it hasn't been built yet (run scripts/ml/build_drop_type_dataset.py).
"""
from pathlib import Path

import pandas as pd
import pytest

from scripts.ml.build_drop_type_dataset import (
    EXPECTED_COLUMNS,
    GATE_TRIGGER_CLASSES,
    SPLIT_BOUNDARY,
    days_since_earnings,
)

DATASET = Path(__file__).resolve().parents[1] / "data" / "ml" / "drop_type_dataset.parquet"

# Verified 2026-07-06 against the frozen window (decision_date <= 2026-07-05).
# These differ from the original plan table (999 rows): the DB is live and
# 2026-07-06 rows were still mutating on build day, so the window was frozen.
REFERENCE_CLASS_COUNTS = {
    "COMPANY_SPECIFIC": 322,
    "EARNINGS_MISS": 223,
    "SECTOR_ROTATION": 173,
    "MACRO_SELLOFF": 160,
    "UNKNOWN": 50,
    "ANALYST_DOWNGRADE": 38,
    "TECHNICAL_BREAKDOWN": 25,
}

FORBIDDEN_EXACT = {
    "reasoning", "gate_reasons", "gates_fired", "recommendation",
    "conviction", "ai_score", "status", "entry_price_low", "entry_price_high",
}
FORBIDDEN_PREFIXES = ("deep_research_", "reassess_")


def test_days_since_earnings_basic():
    assert days_since_earnings("2026-06-10", "2026-06-08") == 2


def test_days_since_earnings_null_safe():
    assert days_since_earnings("2026-06-10", None) is None
    assert days_since_earnings("2026-06-10", "") is None
    assert days_since_earnings("2026-06-10", "not-a-date") is None


def test_gate_trigger_classes():
    assert GATE_TRIGGER_CLASSES == frozenset(
        {"EARNINGS_MISS", "COMPANY_SPECIFIC", "ANALYST_DOWNGRADE"}
    )


def test_split_boundary():
    assert SPLIT_BOUNDARY == "2026-06-08"


needs_artifact = pytest.mark.skipif(
    not DATASET.exists(), reason="build the dataset first"
)


@needs_artifact
def test_columns_exact_and_forbidden_absent():
    df = pd.read_parquet(DATASET)
    assert sorted(df.columns) == sorted(EXPECTED_COLUMNS)
    for col in df.columns:
        assert col not in FORBIDDEN_EXACT, f"forbidden column {col}"
        assert not col.startswith(FORBIDDEN_PREFIXES), f"forbidden column {col}"


@needs_artifact
def test_no_test_date_in_train():
    df = pd.read_parquet(DATASET)
    train_dates = set(df.loc[df["split"] == "train", "decision_date"])
    test_dates = set(df.loc[df["split"] == "test", "decision_date"])
    assert not train_dates & test_dates
    assert max(train_dates) < SPLIT_BOUNDARY <= min(test_dates)


@needs_artifact
def test_class_counts_match_reference():
    df = pd.read_parquet(DATASET)
    assert len(df) == 991
    assert df["drop_type"].value_counts().to_dict() == REFERENCE_CLASS_COUNTS


@needs_artifact
def test_gate_trigger_consistent():
    df = pd.read_parquet(DATASET)
    expected = df["drop_type"].isin(GATE_TRIGGER_CLASSES)
    assert (df["gate_trigger"] == expected).all()
