"""Circuit breaker — per-provider rolling-window trip + half-open probe.

Design rationale: baseline showed 91% of Mira ERRORs come from oMLX
endpoints (gemma-4 HTTP 507, Qwen3.5-27B timeout). A simple retry
policy just amplifies the load. A circuit breaker drops requests
fast when a provider is dying, lets other code take the `CircuitOpen`
signal and fall back gracefully, and self-recovers.

States:
    CLOSED:    normal operation. Every call goes through. Failures
               accumulate in the rolling window.
    OPEN:      calls short-circuit with CircuitOpen. No network
               attempted. Cooldown timer running.
    HALF_OPEN: cooldown elapsed; one trial request allowed. Success
               → CLOSED (stats reset). Failure → OPEN (new cooldown).

All per-provider state lives in a module-level dict keyed by name so
any caller can `get_circuit("substack")` and share state.

Usage:
    from net import get_circuit, CircuitOpen

    c = get_circuit("substack")
    try:
        response = c.call(lambda: do_network_thing())
    except CircuitOpen:
        # Provider tripped — fall back / skip / enqueue
        pass
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, TypeVar

log = logging.getLogger("mira.net.circuit")

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""


@dataclass
class _Sample:
    ts: float
    success: bool


@dataclass
class Circuit:
    name: str
    window_seconds: float = 300.0  # 5 min rolling window
    min_samples: int = 10  # below this, never trip
    error_rate_threshold: float = 0.5  # 50% errors in window → trip
    cooldown_seconds: float = 300.0  # 5 min before HALF_OPEN probe

    _samples: deque = field(default_factory=deque)
    _state: CircuitState = CircuitState.CLOSED
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- snapshotting for dashboards ------------------------------------

    @property
    def state(self) -> CircuitState:
        # Transition OPEN → HALF_OPEN lazily on read so callers don't
        # need a background tick.
        with self._lock:
            self._maybe_half_open_locked()
            return self._state

    def snapshot(self) -> dict:
        with self._lock:
            self._maybe_half_open_locked()
            total = len(self._samples)
            failures = sum(1 for s in self._samples if not s.success)
            return {
                "name": self.name,
                "state": self._state.value,
                "window_seconds": self.window_seconds,
                "total_recent": total,
                "failures_recent": failures,
                "error_rate": (failures / total) if total else 0.0,
                "opened_at": self._opened_at,
            }

    # ---- core call wrapper ----------------------------------------------

    def call(self, fn: Callable[[], T]) -> T:
        with self._lock:
            self._maybe_half_open_locked()
            state = self._state

        if state is CircuitState.OPEN:
            raise CircuitOpen(f"circuit '{self.name}' is OPEN")

        # CLOSED or HALF_OPEN → run the call outside the lock.
        try:
            result = fn()
        except Exception:
            self._record(success=False)
            raise
        else:
            self._record(success=True)
            return result

    # ---- internals ------------------------------------------------------

    def _record(self, *, success: bool) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append(_Sample(ts=now, success=success))
            cutoff = now - self.window_seconds
            while self._samples and self._samples[0].ts < cutoff:
                self._samples.popleft()

            if self._state is CircuitState.HALF_OPEN:
                if success:
                    self._state = CircuitState.CLOSED
                    self._samples.clear()
                    log.info("circuit '%s' recovered → CLOSED", self.name)
                else:
                    self._open_locked()
                return

            if self._state is CircuitState.CLOSED:
                if len(self._samples) < self.min_samples:
                    return
                failures = sum(1 for s in self._samples if not s.success)
                rate = failures / len(self._samples)
                if rate >= self.error_rate_threshold:
                    self._open_locked()

    def _open_locked(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        log.warning(
            "circuit '%s' tripped OPEN (samples=%d, cooldown=%ds)",
            self.name,
            len(self._samples),
            int(self.cooldown_seconds),
        )

    def _maybe_half_open_locked(self) -> None:
        if self._state is not CircuitState.OPEN:
            return
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            self._state = CircuitState.HALF_OPEN
            log.info("circuit '%s' cooldown elapsed → HALF_OPEN probe", self.name)


# ---------------------------------------------------------------------------
# Module-level registry
# ---------------------------------------------------------------------------

_registry: dict[str, Circuit] = {}
_registry_lock = threading.Lock()


def get_circuit(name: str, **kwargs) -> Circuit:
    """Return (and cache) the Circuit for a named provider."""
    with _registry_lock:
        c = _registry.get(name)
        if c is None:
            c = Circuit(name=name, **kwargs)
            _registry[name] = c
        return c


def circuit(name: str, **circuit_kwargs):
    """Decorator form: wrap a function so its calls go through a Circuit.

    The wrapped function may be a closure capturing network state.
    On OPEN, callers see `CircuitOpen` and can decide fallback policy;
    this decorator intentionally does NOT swallow the exception.
    """

    def _decorator(fn):
        from functools import wraps

        breaker = get_circuit(name, **circuit_kwargs)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return breaker.call(lambda: fn(*args, **kwargs))

        return wrapper

    return _decorator
