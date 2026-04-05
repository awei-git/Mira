from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent
sys.path.insert(0, str(_SHARED))


class _FakeCursor:
    def __init__(self, fetchone_values=None, fetchall_values=None):
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_values = list(fetchall_values or [])
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.fetchone_values.pop(0) if self.fetchone_values else None

    def fetchall(self):
        return self.fetchall_values.pop(0) if self.fetchall_values else []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor_obj):
        self.cursor_obj = cursor_obj
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_add_link_scopes_duplicates_by_user(monkeypatch):
    import knowledge_links

    cursor = _FakeCursor(fetchone_values=[(1,), None])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(knowledge_links, "_get_conn", lambda: conn)
    monkeypatch.setattr(knowledge_links, "_ensure_table", lambda: True)
    monkeypatch.setattr(knowledge_links, "_has_user_id_column", lambda: True)

    created = knowledge_links.add_link("wiki", "a", "reading_note", "b", "related", user_id="liquan")

    assert created is False
    duplicate_sql, duplicate_params = cursor.calls[0]
    assert "user_id = %s" in duplicate_sql
    assert duplicate_params[0] == "liquan"


def test_get_links_filters_by_user(monkeypatch):
    import knowledge_links

    cursor = _FakeCursor(fetchall_values=[[("wiki", "page1", "related", 0.8, None)]])
    conn = _FakeConn(cursor)
    monkeypatch.setattr(knowledge_links, "_get_conn", lambda: conn)
    monkeypatch.setattr(knowledge_links, "_ensure_table", lambda: True)
    monkeypatch.setattr(knowledge_links, "_has_user_id_column", lambda: True)

    result = knowledge_links.get_links("reading_note", "note1", user_id="liquan")

    assert len(result) == 1
    sql, params = cursor.calls[0]
    assert "user_id = %s" in sql
    assert params[0] == "liquan"
