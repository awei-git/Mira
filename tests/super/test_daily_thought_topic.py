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

    def create_discussion(self, disc_id: str, title: str, content: str, sender="agent", tags=None, parent_id=""):
        item = {
            "id": disc_id,
            "type": "discussion",
            "title": title,
            "status": "needs-input",
            "tags": tags or [],
            "origin": "agent" if sender == "agent" else "user",
            "pinned": False,
            "parent_id": parent_id,
            "messages": [{"id": "first", "sender": sender, "content": content, "kind": "text"}],
        }
        self.items[disc_id] = item
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
    assert item["type"] == "discussion"
    assert item["status"] == "done"
    assert item["origin"] == "agent"
    assert item["title"] == "Mira Thoughts"
    assert item["tags"] == ["mira", "chat", "daily-topic"]
    assert item["pinned"] is True
    assert item["metadata"]["daily_topic"] == "What makes Mira reliable enough to be useful"
    assert len(item["messages"]) == 2
    assert "今天我想抓住一个问题：What makes Mira reliable enough to be useful" in item["messages"][0]["content"]
    assert "Midday angle" not in item["messages"][1]["content"]
    assert (
        item["messages"][1]["content"] == "A second angle is whether progress is visible while work is still running."
    )


def test_append_topic_thought_migrates_existing_feed_to_replyable_discussion(monkeypatch, tmp_path):
    bridge = FakeBridge()
    monkeypatch.setattr(daily, "Mira", lambda *args, **kwargs: bridge)
    monkeypatch.setattr(daily, "MIRA_DIR", tmp_path)
    topic = {
        "date": "2026-05-03",
        "topic": "How should Mira talk with the user instead of broadcasting at them",
        "seed": "A thought only becomes useful when it can be pushed back on.",
        "source": "user_feedback",
    }
    item_id = f"feed_chat_{daily.datetime.now().strftime('%Y%m%d')}"
    bridge.create_feed(item_id, "Mira Thoughts", "old one-way content", tags=["mira"], pinned=False)

    daily._append_topic_thought("This should now be commentable.", topic)

    item = bridge.items[item_id]
    assert item["type"] == "discussion"
    assert item["status"] == "done"
    assert item["origin"] == "agent"
    assert item["pinned"] is True
    assert item["metadata"]["topic_source"] == "user_feedback"
    assert item["messages"][-1]["content"] == "This should now be commentable."


def test_trim_chat_result_keeps_two_short_sentences():
    text = "我突然觉得，可靠性不是能不能回答，而是敢不敢停下来承认没做完。这个区别比看起来大。后面这些都应该被截掉。"

    result = daily._trim_chat_result(text)

    assert result == "我突然觉得，可靠性不是能不能回答，而是敢不敢停下来承认没做完。这个区别比看起来大。"
    assert len(result) <= 120


def test_topic_too_dry_rejects_jargon_title():
    assert daily._topic_too_dry("The structural failure of AI handoff protocol as authority laundering")
    assert not daily._topic_too_dry("一个 agent 到底什么时候才算真的可靠？")
