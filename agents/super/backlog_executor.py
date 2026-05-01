"""Minimal executor for production-approved self-improvement actions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config import CONTROL_RUNTIME_DB_ENABLED
from ops.backlog import ActionBacklog

log = logging.getLogger("mira.backlog_executor")

SUPPORTED_EXECUTORS = {"self_evolve_proposal"}
CONTROL_EXECUTORS = {"request_verify.apply"}


def run_once(*, dry_run: bool = False, backlog_path: Path | None = None) -> dict:
    """Execute one approved low-risk backlog item, if available."""
    if backlog_path is None:
        control_result = _run_control_once(dry_run=dry_run)
        if control_result.get("executed"):
            return control_result

    backlog = ActionBacklog(path=backlog_path) if backlog_path else ActionBacklog()
    item = backlog.claim_next_approved(SUPPORTED_EXECUTORS)
    if not item:
        return {"executed": False, "reason": "no approved executable actions"}

    try:
        if item.executor == "self_evolve_proposal":
            result = _execute_self_evolve_proposal(item, dry_run=dry_run)
        else:
            result = {"success": False, "reason": f"unsupported executor: {item.executor}"}
    except Exception as exc:
        log.exception("Backlog execution failed for %s", item.title)
        result = {"success": False, "reason": f"executor crashed: {exc}"}

    backlog.finish_execution(
        item.title,
        success=bool(result.get("success")),
        resolution=str(result.get("reason", ""))[:500],
        verification_summary=str(result.get("verification_summary", ""))[:500],
        error="" if result.get("success") else str(result.get("reason", ""))[:500],
    )
    return {"executed": True, "title": item.title, **result}


def _execute_self_evolve_proposal(item, *, dry_run: bool) -> dict:
    proposal_path = Path(str(item.payload.get("proposal_path", ""))).expanduser()
    if not proposal_path.exists():
        return {"success": False, "reason": f"proposal missing: {proposal_path}"}

    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    if proposal.get("risk_level") != "low":
        return {"success": False, "reason": "proposal is not low risk"}

    if dry_run:
        return {
            "success": True,
            "reason": f"Dry run: would execute {proposal.get('title', proposal_path.name)}",
            "verification_summary": "dry-run only",
        }

    from self_evolve import auto_implement

    impl = auto_implement(proposal, proposal_path)
    if impl.get("success"):
        return {
            "success": True,
            "reason": impl.get("reason", "implemented"),
            "verification_summary": "proposal implemented and tests passed",
        }
    return {
        "success": False,
        "reason": impl.get("reason", "implementation failed"),
        "verification_summary": "implementation failed",
    }


def _run_control_once(*, dry_run: bool) -> dict:
    if not CONTROL_RUNTIME_DB_ENABLED:
        return {"executed": False, "reason": "control DB disabled"}
    try:
        from control.db import transaction
        from control.repository import ControlRepository
    except Exception as exc:
        return {"executed": False, "reason": f"control backlog unavailable: {exc}"}

    try:
        with transaction() as conn:
            repo = ControlRepository(conn)
            item = None
            for executor in sorted(CONTROL_EXECUTORS):
                item = repo.claim_backlog_item(executor)
                if item:
                    break
            if not item:
                return {"executed": False, "reason": "no proposed control backlog items"}
            if dry_run:
                return {
                    "executed": True,
                    "title": item.get("title", item["id"]),
                    "success": True,
                    "reason": "Dry run: would execute control backlog item",
                    "verification_summary": "dry-run only",
                }
            result = _execute_control_item(repo, item)
            repo.finish_backlog_item(
                item["id"],
                success=bool(result.get("success")),
                verification_summary=str(result.get("verification_summary", ""))[:500],
                last_error=str(result.get("reason", ""))[:500],
            )
            return {"executed": True, "title": item.get("title", item["id"]), **result}
    except Exception as exc:
        log.exception("Control backlog execution failed")
        return {"executed": True, "success": False, "reason": f"control executor crashed: {exc}"}


def _execute_control_item(repo, item: dict) -> dict:
    executor = item.get("executor")
    if executor == "request_verify.apply":
        return _execute_request_verify(repo, item)
    return {"success": False, "reason": f"unsupported control executor: {executor}"}


def _execute_request_verify(repo, item: dict) -> dict:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else {}
    task_id = str(item.get("task_id") or payload.get("task_id") or "")
    user_id = str(item.get("user_id") or "")

    if verification.get("verified"):
        return {
            "success": True,
            "reason": "request verifier already passed at task completion",
            "verification_summary": str(verification.get("summary") or "verified")[:500],
        }

    if user_id and task_id:
        current = repo.get_item(user_id, task_id)
        if current and current.get("outcome_verified"):
            current_verification = current.get("verification") if isinstance(current.get("verification"), dict) else {}
            return {
                "success": True,
                "reason": "request verifier passed after backlog enqueue",
                "verification_summary": str(current_verification.get("summary") or "verified")[:500],
            }

    summary = str(verification.get("summary") or "request outcome was not verified")[:500]
    return {
        "success": False,
        "reason": summary,
        "verification_summary": summary,
    }
