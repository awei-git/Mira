"""Mira Health Monitor — watchdog for background processes.

Tracks dispatch/outcome of background processes, detects repeated failures,
sends alerts via iPhone bridge, and generates daily health summaries.
"""
import fcntl
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("mira.health")

# Paths
_AGENTS_DIR = Path(__file__).resolve().parent.parent
_MIRA_ROOT = _AGENTS_DIR.parent
_BG_PID_DIR = _MIRA_ROOT / "agents" / ".bg_pids"
_LOGS_DIR = _MIRA_ROOT / "logs"
_HEALTH_FILE = _MIRA_ROOT / ".bg_health.json"
from config import ARTIFACTS_DIR as _ARTIFACTS_DIR
_BRIEFINGS_DIR = _ARTIFACTS_DIR / "briefings"
_PUBLISHED_DIR = _ARTIFACTS_DIR / "writings" / "_published"

# Thresholds
CONSECUTIVE_FAILURE_THRESHOLD = 3
CRITICAL_FAILURE_THRESHOLD = 1  # alert on first failure for critical processes
ALERT_DEDUP_HOURS = 12
MAX_ALERTS_PER_DAY = 3
HISTORY_CAP = 10  # max entries per process

CRITICAL_PROCESSES = {"journal", "reflect", "writing-pipeline"}


# ---------------------------------------------------------------------------
# Health file I/O
# ---------------------------------------------------------------------------

def _load_health() -> dict:
    try:
        return json.loads(_HEALTH_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"processes": {}, "daily_stats": {}, "alert_dedup": {}}


def _save_health(health: dict):
    lock_file = _HEALTH_FILE.with_suffix(".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock_file, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                tmp = _HEALTH_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(health, indent=2, ensure_ascii=False),
                               encoding="utf-8")
                tmp.replace(_HEALTH_FILE)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except BlockingIOError:
        log.debug("Health file locked by another process, skipping write")


# ---------------------------------------------------------------------------
# Recording dispatch and outcomes
# ---------------------------------------------------------------------------

def record_dispatch(name: str, pid: int):
    """Record that a background process was dispatched."""
    health = _load_health()
    proc = health["processes"].setdefault(name, {})
    proc["last_dispatch"] = datetime.now().isoformat()
    proc["last_pid"] = pid

    today = datetime.now().strftime("%Y-%m-%d")
    daily = health.setdefault("daily_stats", {}).setdefault(today, {})
    daily["dispatched"] = daily.get("dispatched", 0) + 1

    _save_health(health)


def record_outcome(name: str):
    """Inspect the bg log file and PID file to determine if a process succeeded or failed.

    Called when we detect a previously-dispatched process has exited.
    """
    health = _load_health()
    proc = health["processes"].setdefault(name, {})
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Determine success/failure from PID file exit code + fatal log errors
    pid_file = _LOGS_DIR.parent / "bg_pids" / f"{name}.pid"
    log_file = _LOGS_DIR / f"bg-{name}.log"
    failed = False
    reason = ""

    # Primary signal: check process exit code via PID
    last_pid = proc.get("last_pid")
    if last_pid:
        try:
            import subprocess
            result = subprocess.run(
                ["ps", "-p", str(last_pid), "-o", "stat="],
                capture_output=True, text=True, timeout=5,
            )
            # If ps returns nothing, process exited — check log for fatal errors only
        except Exception:
            pass

    # Secondary signal: only count Traceback (unhandled exception) as failure.
    # WARNING/ERROR log lines from non-fatal issues (e.g. oMLX embed 500,
    # MiniMax credit) should NOT mark the whole process as failed.
    if log_file.exists():
        try:
            size = log_file.stat().st_size
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                if size > 2048:
                    f.seek(size - 2048)
                    f.readline()  # skip partial line
                tail = f.read()

            # Only unhandled exceptions (Traceback) count as real failures
            if "traceback" in tail.lower():
                for line in reversed(tail.strip().splitlines()):
                    line_s = line.strip()
                    if line_s and ("error" in line_s.lower() or "exception" in line_s.lower()):
                        reason = line_s[:200]
                        break
                if not reason:
                    reason = "Traceback detected in log (see bg-{}.log)".format(name)
                failed = True
        except Exception as e:
            log.debug("Could not read bg log for '%s': %s", name, e)

    # Calculate duration
    dispatch_ts = proc.get("last_dispatch", "")
    duration_s = 0
    if dispatch_ts:
        try:
            d = datetime.fromisoformat(dispatch_ts)
            duration_s = int((now - d).total_seconds())
        except ValueError:
            pass

    # Update process record
    proc["last_exit"] = now.isoformat()
    if failed:
        proc["consecutive_failures"] = proc.get("consecutive_failures", 0) + 1
        proc["last_failure_reason"] = reason
        daily = health.setdefault("daily_stats", {}).setdefault(today, {})
        daily["failed"] = daily.get("failed", 0) + 1
    else:
        proc["consecutive_failures"] = 0
        proc["last_success"] = now.isoformat()
        proc["last_failure_reason"] = ""
        daily = health.setdefault("daily_stats", {}).setdefault(today, {})
        daily["succeeded"] = daily.get("succeeded", 0) + 1

    # Append to history (capped)
    history = proc.setdefault("history", [])
    entry = {"ts": now.isoformat(), "ok": not failed, "duration_s": duration_s}
    if failed:
        entry["reason"] = reason
    history.append(entry)
    proc["history"] = history[-HISTORY_CAP:]

    _save_health(health)

    # Check if we need to alert
    if failed:
        _maybe_alert(name, health)

    return not failed


