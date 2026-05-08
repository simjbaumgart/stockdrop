"""Recompute downside_risk_percent and risk_reward_ratio from
post-guard stop_loss values."""
import pytest

from app.utils.stop_loss_guard import recompute_risk_metrics


def test_recompute_basic():
    # entry 100, stop 90 -> downside 10%, upside 20% -> R/R 2.0
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=90.0, upside_percent=20.0)
    assert out["downside_risk_percent"] == 10.0
    assert out["risk_reward_ratio"] == 2.0


def test_recompute_widens_after_guard_lowers_rr():
    # Mirrors the EXPE case from the deep-research log: entry 227, stop 201.9, upside 10%
    # downside ~= (227-201.9)/227 * 100 = 11.05% -> R/R ~= 0.9
    out = recompute_risk_metrics(entry_low=227.0, stop_loss=201.9, upside_percent=10.0)
    assert out["downside_risk_percent"] == pytest.approx(11.06, abs=0.05)
    assert out["risk_reward_ratio"] == pytest.approx(0.9, abs=0.05)


def test_recompute_returns_none_when_inputs_missing():
    out = recompute_risk_metrics(entry_low=None, stop_loss=90.0, upside_percent=20.0)
    assert out["downside_risk_percent"] is None
    assert out["risk_reward_ratio"] is None


def test_recompute_returns_none_when_stop_above_entry():
    # Defensive: invalid stop above entry should not produce a negative downside
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=105.0, upside_percent=20.0)
    assert out["downside_risk_percent"] is None
    assert out["risk_reward_ratio"] is None


def test_recompute_zero_downside_yields_none_rr():
    out = recompute_risk_metrics(entry_low=100.0, stop_loss=100.0, upside_percent=20.0)
    assert out["downside_risk_percent"] == 0.0
    assert out["risk_reward_ratio"] is None  # division by zero
