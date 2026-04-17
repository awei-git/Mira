"""record_task_outcome() — legacy path unchanged; Phase 1 path opt-in."""

from __future__ import annotations

import json
from pathlib import Path

from evolution import record_task_outcome
from evolution.trajectory_recorder import TrajectoryRecorder


def _redirect_paths(monkeypatch, tmp_path: Path) -> dict:
    import evolution.config as cfg
    import evolution.experience as experience_mod
    import evolution.trajectory_recorder as recorder_mod
    import evolution.tool_stats as tool_stats_mod

    paths = {
        "experiences": tmp_path / "experiences",
        "trajectories": tmp_path / "trajectories.jsonl",
        "tool_stats": tmp_path / "tool_stats.json",
    }
    paths["experiences"].mkdir(parents=True)

    # Monkeypatch the config source AND each module's cached import —
    # Python binds names at module load time.
    monkeypatch.setattr(cfg, "EXPERIENCE_DIR", paths["experiences"])
    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", paths["trajectories"])
    monkeypatch.setattr(cfg, "TOOL_STATS_FILE", paths["tool_stats"])

    monkeypatch.setattr(experience_mod, "EXPERIENCE_DIR", paths["experiences"])
    monkeypatch.setattr(recorder_mod, "TRAJECTORY_FILE", paths["trajectories"])
    monkeypatch.setattr(tool_stats_mod, "TOOL_STATS_FILE", paths["tool_stats"])

    # Force compressor to skip LLM call (no network in tests)
    import evolution.trajectory_compressor as compressor_mod

    monkeypatch.setattr(compressor_mod, "_default_summarizer", lambda: None)
    return paths


def test_legacy_path_still_works_with_flag_off(tmp_path, monkeypatch):
    paths = _redirect_paths(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", False)

    record_task_outcome(
        task_id="legacy-1",
        agent="writer",
        action="test action",
        status="done",
        summary="ok",
    )
    # Experience file created, trajectory file NOT.
    files = list(paths["experiences"].glob("*.jsonl"))
    assert files, "expected legacy experience jsonl"
    assert not paths["trajectories"].exists()
    assert not paths["tool_stats"].exists()


def test_v2_path_writes_trajectory_and_tool_stats(tmp_path, monkeypatch):
    paths = _redirect_paths(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    rec = TrajectoryRecorder("trj-1", "writer", model="claude-opus-4-7")
    rec.add_user("do the thing")
    rec.add_assistant("on it", tool_name="Read")
    rec.record_tool("Read", success=True)
    rec.add_tool_result("Read", "ok", success=True)
    rec.add_assistant("done")
    trajectory = rec.finalize(completed=True)

    record_task_outcome(
        task_id="trj-1",
        agent="writer",
        action="do the thing",
        status="done",
        summary="shipped",
        trajectory=trajectory,
        outcome_verified=True,
        elapsed_seconds=20.0,
        budget_seconds=60.0,
    )

    # Legacy experience still written
    files = list(paths["experiences"].glob("*.jsonl"))
    assert files

    # Global trajectories now has one entry
    assert paths["trajectories"].exists()
    line = paths["trajectories"].read_text(encoding="utf-8").strip().splitlines()[0]
    doc = json.loads(line)
    assert doc["task_id"] == "trj-1"

    # Tool stats now has Read
    assert paths["tool_stats"].exists()
    stats = json.loads(paths["tool_stats"].read_text(encoding="utf-8"))
    assert stats["Read"]["count"] == 1
    assert stats["Read"]["success"] == 1


def test_v2_path_never_raises_on_broken_telemetry(tmp_path, monkeypatch, caplog):
    """Telemetry failures must not kill the main task path."""
    _redirect_paths(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    # Point trajectory file at a directory path to force write failure
    bad = tmp_path / "blocker_dir"
    bad.mkdir()
    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", bad)
    import evolution.trajectory_recorder as recorder_mod

    monkeypatch.setattr(recorder_mod, "TRAJECTORY_FILE", bad)

    trajectory = TrajectoryRecorder("broken", "writer").finalize(completed=True)
    # Should return normally — warning logged, no exception propagated.
    record_task_outcome(
        task_id="broken",
        agent="writer",
        action="x",
        status="done",
        trajectory=trajectory,
    )
