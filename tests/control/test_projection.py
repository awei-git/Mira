from __future__ import annotations

from control.projection import app_status, item_from_rows
from control.repository import ControlRepository, _is_human_review_draft


def test_app_status_maps_runtime_statuses_to_app_surface():
    assert app_status("dispatched") == "working"
    assert app_status("running") == "working"
    assert app_status("completed") == "done"
    assert app_status("error") == "failed"
    assert app_status("paused_horizon_limit") == "needs-input"
    assert app_status("blocked") == "failed"
    assert app_status("timeout") == "failed"
    assert app_status("surprise-status") == "failed"


def test_item_projection_matches_mira_item_shape():
    item = item_from_rows(
        {
            "id": "req_123",
            "type": "request",
            "title": "Test task",
            "status": "failed",
            "tags": ["test"],
            "origin": "user",
            "pinned": False,
            "quick": False,
            "parent_id": None,
            "created_at": "2026-04-30T00:00:00Z",
            "updated_at": "2026-04-30T00:01:00Z",
            "error_code": "worker_crash",
            "error_message": "Worker exited",
            "retryable": True,
            "completed_at": "2026-04-30T00:01:00Z",
            "result_path": None,
        },
        [
            {
                "id": "m1",
                "sender": "ang",
                "content": "please run",
                "kind": "text",
                "created_at": "2026-04-30T00:00:00Z",
            }
        ],
    )

    assert item["id"] == "req_123"
    assert item["status"] == "failed"
    assert item["messages"][0]["content"] == "please run"
    assert item["error"] == {
        "code": "worker_crash",
        "message": "Worker exited",
        "retryable": True,
        "timestamp": "2026-04-30T00:01:00Z",
    }


def test_list_items_limits_messages_per_item(monkeypatch):
    class FakeCursor:
        def __init__(self):
            self.calls = 0

        def execute(self, *args, **kwargs):
            self.calls += 1

        def fetchall(self):
            if self.calls == 1:
                return [
                    {
                        "id": "req_1",
                        "type": "request",
                        "title": "Task",
                        "status": "done",
                        "origin": "user",
                        "tags": [],
                        "quick": False,
                        "pinned": False,
                        "parent_id": None,
                        "created_at": "2026-05-01T00:00:00Z",
                        "updated_at": "2026-05-01T00:00:00Z",
                        "error_message": None,
                        "result_path": None,
                    }
                ]
            return [
                {
                    "id": f"msg_{idx}",
                    "task_id": "req_1",
                    "sender": "agent",
                    "kind": "text",
                    "content": str(idx),
                    "image_path": None,
                    "created_at": f"2026-05-01T00:00:0{idx}Z",
                }
                for idx in range(5)
            ]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    import control.repository as repository

    cursor = FakeCursor()
    monkeypatch.setattr(repository, "dict_cursor", lambda conn: cursor)

    repo = ControlRepository(object())
    items = repo.list_items("ang", messages_per_item=2)

    assert [m["id"] for m in items[0]["messages"]] == ["msg_3", "msg_4"]


def test_human_review_draft_detection():
    assert _is_human_review_draft("x_reply_abc123")
    assert _is_human_review_draft("draft_123", {"tags": ["socialmedia", "needs-human"]})
    assert not _is_human_review_draft("req_abc123", {"tags": ["todo"]})


def test_list_dispatchable_escapes_literal_percent_for_psycopg(monkeypatch):
    class FakeCursor:
        def execute(self, query, params):
            assert "x_reply_%%" in query

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    import control.repository as repository

    monkeypatch.setattr(repository, "dict_cursor", lambda conn: FakeCursor())

    repo = ControlRepository(object())
    assert repo.list_dispatchable_tasks(limit=1) == []
