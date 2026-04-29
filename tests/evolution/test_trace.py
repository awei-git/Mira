"""trace_task context manager — flag-off no-op + flag-on persistence."""

from __future__ import annotations

import json
from pathlib import Path

from evolution.trace import trace_task


def _redirect(monkeypatch, tmp_path: Path) -> dict:
    import evolution.config as cfg
    import evolution.trajectory_recorder as rec_mod
    import evolution.tool_stats as ts_mod
    import evolution.trajectory_compressor as comp_mod
    import memory.session_index as idx_mod

    paths = {
        "traj": tmp_path / "trajectories.jsonl",
        "stats": tmp_path / "tool_stats.json",
        "index": tmp_path / "session_index.db",
    }
    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", paths["traj"])
    monkeypatch.setattr(cfg, "TOOL_STATS_FILE", paths["stats"])
    monkeypatch.setattr(rec_mod, "TRAJECTORY_FILE", paths["traj"])
    monkeypatch.setattr(ts_mod, "TOOL_STATS_FILE", paths["stats"])
    monkeypatch.setattr(comp_mod, "_default_summarizer", lambda: None)
    monkeypatch.setattr(idx_mod, "DB_FILE", paths["index"])
    return paths


def test_trace_noop_when_flag_off(tmp_path, monkeypatch):
    paths = _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", False)

    with trace_task("t", "writer") as trace:
        trace.add_user("go")
        trace.record_tool("Read", success=True)
        trace.mark_completed(outcome_verified=True)

    assert not paths["traj"].exists()
    assert not paths["stats"].exists()


def test_trace_persists_when_flag_on(tmp_path, monkeypatch):
    paths = _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    with trace_task("t1", "writer", budget_seconds=60) as trace:
        trace.add_system("soul")
        trace.add_user("write about X")
        trace.add_assistant("searching", tool_name="WebSearch")
        trace.record_tool("WebSearch", success=True)
        trace.add_tool_result("WebSearch", "result body", success=True)
        trace.add_assistant("wrote it")
        trace.bump_api_calls(2)
        trace.mark_completed(outcome_verified=True)

    assert paths["traj"].exists()
    line = paths["traj"].read_text(encoding="utf-8").strip().splitlines()[0]
    doc = json.loads(line)
    assert doc["task_id"] == "t1"
    assert doc["completed"] is True
    assert doc["api_calls"] == 2

    assert paths["stats"].exists()
    stats = json.loads(paths["stats"].read_text(encoding="utf-8"))
    assert stats["WebSearch"]["count"] == 1

    # Phase 2: session index populated by the same trace_task exit.
    from memory.session_index import row_count, search

    assert row_count(path=paths["index"]) >= 1
    hits = search("write about X", path=paths["index"])
    assert hits, "expected FTS5 hit for indexed trajectory turn"


def test_workflow_trace_helper_uses_name_and_user_in_task_id(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg
    from evolution.trace import workflow_trace

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    with workflow_trace("unit-demo", user_id="liquan") as trace:
        trace.add_user("cycle")
        trace.mark_completed(outcome_verified=True)

    paths = _redirect.__wrapped__ if hasattr(_redirect, "__wrapped__") else None  # noqa: B019
    # Simpler: reload via config + verify at least one entry exists
    assert cfg.TRAJECTORY_FILE.exists()
    import json

    line = cfg.TRAJECTORY_FILE.read_text(encoding="utf-8").strip().splitlines()[-1]
    doc = json.loads(line)
    assert doc["task_id"].startswith("unit-demo_")
    assert doc["task_id"].endswith("_liquan")


def test_traced_decorator_wraps_and_preserves_return(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg
    from evolution import traced

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    @traced("decofn")
    def compute(user_id: str = "ang", x: int = 2):
        return x * 3

    assert compute(x=7) == 21
    # Trajectory appended on call
    lines = cfg.TRAJECTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    import json

    assert any(json.loads(l)["task_id"].startswith("decofn_") for l in lines)


def test_traced_decorator_flag_off_is_pure_noop(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg
    from evolution import traced

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", False)

    @traced("off")
    def compute(user_id="ang"):
        return "yes"

    assert compute() == "yes"
    assert not cfg.TRAJECTORY_FILE.exists()


def test_trace_marks_crashed_on_exception(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    import evolution.config as cfg

    monkeypatch.setattr(cfg, "ENABLE_TRAJECTORY_V2", True)

    try:
        with trace_task("boom", "writer") as trace:
            trace.add_user("crash me")
            raise RuntimeError("simulated worker crash")
    except RuntimeError:
        pass

    import evolution.config as cfg

    # Reload from the patched global file to confirm crashed=True landed.
    from evolution.rewards_v2 import load_recent_trajectories

    recs = load_recent_trajectories(days=1, path=cfg.TRAJECTORY_FILE)
    assert any(r.task_id == "boom" and r.crashed for r in recs)
