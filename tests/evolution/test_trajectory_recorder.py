"""TrajectoryRecorder builder + persistence helpers."""

from __future__ import annotations

from evolution.trajectory_recorder import (
    TrajectoryRecorder,
    append_to_global,
    load_trajectory_jsonl,
    persist_per_task,
)


def test_recorder_builds_expected_record():
    rec = TrajectoryRecorder("t1", "writer", model="claude-opus-4-7")
    rec.add_system("you are Mira")
    rec.add_user("write a note about X")
    rec.add_assistant("checking sources", tool_name="WebSearch", tool_args={"q": "X"})
    rec.record_tool("WebSearch", success=True)
    rec.add_tool_result("WebSearch", "search result body", success=True)
    rec.add_assistant("done")
    rec.bump_api_calls(3)
    trajectory = rec.finalize(completed=True)

    assert trajectory.task_id == "t1"
    assert trajectory.model == "claude-opus-4-7"
    assert trajectory.api_calls == 3
    assert trajectory.completed is True
    assert len(trajectory.conversations) == 5
    assert trajectory.tool_stats["WebSearch"].count == 1


def test_persist_per_task_writes_single_line_jsonl(tmp_path):
    rec = TrajectoryRecorder("t2", "writer").finalize(completed=True)
    path = persist_per_task(tmp_path, rec)
    assert path is not None
    restored = load_trajectory_jsonl(path)
    assert restored is not None
    assert restored.task_id == "t2"


def test_append_to_global_is_additive(tmp_path, monkeypatch):
    # Redirect TRAJECTORY_FILE to tmp_path
    import evolution.config as cfg
    import evolution.trajectory_recorder as mod

    target = tmp_path / "trajectories.jsonl"
    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", target)
    monkeypatch.setattr(mod, "TRAJECTORY_FILE", target)

    rec_a = TrajectoryRecorder("tA", "writer").finalize(completed=True)
    rec_b = TrajectoryRecorder("tB", "explorer").finalize(completed=False, partial=True)

    append_to_global(rec_a)
    append_to_global(rec_b)

    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert '"tA"' in lines[0]
    assert '"tB"' in lines[1]


def test_persist_per_task_never_raises_on_bad_dir(tmp_path):
    rec = TrajectoryRecorder("t3", "writer").finalize(completed=True)
    # Use a file-as-dir to force OSError internally; should return None, not raise.
    bad = tmp_path / "file.txt"
    bad.write_text("blocker")
    result = persist_per_task(bad, rec)
    assert result is None
