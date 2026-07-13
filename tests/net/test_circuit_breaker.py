"""Circuit breaker state machine + decorator."""

from __future__ import annotations

import time

import pytest

from net.circuit_breaker import (
    Circuit,
    CircuitOpen,
    CircuitState,
    circuit,
    get_circuit,
)


def _boom():
    raise RuntimeError("simulated provider failure")


def _ok():
    return "ok"


def test_closed_by_default_allows_calls():
    c = Circuit(name="t-closed", min_samples=3, error_rate_threshold=0.5)
    assert c.state is CircuitState.CLOSED
    assert c.call(_ok) == "ok"


def test_does_not_trip_before_min_samples():
    c = Circuit(name="t-few", min_samples=5, error_rate_threshold=0.5)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            c.call(_boom)
    # Still CLOSED because fewer than min_samples observations.
    assert c.state is CircuitState.CLOSED


def test_opens_after_threshold_exceeded():
    c = Circuit(name="t-trip", min_samples=4, error_rate_threshold=0.5, cooldown_seconds=60)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            c.call(_boom)
    assert c.state is CircuitState.OPEN
    # Subsequent calls short-circuit.
    with pytest.raises(CircuitOpen):
        c.call(_ok)


def test_half_open_probe_closes_on_success(monkeypatch):
    c = Circuit(name="t-recover", min_samples=3, error_rate_threshold=0.5, cooldown_seconds=30)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            c.call(_boom)
    assert c.state is CircuitState.OPEN

    # Fast-forward by stubbing time.monotonic in the module under test.
    import net.circuit_breaker as mod

    future = time.monotonic() + 31
    monkeypatch.setattr(mod.time, "monotonic", lambda: future)

    # Reading state should flip to HALF_OPEN
    assert c.state is CircuitState.HALF_OPEN

    # A successful trial closes the circuit and resets samples.
    assert c.call(_ok) == "ok"
    assert c.state is CircuitState.CLOSED
    snap = c.snapshot()
    assert snap["total_recent"] == 0


def test_half_open_failed_probe_reopens(monkeypatch):
    c = Circuit(name="t-reopen", min_samples=3, error_rate_threshold=0.5, cooldown_seconds=20)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            c.call(_boom)
    assert c.state is CircuitState.OPEN

    import net.circuit_breaker as mod

    t0 = time.monotonic()
    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 25)
    assert c.state is CircuitState.HALF_OPEN

    with pytest.raises(RuntimeError):
        c.call(_boom)
    # Back to OPEN with fresh cooldown.
    # (Use updated monotonic so state transitions deterministically.)
    monkeypatch.setattr(mod.time, "monotonic", lambda: t0 + 26)
    assert c.state is CircuitState.OPEN


def test_get_circuit_registers_and_reuses():
    a = get_circuit("reg-probe", min_samples=2)
    b = get_circuit("reg-probe")
    assert a is b


def test_circuit_decorator():
    @circuit("deco-demo", min_samples=3, error_rate_threshold=0.5)
    def flaky(should_fail):
        if should_fail:
            raise RuntimeError("fail")
        return 42

    flaky(False)
    with pytest.raises(RuntimeError):
        flaky(True)
    with pytest.raises(RuntimeError):
        flaky(True)
    # Threshold crossed: 2/3 failures ≥ 0.5. Circuit now OPEN.
    with pytest.raises(CircuitOpen):
        flaky(False)


def test_snapshot_reports_rates_and_state():
    c = Circuit(name="t-snap", min_samples=2, error_rate_threshold=0.99)
    c.call(_ok)
    with pytest.raises(RuntimeError):
        c.call(_boom)
    snap = c.snapshot()
    assert snap["name"] == "t-snap"
    assert 0 <= snap["error_rate"] <= 1
    assert snap["state"] in {"closed", "open", "half_open"}
