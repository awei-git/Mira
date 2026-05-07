"""Operator dashboard summary for Mira production runtime."""

from __future__ import annotations

import json
import logging
import hashlib
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LOGS_DIR, MIRA_DIR, STATE_DIR
from ops.failure_log import load_recent_failures
from ops.backlog import ActionBacklog
from execution.runtime_contract import normalize_task_status
from publish.manifest import get_stuck_articles, load_manifest
from task_manager import HISTORY_FILE, STATUS_FILE

log = logging.getLogger("mira.operator_dashboard")

_RESTORE_DRILL_LOG = LOGS_DIR / "restore_drills.jsonl"
_OPERATOR_ALERT_STATE = STATE_DIR / "operator_alert_state.json"
_PROVIDER_CIRCUIT_FILE = STATE_DIR / "api_provider_circuit.json"
_DEFAULT_STUCK_MINUTES = 30
_PROCESS_RECENCY_HOURS = 72
_ALERT_REPEAT_HOURS = 6
_ONE_SHOT_PROCESS_FAILURE_HOURS = 6
_ONE_SHOT_PROCESS_RE = re.compile(r"^(autowrite-\d{4}-\d{2}-\d{2}|podcast-|voiceover-)")


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
        _publish_entry(entry) for entry in articles if entry.get("status") and entry.get("status") != "complete"
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
        "provider_circuits": _api_provider_circuits(),
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
    _maybe_publish_operator_alert(user_id=user_id, summary=data, bridge_dir=target_root)
    return out_path


