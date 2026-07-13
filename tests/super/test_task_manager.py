from __future__ import annotations

import json
import sys
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace

_AGENTS = Path(__file__).resolve().parent.parent.parent / "agents"


def test_talk_slug_uses_full_message_id_hash():
    import talk

    first = talk._talk_slug("今天发的哈贝马斯的读书笔记", "req_todo_1cacf0e3")
    second = talk._talk_slug("今天发的哈贝马斯的读书笔记", "req_todo_0ffe291c")

    assert first != second
    assert not first.endswith("_req_to")
    assert not second.endswith("_req_to")


def test_x_reply_drafts_are_not_dispatched_as_tasks(tmp_path):
    import talk

    class FakeTaskManager:
        def __init__(self):
            self.dispatched = False

        def is_busy(self):
            return False

        def get_active_count(self):
            return 0

        def dispatch(self, *args, **kwargs):
            self.dispatched = True
            return "x_reply_123"

    class FakeBridge:
        def __init__(self):
            self.statuses = []

        def update_status(self, item_id, status, **kwargs):
            self.statuses.append((item_id, status))

    mgr = FakeTaskManager()
    bridge = FakeBridge()
    msg = SimpleNamespace(id="x_reply_123", sender="user", content="回复草稿", thread_id="x_reply_123")

    result = talk._dispatch_or_requeue(mgr, bridge, msg, tmp_path / "workspace")

    assert result == "ok"
    assert mgr.dispatched is False
    assert bridge.statuses == [("x_reply_123", "needs-input")]


