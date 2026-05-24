# tests/test_token_tracker.py
import sqlite3
import threading


def test_record_known_model_inserts_row_with_cost(temp_db):
    path, decision_id = temp_db
    from app.services import token_pricing, token_tracker
    token_pricing.GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}
    try:
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="sensor", agent_name="sensor_news",
            model="__test_model__", tokens_in=1_000_000, tokens_out=500_000,
        )
    finally:
        del token_pricing.GEMINI_PRICING["__test_model__"]

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT decision_id, ticker, stage, agent_name, model, tokens_in, "
        "tokens_out, cost_usd FROM agent_token_usage"
    ).fetchone()
    conn.close()
    assert row == (decision_id, "TEST", "sensor", "sensor_news",
                   "__test_model__", 1_000_000, 500_000, 6.0)


def test_record_unknown_model_stores_null_cost(temp_db):
    path, decision_id = temp_db
    from app.services import token_tracker
    token_tracker.record_llm_call(
        decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
        stage="pm", agent_name="pm", model="totally-unknown-model",
        tokens_in=100, tokens_out=200,
    )
    conn = sqlite3.connect(path)
    cost_usd = conn.execute("SELECT cost_usd FROM agent_token_usage").fetchone()[0]
    conn.close()
    assert cost_usd is None


def test_concurrent_inserts_from_threads(temp_db):
    """5 sensor threads + 3 debate threads writing at once must all land."""
    path, decision_id = temp_db
    from app.services import token_tracker

    def writer(name):
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="sensor", agent_name=name, model="gemini-3-flash-preview",
            tokens_in=1000, tokens_out=500,
        )

    threads = [threading.Thread(target=writer, args=(f"sensor_{i}",)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()

    conn = sqlite3.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM agent_token_usage").fetchone()[0]
    conn.close()
    assert count == 8


def test_rollup_writes_totals_to_decision_points(temp_db):
    path, decision_id = temp_db
    from app.services import token_pricing, token_tracker
    token_pricing.GEMINI_PRICING["__test_model__"] = {"in": 2.0, "out": 8.0}
    try:
        for i in range(3):
            token_tracker.record_llm_call(
                decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
                stage="sensor", agent_name=f"sensor_{i}",
                model="__test_model__", tokens_in=1_000_000, tokens_out=500_000,
            )
        token_tracker.rollup_decision_totals(decision_id)
    finally:
        del token_pricing.GEMINI_PRICING["__test_model__"]

    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT total_tokens_in, total_tokens_out, total_cost_usd, total_llm_calls "
        "FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()
    conn.close()
    assert row == (3_000_000, 1_500_000, 18.0, 3)


def test_rollup_is_idempotent(temp_db):
    path, decision_id = temp_db
    from app.services import token_tracker
    token_tracker.record_llm_call(
        decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
        stage="pm", agent_name="pm", model="gemini-3.1-pro-preview",
        tokens_in=100, tokens_out=200,
    )
    token_tracker.rollup_decision_totals(decision_id)
    token_tracker.rollup_decision_totals(decision_id)  # second run
    conn = sqlite3.connect(path)
    calls = conn.execute(
        "SELECT total_llm_calls FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()[0]
    conn.close()
    assert calls == 1  # still 1, not 2


def test_rollup_under_reports_when_some_rows_have_null_cost(temp_db):
    """SQLite's SUM skips NULLs, so total_cost_usd reflects only the priced rows.
    The 'pricing gap' is detected by comparing total_llm_calls (always full count)
    against the count of non-NULL cost rows in agent_token_usage. This test pins
    that contract: if anyone changes SUM to COALESCE(SUM,0), priced and unpriced
    runs would become indistinguishable in total_cost_usd, and this test fails.
    """
    path, decision_id = temp_db
    from app.services import token_pricing, token_tracker
    token_pricing.GEMINI_PRICING["__priced__"] = {"in": 2.0, "out": 8.0}
    try:
        # One priced row → cost_usd = 2.0
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="sensor", agent_name="sensor_priced",
            model="__priced__", tokens_in=1_000_000, tokens_out=0,
        )
        # One unknown-model row → cost_usd = NULL (skipped by SUM, not summed as 0)
        token_tracker.record_llm_call(
            decision_id=decision_id, ticker="TEST", run_date="2026-05-23",
            stage="pm", agent_name="pm",
            model="unknown-model", tokens_in=100, tokens_out=100,
        )
        token_tracker.rollup_decision_totals(decision_id)
    finally:
        del token_pricing.GEMINI_PRICING["__priced__"]

    conn = sqlite3.connect(path)
    total_cost, total_in, total_out, calls = conn.execute(
        "SELECT total_cost_usd, total_tokens_in, total_tokens_out, total_llm_calls "
        "FROM decision_points WHERE id = ?", (decision_id,)
    ).fetchone()
    # cost reflects only the priced row — NULL row is silently skipped by SUM
    assert total_cost == 2.0
    # tokens are NOT skipped (rollup uses COALESCE on tokens) — full picture
    assert total_in  == 1_000_100
    assert total_out == 100
    # llm_calls counts ALL rows, priced or not — this is the signal that lets
    # operators detect a pricing gap (calls=2 but the cost source has 1 null row).
    assert calls == 2
    # Confirm the underlying gap is observable directly
    priced_row_count = conn.execute(
        "SELECT COUNT(cost_usd) FROM agent_token_usage WHERE decision_id = ?",
        (decision_id,)
    ).fetchone()[0]
    conn.close()
    assert priced_row_count == 1  # gap of 1 vs calls=2 → operator action needed
