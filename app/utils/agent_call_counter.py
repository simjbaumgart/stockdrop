"""Process-local agent-call counter. Thread-safe. Two views: a
reset-on-cycle per-cycle tally, and a rolling window for ops visibility.

Not durable — a process restart loses history. That's deliberate: zero-
dependency telemetry, and the provider dashboard is the source of truth
for billing."""

from __future__ import annotations

import threading
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Tuple


class AgentCallCounter:
    def __init__(self, window: timedelta = timedelta(hours=24)):
        self._lock = threading.Lock()
        self._cycle: Counter = Counter()
        self._rolling: Deque[Tuple[datetime, str]] = deque()
        self._window = window

    def record(self, agent: str) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._cycle[agent] += 1
            self._rolling.append((now, agent))
            self._evict_locked(now)

    def reset_cycle(self) -> None:
        with self._lock:
            self._cycle.clear()

    def snapshot(self) -> Dict:
        now = datetime.now(timezone.utc)
        with self._lock:
            self._evict_locked(now)
            return {
                "total_cycle": sum(self._cycle.values()),
                "total_rolling_24h": len(self._rolling),
                "by_agent": dict(self._cycle),
            }

    def _evict_locked(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._rolling and self._rolling[0][0] < cutoff:
            self._rolling.popleft()


# Module-level singleton. Import from here at call sites.
counter = AgentCallCounter()
