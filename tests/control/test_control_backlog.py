from __future__ import annotations


class FakeAuditCursor:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return (42,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self):
        self.cursor_obj = FakeAuditCursor()

    def cursor(self):
        return self.cursor_obj


class FakeDictCursor:
    def __init__(self, *, one=None, many=None):
        self.one = list(one or [])
        self.many = list(many or [])
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        if not self.one:
            return None
        return self.one.pop(0)

    def fetchall(self):
        return self.many

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_enqueue_request_verify_marks_verified_when_task_verifier_passed(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    row = {
        "id": "request_verify:task_verified",
        "user_id": "ang",
        "task_id": "task_verified",
        "kind": "request_verify",
        "executor": "request_verify.apply",
        "status": "verified",
        "priority": "medium",
        "payload": {
            "task_type": "file_artifact",
            "verification": {"verified": True},
        },
    }
    cursor = FakeDictCursor(one=[row])
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    item = repo.enqueue_request_verify(
        {
            "id": "task_verified",
            "user_id": "ang",
            "status": "verified",
            "title": "Build the report",
            "task_type": "file_artifact",
            "outcome_verified": True,
            "verification": {
                "verified": True,
                "summary": "output.md exists",
                "expected_observable_outcome": "requested artifact exists",
            },
        }
    )

    assert item == row
    assert cursor.queries[0][1]["status"] == "verified"
    assert cursor.queries[0][1]["verification_summary"] == "output.md exists"


def test_enqueue_request_verify_leaves_unverified_task_in_backlog(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    cursor = FakeDictCursor(one=[{"id": "request_verify:task_unverified", "status": "proposed"}])
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    repo.enqueue_request_verify(
        {
            "id": "task_unverified",
            "user_id": "ang",
            "status": "completed_unverified",
            "title": "Draft something",
            "outcome_verified": False,
            "verification": {"verified": False, "summary": "semantic intent not checked"},
        }
    )

    params = cursor.queries[0][1]
    assert params["status"] == "proposed"
    assert params["priority"] == "high"
    assert params["last_error"] == "semantic intent not checked"


def test_claim_backlog_item(monkeypatch):
    import control.repository as repository
    from control.repository import ControlRepository

    claimed = {"id": "request_verify:t1", "task_id": "t1", "user_id": "ang", "status": "in_progress"}
    cursor = FakeDictCursor(
        one=[
            {"id": "request_verify:t1"},
            claimed,
        ]
    )
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(FakeConn())
    assert repo.claim_backlog_item("request_verify.apply") == claimed
    assert "FOR UPDATE SKIP LOCKED" in cursor.queries[0][0]


def test_claim_task_for_dispatch_is_atomic(monkeypatch):
    from control.repository import ControlRepository

    events = []
    conn = FakeConn()
    repo = ControlRepository(conn)
    monkeypatch.setattr(repo, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))

    assert repo.claim_task_for_dispatch("ang", "req_1") is True

    query, params = conn.cursor_obj.queries[0]
    assert "status = 'dispatched'" in query
    assert "AND status = 'queued'" in query
    assert "AND origin = 'user'" in query
    assert params[-2:] == ("req_1", "ang")
    assert events[0][0][:3] == ("req_1", "ang", "task.dispatch_claimed")


def test_release_dispatch_claim_only_requeues_unstarted_claim(monkeypatch):
    from control.repository import ControlRepository

    events = []
    conn = FakeConn()
    repo = ControlRepository(conn)
    monkeypatch.setattr(repo, "_record_event", lambda *args, **kwargs: events.append((args, kwargs)))

    repo.release_dispatch_claim("ang", "req_1", reason="spawn failed")

    query, params = conn.cursor_obj.queries[0]
    assert "status = 'queued'" in query
    assert "status = 'dispatched'" in query
    assert "worker_pid IS NULL" in query
    assert params[-2:] == ("req_1", "ang")
    assert events[0][0][:3] == ("req_1", "ang", "task.dispatch_claim_released")
    assert events[0][1]["payload"] == {"reason": "spawn failed"}