def test_terminal_projection_writes_agent_message_to_control_db(monkeypatch):
    import talk
    import control.db
    import control.repository

    calls = []

    @contextmanager
    def fake_transaction():
        yield object()

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def update_task_status(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(talk, "CONTROL_RUNTIME_DB_ENABLED", True)
    monkeypatch.setattr(control.db, "transaction", fake_transaction)
    monkeypatch.setattr(control.repository, "ControlRepository", FakeRepo)

    rec = SimpleNamespace(task_id="disc_123", user_id="default", summary="done")
    talk._project_record_to_control_db(rec, "done", agent_message="final answer")

    assert calls == [
        (
            ("default", "disc_123", "done"),
            {
                "summary": "done",
                "error_code": None,
                "error_message": None,
                "agent_message": "final answer",
                "message_kind": "text",
                "verification": None,
                "task_type": None,
                "outcome_verified": None,
                "verification_method": None,
            },
        )
    ]


def test_daily_collab_projection_omits_status_footer(monkeypatch):
    import talk

    class FakeTaskManager:
        def get_reply_content(self, rec):
            return "one conversational reply"

        def get_status_summary(self):
            raise AssertionError("conversation projection should not append status footer")

    class FakeBridge:
        def __init__(self):
            self.updated = []
            self.tags = []

        def update_status(self, item_id, status, agent_message="", error=None):
            self.updated.append((item_id, status, agent_message, error))

        def set_tags(self, item_id, tags):
            self.tags.append((item_id, tags))

    monkeypatch.setattr(talk, "CONTROL_RUNTIME_DB_ENABLED", False)
    rec = SimpleNamespace(
        task_id="disc_daily_collab",
        status="completed_unverified",
        tags=["daily-collab", "mira", "conversation"],
    )

    bridge = FakeBridge()
    talk._project_record_to_bridge(bridge, FakeTaskManager(), rec)

    assert bridge.updated == [("disc_daily_collab", "done", "one conversational reply", None)]
    assert bridge.tags == [("disc_daily_collab", ["daily-collab", "mira", "conversation"])]


def test_dispatch_records_message_user_id(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "WORKER_SCRIPT", tmp_path / "task_worker.py")

    class FakeProcess:
        pid = 4321

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    mgr = task_manager.TaskManager()
    msg = SimpleNamespace(
        id="req_123",
        thread_id="req_123",
        sender="user",
        content="hello",
        user_id="liquan",
        to_dict=lambda: {
            "id": "req_123",
            "thread_id": "req_123",
            "sender": "user",
            "content": "hello",
            "user_id": "liquan",
        },
    )

    task_id = mgr.dispatch(msg, tmp_path / "workspace")

    assert task_id == "req_123"
    assert mgr._records[0].user_id == "liquan"
    assert mgr._records[0].workflow_id == "req_123"
    assert mgr._records[0].attempt_count == 1
    assert mgr._records[0].max_attempts >= 1
    assert (Path(mgr._records[0].workspace) / ".task_id").read_text(encoding="utf-8").strip() == "req_123"


def test_dispatch_persists_message_tags(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "WORKER_SCRIPT", tmp_path / "task_worker.py")

    class FakeProcess:
        pid = 4322

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    mgr = task_manager.TaskManager()
    msg = SimpleNamespace(
        id="disc_daily_collab",
        thread_id="disc_daily_collab",
        sender="default",
        content="one small thought",
        user_id="default",
        tags=["daily-collab", "mira", "conversation"],
        to_dict=lambda: {
            "id": "disc_daily_collab",
            "thread_id": "disc_daily_collab",
            "sender": "default",
            "content": "one small thought",
            "user_id": "default",
        },
    )

    task_id = mgr.dispatch(msg, tmp_path / "workspace")

    assert task_id == "disc_daily_collab"
    workspace = Path(mgr._records[0].workspace)
    payload = json.loads((workspace / "message.json").read_text(encoding="utf-8"))
    assert payload["tags"] == ["daily-collab", "mira", "conversation"]
    assert mgr._records[0].tags == ["daily-collab", "mira", "conversation"]


def test_load_status_backfills_missing_user_id(monkeypatch, tmp_path):
    import task_manager

    status_file = tmp_path / "tasks" / "status.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            [
                {
                    "task_id": "req_legacy",
                    "msg_id": "req_legacy",
                    "thread_id": "req_legacy",
                    "sender": "user",
                    "content_preview": "legacy",
                    "pid": 1,
                    "status": "done",
                    "started_at": "2026-04-05T00:00:00Z",
                    "completed_at": "2026-04-05T00:05:00Z",
                    "workspace": "",
                    "summary": "",
                    "tags": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", status_file)
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()

    assert len(mgr._records) == 1
    assert mgr._records[0].user_id == "default"
    assert mgr._records[0].workflow_id == "req_legacy"
    assert mgr._records[0].attempt_count == 1
    assert mgr._records[0].failure_class == ""
    assert mgr._records[0].verification is None
    assert mgr._records[0].outcome_verified is False


def test_dispatch_allows_explicit_retry_attempts(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "WORKER_SCRIPT", tmp_path / "task_worker.py")

    class FakeProcess:
        pid = 9876

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    mgr = task_manager.TaskManager()
    msg = SimpleNamespace(
        id="req_retry",
        thread_id="req_retry",
        sender="user",
        content="retry me",
        user_id="default",
        to_dict=lambda: {
            "id": "req_retry",
            "thread_id": "req_retry",
            "sender": "user",
            "content": "retry me",
            "user_id": "default",
        },
    )

    mgr.dispatch(msg, tmp_path / "workspace", attempt_count=2, max_attempts=3)

    assert mgr._records[0].attempt_count == 2
    assert mgr._records[0].max_attempts == 3
    assert mgr._records[0].workflow_id == "req_retry"


def test_dispatch_avoids_unowned_stale_workspace(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "WORKER_SCRIPT", tmp_path / "task_worker.py")

    class FakeProcess:
        pid = 2468

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    stale_workspace = tmp_path / "same-human-slug_req_to"
    stale_workspace.mkdir()
    (stale_workspace / "progress.md").write_text("# Progress — req_old\n\nstale", encoding="utf-8")

    mgr = task_manager.TaskManager()
    msg = SimpleNamespace(
        id="req_todo_1cacf0e3",
        thread_id="req_todo_1cacf0e3",
        sender="user",
        content="new task",
        user_id="default",
        to_dict=lambda: {
            "id": "req_todo_1cacf0e3",
            "thread_id": "req_todo_1cacf0e3",
            "sender": "user",
            "content": "new task",
            "user_id": "default",
        },
    )

    mgr.dispatch(msg, stale_workspace)

    actual_workspace = Path(mgr._records[0].workspace)
    assert actual_workspace != stale_workspace
    assert actual_workspace.name.startswith(stale_workspace.name + "_")
    assert (actual_workspace / ".task_id").read_text(encoding="utf-8").strip() == "req_todo_1cacf0e3"


def test_can_retry_and_reset_for_retry_return_removed_record(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_fail",
        workflow_id="req_fail",
        msg_id="req_fail",
        thread_id="req_fail",
        sender="user",
        content_preview="failed",
        pid=123,
        status="failed",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(tmp_path / "workspace"),
        attempt_count=1,
        max_attempts=2,
    )
    mgr._records = [rec]
    mgr._save_status()

    assert mgr.can_retry(rec) is True
    removed = mgr.reset_for_retry("req_fail")
    assert removed is not None
    assert removed.task_id == "req_fail"
    assert removed.attempt_count == 1
    assert mgr._records == []


def test_can_retry_respects_retry_ceiling(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_fail",
        workflow_id="req_fail",
        msg_id="req_fail",
        thread_id="req_fail",
        sender="user",
        content_preview="failed",
        pid=123,
        status="failed",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(tmp_path / "workspace"),
        attempt_count=2,
        max_attempts=2,
    )

    assert mgr.can_retry(rec) is False


def test_check_tasks_records_timeout_alert_once(monkeypatch, tmp_path):
    import sys
    import types
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "_resolve_timeout", lambda tags: 1)

    created = []

    class FakeBridge:
        def __init__(self, root, user_id="default"):
            self.user_id = user_id

        def create_item(self, *args, **kwargs):
            created.append((args, kwargs))

        def get_item(self, item_id):
            return None

    fake_mira = types.SimpleNamespace(Mira=FakeBridge)
    monkeypatch.setitem(sys.modules, "bridge", fake_mira)

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_timeout",
        workflow_id="req_timeout",
        msg_id="req_timeout",
        thread_id="req_timeout",
        sender="user",
        content_preview="long running",
        pid=99999,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(tmp_path / "workspace"),
    )
    mgr._records = [rec]
    monkeypatch.setattr(task_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(task_manager, "_pid_matches_worker", lambda pid, task_id: True)

    mgr.check_tasks()
    assert len(created) == 1
    assert mgr._records[0].timeout_alerted_at
    first_alerted_at = mgr._records[0].timeout_alerted_at

    mgr.check_tasks()
    assert len(created) == 1
    assert mgr._records[0].timeout_alerted_at == first_alerted_at


def test_collect_result_normalizes_error_status_to_failed(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.json").write_text(
        json.dumps(
            {
                "task_id": "req_fail",
                "workflow_id": "req_fail",
                "status": "error",
                "summary": "boom",
                "completed_at": "2026-04-05T00:05:00Z",
            }
        ),
        encoding="utf-8",
    )

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_fail",
        workflow_id="req_fail",
        msg_id="req_fail",
        thread_id="req_fail",
        sender="user",
        content_preview="failed",
        pid=123,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
    )

    mgr._collect_result(rec)

    assert rec.status == "failed"


