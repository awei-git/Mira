"""cached_call + clear_expired semantics."""

from __future__ import annotations

import time

import pytest

from net.idempotent import cached_call, cached_value, clear_expired


def test_first_call_runs_fn_second_call_returns_cache(tmp_path):
    db = tmp_path / "idem.db"
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return {"x": 1}

    a = cached_call(key="pub:1", fn=fn, ttl_seconds=60, path=db)
    b = cached_call(key="pub:1", fn=fn, ttl_seconds=60, path=db)
    assert a == {"x": 1}
    assert b == {"x": 1}
    assert calls["n"] == 1  # second call short-circuits


def test_different_keys_run_fn_separately(tmp_path):
    db = tmp_path / "idem.db"
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return calls["n"]

    cached_call(key="a", fn=fn, ttl_seconds=60, path=db)
    cached_call(key="b", fn=fn, ttl_seconds=60, path=db)
    assert calls["n"] == 2


def test_expired_entry_gets_recomputed(tmp_path):
    db = tmp_path / "idem.db"
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return calls["n"]

    cached_call(key="k", fn=fn, ttl_seconds=0.001, path=db)
    time.sleep(0.01)
    cached_call(key="k", fn=fn, ttl_seconds=60, path=db)
    assert calls["n"] == 2


def test_clear_expired_removes_only_expired(tmp_path):
    db = tmp_path / "idem.db"
    cached_call(key="fresh", fn=lambda: "x", ttl_seconds=60, path=db)
    cached_call(key="stale", fn=lambda: "y", ttl_seconds=0.001, path=db)
    time.sleep(0.01)
    removed = clear_expired(path=db)
    assert removed >= 1
    assert cached_value("fresh", path=db) == "x"
    assert cached_value("stale", path=db) is None


def test_fn_exception_bubbles_and_is_not_cached(tmp_path):
    db = tmp_path / "idem.db"
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise RuntimeError("network fail")

    with pytest.raises(RuntimeError):
        cached_call(key="err", fn=fn, ttl_seconds=60, path=db)
    # Retry should call again — failures are not cached.
    with pytest.raises(RuntimeError):
        cached_call(key="err", fn=fn, ttl_seconds=60, path=db)
    assert calls["n"] == 2


def test_unserializable_result_returns_uncached_but_does_not_raise(tmp_path):
    db = tmp_path / "idem.db"

    class Opaque:
        pass

    result = cached_call(key="opaque", fn=Opaque, ttl_seconds=60, path=db)
    assert isinstance(result, Opaque)
    # Next call invokes fn again since nothing was cached.
    result2 = cached_call(key="opaque", fn=Opaque, ttl_seconds=60, path=db)
    assert isinstance(result2, Opaque)
    assert result is not result2
