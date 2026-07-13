"""Supervisor lifecycle: heartbeat → stale → crash report → kill → Phase 1 penalty."""

from __future__ import annotations

import json
import time


def test_healthy_worker_never_flagged(tmp_path):
    from supervisor.worker_supervisor import WorkerSupervisor, write_heartbeat

    root = tmp_path / "workers"
    write_heartbeat("healthy-1", agent="writer", pid=111, root=root)

    sup = WorkerSupervisor(root=root, stale_seconds=30, crashes_file=tmp_path / "crashes.jsonl")
    assert sup.scan() == []


def test_stale_worker_records_crash_and_triggers_kill_path(tmp_path, monkeypatch):
    import signal as _signal

    from supervisor.worker_supervisor import WorkerSupervisor

    root = tmp_path / "workers"
    (root / "hung-1").mkdir(parents=True)
    (root / "hung-1" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 200, "agent": "writer", "pid": 7777}),
        encoding="utf-8",
    )

    signals = []

    def fake_kill(pid, sig):
        if sig == 0:
            raise ProcessLookupError
        signals.append(sig)

    monkeypatch.setattr("os.kill", fake_kill)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    crashes = tmp_path / "crashes.jsonl"
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=crashes)
    reports, killed = sup.enforce()

    assert len(reports) == 1
    assert 7777 in killed
    assert _signal.SIGTERM in signals
    # crash was persisted
    lines = crashes.read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[0])
    assert row["task_id"] == "hung-1"


def test_crash_signal_feeds_phase1_reward_via_crashed_flag(tmp_path, monkeypatch):
    """A trajectory marked crashed=True earns crash_penalty in reward_v2."""
    from evolution.rewards_v2 import compute_trajectory_reward
    from evolution.trajectory_recorder import TrajectoryRecorder

    rec = TrajectoryRecorder("hung-2", "writer")
    rec.record_tool("Read", success=True)
    crashed_traj = rec.finalize(completed=False, crashed=True)

    score, components = compute_trajectory_reward(crashed_traj)
    assert "crash_penalty" in components
    assert components["crash_penalty"] < 0
    assert score < 0