def test_collect_result_treats_legacy_output_fallback_as_unverified(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "output.md").write_text("real output", encoding="utf-8")

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_done",
        workflow_id="req_done",
        msg_id="req_done",
        thread_id="req_done",
        sender="user",
        content_preview="done",
        pid=123,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
    )

    mgr._collect_result(rec)

    assert rec.status == "completed_unverified"


def test_unresolved_inventory_skips_completed_conversation(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()
    mgr._records = [
        task_manager.TaskRecord(
            task_id="disc_daily_collab",
            workflow_id="disc_daily_collab",
            msg_id="disc_daily_collab",
            thread_id="disc_daily_collab",
            sender="default",
            content_preview="chat",
            pid=1,
            status="completed_unverified",
            started_at="2026-07-01T03:49:38Z",
            completed_at="2026-07-01T03:50:11Z",
            tags=["daily-collab", "mira", "conversation"],
            summary="No footer now.",
        ),
        task_manager.TaskRecord(
            task_id="req_failed",
            workflow_id="req_failed",
            msg_id="req_failed",
            thread_id="req_failed",
            sender="default",
            content_preview="failed",
            pid=2,
            status="failed",
            started_at="2026-07-01T03:00:00Z",
            completed_at="2026-07-01T03:01:00Z",
            summary="real failure",
            failure_class="worker_crash",
        ),
    ]

    inventory = mgr.get_unresolved_inventory()

    assert inventory["count"] == 1
    assert inventory["tasks"][0]["task_id"] == "req_failed"


def test_park_task_preserves_record_but_removes_from_unresolved_inventory(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "CONTROL_RUNTIME_DB_ENABLED", False)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_old_failure",
        workflow_id="req_old_failure",
        msg_id="req_old_failure",
        thread_id="req_old_failure",
        sender="default",
        content_preview="old failure",
        pid=123,
        status="failed",
        started_at="2026-06-26T14:40:00Z",
        completed_at="2026-06-26T14:42:07Z",
        workspace=str(workspace),
        summary="Worker crashed",
        failure_class="worker_crash",
        attempt_count=2,
        max_attempts=2,
    )
    mgr._records = [rec]
    mgr._save_status()

    parked = mgr.park_task("req_old_failure", reason="Reviewed and parked")

    assert parked is rec
    assert rec.status == "parked"
    assert rec.failure_class == "worker_crash"
    assert rec.summary == "Reviewed and parked"
    assert mgr.can_retry(rec) is False
    assert mgr.find_failed_task("req_old_failure") is rec
    assert mgr.get_unresolved_inventory()["count"] == 0
    assert "completed_at" in json.loads((workspace / "metadata.json").read_text(encoding="utf-8"))
    history_lines = (tmp_path / "tasks" / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(history_lines[-1])["status"] == "parked"


def test_check_tasks_skips_parked_records(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "CONTROL_RUNTIME_DB_ENABLED", False)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_parked",
        workflow_id="req_parked",
        msg_id="req_parked",
        thread_id="req_parked",
        sender="default",
        content_preview="parked",
        pid=999999,
        status="parked",
        started_at="2026-06-26T14:40:00Z",
        completed_at="2026-06-26T14:42:07Z",
        workspace=str(workspace),
        summary="Reviewed and parked",
        failure_class="worker_crash",
    )
    mgr._records = [rec]

    completed = mgr.check_tasks()

    assert completed == []
    assert rec.status == "parked"
    assert rec.summary == "Reviewed and parked"


