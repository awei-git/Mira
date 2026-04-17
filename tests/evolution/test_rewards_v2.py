"""Phase 1 reward computation — derived from trajectory facts only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evolution.config import REWARD_WEIGHTS
from evolution.rewards_v2 import (
    compute_trajectory_reward,
    load_recent_trajectories,
    summarize_rewards,
)
from evolution.trajectory_recorder import TrajectoryRecorder, append_to_global


def _trajectory_with_tools(task_id: str, *, successes: int, failures: int):
    rec = TrajectoryRecorder(task_id, "writer")
    for _ in range(successes):
        rec.record_tool("Read", success=True)
    for _ in range(failures):
        rec.record_tool("Read", success=False)
    return rec.finalize(completed=True)


def test_reward_zero_when_no_signals():
    t = TrajectoryRecorder("empty", "writer").finalize(completed=True)
    score, components = compute_trajectory_reward(t)
    assert score == 0.0
    assert components == {}


def test_tool_success_rate_contributes_positively():
    t = _trajectory_with_tools("t", successes=3, failures=1)
    score, components = compute_trajectory_reward(t)
    assert "tool_success_rate" in components
    assert components["tool_success_rate"] == 0.75 * REWARD_WEIGHTS["tool_success_rate"]
    assert score == round(components["tool_success_rate"], 3)


def test_outcome_verified_flag_applied():
    t = _trajectory_with_tools("t", successes=1, failures=0)
    score_true, _ = compute_trajectory_reward(t, outcome_verified=True)
    score_false, _ = compute_trajectory_reward(t, outcome_verified=False)
    assert score_true > score_false
    assert score_true - score_false == REWARD_WEIGHTS["outcome_verified"]


def test_crash_penalty_applied():
    rec = TrajectoryRecorder("t", "writer")
    rec.record_tool("Read", success=True)
    t = rec.finalize(completed=False, crashed=True)
    score, components = compute_trajectory_reward(t)
    assert "crash_penalty" in components
    assert components["crash_penalty"] == REWARD_WEIGHTS["crash_penalty"]
    assert score < 0


def test_time_cost_penalty_capped_at_one_budget_unit():
    t = _trajectory_with_tools("t", successes=2, failures=0)
    # 3x overrun — should clip to 1.0 * weight
    _, components = compute_trajectory_reward(t, elapsed_seconds=400.0, budget_seconds=100.0)
    assert components["time_cost_penalty"] == REWARD_WEIGHTS["time_cost_penalty"]


def test_substack_subscribers_diminishing_returns():
    t = TrajectoryRecorder("t", "writer").finalize(completed=True)
    _, c1 = compute_trajectory_reward(t, substack_new_subs_24h=1)
    _, c9 = compute_trajectory_reward(t, substack_new_subs_24h=9)
    _, c100 = compute_trajectory_reward(t, substack_new_subs_24h=100)
    assert c9["substack_new_subs_24h"] == 3.0 * REWARD_WEIGHTS["substack_new_subs_24h"]
    # sqrt growth, not linear
    assert c100["substack_new_subs_24h"] < 100 * c1["substack_new_subs_24h"]


def test_reader_feedback_signed_application():
    t = TrajectoryRecorder("t", "writer").finalize(completed=True)
    _, pos = compute_trajectory_reward(t, reader_feedback=3)
    _, neg = compute_trajectory_reward(t, reader_feedback=-2)
    assert pos["reader_feedback_positive"] == 3 * REWARD_WEIGHTS["reader_feedback_positive"]
    assert neg["reader_feedback_negative"] == 2 * REWARD_WEIGHTS["reader_feedback_negative"]


def test_load_recent_trajectories_filters_by_window(tmp_path, monkeypatch):
    import evolution.config as cfg
    import evolution.trajectory_recorder as recorder_mod

    target = tmp_path / "trajectories.jsonl"
    monkeypatch.setattr(cfg, "TRAJECTORY_FILE", target)
    monkeypatch.setattr(recorder_mod, "TRAJECTORY_FILE", target)

    old = TrajectoryRecorder("old", "writer").finalize(completed=True)
    old.timestamp = datetime.now(timezone.utc) - timedelta(days=30)
    new = TrajectoryRecorder("new", "writer").finalize(completed=True)
    append_to_global(old)
    append_to_global(new)

    recent = load_recent_trajectories(days=7, path=target)
    assert {r.task_id for r in recent} == {"new"}


def test_summarize_rewards_empty_and_populated():
    assert summarize_rewards([]) == {"count": 0}

    trajectories = [
        _trajectory_with_tools("a", successes=2, failures=0),
        _trajectory_with_tools("b", successes=0, failures=2),
    ]
    s = summarize_rewards(trajectories)
    assert s["count"] == 2
    assert s["mean_score"] >= s["min_score"]
    assert s["max_score"] >= s["mean_score"]
