"""Minimal executor for production-approved self-improvement actions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ops.backlog import ActionBacklog

log = logging.getLogger("mira.backlog_executor")

SUPPORTED_EXECUTORS = {"self_evolve_proposal"}


def run_once(*, dry_run: bool = False, backlog_path: Path | None = None) -> dict:
    """Execute one approved low-risk backlog item, if available."""
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