def test_collect_result_preserves_verification_metadata(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    verification = {"verified": True, "task_type": "writing", "summary": "ok"}
    (workspace / "result.json").write_text(
        json.dumps(
            {
                "task_id": "req_verified",
                "workflow_id": "req_verified",
                "status": "verified",
                "summary": "done",
                "completed_at": "2026-04-05T00:05:00Z",
                "task_type": "writing",
                "verification": verification,
                "outcome_verified": True,
                "verification_method": "file_exists",
            }
        ),
        encoding="utf-8",
    )

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_verified",
        workflow_id="req_verified",
        msg_id="req_verified",
        thread_id="req_verified",
        sender="user",
        content_preview="done",
        pid=123,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
    )

    mgr._collect_result(rec)

    assert rec.status == "verified"
    assert rec.task_type == "writing"
    assert rec.verification == verification
    assert rec.outcome_verified is True
    assert rec.verification_method == "file_exists"


def test_collect_result_merges_result_tags_with_routing_tags(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.json").write_text(
        json.dumps(
            {
                "task_id": "disc_daily_collab",
                "status": "completed_unverified",
                "summary": "done",
                "completed_at": "2026-04-05T00:05:00Z",
                "tags": ["daily collab", "AI narratives"],
            }
        ),
        encoding="utf-8",
    )

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="disc_daily_collab",
        workflow_id="disc_daily_collab",
        msg_id="disc_daily_collab",
        thread_id="disc_daily_collab",
        sender="default",
        content_preview="collab",
        pid=123,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
        tags=["daily-collab", "mira", "conversation"],
    )

    mgr._collect_result(rec)

    assert rec.tags == ["daily-collab", "mira", "conversation", "daily collab", "AI narratives"]