def harvest_all() -> list[str]:
    """Check all PID files for dead processes and record their outcomes.

    Called from cmd_run() to catch processes that finished between cycles.
    Returns list of bg-names that completed *successfully* this cycle
    (used by pipeline chaining to trigger follow-up jobs).
    """
    if not _BG_PID_DIR.exists():
        return []

    completed: list[str] = []
    for pid_file in _BG_PID_DIR.glob("*.pid"):
        name = pid_file.stem
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # still alive
        except (OSError, ValueError):
            # Process is dead — record outcome
            ok = record_outcome(name)
            if ok:
                completed.append(name)
    return completed


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def _maybe_alert(name: str, health: dict | None = None):
    """Send an alert if failure threshold is exceeded, with dedup."""
    if health is None:
        health = _load_health()

    proc = health["processes"].get(name, {})
    consecutive = proc.get("consecutive_failures", 0)

    # Determine threshold based on criticality
    # Check both full name and prefix (e.g. "writing-pipeline" and "explore-quanta")
    base_name = name.split("-")[0] if "-" in name else name
    is_critical = name in CRITICAL_PROCESSES or base_name in CRITICAL_PROCESSES
    threshold = CRITICAL_FAILURE_THRESHOLD if is_critical else CONSECUTIVE_FAILURE_THRESHOLD

    if consecutive < threshold:
        return

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Dedup: don't re-alert within 12 hours for same process
    last_alert = health.get("alert_dedup", {}).get(name, "")
    if last_alert:
        try:
            last_dt = datetime.fromisoformat(last_alert)
            if (now - last_dt).total_seconds() < ALERT_DEDUP_HOURS * 3600:
                return
        except ValueError:
            pass

    # Daily cap — critical processes bypass the cap
    daily = health.get("daily_stats", {}).get(today, {})
    if not is_critical and daily.get("alerts_sent", 0) >= MAX_ALERTS_PER_DAY:
        return

    # Send alert via Mira bridge
    reason = proc.get("last_failure_reason", "unknown error")
    msg = (
        f"⚠️ {name} 连续失败 {consecutive} 次\n"
        f"错误: {reason}\n"
        f"日志: logs/bg-{name}.log"
    )

    try:
        import sys
        shared_dir = str(_AGENTS_DIR .parent / "lib")
        if shared_dir not in sys.path:
            sys.path.insert(0, shared_dir)
        from bridge import Mira
        bridge = Mira()
        bridge.post(msg)
        log.warning("Health alert sent for '%s': %d consecutive failures", name, consecutive)
    except Exception as e:
        log.error("Failed to send health alert: %s", e)
        return

    # Record alert
    health.setdefault("alert_dedup", {})[name] = now.isoformat()
    health.setdefault("daily_stats", {}).setdefault(today, {})
    health["daily_stats"][today]["alerts_sent"] = daily.get("alerts_sent", 0) + 1
    _save_health(health)


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

def check_anomalies():
    """Detect abnormal absences — processes that should have run but didn't.

    Called from cmd_run(). Only checks once per hour to avoid noise.
    """
    health = _load_health()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Only check once per hour
    last_check = health.get("last_anomaly_check", "")
    if last_check:
        try:
            if (now - datetime.fromisoformat(last_check)).total_seconds() < 3600:
                return
        except ValueError:
            pass

    health["last_anomaly_check"] = now.isoformat()
    _save_health(health)

    alerts = []

    # 1. Explore should run at least 2x per day by evening
    if now.hour >= 20:
        explore_count = sum(
            1 for p, data in health.get("processes", {}).items()
            if p.startswith("explore-") and data.get("last_success", "")[:10] == today
        )
        if explore_count < 2:
            alerts.append(f"今天只成功跑了 {explore_count} 个 explore (预期 >= 2)")

    # 2. Explore ran but no briefing file produced
    if now.hour >= 14:
        explore_dispatched = any(
            p.startswith("explore-") and data.get("last_dispatch", "")[:10] == today
            for p, data in health.get("processes", {}).items()
        )
        briefing_today = any(_BRIEFINGS_DIR.glob(f"{today}*.md")) if _BRIEFINGS_DIR.exists() else False
        if explore_dispatched and not briefing_today:
            alerts.append("Explore 今天跑了但没有产生 briefing 文件")

    # 3. _published/ directory empty or missing (podcast pipeline will be blind)
    if _PUBLISHED_DIR.exists():
        article_count = len(list(_PUBLISHED_DIR.glob("*.md")))
        if article_count == 0:
            alerts.append("_published/ 目录是空的 — podcast pipeline 无法发现文章")
    else:
        alerts.append("_published/ 目录不存在 — podcast pipeline 无法工作")

    # 4. Growth cycle hasn't run all day (if after 16:00)
    if now.hour >= 16:
        growth_ran = any(
            p.startswith("substack-growth") and data.get("last_dispatch", "")[:10] == today
            for p, data in health.get("processes", {}).items()
        )
        if not growth_ran:
            alerts.append("Growth cycle 今天还没跑过")

    for alert in alerts:
        _maybe_anomaly_alert(alert, health)


