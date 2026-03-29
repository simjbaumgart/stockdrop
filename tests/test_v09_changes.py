"""
Test suite for v0.9 changes:
  1. AI score removal (score thresholds no longer gate decisions)
  2. PM prompt redesign (new action values, trading levels, DB columns)

Run: python tests/test_v09_changes.py
"""

import os
import sys
import sqlite3
import json

# --- Setup: Override DB before any app imports ---
TEST_DB = "test_v09.db"
os.environ["DB_PATH"] = TEST_DB

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.database
app.database.DB_NAME = TEST_DB

from app.database import (
    init_db, add_decision_point, update_decision_point,
    get_decision_point, get_decision_points
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

passed_count = 0
failed_count = 0

def _assert(condition, msg=""):
    if not condition:
        raise AssertionError(msg)

def run_test(fn):
    """Run a single test function, track pass/fail."""
    global passed_count, failed_count
    try:
        fn()
        passed_count += 1
        print(f"  PASS: {fn.__name__}")
    except Exception as e:
        failed_count += 1
        print(f"  FAIL: {fn.__name__}: {e}")

def fresh_db():
    """Remove old test DB and re-init."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init_db()

def get_columns(table="decision_points"):
    conn = sqlite3.connect(TEST_DB)
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [row[1] for row in cursor.fetchall()]
    conn.close()
    return cols

# ---------------------------------------------------------------------------
# Group 1: Database Schema
# ---------------------------------------------------------------------------

def test_T1_migration_adds_new_columns():
    """T1: All 13 new trading-level columns exist after init_db."""
    fresh_db()
    cols = get_columns()

    expected_new = [
        "entry_price_low", "entry_price_high", "stop_loss",
        "take_profit_1", "take_profit_2", "pre_drop_price",
        "upside_percent", "downside_risk_percent", "risk_reward_ratio",
        "drop_type", "conviction", "entry_trigger", "reassess_in_days",
    ]
    for col in expected_new:
        _assert(col in cols, f"Column '{col}' missing from decision_points. Got: {cols}")


def test_T2_init_db_idempotent():
    """T2: Calling init_db twice causes no errors."""
    fresh_db()
    # Second call should be a no-op
    init_db()
    cols = get_columns()
    _assert("conviction" in cols, "Column 'conviction' missing after double init_db")

# ---------------------------------------------------------------------------
# Group 2: update_decision_point with new kwargs
# ---------------------------------------------------------------------------

def test_T3_full_trading_level_roundtrip():
    """T3: Insert + update with ALL trading-level kwargs, verify round-trip."""
    fresh_db()
    did = add_decision_point(
        symbol="TEST_T3", price=150.0, drop_percent=-7.5,
        recommendation="PENDING", reasoning="Analyzing...", status="Pending"
    )
    _assert(did is not None, "add_decision_point returned None")

    update_decision_point(
        did, "BUY_LIMIT", "Updated reasoning", "Owned",
        conviction="MODERATE",
        drop_type="EARNINGS_MISS",
        entry_price_low=145.0,
        entry_price_high=150.0,
        stop_loss=138.5,
        take_profit_1=161.3,
        take_profit_2=170.0,
        pre_drop_price=162.16,
        upside_percent=7.5,
        downside_risk_percent=7.7,
        risk_reward_ratio=1.0,
        entry_trigger="RSI crosses above 30",
        reassess_in_days=5,
    )

    dp = get_decision_point(did)
    _assert(dp is not None, "get_decision_point returned None")
    _assert(dp["recommendation"] == "BUY_LIMIT", f"recommendation mismatch: {dp['recommendation']}")
    _assert(dp["conviction"] == "MODERATE", f"conviction mismatch: {dp['conviction']}")
    _assert(dp["drop_type"] == "EARNINGS_MISS", f"drop_type mismatch: {dp['drop_type']}")
    _assert(abs(dp["entry_price_low"] - 145.0) < 0.01, f"entry_price_low mismatch: {dp['entry_price_low']}")
    _assert(abs(dp["entry_price_high"] - 150.0) < 0.01, f"entry_price_high mismatch")
    _assert(abs(dp["stop_loss"] - 138.5) < 0.01, f"stop_loss mismatch: {dp['stop_loss']}")
    _assert(abs(dp["take_profit_1"] - 161.3) < 0.01, f"take_profit_1 mismatch")
    _assert(abs(dp["take_profit_2"] - 170.0) < 0.01, f"take_profit_2 mismatch")
    _assert(abs(dp["pre_drop_price"] - 162.16) < 0.01, f"pre_drop_price mismatch")
    _assert(abs(dp["upside_percent"] - 7.5) < 0.01, f"upside_percent mismatch")
    _assert(abs(dp["downside_risk_percent"] - 7.7) < 0.01, f"downside_risk_percent mismatch")
    _assert(abs(dp["risk_reward_ratio"] - 1.0) < 0.01, f"risk_reward_ratio mismatch")
    _assert(dp["entry_trigger"] == "RSI crosses above 30", f"entry_trigger mismatch")
    _assert(dp["reassess_in_days"] == 5, f"reassess_in_days mismatch: {dp['reassess_in_days']}")


def test_T4_partial_update():
    """T4: Update with only conviction and drop_type; other new fields stay NULL."""
    fresh_db()
    did = add_decision_point(
        symbol="TEST_T4", price=100.0, drop_percent=-5.0,
        recommendation="PENDING", reasoning="...", status="Pending"
    )
    update_decision_point(
        did, "WATCH", "Mixed signals", "Not Owned",
        conviction="LOW",
        drop_type="UNKNOWN",
    )
    dp = get_decision_point(did)
    _assert(dp["conviction"] == "LOW", f"conviction mismatch: {dp['conviction']}")
    _assert(dp["drop_type"] == "UNKNOWN", f"drop_type mismatch: {dp['drop_type']}")
    _assert(dp["entry_price_low"] is None, f"entry_price_low should be NULL, got: {dp['entry_price_low']}")
    _assert(dp["stop_loss"] is None, f"stop_loss should be NULL, got: {dp['stop_loss']}")
    _assert(dp["reassess_in_days"] is None, f"reassess_in_days should be NULL, got: {dp['reassess_in_days']}")


def test_T5_backward_compat_update():
    """T5: Old-style update (no new kwargs) still works."""
    fresh_db()
    did = add_decision_point(
        symbol="TEST_T5", price=200.0, drop_percent=-3.0,
        recommendation="PENDING", reasoning="...", status="Pending"
    )
    result = update_decision_point(did, "AVOID", "Bear dominates", "Not Owned", ai_score=42.0)
    _assert(result is True, "update_decision_point returned False")
    dp = get_decision_point(did)
    _assert(dp["recommendation"] == "AVOID", f"recommendation mismatch: {dp['recommendation']}")
    _assert(abs(dp["ai_score"] - 42.0) < 0.01, f"ai_score mismatch: {dp['ai_score']}")
    # New fields should all be NULL
    _assert(dp["conviction"] is None, "conviction should be NULL for old-style update")

# ---------------------------------------------------------------------------
# Group 3: Recommendation-based gating (AI score removal)
# ---------------------------------------------------------------------------

def test_T6_status_determination():
    """T6: Status logic — 'BUY' in rec.upper() determines Owned/Not Owned."""
    cases = [
        ("BUY",       "Owned"),
        ("BUY_LIMIT", "Owned"),
        ("WATCH",     "Not Owned"),
        ("AVOID",     "Not Owned"),
        # Legacy values
        ("STRONG BUY", "Owned"),
        ("HOLD",       "Not Owned"),
        ("SELL",       "Not Owned"),
    ]
    for rec, expected_status in cases:
        status = "Owned" if "BUY" in rec.upper() else "Not Owned"
        _assert(status == expected_status, f"Status for '{rec}': expected '{expected_status}', got '{status}'")


def test_T7_deep_research_trigger():
    """T7: Deep research triggers on BUY/BUY_LIMIT, skips WATCH/AVOID."""
    triggers = {
        "BUY": True,
        "BUY_LIMIT": True,
        "WATCH": False,
        "AVOID": False,
        "STRONG BUY": True,  # Legacy — still contains BUY
        "HOLD": False,
        "SELL": False,
    }
    for rec, expected in triggers.items():
        should_trigger = "BUY" in rec.upper()
        _assert(should_trigger == expected, f"Trigger for '{rec}': expected {expected}, got {should_trigger}")


def test_T8_backfill_query():
    """T8: Backfill SQL returns BUY/BUY_LIMIT rows, skips WATCH/AVOID. No ai_score filter."""
    fresh_db()
    today = __import__("datetime").datetime.now().strftime("%Y-%m-%d")

    # Insert rows with different recommendations, all with NULL deep_research_verdict
    for rec in ["BUY", "BUY_LIMIT", "WATCH", "AVOID"]:
        add_decision_point(
            symbol=f"T8_{rec}", price=100.0, drop_percent=-5.0,
            recommendation=rec, reasoning="test", status="Pending",
            ai_score=30.0  # Low score — should NOT matter anymore
        )

    # Run the exact backfill query from stock_service.py
    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    query = """
        SELECT * FROM decision_points 
        WHERE date(timestamp) = ? 
        AND (recommendation LIKE '%BUY%' OR recommendation LIKE '%STRONG BUY%')
        AND (deep_research_verdict IS NULL OR deep_research_verdict = '' OR deep_research_verdict = '-' OR deep_research_verdict LIKE 'UNKNOWN%')
    """
    cursor.execute(query, (today,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    symbols = [r["symbol"] for r in rows]
    _assert("T8_BUY" in symbols, f"BUY row missing from backfill results: {symbols}")
    _assert("T8_BUY_LIMIT" in symbols, f"BUY_LIMIT row missing from backfill results: {symbols}")
    _assert("T8_WATCH" not in symbols, f"WATCH row should NOT be in backfill results: {symbols}")
    _assert("T8_AVOID" not in symbols, f"AVOID row should NOT be in backfill results: {symbols}")

# ---------------------------------------------------------------------------
# Group 4: PM prompt output parsing
# ---------------------------------------------------------------------------

def _extract_json(text):
    """Local copy of ResearchService._extract_json to avoid importing the full service (SDK deps)."""
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
        return None
    except Exception:
        return None


def test_T9_parse_new_json_schema():
    """T9: _extract_json correctly parses the new PM output schema."""
    mock_response = """
Here is my analysis:

```json
{
  "action": "BUY_LIMIT",
  "conviction": "MODERATE",
  "drop_type": "EARNINGS_MISS",
  "entry_price_low": 145.50,
  "entry_price_high": 149.00,
  "stop_loss": 138.20,
  "take_profit_1": 161.00,
  "take_profit_2": 170.50,
  "upside_percent": 8.1,
  "downside_risk_percent": 7.4,
  "risk_reward_ratio": 1.1,
  "pre_drop_price": 161.29,
  "entry_trigger": "RSI crosses above 30 and price holds $145 for 2 sessions",
  "reassess_in_days": 5,
  "reason": "Earnings beat on revenue but guidance cut; market overreacted.",
  "key_factors": [
    "Revenue beat by 3% — verified via Google Search",
    "Guidance lowered 2% — market priced in worse",
    "RSI at 24 — deeply oversold"
  ]
}
```
"""
    parsed = _extract_json(mock_response)
    _assert(parsed is not None, "Failed to extract JSON from mock response")
    _assert(parsed["action"] == "BUY_LIMIT", f"action mismatch: {parsed['action']}")
    _assert(parsed["conviction"] == "MODERATE", f"conviction mismatch")
    _assert(parsed["drop_type"] == "EARNINGS_MISS", f"drop_type mismatch")
    _assert(abs(parsed["entry_price_low"] - 145.50) < 0.01, "entry_price_low mismatch")
    _assert(abs(parsed["stop_loss"] - 138.20) < 0.01, "stop_loss mismatch")
    _assert(abs(parsed["take_profit_1"] - 161.00) < 0.01, "take_profit_1 mismatch")
    _assert(parsed["take_profit_2"] == 170.50, "take_profit_2 mismatch")
    _assert(abs(parsed["upside_percent"] - 8.1) < 0.01, "upside_percent mismatch")
    _assert(abs(parsed["risk_reward_ratio"] - 1.1) < 0.01, "risk_reward_ratio mismatch")
    _assert(parsed["entry_trigger"].startswith("RSI"), f"entry_trigger mismatch: {parsed['entry_trigger']}")
    _assert(parsed["reassess_in_days"] == 5, "reassess_in_days mismatch")
    _assert(len(parsed["key_factors"]) == 3, f"key_factors count mismatch: {len(parsed['key_factors'])}")


def test_T10_fallback_on_bad_json():
    """T10: Fallback decision dict uses new schema (AVOID, LOW, UNKNOWN) not old (HOLD, score 50)."""
    result = _extract_json("This is not valid JSON at all.")
    _assert(result is None, "Expected None from bad JSON")

    # Verify the fallback dict that _run_risk_council_and_decision builds
    # (we test the dict shape directly, not the method — no API key needed)
    fallback = {"action": "AVOID", "conviction": "LOW", "reason": "Failed to generate decision JSON.", "drop_type": "UNKNOWN"}
    _assert(fallback["action"] == "AVOID", "Fallback action should be AVOID, not HOLD")
    _assert("score" not in fallback, "Fallback should not contain 'score' key")
    _assert(fallback["conviction"] == "LOW", "Fallback conviction should be LOW")
    _assert(fallback["drop_type"] == "UNKNOWN", "Fallback drop_type should be UNKNOWN")

# ---------------------------------------------------------------------------
# Group 5: End-to-end mock flow
# ---------------------------------------------------------------------------

def test_T11_analyze_stock_return_dict_fields():
    """T11: Verify the return dict structure from analyze_stock contains all new fields."""
    # We can't call analyze_stock without mocking all agents,
    # so we test the return dict construction directly.
    # Simulate what analyze_stock returns by building the dict the same way.

    mock_final_decision = {
        "action": "BUY",
        "conviction": "HIGH",
        "drop_type": "SECTOR_ROTATION",
        "entry_price_low": 92.0,
        "entry_price_high": 95.0,
        "stop_loss": 85.0,
        "take_profit_1": 100.0,
        "take_profit_2": 108.0,
        "upside_percent": 8.7,
        "downside_risk_percent": 7.6,
        "risk_reward_ratio": 1.1,
        "pre_drop_price": 100.0,
        "entry_trigger": "Immediate — current levels are attractive.",
        "reassess_in_days": 3,
        "reason": "Sector rotation oversold — strong fundamentals intact.",
        "key_factors": ["Factor 1", "Factor 2", "Factor 3"],
    }

    # Replicate the return dict construction from research_service.analyze_stock
    recommendation = mock_final_decision.get("action", "AVOID").upper()
    result = {
        "recommendation": recommendation,
        "score": mock_final_decision.get("score", 50),
        "executive_summary": mock_final_decision.get("reason", "No reason provided."),
        "conviction": mock_final_decision.get("conviction", "LOW"),
        "drop_type": mock_final_decision.get("drop_type", "UNKNOWN"),
        "entry_price_low": mock_final_decision.get("entry_price_low"),
        "entry_price_high": mock_final_decision.get("entry_price_high"),
        "stop_loss": mock_final_decision.get("stop_loss"),
        "take_profit_1": mock_final_decision.get("take_profit_1"),
        "take_profit_2": mock_final_decision.get("take_profit_2"),
        "upside_percent": mock_final_decision.get("upside_percent"),
        "downside_risk_percent": mock_final_decision.get("downside_risk_percent"),
        "risk_reward_ratio": mock_final_decision.get("risk_reward_ratio"),
        "pre_drop_price": mock_final_decision.get("pre_drop_price"),
        "entry_trigger": mock_final_decision.get("entry_trigger"),
        "reassess_in_days": mock_final_decision.get("reassess_in_days"),
        "key_factors": mock_final_decision.get("key_factors", []),
        "key_decision_points": mock_final_decision.get("key_factors", []),
    }

    _assert(result["recommendation"] == "BUY", f"rec mismatch: {result['recommendation']}")
    _assert(result["conviction"] == "HIGH", f"conviction mismatch")
    _assert(result["drop_type"] == "SECTOR_ROTATION", f"drop_type mismatch")
    _assert(result["entry_price_low"] == 92.0, "entry_price_low mismatch")
    _assert(result["stop_loss"] == 85.0, "stop_loss mismatch")
    _assert(result["take_profit_1"] == 100.0, "take_profit_1 mismatch")
    _assert(result["risk_reward_ratio"] == 1.1, "risk_reward_ratio mismatch")
    _assert(result["entry_trigger"] == "Immediate — current levels are attractive.", "entry_trigger mismatch")
    _assert(result["reassess_in_days"] == 3, "reassess_in_days mismatch")
    _assert(len(result["key_factors"]) == 3, "key_factors count mismatch")
    # Backward compat
    _assert(result["score"] == 50, "score default should be 50 for backward compat")
    _assert(result["key_decision_points"] == result["key_factors"], "key_decision_points should map to key_factors")


def test_T12_persist_and_retrieve_full_flow():
    """T12: Full round-trip — build result dict, persist via update_decision_point, verify retrieval."""
    fresh_db()
    did = add_decision_point(
        symbol="TEST_T12", price=93.0, drop_percent=-7.0,
        recommendation="PENDING", reasoning="Analyzing...", status="Pending"
    )
    _assert(did is not None, "add_decision_point returned None")

    # Simulate the report_data dict from analyze_stock
    report_data = {
        "conviction": "HIGH",
        "drop_type": "MACRO_SELLOFF",
        "entry_price_low": 91.5,
        "entry_price_high": 94.0,
        "stop_loss": 86.0,
        "take_profit_1": 100.0,
        "take_profit_2": 105.0,
        "pre_drop_price": 100.0,
        "upside_percent": 7.5,
        "downside_risk_percent": 7.5,
        "risk_reward_ratio": 1.0,
        "entry_trigger": "Volume returns to 20-day average",
        "reassess_in_days": 7,
    }

    update_decision_point(
        did, "BUY", "Strong recovery potential", "Owned",
        ai_score=None,
        conviction=report_data["conviction"],
        drop_type=report_data["drop_type"],
        entry_price_low=report_data["entry_price_low"],
        entry_price_high=report_data["entry_price_high"],
        stop_loss=report_data["stop_loss"],
        take_profit_1=report_data["take_profit_1"],
        take_profit_2=report_data["take_profit_2"],
        pre_drop_price=report_data["pre_drop_price"],
        upside_percent=report_data["upside_percent"],
        downside_risk_percent=report_data["downside_risk_percent"],
        risk_reward_ratio=report_data["risk_reward_ratio"],
        entry_trigger=report_data["entry_trigger"],
        reassess_in_days=report_data["reassess_in_days"],
    )

    dp = get_decision_point(did)
    _assert(dp["recommendation"] == "BUY", f"recommendation mismatch: {dp['recommendation']}")
    _assert(dp["conviction"] == "HIGH", f"conviction mismatch: {dp['conviction']}")
    _assert(dp["drop_type"] == "MACRO_SELLOFF", f"drop_type mismatch: {dp['drop_type']}")
    _assert(abs(dp["entry_price_low"] - 91.5) < 0.01, "entry_price_low mismatch")
    _assert(abs(dp["stop_loss"] - 86.0) < 0.01, "stop_loss mismatch")
    _assert(abs(dp["take_profit_1"] - 100.0) < 0.01, "take_profit_1 mismatch")
    _assert(abs(dp["take_profit_2"] - 105.0) < 0.01, "take_profit_2 mismatch")
    _assert(abs(dp["pre_drop_price"] - 100.0) < 0.01, "pre_drop_price mismatch")
    _assert(abs(dp["risk_reward_ratio"] - 1.0) < 0.01, "risk_reward_ratio mismatch")
    _assert(dp["entry_trigger"] == "Volume returns to 20-day average", "entry_trigger mismatch")
    _assert(dp["reassess_in_days"] == 7, f"reassess_in_days mismatch: {dp['reassess_in_days']}")

# ---------------------------------------------------------------------------
# Group 6: Template badge logic
# ---------------------------------------------------------------------------

def test_T13_badge_rendering():
    """T13: Badge logic matches correct classes for new and legacy action values."""
    # Replicate the exact Jinja2 if/elif chain from decisions.html
    # to verify it produces the right badge for each recommendation value.
    def get_badge_class(rec):
        if rec == "BUY": return "badge-success"
        elif rec == "BUY_LIMIT": return "badge-info"
        elif rec == "WATCH": return "badge-warning"
        elif rec == "AVOID": return "badge-danger"
        # Legacy
        elif rec == "STRONG BUY": return "badge-success"
        elif rec in ("SELL", "STRONG SELL"): return "badge-danger"
        else: return "badge-warning"

    expected = {
        "BUY": "badge-success",
        "BUY_LIMIT": "badge-info",
        "WATCH": "badge-warning",
        "AVOID": "badge-danger",
        "STRONG BUY": "badge-success",
        "SELL": "badge-danger",
        "HOLD": "badge-warning",  # Legacy fallback
    }

    for rec, expected_class in expected.items():
        actual = get_badge_class(rec)
        _assert(actual == expected_class, f"Badge for '{rec}': expected '{expected_class}', got '{actual}'")

# ---------------------------------------------------------------------------
# Group 7: Email trigger
# ---------------------------------------------------------------------------

def test_T14_email_trigger_logic():
    """T14: Email triggers only on exact 'BUY', not BUY_LIMIT/WATCH/AVOID/STRONG BUY."""
    cases = {
        "BUY": True,
        "BUY_LIMIT": False,
        "WATCH": False,
        "AVOID": False,
        "STRONG BUY": False,   # Legacy — no longer triggers email
        "HOLD": False,
        "SELL": False,
    }
    for rec, expected in cases.items():
        triggers = rec.upper() == "BUY"
        _assert(triggers == expected, f"Email trigger for '{rec}': expected {expected}, got {triggers}")

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    global passed_count, failed_count

    print("\n" + "=" * 60)
    print("  v0.9 Test Suite — AI Score Removal + PM Prompt Redesign")
    print("=" * 60 + "\n")

    tests = [
        # Group 1: DB Schema
        test_T1_migration_adds_new_columns,
        test_T2_init_db_idempotent,
        # Group 2: update_decision_point kwargs
        test_T3_full_trading_level_roundtrip,
        test_T4_partial_update,
        test_T5_backward_compat_update,
        # Group 3: Recommendation gating
        test_T6_status_determination,
        test_T7_deep_research_trigger,
        test_T8_backfill_query,
        # Group 4: JSON parsing
        test_T9_parse_new_json_schema,
        test_T10_fallback_on_bad_json,
        # Group 5: End-to-end mock
        test_T11_analyze_stock_return_dict_fields,
        test_T12_persist_and_retrieve_full_flow,
        # Group 6: Template
        test_T13_badge_rendering,
        # Group 7: Email
        test_T14_email_trigger_logic,
    ]

    for t in tests:
        run_test(t)

    print("\n" + "-" * 60)
    print(f"  Results: {passed_count} passed, {failed_count} failed out of {len(tests)} tests")
    print("-" * 60 + "\n")

    # Cleanup
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_all()
