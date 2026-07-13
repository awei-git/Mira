"""Throttled log helpers — dedup semantics + prefix on re-emit."""

from __future__ import annotations

import logging

import pytest

from logging_util import throttled_warning
from logging_util.throttle import reset, suppressed_stats


@pytest.fixture(autouse=True)
def _clear_cache():
    reset()
    yield
    reset()


def test_first_call_always_emits(caplog):
    caplog.set_level(logging.WARNING)
    log = logging.getLogger("test.throttle.a")
    throttled_warning(log, "hello", key="k1", interval_seconds=60)
    assert "hello" in caplog.text


def test_duplicate_inside_window_suppressed(caplog):
    caplog.set_level(logging.WARNING)
    log = logging.getLogger("test.throttle.b")
    throttled_warning(log, "one", key="k2", interval_seconds=60)
    caplog.clear()
    throttled_warning(log, "one", key="k2", interval_seconds=60)
    assert "one" not in caplog.text


def test_suppressed_count_prefix_on_reemit(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    log = logging.getLogger("test.throttle.c")
    import logging_util.throttle as mod

    t = [100.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])
    throttled_warning(log, "msg", key="k3", interval_seconds=60)
    for _ in range(5):
        throttled_warning(log, "msg", key="k3", interval_seconds=60)
    assert suppressed_stats()["test.throttle.c:k3"] == 5

    t[0] = 200.0
    caplog.clear()
    throttled_warning(log, "msg", key="k3", interval_seconds=60)
    assert "(×5 suppressed since last) msg" in caplog.text
    assert suppressed_stats()["test.throttle.c:k3"] == 0


def test_different_keys_tracked_separately(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    log = logging.getLogger("test.throttle.d")
    throttled_warning(log, "a", key="ka", interval_seconds=60)
    throttled_warning(log, "b", key="kb", interval_seconds=60)
    assert "a" in caplog.text and "b" in caplog.text


def test_args_substitution_works(caplog):
    caplog.set_level(logging.WARNING)
    log = logging.getLogger("test.throttle.e")
    throttled_warning(log, "value=%d", 42, key="e", interval_seconds=60)
    assert "value=42" in caplog.text
