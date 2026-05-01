from __future__ import annotations

import logging
from typing import Any, Callable

from control.db import transaction
from control.repository import ControlRepository

from .dbos_runtime import get_dbos


log = logging.getLogger("mira.task_workflow")

_workflow_func: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _ensure_dispatch_workflow():
    global _workflow_func
    if _workflow_func is not None:
        return _workflow_func
    dbos = get_dbos()

    @dbos.step(name="mira_record_dispatch_step", retries_allowed=True, max_attempts=3)
    def record_dispatch_step(payload: dict[str, Any]) -> dict[str, Any]:
        with transaction() as conn:
            repo = ControlRepository(conn)
            repo.record_task_event(
                payload["user_id"],
                payload["task_id"],
                "workflow.dispatch_recorded",
                status=payload.get("status", "dispatched"),
                payload={
                    "workflow_id": payload.get("workflow_id", ""),
                    "pid": payload.get("pid"),
                    "workspace": payload.get("workspace", ""),
                    "attempt_count": payload.get("attempt_count", 1),
                },
            )
        return payload

    @dbos.workflow(name="mira_task_dispatch_v1")
    def task_dispatch_workflow(payload: dict[str, Any]) -> dict[str, Any]:
        return record_dispatch_step(payload)

    _workflow_func = task_dispatch_workflow
    return _workflow_func


def start_dispatch_workflow(payload: dict[str, Any]) -> str:
    """Start the DBOS dispatch bookkeeping workflow and return its workflow id.

    The worker remains a subprocess; DBOS records the dispatch boundary and
    retries audit persistence. Crash/resume of the worker itself is enforced by
    TaskManager's status scan and bounded auto-retry.
    """
    workflow = _ensure_dispatch_workflow()
    handle = get_dbos().start_workflow(workflow, payload)
    workflow_id = getattr(handle, "workflow_id", None)
    if callable(workflow_id):
        return str(workflow_id())
    return str(workflow_id or payload.get("workflow_id") or payload.get("task_id") or "")
