"""Shared runtime contract helpers for task state and trace IDs."""

from __future__ import annotations


_STATUS_ALIASES = {
    "completed": "done",
    "error": "failed",
    "needs_input": "needs-input",
}


def normalize_task_status(status: str | None) -> str:
    raw = str(status or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    return _STATUS_ALIASES.get(lowered, lowered)


def derive_workflow_id(
    *,
    task_id: str,
    thread_id: str = "",
    workflow_id: str = "",
) -> str:
    explicit = str(workflow_id or "").strip()
    if explicit:
        return explicit
    thread = str(thread_id or "").strip()
    if thread:
        return thread
    return str(task_id or "").strip()
