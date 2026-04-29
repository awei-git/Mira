"""Tests for unified runtime context bundle."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent


def test_build_runtime_context_formats_memory_recall(monkeypatch):
    import ops.runtime_context as rc

    class FakePersona:
        def as_prompt(self, max_length: int = 3000) -> str:
            return "persona"

    class FakeStore:
        def recall(self, query: str, top_k: int = 5, user_id: str = "ang"):
            return [
                {
                    "content": "Important lesson about planning and retries.",
                    "source_type": "episode",
                    "source_id": "task_123",
                    "title": "",
                    "score": 0.72,
                    "created_at": datetime.now(timezone.utc) - timedelta(days=2),
                }
            ]

    monkeypatch.setattr(rc, "get_persona_context", lambda domains=None: FakePersona())
    monkeypatch.setattr(rc, "load_thread_history", lambda *args, **kwargs: "thread history")
    monkeypatch.setattr(rc, "load_thread_memory", lambda *args, **kwargs: "thread memory")
    monkeypatch.setattr(rc, "_load_recent_journals", lambda n=3: "journal")
    monkeypatch.setattr(rc, "_load_recent_briefings", lambda n=2: "briefing")
    import memory

    monkeypatch.setitem(sys.modules, "memory.store", SimpleNamespace(get_store=lambda: FakeStore()))
    monkeypatch.setattr(memory, "store", SimpleNamespace(get_store=lambda: FakeStore()), raising=False)

    bundle = rc.build_runtime_context(
        "planning retries",
        user_id="ang",
        thread_id="thread1",
        include_journals=1,
        include_briefings=1,
    )

    assert bundle.thread_history == "thread history"
    assert bundle.thread_memory == "thread memory"
    assert bundle.recent_journals == "journal"
    assert bundle.recent_briefings == "briefing"
    assert len(bundle.memory_recall) == 1
    entry = bundle.memory_recall[0]
    assert entry.freshness == "fresh"
    assert entry.confidence == "high"
    assert entry.provenance == "episode:task_123"
    recall_block = bundle.recall_block()
    assert "Relevant Memory Recall" in recall_block
    assert "high confidence" in recall_block
    assert "fresh" in recall_block


def test_runtime_context_recall_block_respects_char_limit():
    import ops.runtime_context as rc

    bundle = rc.RuntimeContextBundle(
        persona=rc.PersonaContext("", "", "", "", "", ""),
        memory_recall=[
            rc.RecallEntry(
                content="A" * 500,
                source_type="episode",
                source_id="task1",
                title="",
                score=0.5,
                created_at=None,
                freshness="unknown",
                confidence="medium",
                provenance="episode:task1",
            ),
            rc.RecallEntry(
                content="B" * 500,
                source_type="episode",
                source_id="task2",
                title="",
                score=0.5,
                created_at=None,
                freshness="unknown",
                confidence="medium",
                provenance="episode:task2",
            ),
        ],
    )

    block = bundle.recall_block(max_chars=200)
    assert "episode:task1" in block
    assert "episode:task2" not in block