def test_daily_collab_reply_omits_verification_receipt(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "output.md").write_text("one conversational reply", encoding="utf-8")
    (workspace / "result.json").write_text(
        json.dumps(
            {
                "status": "completed_unverified",
                "outcome_verified": False,
                "verification": {"summary": "not required"},
            }
        ),
        encoding="utf-8",
    )

    rec = task_manager.TaskRecord(
        task_id="disc_daily_collab",
        workflow_id="disc_daily_collab",
        msg_id="disc_daily_collab",
        thread_id="disc_daily_collab",
        sender="default",
        content_preview="collab",
        pid=123,
        status="completed_unverified",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
        tags=["daily-collab", "mira", "conversation"],
    )

    reply = task_manager.TaskManager().get_reply_content(rec)

    assert reply == "one conversational reply"


def test_check_tasks_wait_reply_keeps_running_status(monkeypatch, tmp_path):
    import sys
    import types
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "_resolve_timeout", lambda tags: 1)

    class FakeBridge:
        def __init__(self, root, user_id="default"):
            self.user_id = user_id

        def create_item(self, *args, **kwargs):
            pass

        def get_item(self, item_id):
            return {
                "messages": [{"sender": "user", "content": "wait"}],
            }

    fake_mira = types.SimpleNamespace(Mira=FakeBridge)
    monkeypatch.setitem(sys.modules, "bridge", fake_mira)

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_timeout_wait",
        workflow_id="req_timeout_wait",
        msg_id="req_timeout_wait",
        thread_id="req_timeout_wait",
        sender="user",
        content_preview="long running",
        pid=99999,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(tmp_path / "workspace"),
    )
    mgr._records = [rec]
    monkeypatch.setattr(task_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(task_manager, "_pid_matches_worker", lambda pid, task_id: True)

    completed = mgr.check_tasks()

    assert completed == []
    assert mgr._records[0].status == "running"
    assert mgr._records[0].timeout_alerted_at


def test_check_tasks_does_not_trust_reused_pid(monkeypatch, tmp_path):
    import subprocess
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "CONTROL_RUNTIME_DB_ENABLED", False)
    monkeypatch.setattr(task_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        task_manager.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="/usr/bin/other-process\n"),
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "worker_stderr.log").write_text("Traceback\nboom\n", encoding="utf-8")
    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_reused_pid",
        workflow_id="req_reused_pid",
        msg_id="req_reused_pid",
        thread_id="req_reused_pid",
        sender="user",
        content_preview="crash",
        pid=12345,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
        max_attempts=1,
    )
    mgr._records = [rec]

    completed = mgr.check_tasks()

    assert completed == [rec]
    assert rec.status == "failed"
    assert rec.failure_class == "worker_crash"


def test_worker_crash_auto_retries_from_message_json(monkeypatch, tmp_path):
    import subprocess
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "WORKER_SCRIPT", tmp_path / "task_worker.py")
    monkeypatch.setattr(task_manager, "CONTROL_RUNTIME_DB_ENABLED", False)
    monkeypatch.setattr(task_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(
        task_manager.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="/usr/bin/other-process\n"),
    )

    class FakeProcess:
        pid = 24680

    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "message.json").write_text(
        json.dumps(
            {
                "id": "req_retry_crash",
                "thread_id": "req_retry_crash",
                "sender": "user",
                "content": "retry crash",
                "user_id": "default",
            }
        ),
        encoding="utf-8",
    )
    (workspace / "worker_stderr.log").write_text("Traceback\nboom\n", encoding="utf-8")
    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_retry_crash",
        workflow_id="req_retry_crash",
        msg_id="req_retry_crash",
        thread_id="req_retry_crash",
        sender="user",
        content_preview="retry crash",
        pid=12345,
        status="running",
        started_at="2026-04-05T00:00:00Z",
        workspace=str(workspace),
        attempt_count=1,
        max_attempts=2,
    )
    mgr._records = [rec]

    completed = mgr.check_tasks()

    assert completed == []
    assert len(mgr._records) == 1
    assert mgr._records[0].task_id == "req_retry_crash"
    assert mgr._records[0].pid == 24680
    assert mgr._records[0].attempt_count == 2
    assert mgr._records[0].status == "dispatched"
