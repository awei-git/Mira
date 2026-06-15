#!/usr/bin/env python3
"""Mira Survival Kernel — the unkillable supervisor (V4 Phase 1).

This file is STDLIB-ONLY by design. It imports NOTHING from the Mira app, so an
application import error (the class of fault that kept the agent dead for weeks)
can never kill the supervisor itself.

What it does each tick:
  1. reap leaked detached LLM subprocesses (codex/claude `start_new_session=True`
     children survive `launchctl kickstart -k`, so the watchdog can't reap them)
  2. loading-preflight: `import core` in a throwaway subprocess — catches
     module-level import drift that `py_compile` cannot (RC-2)
  3. run one `core.py run` cycle as a TIME-BOXED child; on hang, SIGKILL the whole
     process group so a stuck LLM call can't freeze the organism forever (RC-5)
  4. write a status-rich supervisor heartbeat every tick, so a crash-looping agent
     (fresh-but-failing) is distinguishable from a healthy one (RC-6)
  5. on repeated failure: alert + back off. NEVER `git stash` (the old launcher's
     auto-rollback deleted uncommitted fixes and re-broke the agent — RC-3).

It does NOT touch the bridge heartbeat (core.py owns that rich schema); it writes
its own data/state/supervisor.json and data/state/run_ledger.jsonl.

Modes:
  (default)    supervised loop — intended to replace bin/mira-agent.sh
  --once       run exactly one supervised cycle, then exit
  --self-test  preflight + reaper + heartbeat write once (no cmd_run); exit 0/1
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --- paths (resolved from this file's location; no app imports) ---------------
MIRA_ROOT = Path(__file__).resolve().parents[2]  # agents/super/ -> Mira/
CORE_DIR = MIRA_ROOT / "agents" / "super"
STATE_DIR = MIRA_ROOT / "data" / "state"
SUPERVISOR_FILE = STATE_DIR / "supervisor.json"
RUN_LEDGER = STATE_DIR / "run_ledger.jsonl"
SUPERVISOR_LOG = Path("/tmp/mira-survival.log")
BRIDGE_ITEMS = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/MtJoy/Mira-Bridge/users/ang/items"

# --- tunables (env-overridable) ----------------------------------------------
PYTHON = os.environ.get("MIRA_PYTHON", sys.executable)
TICK = int(os.environ.get("MIRA_TICK", "30"))  # seconds between cycles
CYCLE_BUDGET = int(os.environ.get("MIRA_CYCLE_BUDGET", "600"))  # hard-kill a cmd_run cycle after this
PREFLIGHT_BUDGET = int(os.environ.get("MIRA_PREFLIGHT_BUDGET", "90"))
REAP_AGE = int(os.environ.get("MIRA_REAP_AGE", "900"))  # kill leaked LLM subprocs older than this
MAX_CONSEC_FAIL = int(os.environ.get("MIRA_MAX_CONSEC_FAIL", "5"))
BACKOFF_SLEEP = int(os.environ.get("MIRA_BACKOFF_SLEEP", "300"))

_running = True


# --- small helpers ------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _log(msg: str) -> None:
    stamp = datetime.now().astimezone().strftime("%a %b %d %H:%M:%S %Z %Y")
    line = f"{stamp}: [survival_kernel] {msg}"
    try:
        with SUPERVISOR_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_heartbeat(status: str, last_cycle: dict | None = None, consec_fail: int = 0) -> None:
    """status: ok | degraded | preflight_failed | crash_backoff | self_test"""
    try:
        _atomic_write(
            SUPERVISOR_FILE,
            {
                "updated_at": _now_iso(),
                "ts": time.time(),
                "status": status,
                "pid": os.getpid(),
                "last_cycle": last_cycle or {},
                "consecutive_failures": consec_fail,
                "tick_s": TICK,
                "cycle_budget_s": CYCLE_BUDGET,
            },
        )
    except OSError as e:
        _log(f"heartbeat write failed: {e}")


def append_ledger(rec: dict) -> None:
    rec = {"ts": time.time(), "at": _now_iso(), **rec}
    try:
        RUN_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with RUN_LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        _log(f"ledger write failed: {e}")


def _etime_to_seconds(et: str) -> int:
    """Parse BSD/macOS `ps -o etime` ([[dd-]hh:]mm:ss) into seconds."""
    et = et.strip()
    days = 0
    if "-" in et:
        d, et = et.split("-", 1)
        days = int(d)
    parts = [int(p) for p in et.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3], parts[-2], parts[-1]
    return days * 86400 + h * 3600 + m * 60 + s


def reap_orphans() -> int:
    """Kill leaked detached `codex exec` subprocesses older than REAP_AGE.

    These accumulate because they are spawned start_new_session=True and survive
    `launchctl kickstart -k`. A legitimate in-cycle codex call is young
    (<< REAP_AGE), so age-gating protects live work. We deliberately match only
    'codex exec' (the proven leak) — never a bare 'claude' substring, which could
    match this very supervisor's tooling.
    """
    killed = 0
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid,etime,command"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except Exception as e:  # noqa: BLE001 — reaper must never raise into the loop
        _log(f"reaper ps failed: {e}")
        return 0
    me = os.getpid()
    for line in out.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, et_s, cmd = parts
        if "codex exec" not in cmd:
            continue
        try:
            pid = int(pid_s)
            age = _etime_to_seconds(et_s)
        except ValueError:
            continue
        if pid == me or age < REAP_AGE:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
            _log(f"reaped leaked subprocess pid={pid} age={age}s")
        except OSError:
            pass
    return killed


def preflight() -> tuple[bool, str]:
    """Faithfully reproduce core.py's import to catch module-level import drift.

    core.py bootstraps its own sys.path (lines 28-31) and guards main() behind
    __main__, so `import core` is side-effect-free and exercises the real import
    graph. NOTE: this catches module-level failures; function-level lazy-import
    failures are contained by Layer 2 (per-phase try/except) instead.
    """
    try:
        r = subprocess.run(
            [PYTHON, "-c", "import core"],
            cwd=str(CORE_DIR),
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_BUDGET,
        )
    except subprocess.TimeoutExpired:
        return False, "preflight import timed out"
    except Exception as e:  # noqa: BLE001
        return False, f"preflight spawn failed: {e}"
    if r.returncode != 0:
        tail = [ln for ln in (r.stderr or "").strip().splitlines() if ln.strip()]
        return False, (tail[-1] if tail else f"import exit {r.returncode}")
    return True, ""


def _cycle_env() -> dict:
    """Match the old launcher: clear nested-session detection so core.py and its
    sub-agents don't mistake the supervisor for a nested Claude Code session."""
    env = dict(os.environ)
    for k in ("CLAUDE_CODE_ENTRYPOINT", "CLAUDECODE", "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING"):
        env.pop(k, None)
    return env


