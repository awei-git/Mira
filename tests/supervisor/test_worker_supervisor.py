"""Heartbeat write/clear + stale detection."""

from __future__ import annotations

import json
import time

from supervisor.worker_supervisor import (
    WorkerSupervisor,
    clear_heartbeat,
    write_heartbeat,
)


def test_heartbeat_roundtrip(tmp_path):
    root = tmp_path / "workers"
    write_heartbeat("task-1", agent="writer", pid=12345, root=root)
    hb = root / "task-1" / "heartbeat"
    assert hb.exists()
    payload = json.loads(hb.read_text(encoding="utf-8"))
    assert payload["agent"] == "writer"
    assert payload["pid"] == 12345
    assert abs(payload["ts"] - time.time()) < 2


def test_clear_removes_heartbeat_and_dir(tmp_path):
    root = tmp_path / "workers"
    write_heartbeat("task-2", agent="explorer", root=root)
    clear_heartbeat("task-2", root=root)
    assert not (root / "task-2" / "heartbeat").exists()


def test_scan_ignores_fresh_heartbeats(tmp_path):
    root = tmp_path / "workers"
    write_heartbeat("fresh-1", agent="writer", root=root)
    sup = WorkerSupervisor(root=root, stale_seconds=30, crashes_file=tmp_path / "crashes.jsonl")
    assert sup.scan() == []


def test_scan_detects_stale_heartbeats(tmp_path):
    root = tmp_path / "workers"
    (root / "stale-1").mkdir(parents=True)
    (root / "stale-1" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "writer", "pid": 42}),
        encoding="utf-8",
    )
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=tmp_path / "crashes.jsonl")
    reports = sup.scan()
    assert len(reports) == 1
    assert reports[0].task_id == "stale-1"
    assert reports[0].agent == "writer"


def test_scan_dedups_subsequent_calls(tmp_path):
    root = tmp_path / "workers"
    (root / "stale-2").mkdir(parents=True)
    (root / "stale-2" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "writer", "pid": 42}),
        encoding="utf-8",
    )
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=tmp_path / "crashes.jsonl")
    first = sup.scan()
    second = sup.scan()
    assert len(first) == 1
    assert second == []


def test_record_crashes_appends_jsonl(tmp_path):
    root = tmp_path / "workers"
    (root / "stale-3").mkdir(parents=True)
    (root / "stale-3" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "writer", "pid": 99}),
        encoding="utf-8",
    )
    crashes_file = tmp_path / "crashes.jsonl"
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=crashes_file)
    reports = sup.scan()
    assert sup.record_crashes(reports) == 1
    lines = crashes_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["task_id"] == "stale-3"
    assert "stale" in doc["reason"]


def test_scan_ignores_malformed_heartbeats(tmp_path):
    root = tmp_path / "workers"
    (root / "bad-1").mkdir(parents=True)
    (root / "bad-1" / "heartbeat").write_text("not json", encoding="utf-8")
    sup = WorkerSupervisor(root=root, stale_seconds=30, crashes_file=tmp_path / "crashes.jsonl")
    assert sup.scan() == []


def test_tick_runs_scan_and_record(tmp_path):
    root = tmp_path / "workers"
    (root / "stale-tick").mkdir(parents=True)
    (root / "stale-tick" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "x", "pid": 1}),
        encoding="utf-8",
    )
    crashes = tmp_path / "crashes.jsonl"
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=crashes)
    reports = sup.tick()
    assert len(reports) == 1
    assert crashes.exists()


def test_terminate_stale_sigterms_then_sigkills(tmp_path, monkeypatch):
    """Supervisor SIGTERMs first; escalates to SIGKILL if PID still alive."""
    import signal as _signal

    root = tmp_path / "workers"
    (root / "stubborn").mkdir(parents=True)
    (root / "stubborn" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "writer", "pid": 4242}),
        encoding="utf-8",
    )

    signals_seen: list[tuple[int, int]] = []
    alive = {"state": True}

    def fake_kill(pid, sig):
        if sig == 0:
            if alive["state"]:
                return
            raise ProcessLookupError
        signals_seen.append((pid, sig))
        # Pretend SIGTERM is ignored; SIGKILL always works.
        if sig == _signal.SIGKILL:
            alive["state"] = False

    monkeypatch.setattr("os.kill", fake_kill)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=tmp_path / "crashes.jsonl")
    killed = sup.terminate_stale(sigterm_grace_seconds=0.5)
    assert 4242 in killed
    signal_types = [s for _pid, s in signals_seen]
    assert _signal.SIGTERM in signal_types
    assert _signal.SIGKILL in signal_types
    # Heartbeat cleaned so next scan won't re-flag.
    assert not (root / "stubborn" / "heartbeat").exists()


def test_terminate_stale_handles_already_dead_pid(tmp_path, monkeypatch):
    root = tmp_path / "workers"
    (root / "ghost").mkdir(parents=True)
    (root / "ghost" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "x", "pid": 9999}),
        encoding="utf-8",
    )

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", fake_kill)
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=tmp_path / "crashes.jsonl")
    killed = sup.terminate_stale()
    assert killed == []


def test_enforce_is_one_shot_scan_record_kill(tmp_path, monkeypatch):
    root = tmp_path / "workers"
    (root / "one-shot").mkdir(parents=True)
    (root / "one-shot" / "heartbeat").write_text(
        json.dumps({"ts": time.time() - 120, "agent": "x", "pid": 111}),
        encoding="utf-8",
    )

    def fake_kill(pid, sig):
        # Simulate clean SIGTERM handling.
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr("os.kill", fake_kill)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    crashes = tmp_path / "crashes.jsonl"
    sup = WorkerSupervisor(root=root, stale_seconds=60, crashes_file=crashes)
    reports, killed = sup.enforce()
    assert len(reports) == 1
    assert 111 in killed
    assert crashes.exists()
