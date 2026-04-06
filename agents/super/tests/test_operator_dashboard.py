"""Tests for operator dashboard summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent
_SHARED = _SUPER.parent / "shared"
sys.path.insert(0, str(_SUPER))
sys.path.insert(0, str(_SHARED))


def test_build_operator_summary_aggregates_runtime_signals(monkeypatch, tmp_path: Path):
    import operator_dashboard as od

    status_file = tmp_path / "status.json"
    history_file = tmp_path / "history.jsonl"
    restore_log = tmp_path / "restore_drills.jsonl"
    status_file.write_text(
        json.dumps(
            [
                {
                    "task_id": "task-running",
                    "user_id": "ang",
                    "status": "running",
                    "content_preview": "Investigate backlog",
                    "started_at": "2026-04-05T00:00:00Z",
                    "tags": ["research"],
                }
            ]
        ),
        encoding="utf-8",
    )
    history_file.write_text(
        json.dumps(
            {
                "task_id": "task-failed",
                "workflow_id": "task-failed",
                "user_id": "ang",
                "status": "failed",
                "failure_class": "worker_crash",
                "summary": "boom",
                "completed_at": "2026-04-05T01:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    restore_log.write_text(
        json.dumps({"timestamp": "2026-04-05T02:00:00Z", "ok": True, "backup_dir": "/tmp/backup"}),
        encoding="utf-8",
    )

    class FakeBacklog:
        def __init__(self):
            self._items = [
                type("Item", (), {"status": "approved"})(),
                type("Item", (), {"status": "verified"})(),
            ]

        def get_active(self):
            return [
                type(
                    "Item",
                    (),
                    {
                        "title": "Improve retry handling",
                        "status": "approved",
                        "priority": "high",
                        "source": "self_evolve",
                        "executor": "self_evolve_proposal",
                        "updated_at": "2026-04-05T03:00:00Z",
                    },
                )()
            ]

    monkeypatch.setattr(od, "STATUS_FILE", status_file)
    monkeypatch.setattr(od, "HISTORY_FILE", history_file)
    monkeypatch.setattr(od, "_RESTORE_DRILL_LOG", restore_log)
    monkeypatch.setattr(od, "_valid_restore_drill", lambda record: True)
    monkeypatch.setattr(
        od,
        "load_manifest",
        lambda: {
            "articles": {
                "essay-1": {
                    "slug": "essay-1",
                    "title": "Essay",
                    "status": "approved",
                    "timestamps": {"approved": "2026-04-05T00:30:00Z"},
                }
            }
        },
    )
    monkeypatch.setattr(od, "get_stuck_articles", lambda: [])
    monkeypatch.setattr(
        od,
        "load_recent_failures",
        lambda days=7, limit=10: [
            {
                "timestamp": "2026-04-05T04:00:00Z",
                "pipeline": "publish",
                "step": "substack_publish",
                "slug": "essay-1",
                "error_type": "api_timeout",
                "error_message": "timed out",
            }
        ],
    )
    monkeypatch.setattr(od, "ActionBacklog", FakeBacklog)
    monkeypatch.setattr(
        od,
        "_load_bg_health",
        lambda: {"daily_stats": {"failed": 1}, "processes": [{"name": "self-evolve"}]},
    )

    summary = od.build_operator_summary(user_id="ang")

    assert summary["tasks"]["active"][0]["task_id"] == "task-running"
    assert summary["tasks"]["failed_recent"][0]["task_id"] == "task-failed"
    assert summary["tasks"]["failed_recent"][0]["workflow_id"] == "task-failed"
    assert summary["publish"]["queue"][0]["slug"] == "essay-1"
    assert summary["backlog"]["counts"]["approved"] == 1
    assert summary["recent_incidents"][0]["pipeline"] == "publish"
    assert summary["latest_restore_drill"]["ok"] is True


def test_operator_dashboard_filters_ephemeral_restore_and_stale_processes(monkeypatch, tmp_path: Path):
    import operator_dashboard as od

    restore_log = tmp_path / "restore_drills.jsonl"
    backup_dir = Path("/Users/angwei/Sandbox/Mira/logs/operator-dashboard-backup-test")
    backup_dir.mkdir(parents=True, exist_ok=True)
    restore_log.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-05T02:00:00Z",
                        "ok": False,
                        "backup_dir": "/private/var/folders/wh/test",
                    }
                ),
                    json.dumps(
                        {
                            "timestamp": "2026-04-05T03:00:00Z",
                            "ok": True,
                            "backup_dir": str(backup_dir),
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(od, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(od, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(od, "_RESTORE_DRILL_LOG", restore_log)
    monkeypatch.setattr(od, "load_manifest", lambda: {"articles": {}})
    monkeypatch.setattr(od, "get_stuck_articles", lambda: [])
    monkeypatch.setattr(od, "load_recent_failures", lambda days=7, limit=50: [])
    monkeypatch.setattr(od, "ActionBacklog", lambda: type("B", (), {"_items": [], "get_active": lambda self: []})())
    monkeypatch.setattr(
        od,
        "_load_bg_health",
        lambda: {
            "daily_stats": {},
            "failing_processes": 1,
            "processes": [{"name": "analyst-1800", "consecutive_failures": 3}],
        },
    )

    summary = od.build_operator_summary(user_id="ang")

    assert summary["latest_restore_drill"]["backup_dir"] == str(backup_dir)
    assert summary["health"]["processes"][0]["name"] == "analyst-1800"
