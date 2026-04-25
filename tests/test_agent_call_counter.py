"""Process-local counter that tallies Gemini agent calls per cycle and over
a rolling 24h window. Reset-on-cycle, thread-safe, zero dependencies."""

import threading
import time
from datetime import timedelta

from app.utils.agent_call_counter import AgentCallCounter


def test_records_and_reports_per_cycle():
    c = AgentCallCounter(window=timedelta(hours=24))
    c.record("phase1.technical")
    c.record("phase1.news")
    c.record("pm")
    snap = c.snapshot()
    assert snap["total_cycle"] == 3
    assert snap["by_agent"]["phase1.technical"] == 1
    assert snap["by_agent"]["pm"] == 1


def test_reset_cycle_clears_cycle_counters_but_keeps_rolling():
    c = AgentCallCounter(window=timedelta(hours=24))
    c.record("pm")
    c.record("pm")
    c.reset_cycle()
    snap = c.snapshot()
    assert snap["total_cycle"] == 0
    assert snap["total_rolling_24h"] == 2


def test_rolling_window_evicts_old_entries():
    c = AgentCallCounter(window=timedelta(milliseconds=50))
    c.record("pm")
    time.sleep(0.12)
    c.record("pm")
    snap = c.snapshot()
    assert snap["total_rolling_24h"] == 1


def test_thread_safety():
    c = AgentCallCounter(window=timedelta(hours=24))
    def worker():
        for _ in range(100):
            c.record("pm")
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    snap = c.snapshot()
    assert snap["total_cycle"] == 800


def test_module_singleton_importable():
    """A module-level singleton named `counter` must be importable."""
    from app.utils.agent_call_counter import counter
    assert isinstance(counter, AgentCallCounter)
