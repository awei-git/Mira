from __future__ import annotations

from datetime import datetime, timezone


class FakeCursor:
    def __init__(self, duplicate: bool):
        self.duplicate = duplicate
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.statements.append((sql, params))

    def fetchone(self):
        return (1,) if self.duplicate else None


class FakeConnection:
    closed = False

    def __init__(self, duplicate: bool):
        self.cursor_obj = FakeCursor(duplicate)

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


def test_insert_metric_skips_exact_duplicate():
    from agents.health.health_store import HealthStore

    store = HealthStore("postgresql://test")
    store._conn = FakeConnection(duplicate=True)

    inserted = store.insert_metric(
        "default",
        "readiness_score",
        66,
        source="oura",
        recorded_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )

    assert inserted is False
    assert len(store._conn.cursor_obj.statements) == 1


def test_insert_metric_returns_true_for_new_row():
    from agents.health.health_store import HealthStore

    store = HealthStore("postgresql://test")
    store._conn = FakeConnection(duplicate=False)

    inserted = store.insert_metric(
        "default",
        "readiness_score",
        66,
        source="oura",
        recorded_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
    )

    assert inserted is True
    assert len(store._conn.cursor_obj.statements) == 2
