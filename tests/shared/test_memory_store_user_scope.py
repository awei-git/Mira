from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent


def test_remember_includes_user_id(monkeypatch):
    import memory.store as memory_store

    monkeypatch.setattr(memory_store, "_embed_texts", lambda texts: [[0.1, 0.2, 0.3]])
    store = memory_store.MemoryStore("postgresql://test")
    calls: list[tuple[str, tuple, bool]] = []

    def fake_execute(sql: str, params: tuple = (), fetch: bool = False):
        calls.append((sql, params, fetch))
        return [(7,)] if fetch else None

    monkeypatch.setattr(store, "_execute", fake_execute)

    row_id = store.remember("hello", source_type="episode", user_id="liquan")

    assert row_id == 7
    assert "user_id" in calls[0][0]
    assert calls[0][1][0] == "liquan"


def test_search_table_filters_by_user_id(monkeypatch):
    import memory.store as memory_store

    store = memory_store.MemoryStore("postgresql://test")
    calls: list[tuple[str, tuple, bool]] = []

    def fake_execute(sql: str, params: tuple = (), fetch: bool = False):
        calls.append((sql, params, fetch))
        return []

    monkeypatch.setattr(store, "_execute", fake_execute)

    results = store._search_table("episodic_memory", "hello", [], None, True, "liquan", 5)

    assert results == []
    assert "user_id = %s" in calls[0][0]
    assert "liquan" in calls[0][1]


def test_store_thought_includes_user_id(monkeypatch):
    import memory.store as memory_store

    monkeypatch.setattr(memory_store, "_embed_texts", lambda texts: [[0.1, 0.2, 0.3]])
    store = memory_store.MemoryStore("postgresql://test")
    calls: list[tuple[str, tuple, bool]] = []

    def fake_execute(sql: str, params: tuple = (), fetch: bool = False):
        calls.append((sql, params, fetch))
        return [(11,)] if fetch else None

    monkeypatch.setattr(store, "_execute", fake_execute)

    row_id = store.store_thought("idea", "connection", user_id="liquan")

    assert row_id == 11
    assert "user_id" in calls[0][0]
    assert calls[0][1][0] == "liquan"


def test_conn_property_returns_live_connection(monkeypatch):
    import memory.store as memory_store

    store = memory_store.MemoryStore("postgresql://test")
    sentinel = object()
    monkeypatch.setattr(store, "_get_conn", lambda: sentinel)

    assert store.conn is sentinel
