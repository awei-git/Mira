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
