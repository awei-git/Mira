"""Plan and step state artifacts for task execution.

Phase 0 production hardening requires plan artifacts and explicit step state.
This module owns the workspace-local JSON files that describe the declared plan
and the current state of each execution step.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


PLAN_ARTIFACT = "plan.json"
STEP_STATE_ARTIFACT = "step_states.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _step_stub(index: int, step: dict) -> dict:
    prediction = step.get("prediction") or {}
    return {
        "step_index": index,
        "step_id": f"step-{index + 1:02d}",
        "declared_agent": step.get("agent", ""),
        "execution_agent": "",
        "tier": step.get("tier", "light"),
        "instruction_preview": str(step.get("instruction", ""))[:300],
        "success_criteria": str(prediction.get("success_criteria", ""))[:200],
        "capability_class": step.get("capability_class", "read-only"),
        "policy": step.get("policy", {}),
        "status": "pending",
        "started_at": "",
        "completed_at": "",
        "input_summary": "",
        "output_summary": "",
        "failure_reason": "",
        "retry_count": 0,
    }


def _derive_plan_status(steps: list[dict]) -> str:
    statuses = {step.get("status", "pending") for step in steps}
    for terminal in ("error", "failed", "blocked", "needs-input"):
        if terminal in statuses:
            return terminal
    if statuses and statuses <= {"done", "skipped"}:
        return "done"
    if "running" in statuses:
        return "running"
    return "pending"


def initialize_plan_artifacts(
    workspace: Path,
    *,
    task_id: str,
    user_id: str,
    request: str,
    plan: list[dict],
) -> None:
    """Create or replace the canonical plan + step state artifacts."""
    created_at = _utc_iso()
    plan_payload = {
        "task_id": task_id,
        "user_id": user_id,
        "created_at": created_at,
        "updated_at": created_at,
        "request_preview": request[:300],
        "step_count": len(plan),
        "status": "pending",
        "steps": [
            {
                "step_index": index,
                "step_id": f"step-{index + 1:02d}",
                "agent": step.get("agent", ""),
                "tier": step.get("tier", "light"),
                "instruction_preview": str(step.get("instruction", ""))[:300],
                "prediction": step.get("prediction", {}),
                "capability_class": step.get("capability_class", "read-only"),
                "policy": step.get("policy", {}),
            }
            for index, step in enumerate(plan)
        ],
    }
    state_payload = {
        "task_id": task_id,
        "user_id": user_id,
        "created_at": created_at,
        "updated_at": created_at,
        "status": "pending",
        "steps": [_step_stub(index, step) for index, step in enumerate(plan)],
    }
    _atomic_write_json(workspace / PLAN_ARTIFACT, plan_payload)
    _atomic_write_json(workspace / STEP_STATE_ARTIFACT, state_payload)


def update_plan_status(workspace: Path, status: str, *, summary: str = "") -> None:
    """Update the top-level plan and step-state status."""
    now = _utc_iso()
    for artifact_name in (PLAN_ARTIFACT, STEP_STATE_ARTIFACT):
        path = workspace / artifact_name
        payload = _load_json(path)
        if not payload:
            continue
        payload["status"] = status
        payload["updated_at"] = now
        if summary:
            payload["summary"] = summary[:500]
        _atomic_write_json(path, payload)


def mark_step_running(
    workspace: Path,
    *,
    step_index: int,
    declared_agent: str,
    execution_agent: str,
    input_summary: str,
) -> None:
    state_path = workspace / STEP_STATE_ARTIFACT
    payload = _load_json(state_path)
    steps = payload.get("steps", [])
    if step_index >= len(steps):
        return
    step = steps[step_index]
    step["declared_agent"] = declared_agent
    step["execution_agent"] = execution_agent
    step["status"] = "running"
    step["started_at"] = step.get("started_at") or _utc_iso()
    step["input_summary"] = input_summary[:500]
    payload["status"] = "running"
    payload["updated_at"] = _utc_iso()
    _atomic_write_json(state_path, payload)
    update_plan_status(workspace, "running")


def mark_step_finished(
    workspace: Path,
    *,
    step_index: int,
    status: str,
    declared_agent: str,
    execution_agent: str,
    output_summary: str = "",
    failure_reason: str = "",
    retry_count: int = 0,
) -> None:
    state_path = workspace / STEP_STATE_ARTIFACT
    payload = _load_json(state_path)
    steps = payload.get("steps", [])
    if step_index >= len(steps):
        return

    step = steps[step_index]
    step["declared_agent"] = declared_agent
    step["execution_agent"] = execution_agent
    step["status"] = status
    step["completed_at"] = _utc_iso()
    step["output_summary"] = output_summary[:500]
    step["failure_reason"] = failure_reason[:500]
    step["retry_count"] = retry_count

    payload["updated_at"] = _utc_iso()
    payload["status"] = _derive_plan_status(steps)

    _atomic_write_json(state_path, payload)
    if payload["status"] == "done":
        update_plan_status(workspace, "done", summary=output_summary)
    elif payload["status"] in {"failed", "error", "blocked", "needs-input"}:
        update_plan_status(workspace, payload["status"], summary=failure_reason or output_summary)
