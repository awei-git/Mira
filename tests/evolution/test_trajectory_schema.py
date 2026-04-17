"""Schema contract tests for TrajectoryRecord / Turn / ToolStat."""

from __future__ import annotations

import json

import pytest

from schemas.trajectory import ToolStat, TrajectoryRecord, Turn


def test_turn_rejects_unknown_role():
    with pytest.raises(Exception):
        Turn(role="robot", content="nope")


def test_tool_stat_record_and_rate():
    s = ToolStat(name="Read")
    assert s.success_rate == 0.0
    s.record(True)
    s.record(True)
    s.record(False)
    assert s.count == 3 and s.success == 2 and s.failure == 1
    assert abs(s.success_rate - 2 / 3) < 1e-9


def test_trajectory_add_turn_and_record_tool():
    rec = TrajectoryRecord(task_id="t", agent="writer")
    rec.add_turn(Turn(role="system", content="hi"))
    rec.record_tool("Grep", success=True)
    rec.record_tool("Grep", success=False)
    assert len(rec.conversations) == 1
    assert rec.tool_stats["Grep"].count == 2
    assert rec.tool_stats["Grep"].failure == 1


def test_trajectory_roundtrip_json():
    rec = TrajectoryRecord(
        task_id="abc",
        agent="explorer",
        model="claude-opus-4-7",
        conversations=[
            Turn(role="system", content="sys"),
            Turn(role="human", content="what is X?"),
            Turn(role="assistant", content="checking", tool_name="WebSearch"),
            Turn(role="tool", content="", tool_name="WebSearch", tool_result_preview="...", tool_success=True),
            Turn(role="assistant", content="here is X"),
        ],
    )
    rec.record_tool("WebSearch", success=True)
    line = rec.as_jsonl_line()
    data = json.loads(line)
    restored = TrajectoryRecord.model_validate(data)
    assert restored.task_id == "abc"
    assert len(restored.conversations) == 5
    assert restored.tool_stats["WebSearch"].count == 1
