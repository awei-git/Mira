from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve().parent
_AGENTS = _HERE.parent.parent
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))


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
    assert mgr._records[0].attempt_count == 1
    assert mgr._records[0].max_attempts >= 1


def test_load_status_backfills_missing_user_id(monkeypatch, tmp_path):
    import task_manager

    status_file = tmp_path / "tasks" / "status.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(json.dumps([{
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
    }]), encoding="utf-8")

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", status_file)
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()

    assert len(mgr._records) == 1
    assert mgr._records[0].user_id == "ang"
    assert mgr._records[0].attempt_count == 1
    assert mgr._records[0].failure_class == ""


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
        user_id="ang",
        to_dict=lambda: {
            "id": "req_retry",
            "thread_id": "req_retry",
            "sender": "user",
            "content": "retry me",
            "user_id": "ang",
        },
    )

    mgr.dispatch(msg, tmp_path / "workspace", attempt_count=2, max_attempts=3)

    assert mgr._records[0].attempt_count == 2
    assert mgr._records[0].max_attempts == 3


def test_can_retry_and_reset_for_retry_return_removed_record(monkeypatch, tmp_path):
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_fail",
        msg_id="req_fail",
        thread_id="req_fail",
        sender="user",
        content_preview="failed",
        pid=123,
        status="error",
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
        msg_id="req_fail",
        thread_id="req_fail",
        sender="user",
        content_preview="failed",
        pid=123,
        status="error",
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
        def __init__(self, root, user_id="ang"):
            self.user_id = user_id

        def create_item(self, *args, **kwargs):
            created.append((args, kwargs))

        def get_item(self, item_id):
            return None

    fake_mira = types.SimpleNamespace(Mira=FakeBridge)
    monkeypatch.setitem(sys.modules, "mira", fake_mira)

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_timeout",
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

    mgr.check_tasks()
    assert len(created) == 1
    assert mgr._records[0].timeout_alerted_at
    first_alerted_at = mgr._records[0].timeout_alerted_at

    mgr.check_tasks()
    assert len(created) == 1
    assert mgr._records[0].timeout_alerted_at == first_alerted_at


def test_check_tasks_wait_reply_keeps_running_status(monkeypatch, tmp_path):
    import sys
    import types
    import task_manager

    monkeypatch.setattr(task_manager, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(task_manager, "STATUS_FILE", tmp_path / "tasks" / "status.json")
    monkeypatch.setattr(task_manager, "HISTORY_FILE", tmp_path / "tasks" / "history.jsonl")
    monkeypatch.setattr(task_manager, "_resolve_timeout", lambda tags: 1)

    class FakeBridge:
        def __init__(self, root, user_id="ang"):
            self.user_id = user_id

        def create_item(self, *args, **kwargs):
            pass

        def get_item(self, item_id):
            return {
                "messages": [{"sender": "user", "content": "wait"}],
            }

    fake_mira = types.SimpleNamespace(Mira=FakeBridge)
    monkeypatch.setitem(sys.modules, "mira", fake_mira)

    mgr = task_manager.TaskManager()
    rec = task_manager.TaskRecord(
        task_id="req_timeout_wait",
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

    completed = mgr.check_tasks()

    assert completed == []
    assert mgr._records[0].status == "running"
    assert mgr._records[0].timeout_alerted_at
