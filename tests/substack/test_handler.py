"""Tests for the Substack publisher-operator handler."""

from __future__ import annotations

import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def _load_substack_handler():
    import importlib.util

    spec = importlib.util.spec_from_file_location("substack_handler_test", _SUBSTACK_AGENT / "handler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_publish_intent_is_delegated(monkeypatch, tmp_path: Path):
    handler = _load_substack_handler()

    calls = []

    def fake_delegate(workspace, task_id, content, sender, thread_id, **kwargs):
        calls.append((task_id, content))
        return "delegated"

    monkeypatch.setattr(handler, "_delegate_to_socialmedia", fake_delegate)

    result = handler.handle(tmp_path, "task1", "publish this article to Substack", "ang", "thread1")

    assert result == "delegated"
    assert calls == [("task1", "publish this article to Substack")]


def test_planning_request_writes_strategy_report(monkeypatch, tmp_path: Path):
    handler = _load_substack_handler()
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


def test_growth_recovery_request_uses_sprint_tracker(monkeypatch, tmp_path: Path):
    handler = _load_substack_handler()
    from models import GrowthRecoverySprint
    from storage import SubstackStore

    store = SubstackStore(root=tmp_path / "store")
    sprint = GrowthRecoverySprint(
        id="growth-recovery-test",
        anchor_article={"title": "Can an agent develop taste?", "url": "https://example.com"},
        baseline={"subscribers_total": 40, "subscribers_delta_30d": 7},
        weeks=[],
    )

    monkeypatch.setattr(handler, "SubstackStore", lambda: store)
    monkeypatch.setattr(handler, "load_or_create_growth_recovery", lambda active_store: sprint)
    monkeypatch.setattr(
        handler, "write_growth_recovery_report", lambda active_store, active_sprint: tmp_path / "report.md"
    )
    monkeypatch.setattr(
        handler, "format_growth_recovery_report", lambda active_sprint: "# Substack Growth Recovery Sprint"
    )

    result = handler.handle(tmp_path, "task3", "show the growth recovery sprint", "ang", "thread3")

    assert result == "# Substack Growth Recovery Sprint"
    assert store.load_growth_recovery() is not None
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == "# Substack Growth Recovery Sprint"
