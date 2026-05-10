"""Regression: DR must not enqueue the same (symbol, date) twice in one run.

Production failure: SNY/ODFL/UPS got DR'd twice in cycle 2 (2026-05-04).
Live trigger queued first; backfill swept the DB seconds later, saw verdict
still NULL, and re-queued. Both ran, second overwrote first, DB ended up with
contradictory verdicts.
"""
from app.services.deep_research_service import DeepResearchService


def test_duplicate_enqueue_is_rejected():
    svc = DeepResearchService.__new__(DeepResearchService)
    # Manually init the bits we need (avoid __init__ which spins up worker threads).
    import threading
    from queue import Queue
    svc.individual_queue = Queue()
    svc._inflight = set()
    svc._inflight_lock = threading.Lock()

    ctx = {"pm_decision": {"action": "BUY"}}

    queued1 = svc.queue_research_task("SNY", ctx, decision_id=1)
    queued2 = svc.queue_research_task("SNY", ctx, decision_id=2)

    assert queued1 is True, "first enqueue should succeed"
    assert queued2 is False, "duplicate enqueue should be rejected"
    assert svc.individual_queue.qsize() == 1


def test_enqueue_after_inflight_clear_succeeds():
    svc = DeepResearchService.__new__(DeepResearchService)
    import threading
    from queue import Queue
    svc.individual_queue = Queue()
    svc._inflight = set()
    svc._inflight_lock = threading.Lock()

    ctx = {"pm_decision": {"action": "BUY"}}

    svc.queue_research_task("SNY", ctx, decision_id=1)
    # Simulate completion clearing the inflight key.
    svc._inflight.discard(("SNY", svc._today_str()))

    queued = svc.queue_research_task("SNY", ctx, decision_id=2)
    assert queued is True


def test_different_symbols_both_enqueue():
    svc = DeepResearchService.__new__(DeepResearchService)
    import threading
    from queue import Queue
    svc.individual_queue = Queue()
    svc._inflight = set()
    svc._inflight_lock = threading.Lock()

    ctx = {"pm_decision": {"action": "BUY"}}

    assert svc.queue_research_task("SNY", ctx, decision_id=1) is True
    assert svc.queue_research_task("ODFL", ctx, decision_id=2) is True
    assert svc.individual_queue.qsize() == 2


def test_inflight_key_survives_utc_midnight_crossing():
    """Regression: enqueue on day N, complete on day N+1 must still clear
    the original key (otherwise a same-day retry next morning is wrongly
    rejected as a duplicate)."""
    import threading
    from queue import Queue
    from unittest.mock import patch

    svc = DeepResearchService.__new__(DeepResearchService)
    svc.individual_queue = Queue()
    svc._inflight = set()
    svc._inflight_lock = threading.Lock()

    ctx = {"pm_decision": {"action": "BUY"}}

    # Enqueue on 2026-05-04
    with patch.object(svc, "_today_str", return_value="2026-05-04"):
        assert svc.queue_research_task("SNY", ctx, decision_id=1) is True
        # Pull the payload back out so we can verify the stored key.
        item = svc.individual_queue.get_nowait()
        payload = item["payload"]
        # The payload must carry the inflight key from enqueue time.
        assert payload.get("_inflight_key") == ("SNY", "2026-05-04"), (
            f"payload should carry stored inflight key, got: {payload!r}"
        )

    # Verify _inflight has the original-day key
    assert ("SNY", "2026-05-04") in svc._inflight

    # Now simulate processing finishing on 2026-05-05 (after UTC midnight).
    # The finally backstop in _process_individual_task uses the payload's
    # stored key, NOT a fresh _today_str(), so the original key clears.
    with patch.object(svc, "_today_str", return_value="2026-05-05"):
        # Simulate the finally clause:
        with svc._inflight_lock:
            svc._inflight.discard(payload["_inflight_key"])

    assert ("SNY", "2026-05-04") not in svc._inflight, (
        "original-day key must be cleared even when completion happens next day"
    )

    # And a same-symbol enqueue on 2026-05-05 must succeed.
    with patch.object(svc, "_today_str", return_value="2026-05-05"):
        assert svc.queue_research_task("SNY", ctx, decision_id=2) is True
