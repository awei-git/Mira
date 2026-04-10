"""Operator dashboard summary for Mira production runtime."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOGS_DIR, MIRA_DIR
from ops.failure_log import load_recent_failures
from ops.backlog import ActionBacklog
from execution.runtime_contract import normalize_task_status
from publish.manifest import get_stuck_articles, load_manifest
from task_manager import HISTORY_FILE, STATUS_FILE

log = logging.getLogger("mira.operator_dashboard")

_RESTORE_DRILL_LOG = LOGS_DIR / "restore_drills.jsonl"
_DEFAULT_STUCK_MINUTES = 30
_PROCESS_RECENCY_HOURS = 72


def build_operator_summary(user_id: str = "ang") -> dict:
    """Build the current operator dashboard snapshot."""
    task_status = _load_json(STATUS_FILE, default=[])
    active_tasks = []
    stuck_tasks = []
    now = datetime.now(timezone.utc)

    for rec in task_status:
        if rec.get("user_id", "ang") != user_id:
            continue
        status = normalize_task_status(rec.get("status", ""))
        if status not in ("dispatched", "running"):
            continue
        active_entry = {
            "task_id": rec.get("task_id", ""),
            "workflow_id": rec.get("workflow_id", rec.get("task_id", "")),
            "status": status,
            "preview": rec.get("content_preview", ""),
            "started_at": rec.get("started_at", ""),
            "tags": rec.get("tags", []),
        }
        active_tasks.append(active_entry)
        if _is_stuck_task(rec, now=now):
            stuck_tasks.append(active_entry | {"timeout_alerted_at": rec.get("timeout_alerted_at", "")})

    recent_history = _load_history(limit=50, user_id=user_id)
    failed_tasks = [
        {
            "task_id": rec.get("task_id", ""),
            "workflow_id": rec.get("workflow_id", rec.get("task_id", "")),
            "status": normalize_task_status(rec.get("status", "")),
            "failure_class": rec.get("failure_class", ""),
            "summary": rec.get("summary", ""),
            "completed_at": rec.get("completed_at", ""),
        }
        for rec in recent_history
        if normalize_task_status(rec.get("status", "")) in ("failed", "timeout", "blocked")
    ][:10]

    manifest = load_manifest()
    articles = list(manifest.get("articles", {}).values())
    publish_queue = [
        _publish_entry(entry)
        for entry in articles
        if entry.get("status") and entry.get("status") != "complete"
    ]
    publish_queue.sort(key=lambda item: item.get("updated_at", ""))

    publish_counts: dict[str, int] = {}
    for entry in articles:
        status = entry.get("status", "unknown") or "unknown"
        publish_counts[status] = publish_counts.get(status, 0) + 1

    health = _load_bg_health()
    backlog = _build_backlog_summary()
    incidents = _recent_incidents()

    return {
        "updated_at": _utc_iso(),
        "user_id": user_id,
        "tasks": {
            "active": active_tasks,
            "failed_recent": failed_tasks,
            "stuck": stuck_tasks,
        },
        "publish": {
            "counts": publish_counts,
            "queue": publish_queue[:20],
            "stuck": [_publish_entry(entry) for entry in get_stuck_articles()],
        },
        "health": health,
        "backlog": backlog,
        "recent_incidents": incidents,
        "latest_restore_drill": _latest_restore_drill(),
    }


def write_operator_summary(user_id: str = "ang", bridge_dir: Path | None = None) -> Path:
    """Write dashboard summary into the user bridge directory."""
    target_root = Path(bridge_dir) if bridge_dir else MIRA_DIR
    out_dir = target_root / "users" / user_id / "operator"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "dashboard.json"
    data = build_operator_summary(user_id=user_id)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)
    return out_path


def _build_backlog_summary() -> dict:
    backlog = ActionBacklog()
    counts: dict[str, int] = {}
    next_actions = []
    for item in backlog._items:  # noqa: SLF001 - dashboard needs full state counts
        counts[item.status] = counts.get(item.status, 0) + 1
    for item in sorted(
        backlog.get_active(),
        key=lambda i: (
            getattr(i, "priority", "") != "high",
            getattr(i, "updated_at", "") or getattr(i, "created_at", ""),
        ),
    )[:10]:
        next_actions.append(
            {
                "title": item.title,
                "status": item.status,
                "priority": item.priority,
                "source": item.source,
                "executor": getattr(item, "executor", ""),
                "updated_at": item.updated_at,
            }
        )
    return {"counts": counts, "next_actions": next_actions}


def _latest_restore_drill() -> dict:
    if not _RESTORE_DRILL_LOG.exists():
        return {}
    try:
        lines = _RESTORE_DRILL_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _valid_restore_drill(record):
            return record
    return {}


def _load_bg_health() -> dict:
    try:
        from health_monitor import _load_health  # noqa: PLC2701

        health = _load_health()
    except Exception:
        return {"daily_stats": {}, "processes": []}

    today = datetime.now().strftime("%Y-%m-%d")
    processes = []
    for name, data in health.get("processes", {}).items():
        proc = {
            "name": name,
            "last_dispatch": data.get("last_dispatch", ""),
            "last_success": data.get("last_success", ""),
            "consecutive_failures": data.get("consecutive_failures", 0),
            "last_failure_reason": data.get("last_failure_reason", ""),
        }
        if _process_is_relevant(proc, today=today):
            processes.append(proc)
    processes.sort(
        key=lambda proc: (
            int(int(proc.get("consecutive_failures", 0) or 0) > 0),
            int(proc.get("consecutive_failures", 0) or 0),
            _process_sort_ts(proc),
        ),
        reverse=True,
    )
    return {
        "daily_stats": health.get("daily_stats", {}).get(today, {}),
        "failing_processes": sum(1 for proc in processes if int(proc.get("consecutive_failures", 0)) > 0),
        "processes": processes[:12],
    }


def _recent_incidents() -> list[dict]:
    grouped: dict[tuple[str, str, str, str], dict] = {}
    for rec in load_recent_failures(days=7, limit=50):
        key = (
            rec.get("pipeline", ""),
            rec.get("step", ""),
            rec.get("error_type", ""),
            rec.get("error_message", ""),
        )
        incident = grouped.get(key)
        if incident is None:
            grouped[key] = {
                "timestamp": rec.get("timestamp", ""),
                "pipeline": rec.get("pipeline", ""),
                "step": rec.get("step", ""),
                "slug": rec.get("slug", ""),
                "error_type": rec.get("error_type", ""),
                "error_message": rec.get("error_message", ""),
                "count": 1,
            }
            continue
        incident["count"] += 1
        incident["timestamp"] = max(incident.get("timestamp", ""), rec.get("timestamp", ""))
    return sorted(grouped.values(), key=lambda rec: rec.get("timestamp", ""), reverse=True)[:10]


def _publish_entry(entry: dict) -> dict:
    timestamps = entry.get("timestamps", {}) or {}
    status = entry.get("status", "")
    return {
        "slug": entry.get("slug", ""),
        "title": entry.get("title", ""),
        "status": status,
        "updated_at": timestamps.get(status, "") or timestamps.get("last_error", ""),
        "error": entry.get("error", ""),
        "retry_count": entry.get("retry_count", 0),
    }


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _load_history(*, limit: int, user_id: str) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    rows = []
    try:
        with open(HISTORY_FILE, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("user_id", "ang") == user_id:
                    rows.append(rec)
    except OSError:
        return []
    rows.reverse()
    return rows[:limit]


def _is_stuck_task(rec: dict, *, now: datetime) -> bool:
    started_at = rec.get("started_at", "")
    if not started_at:
        return False
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - started).total_seconds() > _DEFAULT_STUCK_MINUTES * 60


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_restore_drill(record: dict) -> bool:
    backup_dir = str(record.get("backup_dir", "")).strip()
    if not backup_dir:
        return False
    path = Path(backup_dir)
    if "/private/var/folders/" in backup_dir:
        return False
    if not path.exists():
        return False
    return True


def _process_is_relevant(proc: dict, *, today: str) -> bool:
    if int(proc.get("consecutive_failures", 0) or 0) > 0:
        return True
    for key in ("last_dispatch", "last_success"):
        if _is_recent_iso(proc.get(key, ""), hours=_PROCESS_RECENCY_HOURS):
            return True
    return False


def _is_recent_iso(value: str, *, hours: int) -> bool:
    if not value:
        return False
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts.astimezone(timezone.utc) <= timedelta(hours=hours)


def _process_sort_ts(proc: dict) -> str:
    return proc.get("last_dispatch", "") or proc.get("last_success", "") or ""
