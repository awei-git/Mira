"""Chaos tests for the V4 Survival Kernel.

Proves the supervisor survives the three death modes that historically killed Mira:
  1. bad import  -> preflight catches it (cycle skipped, not fatal)        [RC-2]
  2. hung cycle  -> SIGKILL of the whole process group; detached kids die  [RC-5]
  3. crash       -> reported as failure; loop continues, no git stash      [RC-1/RC-3]

These spawn real subprocesses so the SIGKILL/process-group guarantees are real,
not mocked. Run: pytest tests/v4/test_survival_chaos.py -v
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

_SUPER = Path(__file__).resolve().parents[2] / "agents" / "super"
sys.path.insert(0, str(_SUPER))
import survival_kernel as sk  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_dead(pid: int, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


def _write_fake_core(d: Path, body: str) -> None:
    (d / "core.py").write_text(body, encoding="utf-8")


# --- RC-2: preflight catches a bad import ------------------------------------
def test_preflight_catches_bad_import(tmp_path, monkeypatch):
    _write_fake_core(tmp_path, "import a_module_that_does_not_exist_xyz123\n")
    monkeypatch.setattr(sk, "CORE_DIR", tmp_path)
    ok, reason = sk.preflight()
    assert ok is False
    assert "ModuleNotFound" in reason or "No module" in reason or "xyz123" in reason


def test_preflight_passes_clean(tmp_path, monkeypatch):
    _write_fake_core(tmp_path, "VALUE = 1\n")
    monkeypatch.setattr(sk, "CORE_DIR", tmp_path)
    ok, reason = sk.preflight()
    assert ok is True and reason == ""


# --- RC-5: a hung cycle is killed, and the WHOLE process group dies -----------
def test_run_cycle_kills_hang_and_whole_group(tmp_path, monkeypatch):
    # fake core.py spawns a grandchild in the same process group, records both
    # pids, then hangs forever. The supervisor must kill the entire group.
    _write_fake_core(
        tmp_path,
        "import json, os, subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(999)'])\n"
        "open('pids.json','w').write(json.dumps({'child': os.getpid(), 'grandchild': gc.pid}))\n"
        "time.sleep(999)\n",
    )
    monkeypatch.setattr(sk, "CORE_DIR", tmp_path)
    monkeypatch.setattr(sk, "CYCLE_BUDGET", 2)

    result = sk.run_cycle()
    assert result["killed"] is True
    assert result["exit_code"] == -9
    assert result["duration_s"] >= 2

    pids = json.loads((tmp_path / "pids.json").read_text())
    assert _wait_dead(pids["child"]), "hung cycle child still alive after SIGKILL"
    assert _wait_dead(pids["grandchild"]), "detached grandchild survived (zombie-leak not fixed)"


# --- RC-1: a crashing cycle is reported, loop survives ------------------------
def test_run_cycle_reports_crash(tmp_path, monkeypatch):
    _write_fake_core(tmp_path, "import sys; sys.exit(3)\n")
    monkeypatch.setattr(sk, "CORE_DIR", tmp_path)
    monkeypatch.setattr(sk, "CYCLE_BUDGET", 30)
    result = sk.run_cycle()
    assert result["exit_code"] == 3 and result["killed"] is False


def test_failure_increments_consec_and_degrades(tmp_path, monkeypatch):
    # must import clean (so preflight passes) but exit nonzero when RUN
    _write_fake_core(tmp_path, "import sys\nif __name__ == '__main__':\n    sys.exit(1)\n")
    monkeypatch.setattr(sk, "CORE_DIR", tmp_path)
    monkeypatch.setattr(sk, "CYCLE_BUDGET", 30)
    monkeypatch.setattr(sk, "SUPERVISOR_FILE", tmp_path / "supervisor.json")
    monkeypatch.setattr(sk, "RUN_LEDGER", tmp_path / "run_ledger.jsonl")
    monkeypatch.setattr(sk, "BRIDGE_ITEMS", tmp_path / "items")
    monkeypatch.setattr(sk, "SUPERVISOR_LOG", tmp_path / "survival.log")
    state = {"consec": 0}
    sk.one_supervised_cycle(state)
    assert state["consec"] == 1
    hb = json.loads((tmp_path / "supervisor.json").read_text())
    assert hb["status"] in ("degraded", "crash_backoff")
    assert hb["last_cycle"]["exit_code"] == 1
    # ledger recorded the failed cycle
    led = (tmp_path / "run_ledger.jsonl").read_text().strip().splitlines()
    assert any(json.loads(line).get("event") == "cycle" for line in led)


# --- RC-3: the supervisor NEVER invokes git (structural guarantee) -----------
def test_source_never_invokes_git():
    # The docstring intentionally explains "we never git stash"; what matters is
    # that git is never actually invoked — i.e. no quoted 'git' argv token exists.
    src = (_SUPER / "survival_kernel.py").read_text()
    assert (
        "'git'" not in src and '"git"' not in src
    ), "survival kernel must never invoke git (RC-3: auto-stash deleted fixes)"


# --- etime parser (reaper relies on it) --------------------------------------
@pytest.mark.parametrize(
    "et,secs",
    [("05", 5), ("01:05", 65), ("02:01:05", 7265), ("1-02:01:05", 93665)],
)
def test_etime_parser(et, secs):
    assert sk._etime_to_seconds(et) == secs
