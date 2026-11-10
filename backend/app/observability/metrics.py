"""Minimal in-process Prometheus-style counters (per API process)."""

import threading
from collections import defaultdict


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, int], int] = defaultdict(int)
        self._latency_ms_sum: dict[str, float] = defaultdict(float)

    def observe(self, route: str, status: int, latency_ms: float) -> None:
        with self._lock:
            self._requests[(route, status)] += 1
            self._latency_ms_sum[route] += latency_ms

    def render(self) -> list[str]:
        with self._lock:
            out = [
                f'factorybridge_http_requests_total{{route="{r}",status="{s}"}} {n}'
                for (r, s), n in sorted(self._requests.items())
            ]
            out += [
                f'factorybridge_http_latency_ms_sum{{route="{r}"}} {v:.1f}'
                for r, v in sorted(self._latency_ms_sum.items())
            ]
            return out


metrics = Metrics()
