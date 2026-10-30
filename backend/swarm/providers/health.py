"""In-process provider health: call counts, latency, and a small circuit breaker.

State is per backend process and resets on restart. It informs adaptive routing and the
provider-health API; it is not a durable audit record (ticket usage is persisted separately).
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass

FAILURE_THRESHOLD = 3  # consecutive failures before the circuit opens
OPEN_SECONDS = 60.0
_EWMA = 0.3


@dataclass
class ProviderStats:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    avg_latency_ms: float | None = None
    last_latency_ms: int | None = None
    last_error: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    circuit_open_until: float | None = None


_stats: dict[str, ProviderStats] = {}
_lock = threading.Lock()


def _get(provider: str) -> ProviderStats:
    return _stats.setdefault(provider, ProviderStats())


def record_success(provider: str, latency_ms: int) -> None:
    with _lock:
        stats = _get(provider)
        stats.calls += 1
        stats.successes += 1
        stats.consecutive_failures = 0
        stats.circuit_open_until = None
        stats.last_latency_ms = latency_ms
        stats.avg_latency_ms = latency_ms if stats.avg_latency_ms is None else (
            _EWMA * latency_ms + (1 - _EWMA) * stats.avg_latency_ms
        )
        stats.last_success_at = time.time()


def record_failure(provider: str, kind: str) -> None:
    with _lock:
        stats = _get(provider)
        stats.calls += 1
        stats.failures += 1
        stats.consecutive_failures += 1
        stats.last_error = kind  # a fixed classification, never a provider error body
        stats.last_failure_at = time.time()
        if stats.consecutive_failures >= FAILURE_THRESHOLD:
            stats.circuit_open_until = time.time() + OPEN_SECONDS


def circuit_open(provider: str) -> bool:
    with _lock:
        until = _get(provider).circuit_open_until
    return until is not None and until > time.time()


def snapshot() -> dict[str, dict]:
    with _lock:
        items = {name: asdict(stats) for name, stats in _stats.items()}
    now = time.time()
    for stats in items.values():
        until = stats["circuit_open_until"]
        stats["status"] = "open" if until and until > now else ("degraded" if stats["consecutive_failures"] else "ok")
    return items


def reset() -> None:
    with _lock:
        _stats.clear()
