from __future__ import annotations

from pathlib import Path


def test_operator_alerts_are_persisted_as_action_backlog(monkeypatch, tmp_path: Path):
    import operator_dashboard as od
    from ops.backlog import ActionBacklog

    storage = tmp_path / "actions.json"
    monkeypatch.setattr(od, "ActionBacklog", lambda: ActionBacklog(storage))
    monkeypatch.setattr(od, "_process_has_active_failure", lambda proc: proc.get("name") == "daily-health")
    summary = {}
    summary["tasks"] = {"stuck": [{"preview": "Still running"}]}
    summary["health"] = {"processes": [{"name": "daily-health", "consecutive_failures": 3}]}
    summary["recent_incidents"] = []
    od._sync_operator_action_backlog(summary)
    od._sync_operator_action_backlog(summary)
    active = ActionBacklog(storage).get_active()

    assert len(active) == 2
    assert {item.source for item in active} == {"operator_dashboard"}
    assert any(item.title == "Resolve scheduled process failure: daily-health" for item in active)
