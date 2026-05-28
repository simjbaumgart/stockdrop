import pytest

from scripts.analysis import news_shadow_report as nsr
from scripts.analysis import news_shadow_judge as nsj


def _row(idx, prod_econ, shadow_econ, perr=None):
    return {
        "id": idx,
        "decision_point_id": idx,
        "symbol": f"SYM{idx}",
        "decision_date": "2026-05-22",
        "production_model": "gemini-3.5-flash",
        "production_report": "Prod report",
        "production_tokens_in": 1000,
        "production_tokens_out": 400,
        "production_latency_ms": 5000,
        "production_needs_economics": prod_econ,
        "shadow_model": "gemini-3-flash-preview",
        "shadow_report": "Shadow report",
        "shadow_tokens_in": 1000,
        "shadow_tokens_out": 400,
        "shadow_latency_ms": 6000,
        "shadow_needs_economics": shadow_econ,
        "shadow_error": perr,
    }


def test_economics_flag_agreement():
    rows = [_row(1, 1, 1), _row(2, 1, 0), _row(3, 0, 0)]
    stats = nsr.compute_deterministic_stats(rows)
    assert stats["economics_flag_agree"] == 2
    assert stats["economics_flag_disagree"] == 1


def test_cost_math_uses_pricing():
    rows = [_row(1, 1, 1)]
    stats = nsr.compute_deterministic_stats(rows)
    pin, pout = nsr.PRICING["gemini-3.5-flash"]["in"], nsr.PRICING["gemini-3.5-flash"]["out"]
    expected = (1000 / 1_000_000) * pin + (400 / 1_000_000) * pout
    assert stats["production_cost_per_dp"] == pytest.approx(expected)


def test_errored_shadow_excluded_from_pairs():
    rows = [_row(1, 1, 1), _row(2, 1, 1, perr="timeout")]
    stats = nsr.compute_deterministic_stats(rows)
    assert stats["completed_pairs"] == 1


def test_render_report_contains_sections():
    rows = [_row(1, 1, 1)]
    md = nsr.render_report(rows, judge_results=None)
    assert "# News Agent Shadow Comparison Report" in md
    assert "Cost per decision point" in md
    assert "SYM1" in md


def test_parse_judge_response_valid():
    raw = '''```json
{"source_classification": "tie",
 "hard_event_detection": "production_better",
 "production_coherence": "high",
 "shadow_coherence": "medium",
 "disagreements": "shadow misclassified a wire source as official"}
```'''
    parsed = nsj.parse_judge_response(raw)
    assert parsed["hard_event_detection"] == "production_better"
    assert parsed["production_coherence"] == "high"


def test_parse_judge_response_malformed_returns_fallback():
    parsed = nsj.parse_judge_response("not json at all")
    assert parsed["source_classification"] == "parse_error"
    assert "disagreements" in parsed


def test_parse_judge_response_empty_returns_fallback():
    parsed = nsj.parse_judge_response("")
    assert parsed["source_classification"] == "parse_error"


def test_build_judge_prompt_includes_both_reports():
    prompt = nsj.build_judge_prompt("PROD TEXT HERE", "SHADOW TEXT HERE")
    assert "PROD TEXT HERE" in prompt
    assert "SHADOW TEXT HERE" in prompt
    assert "source" in prompt.lower()
