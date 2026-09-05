"""Sliding-window counters for control-plane observability (health QPS)."""

from __future__ import annotations

import threading
import time
from typing import Any


class HealthMetrics:
    def __init__(self, window_sec: float = 60.0) -> None:
        self._window = max(1.0, float(window_sec))
        self._lock = threading.Lock()
        self._hits: list[float] = []

    def record(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._hits.append(now)
            cutoff = now - self._window
            if len(self._hits) > 8 and self._hits[0] < cutoff:
                self._hits = [t for t in self._hits if t >= cutoff]

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits if t >= now - self._window]
            self._hits = recent
            n = len(recent)
        return {
            "health_hits_last_60s": n,
            "health_qps_approx": round(n / self._window, 3),
            "window_sec": self._window,
        }


health_metrics = HealthMetrics()
