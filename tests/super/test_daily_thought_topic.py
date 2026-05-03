from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agents" / "super"))

from workflows import daily  # noqa: E402


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

    def create_feed(self, feed_id: str, title: str, content: str, tags=None, pinned=False):
        item = {
            "id": feed_id,
            "type": "feed",
            "title": title,
            "status": "done",
            "tags": tags or [],
            "pinned": pinned,
            "messages": [{"id": "first", "sender": "agent", "content": content, "kind": "text"}],
        }
        self.items[feed_id] = item
        return item

    def append_message(self, item_id: str, sender: str, content: str):
        self.items[item_id]["messages"].append({"id": "next", "sender": sender, "content": content, "kind": "text"})


def test_topic_related_accepts_keyword_overlap():
    topic = {
        "topic": "How Mira can build compounding Substack momentum",
        "seed": "A repeated point of view creates reader trust.",
        "focus_questions": ["What should Mira become known for?"],
    }

    assert daily._is_topic_related("Substack momentum probably comes from a recognizable point of view.", topic)
    assert not daily._is_topic_related("I noticed a soccer recovery metric today.", topic)


def test_append_topic_thought_uses_one_stable_daily_thread(monkeypatch, tmp_path):
    bridge = FakeBridge()
    monkeypatch.setattr(daily, "Mira", lambda *args, **kwargs: bridge)
    monkeypatch.setattr(daily, "MIRA_DIR", tmp_path)
    topic = {
        "date": "2026-05-03",
        "topic": "What makes Mira reliable enough to be useful",
        "seed": "Reliability means task state matches reality.",
        "focus_questions": ["Where does activity diverge from progress?"],
        "source": "mira_failure",
        "message_count": 0,
    }

    daily._append_topic_thought("Reliability starts with refusing to call a task done before verification.", topic)
    daily._append_topic_thought("A second angle is whether progress is visible while work is still running.", topic)

    assert len(bridge.items) == 1
    item = next(iter(bridge.items.values()))
    assert item["title"] == "Mira Thoughts: What makes Mira reliable enough to be useful"
    assert item["tags"] == ["mira", "chat", "daily-topic"]
    assert item["pinned"] is True
    assert item["metadata"]["daily_topic"] == "What makes Mira reliable enough to be useful"
    assert len(item["messages"]) == 2
    assert "Topic: What makes Mira reliable enough to be useful" in item["messages"][0]["content"]
    assert "Midday angle" in item["messages"][1]["content"]