def run_cycle() -> dict:
    """Run one `core.py run` cycle, time-boxed; SIGKILL the whole group on hang."""
    t0 = time.monotonic()
    proc = subprocess.Popen([PYTHON, "core.py", "run"], cwd=str(CORE_DIR), start_new_session=True, env=_cycle_env())
    try:
        rc = proc.wait(timeout=CYCLE_BUDGET)
        return {"exit_code": rc, "duration_s": round(time.monotonic() - t0, 1), "killed": False}
    except subprocess.TimeoutExpired:
        _log(f"cycle exceeded {CYCLE_BUDGET}s — SIGKILL process group {proc.pid}")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        return {"exit_code": -9, "duration_s": round(time.monotonic() - t0, 1), "killed": True}


def alert(msg: str) -> None:
    """Best-effort crash item to the iPhone bridge. NEVER touches git."""
    try:
        BRIDGE_ITEMS.mkdir(parents=True, exist_ok=True)
        mid = uuid.uuid4().hex[:8]
        iso = _now_iso()
        item = {
            "id": f"req_superv_{mid}",
            "type": "request",
            "title": "Survival kernel alert",
            "status": "failed",
            "tags": ["system", "survival"],
            "origin": "agent",
            "created_at": iso,
            "updated_at": iso,
            "messages": [{"id": mid, "sender": "agent", "content": msg, "timestamp": iso, "kind": "error"}],
            "error": {"code": "survival", "message": msg, "retryable": False, "timestamp": iso},
        }
        tmp = BRIDGE_ITEMS / f"req_superv_{mid}.tmp"
        tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(BRIDGE_ITEMS / f"req_superv_{mid}.json")
    except Exception as e:  # noqa: BLE001
        _log(f"alert write failed: {e}")


