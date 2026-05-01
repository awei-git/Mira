"""Background process dispatcher — spawn and track long-running tasks.

Manages PID files, concurrency limits, cooldowns, and stale process cleanup.
Extracted from core.py to reduce file size.
"""

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("mira")

# Lazy imports to avoid circular dependency
_config_loaded = False
_MIRA_ROOT = None
_LOGS_DIR = None


def _ensure_config():
    global _config_loaded, _MIRA_ROOT, _LOGS_DIR
    if not _config_loaded:
        from config import MIRA_ROOT, LOGS_DIR

        _MIRA_ROOT = MIRA_ROOT
        _LOGS_DIR = LOGS_DIR
        _config_loaded = True


def _get_bg_pid_dir() -> Path:
    _ensure_config()
    from config import PIDS_DIR

    return PIDS_DIR


MAX_CONCURRENT_BG = 2  # Legacy fallback — used only when no group is specified

# Per-group concurrency limits.  Jobs in the same group share a slot pool.
# "local" jobs (oMLX-only) don't compete with cloud API jobs.
CONCURRENCY_LIMITS = {
    "heavy": 2,  # Cloud API-heavy: explore, writer, researcher, analyst
    "light": 3,  # Lightweight cloud: growth, comments, spark-check, notes
    "local": 10,  # Local LLM only: idle-think, connection — no API cost
    "content": 2,  # Legacy alias for heavy
    "default": 2,  # Legacy fallback
}


def _count_bg_running(group: str | None = None) -> int:
    """Count how many background processes are currently alive.

    If *group* is given, only count processes whose PID filename matches
    a job in that concurrency group.
    """
    bg_dir = _get_bg_pid_dir()
    if not bg_dir.exists():
        return 0

    if group is None:
        # Global count (legacy behaviour)
        count = 0
        for pid_file in bg_dir.glob("*.pid"):
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)
                count += 1
            except (OSError, ValueError):
                pass
        return count

    # Group-aware count: only count PIDs whose name belongs to *group*.
    group_names = _group_members(group)
    count = 0
    for pid_file in bg_dir.glob("*.pid"):
        stem = pid_file.stem  # e.g. "idle-think-ang", "explore-morning"
        # Match if the PID name starts with any job name in this group
        if any(stem == n or stem.startswith(n + "-") for n in group_names):
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)
                count += 1
            except (OSError, ValueError):
                pass
    return count


def _group_members(group: str) -> set[str]:
    """Return the set of job names that belong to *group*."""
    try:
        from runtime.jobs import get_jobs

        return {j.name for j in get_jobs(enabled_only=False) if j.blocking_group == group}
    except Exception:
        return set()


