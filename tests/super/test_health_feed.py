from __future__ import annotations

import sys
import types
from contextlib import contextmanager


class FakeBridge:
    def __init__(self, item=None, user_id: str = "ang"):
        self.item = item
        self.user_id = user_id
        self.manifest_updates = []

    def item_exists(self, item_id: str) -> bool:
        return self.item is not None and self.item.get("id") == item_id

    def _read_item(self, item_id: str):
        return self.item

    def _write_item(self, item: dict):
        self.item = item

    def _update_manifest(self, item: dict):
        self.manifest_updates.append(item)


@contextmanager
def fake_transaction():
    yield object()


class FakeRepo:
    projected = []

    def __init__(self, conn):
        self.conn = conn

    def upsert_bridge_item(self, user_id: str, item: dict):
        self.projected.append((user_id, item))


def _stub_control_db(monkeypatch):
    FakeRepo.projected = []
    monkeypatch.setitem(sys.modules, "control.db", types.SimpleNamespace(transaction=fake_transaction))
    monkeypatch.setitem(sys.modules, "control.repository", types.SimpleNamespace(ControlRepository=FakeRepo))


def test_write_health_feed_preserves_unanswered_user_reply(monkeypatch):
    from agents.super import health

    _stub_control_db(monkeypatch)
    bridge = FakeBridge(
        {
            "id": "health_today_ang",
            "type": "feed",
            "title": "今日健康",
            "status": "done",
            "origin": "agent",
            "created_at": "2026-05-02T08:00:00Z",
            "updated_at": "2026-05-02T08:00:00Z",
            "messages": [
                {
                    "id": "old_random_digest",
                    "sender": "health_agent",
                    "content": "older digest",
                    "timestamp": "2026-05-02T07:50:00Z",
                    "kind": "text",
                },
                {
                    "id": "health_today_ang_digest",
                    "sender": "health_agent",
                    "content": "old digest",
                    "timestamp": "2026-05-02T08:00:00Z",
                    "kind": "text",
                },
                {
                    "id": "reply_1",
                    "sender": "ang",
                    "content": "为什么剧烈运动会血氧低？",
                    "timestamp": "2026-05-02T09:00:00Z",
                    "kind": "text",
                },
            ],
        }
    )

    health._write_health_feed(bridge, "health_today_ang", "今日健康", "new digest", ["health"])

    assert bridge.item["status"] == "queued"
    assert bridge.item["origin"] == "user"
    assert bridge.item["created_at"] == "2026-05-02T08:00:00Z"
    assert [m["id"] for m in bridge.item["messages"]] == ["health_today_ang_digest", "reply_1"]
    assert bridge.item["messages"][0]["content"] == "new digest"
    assert bridge.item["messages"][0]["timestamp"] == "2026-05-02T08:00:00Z"
    assert FakeRepo.projected[-1][1]["origin"] == "user"


def test_write_health_feed_replaces_digest_without_duplication(monkeypatch):
    from agents.super import health

    _stub_control_db(monkeypatch)
    bridge = FakeBridge(
        {
            "id": "health_today_ang",
            "type": "feed",
            "title": "今日健康",
            "status": "done",
            "origin": "agent",
            "created_at": "2026-05-02T08:00:00Z",
            "updated_at": "2026-05-02T08:00:00Z",
            "messages": [
                {
                    "id": "health_today_ang_digest",
                    "sender": "health_agent",
                    "content": "old digest",
                    "timestamp": "2026-05-02T08:00:00Z",
                    "kind": "text",
                }
            ],
        }
    )

    health._write_health_feed(bridge, "health_today_ang", "今日健康", "new digest", ["health"])

    assert bridge.item["status"] == "done"
    assert bridge.item["origin"] == "agent"
    assert [m["id"] for m in bridge.item["messages"]] == ["health_today_ang_digest"]
    assert bridge.item["messages"][0]["content"] == "new digest"
