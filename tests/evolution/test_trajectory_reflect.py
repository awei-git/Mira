"""Reflect-side helpers: context formatting + proposal parsing/routing."""

from __future__ import annotations

import json
from pathlib import Path

from evolution.trajectory_recorder import TrajectoryRecorder
from evolution.trajectory_reflect import (
    format_reflect_context,
    needs_human_review,
    parse_skill_diff,
    record_proposals,
)


def _build(task_id: str, agent: str, *, successes: int, failures: int, crashed: bool = False):
    rec = TrajectoryRecorder(task_id, agent)
    for _ in range(successes):
        rec.record_tool("Read", success=True)
    for _ in range(failures):
        rec.record_tool("Read", success=False)
    return rec.finalize(completed=not crashed, crashed=crashed)


def test_format_reflect_context_empty_returns_empty_string():
    assert format_reflect_context(trajectories=[]) == ""


def test_format_reflect_context_renders_markdown_sections(tmp_path):
    trajectories = [
        _build("a", "writer", successes=3, failures=0),
        _build("b", "writer", successes=0, failures=2),
        _build("c", "explorer", successes=1, failures=1, crashed=True),
    ]
    text = format_reflect_context(trajectories=trajectories, tool_stats_path=tmp_path / "nope.json")

    assert "## Trajectory reward summary" in text
    assert "records: **3**" in text
    assert "crash count: 1" in text
    assert "## Top-scoring trajectories" in text
    assert "## Bottom-scoring trajectories" in text
    assert "writer" in text and "explorer" in text


def test_parse_skill_diff_tolerates_markdown_fence():
    output = """
    sure, here is the diff:

    ```json
    [
      {"kind": "create", "target": "writer.hook_first_line_v2",
       "rationale": "top scorers all use question leads",
       "affects": "writer skills"},
      {"kind": "config_change", "target": "config.PUBLISH_COOLDOWN_DAYS",
       "rationale": "engagement rebounds after 3d, not 1d",
       "affects": "publish flow"}
    ]
    ```
    """
    proposals = parse_skill_diff(output)
    assert len(proposals) == 2
    assert proposals[0]["kind"] == "create"


def test_parse_skill_diff_empty_or_invalid_returns_empty_list():
    assert parse_skill_diff("") == []
    assert parse_skill_diff("not json at all") == []
    assert parse_skill_diff('{"single": "object"}') == []


def test_needs_human_review_catches_publish_sensitive_targets():
    assert needs_human_review({"target": "config.SUBSTACK_COOLDOWN", "affects": "publish flow"})
    assert needs_human_review({"target": "preflight_check", "affects": ""})
    assert not needs_human_review({"target": "writer.hook_first_line", "affects": "skill content only"})


def test_record_proposals_splits_by_review_need(tmp_path):
    target = tmp_path / "proposed.jsonl"
    proposals = [
        {"kind": "create", "target": "writer.hook_variant", "rationale": "new hook", "affects": "writer skills"},
        {
            "kind": "config_change",
            "target": "config.PUBLISH_COOLDOWN_DAYS",
            "rationale": "tighten cooldown",
            "affects": "publish flow",
        },
    ]
    auto, review = record_proposals(proposals, path=target)
    assert len(auto) == 1 and auto[0]["target"] == "writer.hook_variant"
    assert len(review) == 1 and review[0]["target"] == "config.PUBLISH_COOLDOWN_DAYS"

    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row0 = json.loads(lines[0])
    assert row0["needs_review"] is False
    row1 = json.loads(lines[1])
    assert row1["needs_review"] is True
