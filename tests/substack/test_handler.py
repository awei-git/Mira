"""Tests for the Substack publisher-operator handler."""

from __future__ import annotations

import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_live_publish_intent_is_delegated(monkeypatch, tmp_path: Path):
    import handler

    calls = []

    def fake_delegate(workspace, task_id, content, sender, thread_id, **kwargs):
        calls.append((task_id, content))
        return "delegated"

    monkeypatch.setattr(handler, "_delegate_to_socialmedia", fake_delegate)

    result = handler.handle(tmp_path, "task1", "publish this article to Substack", "ang", "thread1")

    assert result == "delegated"
    assert calls == [("task1", "publish this article to Substack")]


def test_planning_request_writes_strategy_report(monkeypatch, tmp_path: Path):
    import handler
    from models import PublicationStrategy, TopicCandidate
    from storage import SubstackStore

    store = SubstackStore(root=tmp_path / "store")

    monkeypatch.setattr(handler, "SubstackStore", lambda: store)
    monkeypatch.setattr(
        handler,
        "discover_topics_from_writer_ideas",
        lambda strategy: [
            TopicCandidate(
                id="topic-1",
                title="Mira Reliability",
                thesis="Mira should prove outcomes, not just mark tasks done.",
                source="test",
                pillar="Agent reliability",
                priority_score=9,
                originality_score=9,
                audience_fit_score=9,
                monetization_score=6,
                mira_edge="Use Mira's own failures.",
            )
        ],
    )
    store.save_strategy(PublicationStrategy())

    result = handler.handle(tmp_path, "task2", "plan the Substack account", "ang", "thread2")

    assert result is not None
    assert "Substack Publisher Plan" in result
    assert "Mira Reliability" in result
    assert (tmp_path / "output.md").exists()
