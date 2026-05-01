from __future__ import annotations

import pytest

from locks.advisory import AdvisoryLockTimeout, LOCK_DISPATCH_LOOP, advisory_lock


class FakeCursor:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        if self.queries[-1][0].startswith("SELECT pg_try_advisory_lock"):
            return (self.responses.pop(0),)
        return (True,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, responses):
        self.cursor_obj = FakeCursor(responses)

    def cursor(self):
        return self.cursor_obj


def test_advisory_lock_acquires_and_releases():
    conn = FakeConn([True])

    with advisory_lock(LOCK_DISPATCH_LOOP, conn=conn, timeout_s=0):
        pass

    queries = [q for q, _ in conn.cursor_obj.queries]
    assert queries == ["SELECT pg_try_advisory_lock(%s)", "SELECT pg_advisory_unlock(%s)"]
    assert conn.cursor_obj.queries[0][1] == (LOCK_DISPATCH_LOOP,)


def test_advisory_lock_times_out_without_unlocking_unowned_lock():
    conn = FakeConn([False])

    with pytest.raises(AdvisoryLockTimeout):
        with advisory_lock("dispatch_loop", conn=conn, timeout_s=0):
            pass

    queries = [q for q, _ in conn.cursor_obj.queries]
    assert queries == ["SELECT pg_try_advisory_lock(%s)"]


def test_advisory_lock_requires_timeout():
    conn = FakeConn([True])

    with pytest.raises(ValueError):
        with advisory_lock("dispatch_loop", conn=conn, timeout_s=-1):
            pass
