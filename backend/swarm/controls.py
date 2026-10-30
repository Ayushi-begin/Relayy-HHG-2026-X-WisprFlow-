"""Operator controls the swarm consults on every call. Persisted by `agent_service`, held here
so the swarm never imports the database layer."""

import threading

_disabled: set[str] = set()
_lock = threading.Lock()

DISABLED_REASON = "model disabled by operator"


def set_model_enabled(agent: str, enabled: bool) -> None:
    with _lock:
        (_disabled.discard if enabled else _disabled.add)(agent)


def model_enabled(agent: str) -> bool:
    with _lock:
        return agent not in _disabled


def replace(disabled: set[str]) -> None:
    with _lock:
        _disabled.clear()
        _disabled.update(disabled)
