from __future__ import annotations

import json

from control.audit import AuditLogger


class FakeCursor:
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
        self.cursor_obj = FakeCursor()

    def cursor(self):
        return self.cursor_obj


def test_audit_logger_writes_postgres_and_jsonl(tmp_path):
    conn = FakeConn()
    jsonl = tmp_path / "audit" / "events.jsonl"

    event = AuditLogger(conn, jsonl_path=jsonl).append(
        "task.created",
        task_id="req_1",
        workflow_id="req_1",
        user_id="default",
        payload={"status": "queued"},
    )

    assert event["event_id"] == "42"
    assert event["type"] == "task.created"
    assert conn.cursor_obj.queries
    line = json.loads(jsonl.read_text(encoding="utf-8"))
    assert line["task_id"] == "req_1"
    assert line["payload"] == {"status": "queued"}