def one_supervised_cycle(state: dict) -> None:
    reaped = reap_orphans()

    ok, reason = preflight()
    if not ok:
        state["consec"] += 1
        _log(f"PREFLIGHT FAILED ({state['consec']}x): {reason}")
        write_heartbeat("preflight_failed", {"preflight_error": reason, "reaped": reaped}, state["consec"])
        append_ledger({"event": "preflight_failed", "reason": reason, "reaped": reaped})
        if state["consec"] == 1 or state["consec"] % 20 == 0:
            alert(f"Mira preflight failing — agent paused (no auto-stash). {reason}")
        return

    result = run_cycle()
    result["reaped"] = reaped
    append_ledger({"event": "cycle", **result})

    if result["exit_code"] == 0:
        state["consec"] = 0
        write_heartbeat("ok", result, 0)
    else:
        state["consec"] += 1
        status = "crash_backoff" if state["consec"] >= MAX_CONSEC_FAIL else "degraded"
        write_heartbeat(status, result, state["consec"])
        _log(f"cycle failed exit={result['exit_code']} killed={result['killed']} consec={state['consec']}")
        if state["consec"] in (2, MAX_CONSEC_FAIL) or state["consec"] % 20 == 0:
            alert(
                f"Mira cycle failing (exit {result['exit_code']}, {state['consec']}x). "
                f"NO auto-stash performed; a fix + commit is required."
            )


def _handle_sigterm(signum, frame):  # noqa: ARG001
    global _running
    _running = False
    _log(f"received signal {signum} — exiting loop after current cycle")


def loop() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    _log(f"survival kernel starting (pid {os.getpid()}, tick {TICK}s, cycle_budget {CYCLE_BUDGET}s)")
    state = {"consec": 0}
    while _running:
        try:
            one_supervised_cycle(state)
        except Exception as e:  # noqa: BLE001 — the loop must survive anything
            _log(f"supervisor-level error (non-fatal): {e!r}")
            try:
                write_heartbeat("degraded", {"supervisor_error": repr(e)}, state["consec"])
            except Exception:  # noqa: BLE001
                pass
        sleep_s = TICK if state["consec"] < MAX_CONSEC_FAIL else max(TICK, BACKOFF_SLEEP)
        slept = 0
        while _running and slept < sleep_s:
            time.sleep(min(5, sleep_s - slept))
            slept += 5
    _log("survival kernel stopped")


def self_test() -> int:
    print("== survival_kernel self-test ==")
    print(f"MIRA_ROOT      = {MIRA_ROOT}")
    print(f"core.py exists = {(CORE_DIR / 'core.py').exists()}")
    print(f"python         = {PYTHON}")
    reaped = reap_orphans()
    print(f"reaper         = killed {reaped} leaked codex subprocess(es) (>{REAP_AGE}s)")
    ok, reason = preflight()
    print(f"preflight      = {'OK' if ok else 'FAIL: ' + reason}")
    write_heartbeat("self_test", {"self_test": True, "preflight_ok": ok}, 0)
    print(f"heartbeat      = wrote {SUPERVISOR_FILE} (exists={SUPERVISOR_FILE.exists()})")
    print(f"ledger         = {RUN_LEDGER}")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Mira Survival Kernel")
    ap.add_argument("--once", action="store_true", help="run one supervised cycle and exit")
    ap.add_argument("--self-test", action="store_true", help="preflight+reaper+heartbeat, no cmd_run")
    args = ap.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if args.once:
        one_supervised_cycle({"consec": 0})
        return
    loop()


if __name__ == "__main__":
    main()
