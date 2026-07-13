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


def test_ensure_migrated_uses_shared_migration_runner(monkeypatch):
    import agents.shared.migrations.run as migration_run
    import memory.store as memory_store

    store = memory_store.MemoryStore("postgresql://test")
    calls: list[str] = []

    monkeypatch.setattr(migration_run, "run_migrations", lambda: calls.append("migrations"))
    monkeypatch.setattr(store, "_ensure_joint_attention_columns", lambda: calls.append("joint_attention"))
    monkeypatch.setattr(store, "_ensure_trust_confidence_columns", lambda: calls.append("trust_confidence"))

    store._ensure_migrated()

    assert calls == ["migrations", "joint_attention", "trust_confidence"]
    assert store._migrated is True


def test_ensure_migrated_repairs_columns_when_runner_fails(monkeypatch):
    import agents.shared.migrations.run as migration_run
    import memory.store as memory_store

    store = memory_store.MemoryStore("postgresql://test")
    calls: list[str] = []

    def fail_migration():
        raise ModuleNotFoundError("migrations.run")

    monkeypatch.setattr(migration_run, "run_migrations", fail_migration)
    monkeypatch.setattr(store, "_ensure_joint_attention_columns", lambda: calls.append("joint_attention"))
    monkeypatch.setattr(store, "_ensure_trust_confidence_columns", lambda: calls.append("trust_confidence"))

    store._ensure_migrated()

    assert calls == ["joint_attention", "trust_confidence"]
    assert store._migrated is True