def _maybe_anomaly_alert(message: str, health: dict):
    """Send anomaly alert with dedup by message hash."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Dedup by message content (hash first 50 chars)
    key = f"anomaly_{hash(message[:50]) % 100000}"
    last = health.get("alert_dedup", {}).get(key, "")
    if last:
        try:
            if (now - datetime.fromisoformat(last)).total_seconds() < ALERT_DEDUP_HOURS * 3600:
                return
        except ValueError:
            pass

    # Daily cap
    daily = health.get("daily_stats", {}).get(today, {})
    if daily.get("alerts_sent", 0) >= MAX_ALERTS_PER_DAY:
        return

    try:
        import sys
        shared_dir = str(_AGENTS_DIR .parent / "lib")
        if shared_dir not in sys.path:
            sys.path.insert(0, shared_dir)
        from bridge import Mira
        bridge = Mira()
        bridge.post(f"⚠️ 异常检测: {message}")
        log.warning("Anomaly alert: %s", message)
    except Exception as e:
        log.error("Failed to send anomaly alert: %s", e)
        return

    health.setdefault("alert_dedup", {})[key] = now.isoformat()
    health.setdefault("daily_stats", {}).setdefault(today, {})
    health["daily_stats"][today]["alerts_sent"] = daily.get("alerts_sent", 0) + 1
    _save_health(health)


# ---------------------------------------------------------------------------
# Health summary for journal
# ---------------------------------------------------------------------------

def generate_health_summary() -> str:
    """Generate a plain-text health summary for inclusion in the daily journal."""
    health = _load_health()
    today = datetime.now().strftime("%Y-%m-%d")
    daily = health.get("daily_stats", {}).get(today, {})

    dispatched = daily.get("dispatched", 0)
    succeeded = daily.get("succeeded", 0)
    failed = daily.get("failed", 0)

    lines = [
        f"## Pipeline Health ({today})",
        f"Dispatched: {dispatched} | Succeeded: {succeeded} | Failed: {failed}",
    ]

    # List failures
    failures = []
    for name, data in health.get("processes", {}).items():
        consec = data.get("consecutive_failures", 0)
        if consec > 0:
            reason = data.get("last_failure_reason", "unknown")[:100]
            failures.append(f"- {name}: {consec}x consecutive ({reason})")

    if failures:
        lines.append("")
        lines.append("Failures:")
        lines.extend(failures)

    # Process run counts for today
    runs = {}
    for name, data in health.get("processes", {}).items():
        hist = data.get("history", [])
        today_runs = [h for h in hist if h.get("ts", "")[:10] == today]
        if today_runs:
            ok = sum(1 for h in today_runs if h.get("ok"))
            fail = len(today_runs) - ok
            avg_dur = sum(h.get("duration_s", 0) for h in today_runs) / len(today_runs)
            # Group by base name (e.g., explore-* → explore)
            base = name.split("-")[0]
            if base not in runs:
                runs[base] = {"ok": 0, "fail": 0, "durations": [], "names": []}
            runs[base]["ok"] += ok
            runs[base]["fail"] += fail
            runs[base]["durations"].append(avg_dur)
            runs[base]["names"].append(name)

    if runs:
        lines.append("")
        lines.append("Process runs:")
        for base, info in sorted(runs.items()):
            avg = sum(info["durations"]) / len(info["durations"]) if info["durations"] else 0
            status = "all OK" if info["fail"] == 0 else f"{info['fail']} failed"
            total = info["ok"] + info["fail"]
            lines.append(f"- {base}: {total} runs, {status} (avg {avg:.0f}s)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cleanup — prune old daily stats
# ---------------------------------------------------------------------------

def prune_old_stats(keep_days: int = 7):
    """Remove daily stats older than keep_days. Called from reflect or journal."""
    health = _load_health()
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    old_keys = [k for k in health.get("daily_stats", {}) if k < cutoff]
    for k in old_keys:
        del health["daily_stats"][k]

    # Also prune alert_dedup entries older than 24h
    now = datetime.now()
    old_alerts = []
    for k, v in health.get("alert_dedup", {}).items():
        try:
            if (now - datetime.fromisoformat(v)).total_seconds() > 24 * 3600:
                old_alerts.append(k)
        except ValueError:
            old_alerts.append(k)
    for k in old_alerts:
        del health["alert_dedup"][k]

    if old_keys or old_alerts:
        _save_health(health)
