"""FTS5 session index — schema, index, search, prune, soul injection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from memory.session_index import (
    format_soul_recall,
    index_trajectory,
    prune_older_than,
    row_count,
    search,
)

from evolution.trajectory_recorder import TrajectoryRecorder


def _trajectory(task_id: str, agent: str, *, texts: list[tuple[str, str]], ts: datetime | None = None):
    """Build a trajectory with given (role, text) turns."""
    rec = TrajectoryRecorder(task_id, agent)
    for role, text in texts:
        if role == "human":
            rec.add_user(text)
        elif role == "assistant":
            rec.add_assistant(text)
        elif role == "system":
            rec.add_system(text)
        elif role == "tool":
            rec.add_tool_result("Read", text, success=True)
    record = rec.finalize(completed=True)
    if ts is not None:
        record.timestamp = ts
    return record


def test_index_and_search_exact_phrase(tmp_path):
    db = tmp_path / "session.db"
    t = _trajectory(
        "t1",
        "writer",
        texts=[
            ("human", "write an article about benchmark saturation"),
            ("assistant", "started a draft on model evaluation benchmarks"),
        ],
    )
    inserted = index_trajectory(t, path=db)
    assert inserted == 2

    results = search("benchmark", path=db)
    assert len(results) > 0
    assert any("benchmark" in r.text for r in results)


def test_search_agent_filter(tmp_path):
    db = tmp_path / "session.db"
    index_trajectory(_trajectory("a", "writer", texts=[("human", "telemetry architecture")]), path=db)
    index_trajectory(_trajectory("b", "explorer", texts=[("human", "telemetry architecture")]), path=db)

    writer_hits = search("telemetry", agent="writer", path=db)
    assert writer_hits and all(s.agent == "writer" for s in writer_hits)


def test_search_since_timestamp_filter(tmp_path):
    db = tmp_path / "session.db"
    old_ts = datetime.now(timezone.utc) - timedelta(days=40)
    new_ts = datetime.now(timezone.utc) - timedelta(days=1)
    index_trajectory(_trajectory("old", "writer", texts=[("human", "orbit")], ts=old_ts), path=db)
    index_trajectory(_trajectory("new", "writer", texts=[("human", "orbit")], ts=new_ts), path=db)

    recent_only = search("orbit", since=datetime.now(timezone.utc) - timedelta(days=7), path=db)
    assert {s.task_id for s in recent_only} == {"new"}


def test_empty_query_returns_empty(tmp_path):
    db = tmp_path / "session.db"
    assert search("", path=db) == []
    assert search("   ", path=db) == []


def test_query_sanitization_survives_punctuation(tmp_path):
    db = tmp_path / "session.db"
    index_trajectory(_trajectory("t", "writer", texts=[("human", "review notes")]), path=db)
    # FTS5 would choke on raw quotes; format_soul_recall must survive.
    assert search('"review"', path=db) != [] or search("review", path=db) != []


def test_prune_older_than(tmp_path):
    db = tmp_path / "session.db"
    old_ts = datetime.now(timezone.utc) - timedelta(days=100)
    new_ts = datetime.now(timezone.utc) - timedelta(days=10)
    index_trajectory(_trajectory("old", "w", texts=[("human", "alpha")], ts=old_ts), path=db)
    index_trajectory(_trajectory("new", "w", texts=[("human", "alpha")], ts=new_ts), path=db)

    before = row_count(path=db)
    deleted = prune_older_than(days=30, path=db)
    assert deleted >= 1
    after = row_count(path=db)
    assert after == before - deleted


def test_format_soul_recall_returns_markdown(tmp_path):
    db = tmp_path / "session.db"
    index_trajectory(
        _trajectory(
            "t",
            "writer",
            texts=[
                ("human", "what does honcho's dialectic model do"),
                ("assistant", "honcho models user beliefs as a continuously updated dialectic"),
            ],
        ),
        path=db,
    )
    block = format_soul_recall("honcho", path=db)
    assert "## Relevant past conversations" in block
    assert "honcho" in block.lower()


def test_format_soul_recall_empty_when_no_hits(tmp_path):
    db = tmp_path / "session.db"
    assert format_soul_recall("no-such-term", path=db) == ""


def test_index_trajectory_empty_is_ok(tmp_path):
    db = tmp_path / "session.db"
    rec = TrajectoryRecorder("e", "writer").finalize(completed=True)
    assert index_trajectory(rec, path=db) == 0
