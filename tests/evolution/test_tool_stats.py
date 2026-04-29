"""Global tool_stats aggregation + atomic IO."""

from __future__ import annotations

import json

from evolution.tool_stats import (
    load_tool_stats,
    merge_into_global,
    save_tool_stats,
    success_rate_snapshot,
)
from evolution.trajectory_recorder import TrajectoryRecorder


def test_load_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "nope.json"
    assert load_tool_stats(missing) == {}


def test_save_and_load_roundtrip(tmp_path):
    from schemas.trajectory import ToolStat

    target = tmp_path / "stats.json"
    save_tool_stats({"Read": ToolStat(name="Read", count=3, success=2, failure=1)}, target)
    restored = load_tool_stats(target)
    assert restored["Read"].count == 3
    assert restored["Read"].success == 2
    data = json.loads(target.read_text(encoding="utf-8"))
    assert "Read" in data


def test_merge_accumulates_counts(tmp_path):
    target = tmp_path / "stats.json"
    rec = TrajectoryRecorder("tA", "writer")
    rec.record_tool("Grep", success=True)
    rec.record_tool("Grep", success=True)
    rec.record_tool("Read", success=False)
    trajectory_a = rec.finalize(completed=True)

    merge_into_global(trajectory_a, target)

    rec2 = TrajectoryRecorder("tB", "writer")
    rec2.record_tool("Grep", success=False)
    trajectory_b = rec2.finalize(completed=True)
    merge_into_global(trajectory_b, target)

    stats = load_tool_stats(target)
    assert stats["Grep"].count == 3
    assert stats["Grep"].success == 2
    assert stats["Grep"].failure == 1
    assert stats["Read"].count == 1
    assert stats["Read"].failure == 1

    rates = success_rate_snapshot(stats)
    assert abs(rates["Grep"] - 2 / 3) < 1e-9
    assert rates["Read"] == 0.0
