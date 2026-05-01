from __future__ import annotations

import json
from typing import Any

from execution.runtime_contract import normalize_task_status


_APP_STATUSES = {"queued", "working", "verifying", "needs-input", "done", "failed", "archived"}
_INTERNAL_STATUS_MAP = {
    "dispatched": "working",
    "running": "working",
    "completed_unverified": "verifying",
    "verified": "done",
    "blocked": "failed",
    "timeout": "failed",
    "cancelled": "failed",
}


def app_status(status: str | None) -> str:
    normalized = normalize_task_status(status)
    projected = _INTERNAL_STATUS_MAP.get(normalized, normalized)
    if projected in _APP_STATUSES:
        return projected
    if projected:
        return "failed"
    return "queued"


def _json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _message_from_row(row: dict) -> dict:
    msg = {
        "id": row.get("id") or "",
        "sender": row.get("sender") or "agent",
        "content": row.get("content") or "",
        "timestamp": row.get("created_at") or "",
        "kind": row.get("kind") or "text",
    }
    if row.get("image_path"):
        msg["image_path"] = row["image_path"]
    return msg


def item_from_rows(task: dict, messages: list[dict]) -> dict:
    error = None
    if task.get("error_message") or app_status(task.get("status")) == "failed":
        error = {
            "code": task.get("error_code") or task.get("status") or "failed",
            "message": task.get("error_message") or task.get("result_summary") or "Task failed",
            "retryable": bool(task.get("retryable")),
            "timestamp": task.get("completed_at") or task.get("updated_at") or "",
        }

    return {
        "id": task.get("id") or "",
        "type": task.get("type") or "request",
        "title": task.get("title") or task.get("id") or "",
        "status": app_status(task.get("status")),
        "tags": _json_list(task.get("tags")),
        "origin": task.get("origin") or "agent",
        "pinned": bool(task.get("pinned")),
        "quick": bool(task.get("quick")),
        "parent_id": task.get("parent_id"),
        "created_at": task.get("created_at") or task.get("updated_at") or "",
        "updated_at": task.get("updated_at") or task.get("created_at") or "",
        "messages": [_message_from_row(m) for m in messages],
        "error": error,
        "result_path": task.get("result_path"),
        "task_type": task.get("task_type"),
        "verification": task.get("verification") if isinstance(task.get("verification"), dict) else None,
        "outcome_verified": bool(task.get("outcome_verified")),
        "verification_method": task.get("verification_method"),
    }
