"""Tests for operator dashboard summary."""

from __future__ import annotations

import tempfile
import json
import sys
from pathlib import Path

_MIRA = Path(__file__).resolve().parent.parent.parent


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
    backup_dir = Path(tempfile.mkdtemp(prefix="operator-dashboard-backup-test-", dir="/tmp"))
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


def test_operator_alert_lines_surface_actionable_runtime_failures():
    import operator_dashboard as od

    summary = {
        "tasks": {
            "stuck": [
                {
                    "task_id": "task-1",
                    "preview": "Draft a reply that has been running too long",
                }
            ]
        },
        "publish": {
            "stuck": [{"slug": "essay-stuck"}],
            "counts": {"blocked_writer_gate": 2, "blocked_security_claim": 1, "blocked_publish_error": 3},
        },
        "health": {
            "failing_processes": 1,
            "processes": [{"name": "daily-health", "consecutive_failures": 2, "last_exit": "2999-05-07T01:00:00Z"}],
        },
        "provider_circuits": [
            {
                "provider": "deepseek",
                "reason": "HTTP 402",
                "disabled_until": "2026-05-07T07:09:53Z",
            }
        ],
        "recent_incidents": [
            {
                "pipeline": "old",
                "step": "substack_publish",
                "error_type": "old_timeout",
                "count": 4,
                "timestamp": "2000-01-01T00:00:00Z",
            },
            {
                "pipeline": "publish",
                "step": "substack_publish",
                "error_type": "timeout",
                "count": 3,
                "timestamp": "2999-01-01T00:00:00Z",
            },
        ],
    }

    lines = od._operator_alert_lines(summary)

    assert any("task(s) appear stuck" in line for line in lines)
    assert any("scheduled process(es) are failing" in line for line in lines)
    assert any("publish item(s) are stuck" in line for line in lines)
    assert any("writer-quality gate" in line for line in lines)
    assert any("security-claim review" in line for line in lines)
    assert any("returned no Substack URL" in line for line in lines)
    assert any("deepseek provider circuit is open" in line for line in lines)
    assert any("Repeated incident" in line for line in lines)
    assert not any("old_timeout" in line for line in lines)


def test_api_provider_circuits_only_reports_open_entries(monkeypatch, tmp_path: Path):
    import operator_dashboard as od

    circuit_file = tmp_path / "api_provider_circuit.json"
    circuit_file.write_text(
        json.dumps(
            {
                "deepseek": {
                    "reason": "HTTP 402",
                    "disabled_until": "2999-05-07T07:09:53Z",
                    "updated_at": "2026-05-07T01:09:53Z",
                },
                "openai": {
                    "reason": "old",
                    "disabled_until": "2000-01-01T00:00:00Z",
                    "updated_at": "1999-12-31T00:00:00Z",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(od, "_PROVIDER_CIRCUIT_FILE", circuit_file)

    circuits = od._api_provider_circuits()

    assert circuits == [
        {
            "provider": "deepseek",
            "reason": "HTTP 402",
            "disabled_until": "2999-05-07T07:09:53Z",
            "updated_at": "2026-05-07T01:09:53Z",
        }
    ]


def test_process_active_failure_ignores_success_after_old_failure():
    import operator_dashboard as od

    assert not od._process_has_active_failure(
        {
            "consecutive_failures": 12,
            "last_exit": "2026-03-19T23:16:17.578991",
            "last_success": "2026-03-19T23:16:17.607328",
        }
    )
    assert od._process_has_active_failure(
        {
            "consecutive_failures": 2,
            "last_exit": "2999-05-06T21:16:17.607328",
            "last_success": "2026-05-06T20:16:17.607328",
        }
    )


def test_one_shot_process_failures_expire_from_operator_alerts():
    import operator_dashboard as od

    assert not od._process_has_active_failure(
        {
            "name": "autowrite-2026-05-05",
            "consecutive_failures": 2,
            "last_exit": "2026-05-05T19:16:17.607328",
            "last_success": "",
        }
    )
