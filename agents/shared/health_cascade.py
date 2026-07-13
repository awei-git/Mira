"""Transparent operational health cascade for Mira."""

import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from agents.shared.config import MIRA_DIR, TASKS_DIR


def health_cascade() -> dict:
    log = logging.getLogger("mira")
    now = time.time()
    heartbeat = Path(MIRA_DIR) / "heartbeat.json"
    crash_log = Path("/tmp/mira-crash.log")
    tasks_dir = Path(TASKS_DIR)
    cascade_trace = []
    root_cause = None

    def emit(step: str, status: str, detail: str) -> None:
        cascade_trace.append({"step": step, "status": status, "detail": detail})
        log.info("CASCADE step=%s status=%s detail=%s", step, status, detail)

    def parse_timestamp(value) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def heartbeat_timestamp() -> float | None:
        try:
            data = json.loads(heartbeat.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        if isinstance(data, dict):
            for key in ("last_heartbeat", "timestamp", "updated_at", "last_updated", "ts"):
                ts = parse_timestamp(data.get(key))
                if ts is not None:
                    return ts
        try:
            return heartbeat.stat().st_mtime
        except OSError:
            return None

    def tail_crash_log(max_lines: int = 80) -> list[str]:
        try:
            lines = crash_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return lines[-max_lines:]

    def last_error(lines: list[str]) -> str | None:
        for line in reversed(lines):
            clean = line.strip()
            if not clean:
                continue
            lowered = clean.lower()
            if any(token in lowered for token in ("traceback", "error", "exception", "failed", "preflight")):
                return clean[:500]
        for line in reversed(lines):
            clean = line.strip()
            if clean:
                return clean[:500]
        return None

    ts = heartbeat_timestamp()
    if ts is not None:
        age_seconds = max(0.0, now - ts)
        if age_seconds < 300:
            emit("heartbeat", "CASCADE_OK", f"fresh age_seconds={age_seconds:.1f} path={heartbeat}")
            return {"status": "ok", "cascade_trace": cascade_trace, "root_cause": None}
        emit("heartbeat", "stale", f"age_seconds={age_seconds:.1f} path={heartbeat}")
        root_cause = f"heartbeat stale age_seconds={age_seconds:.1f}"
    else:
        emit("heartbeat", "missing", f"timestamp unreadable path={heartbeat}")
        root_cause = f"heartbeat missing or unreadable: {heartbeat}"

    try:
        proc = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True, timeout=3)
        process_lines = proc.stdout.splitlines() if proc.returncode == 0 else []
    except (OSError, subprocess.SubprocessError) as exc:
        process_lines = []
        emit("python_process", "unknown", f"ps failed: {exc}")

    python_processes = [
        line.strip()
        for line in process_lines
        if "python" in line.lower() and ("task_worker.py" in line or "core.py" in line)
    ]
    if not python_processes:
        crash_lines = tail_crash_log()
        error = last_error(crash_lines)
        detail = "no python task_worker.py/core.py process"
        if error:
            detail = f"{detail}; last_error={error}"
        emit("python_process", "dead", detail)
        return {
            "status": "dead",
            "cascade_trace": cascade_trace,
            "root_cause": error or "python task_worker.py/core.py process is not running",
        }
    emit("python_process", "alive", f"matches={len(python_processes)}")

    crash_lines = tail_crash_log()
    crash_tail = "\n".join(crash_lines).lower()
    if "preflight" in crash_tail and any(
        token in crash_tail for token in ("failed", "error", "traceback", "exception")
    ):
        error = last_error(crash_lines)
        emit("preflight", "loop_suspected", error or "recent crash log contains preflight failures")
        root_cause = error or "preflight loop suspected from /tmp/mira-crash.log"
    else:
        emit("preflight", "no_loop_signal", f"checked {crash_log}")

    try:
        workspaces = [path for path in tasks_dir.iterdir() if path.is_dir()]
        workspaces.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError as exc:
        emit("task_worker", "unknown", f"ls failed path={tasks_dir}: {exc}")
        if root_cause is None:
            root_cause = f"cannot inspect task workspaces: {exc}"
        return {"status": "degraded", "cascade_trace": cascade_trace, "root_cause": root_cause}

    if not workspaces:
        emit("task_worker", "no_workspaces", f"no task workspaces in {tasks_dir}")
        if root_cause is None:
            root_cause = "no task workspaces found"
    else:
        recent = []
        for path in workspaces[:5]:
            try:
                age = max(0.0, now - path.stat().st_mtime)
            except OSError:
                continue
            recent.append(f"{path.name}:{age:.0f}s")
        latest_age = None
        try:
            latest_age = max(0.0, now - workspaces[0].stat().st_mtime)
        except OSError:
            pass
        status = "recent_workspaces" if latest_age is not None and latest_age < 900 else "no_recent_workspaces"
        emit("task_worker", status, "recent=" + ",".join(recent))
        if status == "no_recent_workspaces" and root_cause is None:
            root_cause = "heartbeat stale and no recent task workspace activity"

    return {"status": "degraded", "cascade_trace": cascade_trace, "root_cause": root_cause}
