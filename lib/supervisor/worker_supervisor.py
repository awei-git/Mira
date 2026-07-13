"""Worker supervisor — heartbeat-based hang detection.

Workers emit a heartbeat (unix timestamp file) at regular intervals.
The supervisor scans the heartbeat directory on each agent cycle and
flags stale entries. Stale → record a CrashReport to
`data/soul/crashes.jsonl` so Phase 1 reward can pick it up.

Design notes:
- Heartbeats are a single-file-per-worker convention:
  `data/workers/<task_id>/heartbeat` contains `<unix_ts>`.
- Writes are rename-atomic so a crashed worker can't leave a
  half-written file.
- `scan()` is pure: it only reads + reports. Killing (SIGTERM) is a
  future step; when we add it we'll reuse this detection path.
- `record_crash()` is append-only JSONL so the file is safe to tail
  from another process.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR
from evolution.config import CRASHES_FILE

log = logging.getLogger("mira.supervisor")

WORKERS_DIR = DATA_DIR / "workers"

# Heartbeat must be refreshed at least this often; older = stale.
DEFAULT_STALE_SECONDS = 60.0


@dataclass
class CrashReport:
    task_id: str
    agent: str
    last_heartbeat_unix: float
    detected_at_unix: float
    reason: str
    pid: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Worker side — called from task_worker
# ---------------------------------------------------------------------------


def _heartbeat_path(task_id: str, root: Path | None = None) -> Path:
    base = root or WORKERS_DIR
    return base / task_id / "heartbeat"


def write_heartbeat(
    task_id: str,
    *,
    agent: str = "",
    pid: int | None = None,
    root: Path | None = None,
) -> None:
    """Write/refresh the worker's heartbeat. Soft-fails on IO error."""
    try:
        path = _heartbeat_path(task_id, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "agent": agent,
            "pid": pid if pid is not None else os.getpid(),
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="hb.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload))
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        log.debug("heartbeat write failed (task=%s): %s", task_id, e)


def clear_heartbeat(task_id: str, root: Path | None = None) -> None:
    """Remove the worker's heartbeat on clean exit.

    Missing heartbeat = either not started or already cleaned up, both
    fine — scan() ignores tasks without heartbeats.
    """
    try:
        path = _heartbeat_path(task_id, root)
        if path.exists():
            path.unlink()
        # Drop the now-empty task dir if possible.
        try:
            path.parent.rmdir()
        except OSError:
            pass
    except OSError as e:
        log.debug("clear_heartbeat failed (task=%s): %s", task_id, e)


# ---------------------------------------------------------------------------
# Supervisor side — called from core.py each tick
# ---------------------------------------------------------------------------


class WorkerSupervisor:
    def __init__(
        self,
        *,
        root: Path | None = None,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        crashes_file: Path | None = None,
    ) -> None:
        self._root = root or WORKERS_DIR
        self._stale_seconds = stale_seconds
        self._crashes_file = crashes_file or CRASHES_FILE
        self._reported: set[str] = set()

    # ---- detection ------------------------------------------------------

    def scan(self) -> list[CrashReport]:
        """Return crashes detected this tick (task_ids not yet reported)."""
        if not self._root.exists():
            return []
        now = time.time()
        crashes: list[CrashReport] = []
        for task_dir in self._root.iterdir():
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            if task_id in self._reported:
                continue
            hb = task_dir / "heartbeat"
            if not hb.exists():
                continue
            try:
                raw = hb.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue
            ts = float(payload.get("ts") or 0)
            age = now - ts
            if age < self._stale_seconds:
                continue
            report = CrashReport(
                task_id=task_id,
                agent=str(payload.get("agent") or ""),
                last_heartbeat_unix=ts,
                detected_at_unix=now,
                reason=f"heartbeat stale {age:.1f}s (threshold {self._stale_seconds:.0f}s)",
                pid=payload.get("pid"),
            )
            crashes.append(report)
            self._reported.add(task_id)
        return crashes

    # ---- reporting ------------------------------------------------------

    def record_crashes(self, reports: list[CrashReport]) -> int:
        """Append each report to `crashes.jsonl`. Returns count written."""
        if not reports:
            return 0
        try:
            self._crashes_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._crashes_file, "a", encoding="utf-8") as f:
                for r in reports:
                    row = {
                        "detected_at": datetime.fromtimestamp(r.detected_at_unix, tz=timezone.utc).isoformat(),
                        **r.to_dict(),
                    }
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("record_crashes failed: %s", e)
            return 0
        return len(reports)

    # Convenience: one-shot scan + record.
    def tick(self) -> list[CrashReport]:
        reports = self.scan()
        self.record_crashes(reports)
        return reports

    # ---- kill mode ------------------------------------------------------

    def terminate_stale(
        self,
        reports: list[CrashReport] | None = None,
        *,
        sigterm_grace_seconds: float = 5.0,
    ) -> list[int]:
        """SIGTERM the PIDs in `reports` (or a fresh scan); escalate to
        SIGKILL after `sigterm_grace_seconds` if they're still alive.

        Returns the list of PIDs that were signalled. Missing / already-
        dead PIDs are skipped silently. Heartbeat files are cleaned up
        after termination so the same crash isn't re-reported.

        Safe to call repeatedly — the supervisor's `_reported` set
        prevents double-terminating the same worker across ticks.
        """
        import os
        import signal

        if reports is None:
            reports = self.scan()

        killed: list[int] = []
        for r in reports:
            pid = r.pid or 0
            if not pid:
                continue
            # SIGTERM first — lets the worker flush state cleanly if it can.
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
                log.warning(
                    "supervisor: SIGTERM to worker task=%s pid=%d (reason=%s)",
                    r.task_id,
                    pid,
                    r.reason,
                )
            except ProcessLookupError:
                log.debug("supervisor: pid %d already gone", pid)
                continue
            except PermissionError as e:
                log.warning("supervisor: cannot signal pid %d: %s", pid, e)
                continue

            # Grace window, then escalate.
            deadline = time.monotonic() + sigterm_grace_seconds
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)  # probe
                except ProcessLookupError:
                    break
                time.sleep(0.2)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                    log.warning(
                        "supervisor: SIGKILL to worker task=%s pid=%d (SIGTERM ignored)",
                        r.task_id,
                        pid,
                    )
                except ProcessLookupError:
                    pass

            # Clean up heartbeat so scan() doesn't re-flag this task next tick.
            clear_heartbeat(r.task_id, root=self._root)

        return killed

    def enforce(self) -> tuple[list[CrashReport], list[int]]:
        """One-shot: scan → record → terminate. Returns (reports, pids_killed)."""
        reports = self.scan()
        self.record_crashes(reports)
        pids = self.terminate_stale(reports) if reports else []
        return reports, pids

    # Diagnostic helpers
    def reset_reported(self) -> None:
        self._reported.clear()