def _proc_start_time(pid: int) -> str | None:
    """Return process start time as `lstart` string, or None if pid not alive.

    On macOS/Linux, `ps -o lstart= -p PID` returns a stable string that
    differs across PID reuse. We use this to detect when a PID was reused
    by an unrelated process, so we don't block retries for hours when the
    original bg job died but the OS recycled its PID.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        out = result.stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _read_pid_file(pid_file: Path) -> tuple[int, str | None] | None:
    """Read a pid file as (pid, expected_start_time). Backward compatible:
    files written before this change contain only the PID; we treat
    expected_start_time as None and fall back to the bare os.kill check."""
    try:
        raw = pid_file.read_text().strip()
    except OSError:
        return None
    if not raw:
        return None
    if ":" in raw:
        pid_str, start = raw.split(":", 1)
        try:
            return int(pid_str), start.strip() or None
        except ValueError:
            return None
    try:
        return int(raw), None
    except ValueError:
        return None


def _is_bg_running(name: str) -> bool:
    """Check if a background process is still alive AND is the one we started.

    Detects PID reuse by checking the process start time (`ps lstart`).
    Without this, a dead bg job whose PID got recycled by an unrelated
    process would look "still running" forever, suppressing all retries.
    Symptom on 2026-04-29: EOD market analysis missed its entire 4-hour
    window because a stale PID was treated as live.
    """
    pid_file = _get_bg_pid_dir() / f"{name}.pid"
    if not pid_file.exists():
        return False
    parsed = _read_pid_file(pid_file)
    if not parsed:
        return False
    old_pid, expected_start = parsed
    try:
        os.kill(old_pid, 0)
    except (OSError, ValueError):
        return False
    if expected_start is None:
        # Legacy pid file (just the PID). Trust os.kill — pre-existing behavior.
        return True
    actual_start = _proc_start_time(old_pid)
    if actual_start is None or actual_start != expected_start:
        # PID was reused by a different process, or ps could not confirm.
        # Treat as not running so the dispatcher retries.
        return False
    return True


def _reap_stale_pids():
    """Remove PID files for processes that died > 1 hour ago. Runs hourly."""
    bg_dir = _get_bg_pid_dir()
    if not bg_dir.exists():
        return
    import time as _time
    from core import load_state, save_state

    state = load_state()
    last_reap = state.get("last_pid_reap", 0)
    if _time.time() - last_reap < 3600:
        return
    reaped = 0
    for pid_file in bg_dir.glob("*.pid"):
        parsed = _read_pid_file(pid_file)
        alive = False
        if parsed:
            old_pid, expected_start = parsed
            try:
                os.kill(old_pid, 0)
                if expected_start is None:
                    alive = True
                else:
                    actual = _proc_start_time(old_pid)
                    alive = actual is not None and actual == expected_start
            except (OSError, ValueError):
                alive = False
        if not alive:
            # Process dead (or PID reused) — check if stale (mtime > 1 hour)
            try:
                age = _time.time() - pid_file.stat().st_mtime
                if age > 3600:
                    pid_file.unlink()
                    reaped += 1
            except OSError:
                pass
    if reaped:
        log.info("Reaped %d stale PID files", reaped)
    state["last_pid_reap"] = _time.time()
    save_state(state)


def _dispatch_background(name: str, cmd: list[str], group: str = "default"):
    """Spawn a background process if one isn't already running for this name.

    Enforces per-group concurrency limits so local-LLM jobs don't block
    cloud-API jobs. Falls back to MAX_CONCURRENT_BG when group is unknown.
    Tracks PID to avoid duplicate runs. Fire-and-forget.
    """
    _ensure_config()
    import health_monitor

    bg_dir = _get_bg_pid_dir()
    bg_dir.mkdir(parents=True, exist_ok=True)
    pid_file = bg_dir / f"{name}.pid"

    # Per-group concurrency limit
    limit = CONCURRENCY_LIMITS.get(group, MAX_CONCURRENT_BG)
    running = _count_bg_running(group=group)
    if running >= limit:
        log.debug("Background '%s' deferred — group '%s' %d/%d slots occupied", name, group, running, limit)
        return False

    # Check if a previous run is still active or finished recently
    if pid_file.exists():
        if _is_bg_running(name):
            parsed = _read_pid_file(pid_file)
            log.info("Background '%s' still running (PID %s), skipping", name, parsed[0] if parsed else "?")
            return False

        # Harvest outcome of the dead process
        try:
            health_monitor.record_outcome(name)
        except Exception as e:
            log.debug("record_outcome('%s') failed: %s", name, e)

        # Cooldown: don't re-dispatch if the PID file was written recently
        # Reduced from 5min to 1min; processes that crashed in <30s get faster retry
        try:
            import time as _time

            age = _time.time() - pid_file.stat().st_mtime
            cooldown = 60  # 1-minute cooldown (was 5 minutes)
            # If process ran < 30s it likely failed at startup — allow faster retry
            if age < 30:
                cooldown = 30
            if age < cooldown:
                log.debug("Background '%s' in cooldown (%ds since last run, cooldown=%ds)", name, int(age), cooldown)
                return False
        except OSError:
            pass

    # Propagate sys.path to the child via PYTHONPATH. Without this, bg
    # subprocesses inherit only the default site-packages and crash on
    # `from config import ...` at module load. Caught WA's podcast outage
    # on 2026-04-29 — every podcast dispatch had been crashing on import
    # for ~13 days, silently consuming the daily podcast slot. Setting
    # PYTHONPATH at the dispatch boundary fixes every bg-spawned agent
    # at once, not just podcast.
    bg_env = os.environ.copy()
    extra_paths = [
        str(_MIRA_ROOT / "lib"),
        str(_MIRA_ROOT / "agents" / "super"),
    ]
    existing = bg_env.get("PYTHONPATH", "")
    bg_env["PYTHONPATH"] = os.pathsep.join([p for p in extra_paths if p] + ([existing] if existing else []))

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=open(_LOGS_DIR / f"bg-{name}.log", "a"),
            start_new_session=True,
            cwd=str(_MIRA_ROOT / "agents" / "super"),
            env=bg_env,
        )
        # Capture the process start time so we can detect PID reuse later.
        # Falls back to bare PID if `ps` fails for any reason.
        start_time = _proc_start_time(proc.pid)
        if start_time:
            pid_file.write_text(f"{proc.pid}:{start_time}")
        else:
            pid_file.write_text(str(proc.pid))
        health_monitor.record_dispatch(name, proc.pid)
        log.info("Background '%s' dispatched (PID %d)", name, proc.pid)
        return True
    except Exception as e:
        log.error("Failed to dispatch background '%s': %s", name, e)
        return False


# Expose the PID directory getter as a module-level function.
# Usage: from runtime.dispatcher import get_bg_pid_dir; get_bg_pid_dir()
get_bg_pid_dir = _get_bg_pid_dir
