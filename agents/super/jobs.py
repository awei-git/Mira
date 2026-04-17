"""Scheduled job dispatch — run the declarative background scheduler.

Handles inline job execution, pipeline chaining (follow-up jobs for
completed background processes), and the main scheduler loop.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from config import get_known_user_ids
from state import load_state, save_state, session_record
from runtime.dispatcher import _dispatch_background
from runtime.jobs import (
    build_job_dispatch,
    build_job_session_record,
    evaluate_job_payload,
    get_jobs,
)

log = logging.getLogger("mira")


def _run_inline_scheduled_job(job, payload):
    """Execute an inline scheduled job immediately in-process."""
    if job.inline_runner == "health-check":
        from health import _run_health_check

        _run_health_check()
        return
    if job.inline_runner == "log-cleanup":
        from workflows.daily import log_cleanup

        log_cleanup()
        return
    raise ValueError(f"No inline runner registered for job '{job.inline_runner or job.name}'")


def _dispatch_pipeline_followups(completed: list[str], session_new: list[dict]):
    """Trigger follow-up jobs for completed background processes (pipeline chaining).

    When explore finishes -> immediately dispatch autowrite-check, etc.
    Bypasses cooldown/trigger checks — the chain definition is the authorization.
    """
    from runtime.jobs import get_pipeline_followups, get_job, build_job_dispatch

    for bg_name in completed:
        followups = get_pipeline_followups(bg_name)
        if not followups:
            continue
        for job_name in followups:
            job = get_job(job_name)
            if not job or not job.enabled:
                continue
            next_bg_name, cmd = build_job_dispatch(
                job,
                payload=True,
                python_executable=sys.executable,
                core_path=str(Path(__file__).resolve().parent / "core.py"),
            )
            dispatched = _dispatch_background(next_bg_name, cmd, group=job.blocking_group)
            if dispatched:
                log.info("Pipeline chain: %s -> %s dispatched", bg_name, job_name)
                session_new.append(session_record("pipeline_chain", f"{bg_name}->{job_name}"))


def _dispatch_scheduled_jobs(session_new: list[dict]):
    """Run the declarative background scheduler using runtime.jobs."""
    for job in sorted(get_jobs(), key=lambda item: item.priority):
        target_user_ids = get_known_user_ids() if getattr(job, "per_user", False) else [None]
        for target_user_id in target_user_ids:
            payload = evaluate_job_payload(job, user_id=target_user_id)
            if not payload:
                continue

            if job.inline:
                try:
                    _run_inline_scheduled_job(job, payload)
                except Exception as e:
                    log.error("%s failed: %s", job.name, e)
                continue

            bg_name, cmd = build_job_dispatch(
                job,
                payload,
                python_executable=sys.executable,
                core_path=str(Path(__file__).resolve().parent / "core.py"),
                user_id=target_user_id,
            )
            dispatched = _dispatch_background(bg_name, cmd, group=job.blocking_group)
            if dispatched is False:
                continue
            _record_scheduled_job_dispatch(job, payload, user_id=target_user_id)

            session_meta = build_job_session_record(job, payload)
            if session_meta:
                detail = session_meta.get("detail", "")
                if target_user_id:
                    detail = f"{target_user_id}:{detail}" if detail else target_user_id
                session_new.append(session_record(session_meta["action"], detail))


def _record_scheduled_job_dispatch(job, payload, user_id: str | None = None):
    """Persist dispatch state for jobs whose triggers are pure checks."""
    if job.name not in {"backlog-executor", "restore-dry-run"}:
        return

    now = datetime.now()
    state = load_state(user_id=user_id)
    state[job.state_key(today=now.strftime("%Y-%m-%d"))] = now.isoformat()
    save_state(state, user_id=user_id)