def _maybe_publish_operator_alert(*, user_id: str, summary: dict, bridge_dir: Path) -> None:
    """Publish actionable dashboard anomalies into one stable app thread."""
    try:
        state = _load_json(_OPERATOR_ALERT_STATE, default={})
        user_state = state.get(user_id, {})
        lines = _operator_alert_lines(summary)
        digest_src = "\n".join(lines) if lines else "clear"
        digest = hashlib.sha256(digest_src.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc)
        last_digest = user_state.get("digest", "")
        last_published_at = user_state.get("published_at", "")

        should_publish = digest != last_digest
        if not should_publish and lines and not _is_recent_iso(last_published_at, hours=_ALERT_REPEAT_HOURS):
            should_publish = True
        if not should_publish:
            return

        content = _format_operator_alert(summary, lines)
        _publish_operator_alert_item(user_id=user_id, bridge_dir=bridge_dir, content=content)
        state[user_id] = {"digest": digest, "published_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        _save_json(_OPERATOR_ALERT_STATE, state)
    except Exception as exc:
        log.warning("operator alert publish failed: %s", exc)


def _operator_alert_lines(summary: dict) -> list[str]:
    """Return user-visible operator alerts from the dashboard snapshot."""
    lines: list[str] = []
    stuck_tasks = summary.get("tasks", {}).get("stuck", []) or []
    if stuck_tasks:
        previews = ", ".join((task.get("preview") or task.get("task_id") or "unknown")[:48] for task in stuck_tasks[:3])
        lines.append(f"{len(stuck_tasks)} task(s) appear stuck over {_DEFAULT_STUCK_MINUTES} minutes: {previews}")

    health = summary.get("health", {}) or {}
    failing_processes = int(health.get("failing_processes", 0) or 0)
    if failing_processes:
        names = ", ".join(
            proc.get("name", "unknown")
            for proc in (health.get("processes", []) or [])
            if _process_has_active_failure(proc)
        )
        lines.append(f"{failing_processes} scheduled process(es) are failing: {names}")

    publish = summary.get("publish", {}) or {}
    stuck_articles = publish.get("stuck", []) or []
    if stuck_articles:
        slugs = ", ".join((article.get("slug") or "unknown") for article in stuck_articles[:3])
        lines.append(f"{len(stuck_articles)} publish item(s) are stuck: {slugs}")

    counts = publish.get("counts", {}) or {}
    blocked_writer = int(counts.get("blocked_writer_gate", 0) or 0)
    blocked_security = int(counts.get("blocked_security_claim", 0) or 0)
    blocked_publish = int(counts.get("blocked_publish_error", 0) or 0)
    if blocked_writer:
        lines.append(f"{blocked_writer} publish item(s) are blocked by writer-quality gate")
    if blocked_security:
        lines.append(f"{blocked_security} publish item(s) are blocked by security-claim review")
    if blocked_publish:
        lines.append(f"{blocked_publish} publish item(s) returned no Substack URL and need review")

    for circuit in summary.get("provider_circuits", []) or []:
        provider = circuit.get("provider", "provider")
        until = circuit.get("disabled_until", "")
        reason = circuit.get("reason", "")
        lines.append(f"{provider} provider circuit is open until {until}: {reason}")

    incidents = summary.get("recent_incidents", []) or []
    repeated = [
        inc
        for inc in incidents
        if int(inc.get("count", 0) or 0) >= 3 and _is_recent_iso(inc.get("timestamp", ""), hours=_ALERT_REPEAT_HOURS)
    ]
    if repeated:
        inc = repeated[0]
        lines.append(
            "Repeated incident: "
            f"{inc.get('pipeline', 'unknown')}/{inc.get('step', 'unknown')} "
            f"({inc.get('count')}x) {inc.get('error_type', '')}"
        )

    return lines


def _format_operator_alert(summary: dict, lines: list[str]) -> str:
    updated_at = summary.get("updated_at", _utc_iso())
    if not lines:
        return f"Mira Ops Status\n\nAll monitored operator checks are clear as of {updated_at}."
    body = "\n".join(f"- {line}" for line in lines)
    return f"Mira Ops Status\n\nDetected at {updated_at}:\n\n{body}\n\nThis is generated from the operator dashboard, not raw logs."


def _publish_operator_alert_item(*, user_id: str, bridge_dir: Path, content: str) -> None:
    from bridge import Mira

    bridge = Mira(bridge_dir=bridge_dir, user_id=user_id)
    item_id = "mira_ops_status"
    title = "Mira Ops Status"
    if bridge.item_exists(item_id):
        bridge.append_message(item_id, "agent", content)
        item = bridge._read_item(item_id)
        if item:
            item["type"] = "discussion"
            item["title"] = title
            item["status"] = "done"
            item["origin"] = "agent"
            item["tags"] = list(dict.fromkeys(["ops", "system", *item.get("tags", [])]))
            item["pinned"] = True
            bridge._write_item(item)
            bridge._update_manifest(item)
        return

    item = bridge.create_item(
        item_id,
        "discussion",
        title,
        content,
        sender="agent",
        tags=["ops", "system"],
        origin="agent",
    )
    item["status"] = "done"
    item["pinned"] = True
    bridge._write_item(item)
    bridge._update_manifest(item)


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
            "last_exit": data.get("last_exit", ""),
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
        "failing_processes": sum(1 for proc in processes if _process_has_active_failure(proc)),
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


def _api_provider_circuits() -> list[dict]:
    data = _load_json(_PROVIDER_CIRCUIT_FILE, default={})
    now = datetime.now(timezone.utc)
    open_circuits = []
    for provider, entry in data.items():
        until = entry.get("disabled_until", "")
        if not until:
            continue
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        except ValueError:
            continue
        if until_dt.tzinfo is None:
            until_dt = until_dt.replace(tzinfo=timezone.utc)
        if now >= until_dt.astimezone(timezone.utc):
            continue
        open_circuits.append(
            {
                "provider": provider,
                "reason": entry.get("reason", ""),
                "disabled_until": until,
                "updated_at": entry.get("updated_at", ""),
            }
        )
    return sorted(open_circuits, key=lambda item: item.get("disabled_until", ""))


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


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


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
    if _process_has_active_failure(proc):
        return True
    for key in ("last_dispatch", "last_success"):
        if _is_recent_iso(proc.get(key, ""), hours=_PROCESS_RECENCY_HOURS):
            return True
    return False


def _process_has_active_failure(proc: dict) -> bool:
    if int(proc.get("consecutive_failures", 0) or 0) <= 0:
        return False
    recency_hours = (
        _ONE_SHOT_PROCESS_FAILURE_HOURS if _ONE_SHOT_PROCESS_RE.match(proc.get("name", "")) else _PROCESS_RECENCY_HOURS
    )
    if not (
        _is_recent_iso(proc.get("last_exit", ""), hours=recency_hours)
        or _is_recent_iso(proc.get("last_dispatch", ""), hours=recency_hours)
    ):
        return False
    last_success = _parse_iso(proc.get("last_success", ""))
    last_exit = _parse_iso(proc.get("last_exit", ""))
    if last_success and last_exit and last_success >= last_exit:
        return False
    if last_success and not last_exit:
        return False
    return True


def _is_recent_iso(value: str, *, hours: int) -> bool:
    if not value:
        return False
    ts = _parse_iso(value)
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts.astimezone(timezone.utc) <= timedelta(hours=hours)


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _process_sort_ts(proc: dict) -> str:
    return proc.get("last_dispatch", "") or proc.get("last_success", "") or ""
