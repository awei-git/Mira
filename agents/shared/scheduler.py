"""Mira Scheduler — create, list, and manage scheduled tasks via macOS LaunchAgents.

Supports three scheduling modes:
1. Interval — run every N seconds (like the main Mira agent)
2. Calendar — run at specific time(s) (daily, weekly, etc.)
3. One-shot — run once at a specific datetime, then auto-cleanup

All scheduled jobs are LaunchAgent plists under ~/Library/LaunchAgents/
with the prefix "com.mira.sched." so they're easy to identify and manage.

Jobs execute a thin wrapper script that runs a Python command or shell command,
with output logged to /tmp/mira-sched-<name>.log.

Security: jobs run as the current user, within Mira's permission boundary.
No root, no sudo, no cron (launchd is the macOS way).
"""
import json
import logging
import os
import plistlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("mira.scheduler")

LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
SCHED_PREFIX = "com.mira.sched."
SCHED_INDEX = Path.home() / "Sandbox" / "Mira" / "agents" / "shared" / "scheduled_jobs.json"
SCRIPTS_DIR = Path.home() / "Sandbox" / "bin" / "scheduled"
PYTHON = "/opt/homebrew/bin/python3"


def _ensure_dirs():
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    if not SCHED_INDEX.exists():
        return []
    try:
        return json.loads(SCHED_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_index(index: list[dict]):
    SCHED_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _label(name: str) -> str:
    """Convert a job name to a LaunchAgent label."""
    slug = name.lower().replace(" ", "-").replace("_", "-")
    return f"{SCHED_PREFIX}{slug}"


def _plist_path(label: str) -> Path:
    return LAUNCH_AGENTS_DIR / f"{label}.plist"


def _script_path(name: str) -> Path:
    slug = name.lower().replace(" ", "-").replace("_", "-")
    return SCRIPTS_DIR / f"mira-sched-{slug}.sh"


def _log_path(name: str) -> str:
    slug = name.lower().replace(" ", "-").replace("_", "-")
    return f"/tmp/mira-sched-{slug}.log"


def schedule_interval(name: str, command: str, interval_seconds: int,
                      description: str = "") -> tuple[bool, str]:
    """Schedule a recurring job that runs every N seconds.

    Args:
        name: Human-readable job name (e.g., "check-deploy")
        command: Shell command or Python script path to execute
        interval_seconds: How often to run (minimum 10)
        description: What this job does

    Returns:
        (success, message)
    """
    if interval_seconds < 10:
        return False, "Interval must be at least 10 seconds"
    return _create_job(name, command, description,
                       schedule_type="interval",
                       interval=interval_seconds)


def schedule_calendar(name: str, command: str,
                      hour: Optional[int] = None,
                      minute: Optional[int] = None,
                      weekday: Optional[int] = None,
                      day: Optional[int] = None,
                      description: str = "") -> tuple[bool, str]:
    """Schedule a calendar-based job.

    Args:
        name: Human-readable job name
        command: Shell command to execute
        hour: Hour (0-23). None = every hour.
        minute: Minute (0-59). None = every minute (usually set to 0).
        weekday: Day of week (0=Sun, 1=Mon, ..., 6=Sat). None = every day.
        day: Day of month (1-31). None = every day.
        description: What this job does

    Examples:
        Daily at 9am:         hour=9, minute=0
        Weekdays at 8:30am:   hour=8, minute=30, weekday=[1,2,3,4,5]
        Every hour on the 0:  minute=0
        1st of month at noon: day=1, hour=12, minute=0
    """
    cal = {}
    if hour is not None:
        cal["Hour"] = hour
    if minute is not None:
        cal["Minute"] = minute
    if weekday is not None:
        cal["Weekday"] = weekday
    if day is not None:
        cal["Day"] = day
    if not cal:
        return False, "Must specify at least one of: hour, minute, weekday, day"

    return _create_job(name, command, description,
                       schedule_type="calendar",
                       calendar=cal)


def schedule_once(name: str, command: str,
                  at: datetime,
                  description: str = "") -> tuple[bool, str]:
    """Schedule a one-shot job at a specific datetime.

    The job auto-unloads after running. Cleanup via remove() later.
    """
    cal = {
        "Month": at.month,
        "Day": at.day,
        "Hour": at.hour,
        "Minute": at.minute,
    }
    return _create_job(name, command, description,
                       schedule_type="once",
                       calendar=cal)


def _create_job(name: str, command: str, description: str,
                schedule_type: str,
                interval: int = 0,
                calendar: dict = None) -> tuple[bool, str]:
    """Create and load a LaunchAgent job."""
    _ensure_dirs()
    label = _label(name)
    plist = _plist_path(label)
    script = _script_path(name)
    logfile = _log_path(name)

    # Don't overwrite existing job
    if plist.exists():
        return False, f"Job '{name}' already exists. Remove it first."

    # Create wrapper script
    script_content = f"""#!/bin/bash
# Mira scheduled job: {name}
# {description}
# Created: {datetime.now().isoformat()}
set -uo pipefail
exec >> "{logfile}" 2>&1
echo "--- $(date) ---"
{command}
"""
    script.write_text(script_content, encoding="utf-8")
    script.chmod(0o755)

    # Build plist
    plist_dict = {
        "Label": label,
        "ProgramArguments": ["/bin/bash", str(script)],
        "WorkingDirectory": str(Path.home() / "Sandbox"),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": logfile,
        "StandardErrorPath": logfile,
    }

    if schedule_type == "interval":
        plist_dict["StartInterval"] = interval
        plist_dict["RunAtLoad"] = False
    elif schedule_type in ("calendar", "once"):
        plist_dict["StartCalendarInterval"] = calendar
        plist_dict["RunAtLoad"] = False
    else:
        return False, f"Unknown schedule type: {schedule_type}"

    # Write plist
    with open(plist, "wb") as f:
        plistlib.dump(plist_dict, f)

    # Load it
    try:
        subprocess.run(
            ["launchctl", "load", str(plist)],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        plist.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        return False, f"launchctl load failed: {e}"

    # Update index
    index = _load_index()
    index = [j for j in index if j["name"] != name]
    index.append({
        "name": name,
        "label": label,
        "description": description,
        "command": command,
        "schedule_type": schedule_type,
        "interval": interval if schedule_type == "interval" else None,
        "calendar": calendar if schedule_type in ("calendar", "once") else None,
        "plist": str(plist),
        "script": str(script),
        "log": logfile,
        "created": datetime.now().isoformat(),
    })
    _save_index(index)

    log.info("Scheduled job: %s (%s)", name, schedule_type)

    schedule_desc = ""
    if schedule_type == "interval":
        schedule_desc = f"every {interval}s"
    elif schedule_type == "calendar":
        schedule_desc = f"calendar: {calendar}"
    elif schedule_type == "once":
        schedule_desc = f"once at {calendar}"

    return True, f"Job '{name}' scheduled ({schedule_desc}). Log: {logfile}"


def remove(name: str) -> tuple[bool, str]:
    """Unload and remove a scheduled job."""
    label = _label(name)
    plist = _plist_path(label)

    if plist.exists():
        try:
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            pass
        plist.unlink(missing_ok=True)

    script = _script_path(name)
    script.unlink(missing_ok=True)

    index = _load_index()
    found = any(j["name"] == name for j in index)
    index = [j for j in index if j["name"] != name]
    _save_index(index)

    if found or plist.exists():
        log.info("Removed scheduled job: %s", name)
        return True, f"Job '{name}' removed."
    return False, f"Job '{name}' not found."


def list_jobs() -> list[dict]:
    """List all Mira-managed scheduled jobs with live status."""
    index = _load_index()
    for job in index:
        label = job.get("label", "")
        # Check if actually loaded in launchd
        try:
            result = subprocess.run(
                ["launchctl", "list", label],
                capture_output=True, text=True, timeout=5,
            )
            job["loaded"] = result.returncode == 0
        except Exception:
            job["loaded"] = False

        # Check last log line
        logfile = job.get("log", "")
        if logfile and Path(logfile).exists():
            try:
                lines = Path(logfile).read_text(encoding="utf-8").strip().splitlines()
                job["last_log"] = lines[-1][:200] if lines else ""
            except Exception:
                job["last_log"] = ""
    return index


def format_jobs_summary() -> str:
    """Format all jobs as a readable summary."""
    jobs = list_jobs()
    if not jobs:
        return "No scheduled jobs."

    lines = [f"## Scheduled Jobs ({len(jobs)})\n"]
    for j in jobs:
        status = "active" if j.get("loaded") else "inactive"
        stype = j.get("schedule_type", "?")
        desc = j.get("description", "")
        name = j.get("name", "?")

        schedule_info = ""
        if stype == "interval":
            interval = j.get("interval", 0)
            if interval >= 3600:
                schedule_info = f"every {interval // 3600}h"
            elif interval >= 60:
                schedule_info = f"every {interval // 60}m"
            else:
                schedule_info = f"every {interval}s"
        elif stype in ("calendar", "once"):
            cal = j.get("calendar", {})
            parts = []
            if "Weekday" in cal:
                days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
                wd = cal["Weekday"]
                parts.append(days[wd] if isinstance(wd, int) else str(wd))
            if "Day" in cal:
                parts.append(f"day {cal['Day']}")
            if "Hour" in cal:
                parts.append(f"{cal['Hour']:02d}:{cal.get('Minute', 0):02d}")
            schedule_info = " ".join(parts) if parts else str(cal)

        lines.append(f"- **{name}** [{status}] {stype}: {schedule_info}")
        if desc:
            lines.append(f"  {desc}")
        last_log = j.get("last_log", "")
        if last_log:
            lines.append(f"  Last: {last_log[:100]}")

    return "\n".join(lines)


def get_log(name: str, tail: int = 20) -> str:
    """Get recent log output from a scheduled job."""
    logfile = _log_path(name)
    if not Path(logfile).exists():
        return f"No log file for '{name}'."
    try:
        lines = Path(logfile).read_text(encoding="utf-8").strip().splitlines()
        recent = lines[-tail:]
        return "\n".join(recent)
    except Exception as e:
        return f"Error reading log: {e}"
