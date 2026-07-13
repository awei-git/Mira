from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


@dataclass(frozen=True)
class ProcessLockRecord:
    pid: int
    started_at: float
    heartbeat_at: float


class ProcessLockActive(RuntimeError):
    pass


def _default_lock_path() -> Path:
    from config import DATA_DIR

    return DATA_DIR / "locks" / "launchagent.pid"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_record(path: Path) -> ProcessLockRecord | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProcessLockRecord(
            pid=int(data.get("pid", 0)),
            started_at=float(data.get("started_at", 0)),
            heartbeat_at=float(data.get("heartbeat_at", 0)),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_record(path: Path, record: ProcessLockRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "pid": record.pid,
                "started_at": record.started_at,
                "heartbeat_at": record.heartbeat_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


@contextmanager
def launchagent_lock(
    *,
    path: Path | None = None,
    ttl_s: float = 300,
    now: Callable[[], float] = time.time,
    pid: int | None = None,
    is_alive: Callable[[int], bool] = _pid_alive,
) -> Iterator[None]:
    """Single-invocation guard for the LaunchAgent cycle."""
    lock_path = path or _default_lock_path()
    current_pid = int(pid or os.getpid())
    ts = float(now())
    existing = _read_record(lock_path)
    if existing and existing.pid != current_pid:
        fresh = ts - existing.heartbeat_at < ttl_s
        if fresh and is_alive(existing.pid):
            raise ProcessLockActive(f"launchagent cycle already active with pid {existing.pid}")

    record = ProcessLockRecord(pid=current_pid, started_at=ts, heartbeat_at=ts)
    _write_record(lock_path, record)
    try:
        yield
    finally:
        latest = _read_record(lock_path)
        if latest and latest.pid == current_pid:
            try:
                lock_path.unlink()
            except OSError:
                pass
