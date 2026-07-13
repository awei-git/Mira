"""format_soul() with Phase 2 context_query injection."""

from __future__ import annotations

from memory import session_index as idx
from memory.soul import format_soul

from evolution.trajectory_recorder import TrajectoryRecorder


SOUL = {
    "identity": "I am Mira.",
    "worldview": "Curious, honest, incremental.",
    "memory": "",
    "interests": "",
    "skills": "",
}


def test_format_soul_without_context_query_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(idx, "DB_FILE", tmp_path / "session.db")
    text = format_soul(SOUL)
    assert "Relevant past conversations" not in text


def test_format_soul_injects_recall_when_hits(tmp_path, monkeypatch):
    db = tmp_path / "session.db"
    monkeypatch.setattr(idx, "DB_FILE", db)

    rec = TrajectoryRecorder("t1", "writer")
    rec.add_user("how does the circuit breaker protect us from Substack outages")
    rec.add_assistant("it opens after 50% error rate in a 5min window")
    idx.index_trajectory(rec.finalize(completed=True))

    text = format_soul(SOUL, context_query="circuit breaker")
    assert "Relevant past conversations" in text
    assert "circuit breaker" in text.lower()


def test_format_soul_no_hits_does_not_break(tmp_path, monkeypatch):
    monkeypatch.setattr(idx, "DB_FILE", tmp_path / "session.db")
    text = format_soul(SOUL, context_query="a totally unrelated query")
    assert "Relevant past conversations" not in text
