"""End-to-end: trace_task → compress → persist → reward → FTS5 index.

This is the Phase 1/2 happy path with the flag on. If this test
regresses, the Hermes loop is silently dead.
"""

from __future__ import annotations

import json
from pathlib import Path


def _redirect_all(monkeypatch, tmp_path: Path):
    """Point every persistence layer at tmp_path."""
    import evolution.config as cfg
    import evolution.trajectory_recorder as rec_mod
    import evolution.tool_stats as ts_mod
    import evolution.trajectory_compressor as comp_mod
    import memory.session_index as idx_mod

    traj = tmp_path / "trajectories.jsonl"
    stats = tmp_path / "tool_stats.json"
    index = tmp_path / "session_index.db"

    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", traj)
    monkeypatch.setattr(cfg, "TOOL_STATS_FILE", stats)
    monkeypatch.setattr(rec_mod, "TRAJECTORY_FILE", traj)
    monkeypatch.setattr(ts_mod, "TOOL_STATS_FILE", stats)
    monkeypatch.setattr(idx_mod, "DB_FILE", index)
    monkeypatch.setattr(comp_mod, "_default_summarizer", lambda: None)
    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)
    return {"traj": traj, "stats": stats, "index": index}


def test_full_trajectory_lifecycle(tmp_path, monkeypatch):
    paths = _redirect_all(monkeypatch, tmp_path)
    from evolution.trace import trace_task
    from evolution.rewards_v2 import compute_trajectory_reward, load_recent_trajectories
    from memory.session_index import search, row_count

    with trace_task("end2end-1", "writer", budget_seconds=120) as t:
        t.add_system("soul context here")
        t.add_user("analyze the benchmark saturation problem")
        t.add_assistant("searching for papers", tool_name="WebSearch")
        t.record_tool("WebSearch", success=True)
        t.add_tool_result("WebSearch", "found 3 relevant papers", success=True)
        t.add_assistant("reading the first paper", tool_name="Read")
        t.record_tool("Read", success=True)
        t.add_assistant("writing draft")
        t.record_tool("Write", success=True)
        t.mark_completed(outcome_verified=True)

    # 1) trajectory persisted
    assert paths["traj"].exists()
    line = paths["traj"].read_text(encoding="utf-8").strip().splitlines()[0]
    doc = json.loads(line)
    assert doc["task_id"] == "end2end-1"
    assert doc["completed"] is True

    # 2) tool_stats merged
    stats = json.loads(paths["stats"].read_text(encoding="utf-8"))
    assert stats["WebSearch"]["success"] == 1
    assert stats["Read"]["success"] == 1
    assert stats["Write"]["success"] == 1

    # 3) reward computable and non-trivial
    trajectories = load_recent_trajectories(days=1, path=paths["traj"])
    assert len(trajectories) == 1
    score, components = compute_trajectory_reward(trajectories[0], outcome_verified=True)
    assert "tool_success_rate" in components
    assert "outcome_verified" in components
    assert score > 0  # positive outcome should net positive

    # 4) session_index searchable for the raw prompt
    assert row_count(path=paths["index"]) >= 2
    hits = search("benchmark saturation", path=paths["index"])
    assert hits and any("benchmark" in h.text.lower() for h in hits)


def test_crash_path_sets_crashed_flag_and_penalty(tmp_path, monkeypatch):
    paths = _redirect_all(monkeypatch, tmp_path)
    from evolution.trace import trace_task
    from evolution.rewards_v2 import compute_trajectory_reward, load_recent_trajectories

    try:
        with trace_task("crash-1", "writer") as t:
            t.add_user("do the thing")
            t.record_tool("Read", success=True)
            raise RuntimeError("simulated worker crash mid-task")
    except RuntimeError:
        pass

    trajectories = load_recent_trajectories(days=1, path=paths["traj"])
    crashed = next(t for t in trajectories if t.task_id == "crash-1")
    assert crashed.crashed is True

    score, components = compute_trajectory_reward(crashed)
    assert "crash_penalty" in components
    assert components["crash_penalty"] < 0
