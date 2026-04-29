"""Network-level reliability primitives — Phase 0 pillar 3.

Two independent mechanisms that external-IO call sites wrap with:

- `circuit_breaker.Circuit` — state machine per provider (CLOSED /
  OPEN / HALF_OPEN). Trips when error rate crosses threshold inside
  a rolling window; cools down; probes with a single request.
- `idempotent.cached_call` — key→response cache keyed on a caller-
  chosen idempotency_key, TTL 7d. Replays avoid duplicate external
  writes.

Both modules are pure-Python stdlib (no new deps) and safe to use
from any thread / subprocess.
"""

from .circuit_breaker import (
    Circuit,
    CircuitState,
    CircuitOpen,
    get_circuit,
    circuit,
)
from .idempotent import cached_call, clear_expired

__all__ = [
    "Circuit",
    "CircuitState",
    "CircuitOpen",
    "get_circuit",
    "circuit",
    "cached_call",
    "clear_expired",
]
