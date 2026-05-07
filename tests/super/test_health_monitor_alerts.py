from __future__ import annotations

import health_monitor


class FakeBridge:
    def __init__(self):
        self.items: dict[str, dict] = {}

    def item_exists(self, item_id: str) -> bool:
        return item_id in self.items

    def _read_item(self, item_id: str):
        return self.items.get(item_id)

    def _write_item(self, item: dict):
        self.items[item["id"]] = item

    def _update_manifest(self, item: dict):
        return None

    def create_item(
        self, item_id: str, item_type: str, title: str, content: str, sender="agent", tags=None, origin="agent"
    ):
        item = {
            "id": item_id,
            "type": item_type,
            "title": title,
            "status": "queued",
            "origin": origin,
            "tags": tags or [],
            "pinned": False,
            "messages": [{"id": "first", "sender": sender, "content": content, "kind": "text"}],
        }
        self.items[item_id] = item
        return item

    def append_message(self, item_id: str, sender: str, content: str):
        self.items[item_id]["messages"].append({"id": "next", "sender": sender, "content": content, "kind": "text"})


def test_publish_health_alert_uses_stable_visible_item(monkeypatch):
    bridge = FakeBridge()
    monkeypatch.setattr(health_monitor, "_get_bridge", lambda: bridge)

    health_monitor._publish_health_alert("first failure")
    health_monitor._publish_health_alert("second failure")

    assert list(bridge.items) == ["mira_health_alerts"]
    item = bridge.items["mira_health_alerts"]
    assert item["type"] == "discussion"
    assert item["title"] == "Mira Health Alerts"
    assert item["status"] == "done"
    assert item["origin"] == "agent"
    assert item["tags"] == ["health", "ops", "system"]
    assert item["pinned"] is True
    assert [msg["content"] for msg in item["messages"]] == ["first failure", "second failure"]


def test_harvest_all_consumes_dead_pid_once(monkeypatch, tmp_path):
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    pid_file = pid_dir / "autowrite-check.pid"
    pid_file.write_text("999999", encoding="utf-8")
    outcomes = []

    monkeypatch.setattr(health_monitor, "_BG_PID_DIR", pid_dir)
    monkeypatch.setattr(health_monitor.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(health_monitor, "record_outcome", lambda name: outcomes.append(name) or True)

    assert health_monitor.harvest_all() == ["autowrite-check"]
    assert outcomes == ["autowrite-check"]
    assert not pid_file.exists()
    assert health_monitor.harvest_all() == []
