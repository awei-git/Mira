#!/usr/bin/env python3
"""Mira Super Agent — orchestrator with soul, memory, and curiosity.

Modes:
    run     — full cycle: check inbox, maybe explore/reflect
    respond — process inbox requests only
    explore — fetch sources and write briefing
    reflect — weekly reflection and memory consolidation
"""
import fcntl
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

# Add shared + sibling agent dirs to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "explorer"))

import health_monitor

from config import (
    MIRA_ROOT, WORKSPACE_DIR, BRIEFINGS_DIR, LOGS_DIR, STATE_FILE,
    MIRA_DIR, ARTIFACTS_DIR, CLEANUP_DAYS,
    JOURNAL_DIR, WRITINGS_OUTPUT_DIR, WRITINGS_DIR,
    validate_config,
    get_known_user_ids, get_user_config, is_agent_allowed, get_model_restriction, should_filter_content,
)
try:
    from mira import Mira, Message
except (ImportError, ModuleNotFoundError):
    Mira = None
    Message = None
from task_manager import TaskManager, TASKS_DIR
from soul_manager import load_soul, format_soul, append_memory, check_prompt_injection
from sub_agent import claude_think
from writing_workflow import (
    check_writing_responses, advance_project, start_from_plan,
)
from prompts import respond_prompt

# Extracted workflow modules — business logic for each domain
from workflows.helpers import (
    _append_to_daily_feed, _copy_to_briefings, _sync_journals_to_briefings,
    _slugify, _format_feed_items, _extract_deep_dive, _extract_comment_suggestions,
    _extract_section, _extract_recent_briefing_topics, _is_duplicate_topic,
    _extract_recent_published_titles, _gather_recent_briefings,
    _gather_recent_episodes, _prune_episodes_from_reflect,
    _gather_today_tasks, _gather_today_skills, _gather_usage_summary,
    _gather_today_comments, _mine_za_ideas, _mine_za_one,
    _days_since_last_publish, PUBLISH_COOLDOWN_DAYS,
    harvest_observations, _maybe_create_spontaneous_idea,
    _prune_old_logs,
)
from workflows.explore import do_explore
from workflows.reflect import do_reflect
from workflows.journal import do_journal
from workflows.daily import (
    do_daily_report, do_daily_photo, handle_photo_feedback,
    do_zhesi, do_soul_question, do_research, do_book_review,
    do_analyst, do_skill_study, run_podcast_episode,
    do_assess, _run_self_improve, do_idle_think, log_cleanup,
)
from workflows.social import (
    do_check_comments, do_growth_cycle, do_notes_cycle, do_spark_check,
)
from workflows.writing import do_autowrite_check, run_autowrite_pipeline

# Extracted modules — triggers decide "should we run X?", dispatcher spawns bg tasks
from runtime.triggers import (
    _should_health_weekly_report,
)
from runtime.dispatcher import (
    _dispatch_background, _is_bg_running, _reap_stale_pids, _count_bg_running,
    MAX_CONCURRENT_BG,
)
from runtime.jobs import (
    build_job_dispatch,
    build_job_session_record,
    evaluate_job_payload,
    get_jobs,
)

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Graceful shutdown — SIGTERM sets flag, current operation finishes cleanly
# ---------------------------------------------------------------------------
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.info("SIGTERM received — will shut down after current operation")


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def should_shutdown() -> bool:
    """Check if shutdown was requested. Call between operations."""
    return _shutdown_requested


# ---------------------------------------------------------------------------
# State management (tracks when we last ran each mode)
# ---------------------------------------------------------------------------

_LEGACY_USER_STATE_EXACT_KEYS = {
    "last_reflect",
    "last_skill_study",
    "last_spark_check",
    "spark_memory_lines",
    "last_comment_check",
    "last_growth_cycle",
    "last_notes_cycle",
}

_LEGACY_USER_STATE_PREFIXES = (
    "journal_",
    "skill_study_",
    "sparks_",
    "spontaneous_idea_",
)


def _load_state_raw() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _locked_state_write(update_fn):
    lock_file = STATE_FILE.with_suffix(".lock")
    try:
        with open(lock_file, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                state = _load_state_raw()
                new_state = update_fn(state)
                STATE_FILE.write_text(
                    json.dumps(new_state, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except BlockingIOError:
        log.warning("State file locked by another process, skipping save")


def _is_legacy_user_state_key(key: str) -> bool:
    return key in _LEGACY_USER_STATE_EXACT_KEYS or any(
        key.startswith(prefix) for prefix in _LEGACY_USER_STATE_PREFIXES
    )


def load_state(user_id: str | None = None) -> dict:
    state = _load_state_raw()
    if not user_id:
        return state

    users = state.get("users", {})
    if isinstance(users, dict):
        user_state = users.get(user_id)
        if isinstance(user_state, dict):
            return dict(user_state)

    # Backward compatibility: first per-user read can still see migrated keys
    # from the old flat state file until that user writes its own namespace.
    if user_id != "ang":
        return {}
    return {
        key: value for key, value in state.items()
        if _is_legacy_user_state_key(key)
    }


def save_state(state: dict, user_id: str | None = None):
    if not user_id:
        _locked_state_write(lambda _old_state: state)
        return

    def _update(raw_state: dict) -> dict:
        users = raw_state.get("users")
        if not isinstance(users, dict):
            users = {}
        users[user_id] = state
        raw_state["users"] = users
        return raw_state

    _locked_state_write(_update)


# ---------------------------------------------------------------------------
# Session context — rolling short-term memory across cycles (Level 1)
# ---------------------------------------------------------------------------

_SESSION_FILE = MIRA_ROOT / ".session_context.json"
_SESSION_MAX_ENTRIES = 40  # ~20 minutes of context at 30s cycles


def load_session_context() -> list[dict]:
    """Load recent session context entries. Each entry is one cycle's decisions."""
    if not _SESSION_FILE.exists():
        return []
    try:
        data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_session_context(entries: list[dict]):
    """Save session context, keeping only the most recent entries."""
    trimmed = entries[-_SESSION_MAX_ENTRIES:]
    try:
        _SESSION_FILE.write_text(
            json.dumps(trimmed, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:
        log.warning("Failed to save session context: %s", e)


def session_record(action: str, detail: str = "", **extra) -> dict:
    """Create a session context entry."""
    entry = {
        "ts": datetime.now().isoformat(),
        "action": action,
    }
    if detail:
        entry["detail"] = detail
    entry.update(extra)
    return entry


def session_has_recent(action: str, hours: float = 1.0,
                       ctx: list[dict] | None = None) -> dict | None:
    """Check if a specific action was recorded recently. Returns the entry or None."""
    if ctx is None:
        ctx = load_session_context()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    for entry in reversed(ctx):
        if entry.get("ts", "") < cutoff:
            break
        if entry.get("action") == action:
            return entry
    return None


# ---------------------------------------------------------------------------
# TALK mode — handle messages from Mira (iPhone ↔ Mac)
# ---------------------------------------------------------------------------

def _talk_slug(content: str, msg_id: str) -> str:
    """Generate a short meaningful directory name from message content."""
    # Take first ~30 chars of content, slugify
    slug = content[:40].strip()
    slug = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", slug)  # keep CJK, alphanum, spaces
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:30]
    if not slug:
        slug = "talk"
    # Append short id to avoid collisions
    return f"{slug}_{msg_id[:6]}"


def _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd=None):
    """Dispatch a task. Set working only on success. Return 'busy' if all slots full.

    Returns: 'ok', 'busy', or 'failed'.
    Caller should break command loop on 'busy' — command stays in ledger for next cycle.
    """
    if task_mgr.is_busy():
        log.info("STATE %s: dispatch deferred (all %d slots occupied)", msg.id,
                 task_mgr.get_active_count())
        return "busy"
    # Inject user access control context into message for the worker
    if cmd:
        msg.user_id = cmd.get("_user_id", "ang")
        msg.user_role = cmd.get("_user_role", "admin")
        msg.model_restriction = cmd.get("_model_restriction")
        msg.content_filter = cmd.get("_content_filter", False)
        msg.allowed_agents = cmd.get("_allowed_agents", [])
    task_id = task_mgr.dispatch(msg, workspace)
    if task_id:
        bridge.update_status(msg.id, "working")
        log.info("STATE %s: -> working (pid dispatched)", msg.id)
        return "ok"
    else:
        bridge.update_status(msg.id, "failed",
                             error={"code": "dispatch_failed",
                                    "message": "Worker process failed to start",
                                    "retryable": True})
        log.error("STATE %s: -> failed (dispatch error)", msg.id)
        return "failed"


def _quarantine_inbound_command(bridge, cmd: dict, item_id: str, title: str,
                                content: str, reason: str):
    """Record and block a suspicious inbound command before task dispatch."""
    log.warning("Inbound command quarantined: item=%s reason=%s", item_id or "-", reason)
    quarantine_error = {
        "code": "prompt_injection_blocked",
        "message": f"Blocked suspicious input: {reason}",
        "retryable": False,
    }

    if item_id:
        if not bridge.item_exists(item_id):
            bridge.create_task(
                item_id,
                title or item_id,
                content or "",
                sender=cmd.get("sender", "user"),
                tags=["security", "quarantined"],
                origin="user",
            )
        bridge.update_status(item_id, "failed", error=quarantine_error)
        bridge.set_tags(item_id, ["security", "quarantined"])


def _check_inbound_command_safety(bridge, cmd: dict, item_id: str, title: str,
                                  content: str) -> bool:
    """Return True when an inbound command is safe to dispatch."""
    flagged, reason = check_prompt_injection(content)
    if not flagged:
        return True
    _quarantine_inbound_command(bridge, cmd, item_id, title, content, reason)
    return False


def do_talk():
    """Process Mira messages: dispatch new tasks + collect completed results.

    This is the super agent — it dispatches tasks to background workers
    and collects their results. Each call takes seconds, not minutes.
    Processes commands for ALL registered users.
    """
    # Create per-user bridge instances
    all_bridges = Mira.for_all_users()
    bridge = all_bridges[0]  # default (ang) for legacy code paths
    bridges_by_user = {b.user_id: b for b in all_bridges}
    default_bridge = bridges_by_user.get("ang", all_bridges[0])
    task_mgr = TaskManager()

    # Heartbeat is shared state, but publish it to every user bridge.
    status_summary = task_mgr.get_status_summary()
    for user_bridge in all_bridges:
        user_bridge.heartbeat(agent_status=status_summary)

    # --- Phase A: Collect results from previously dispatched tasks ---
    completed = task_mgr.check_tasks()
    for rec in completed:
        bridge = bridges_by_user.get(getattr(rec, "user_id", "ang"), default_bridge)
        content = task_mgr.get_reply_content(rec)
        footer = _status_footer(task_mgr)
        # Comment threads: reply is in .reply.json sidecar ONLY (written by task_worker).
        # Do NOT write to outbox or task JSON — that creates duplicates.
        is_comment = rec.task_id.startswith("comment_")

        if rec.status == "needs-input":
            msg_text = (content + footer) if not is_comment else ""
            bridge.update_status(rec.task_id, "needs-input", agent_message=msg_text)
            if rec.tags:
                bridge.set_tags(rec.task_id, rec.tags)
            log.info("STATE %s: working -> needs-input", rec.task_id)
        elif rec.status == "done":
            msg_text = (content + footer) if not is_comment else ""
            bridge.update_status(rec.task_id, "done", agent_message=msg_text)
            if rec.tags:
                bridge.set_tags(rec.task_id, rec.tags)
            # Write result back to todo followups if this task originated from a todo
            ws = Path(rec.workspace) if rec.workspace else None
            todo_marker = ws / ".todo_id" if ws else None
            if todo_marker and todo_marker.exists():
                _todo_id = todo_marker.read_text().strip()
                if _todo_id and content:
                    bridge.add_followup(_todo_id, content, source="agent")
                    bridge.update_todo(_todo_id, status="done")
                    log.info("Todo %s: agent reply written to followups", _todo_id)
            log.info("STATE %s: working -> done", rec.task_id)
        elif rec.status in ("error", "timeout", "blocked"):
            retryable = task_mgr.can_retry(rec)
            if rec.status == "blocked":
                error_msg = f"处理被阻止: {rec.summary}" if rec.summary else "处理被阻止。"
            else:
                error_msg = f"处理失败: {rec.summary}" if rec.summary else "处理失败，请稍后重试。"
            bridge.update_status(rec.task_id, "failed",
                                 error={"code": rec.status, "message": error_msg,
                                        "retryable": retryable})
            log.warning("STATE %s: working -> failed (%s: %s)", rec.task_id, rec.status, rec.summary)

    # --- Score completed tasks (grounded metrics only) ---
    for rec in completed:
        try:
            from evaluator import evaluate_task_outcome, record_event
            t_scores = evaluate_task_outcome({
                "status": rec.status,
                "summary": rec.summary or "",
                "workspace": rec.workspace or "",
            })
            if t_scores:
                record_event("task_complete", t_scores, {
                    "task_id": rec.task_id,
                    "agent": getattr(rec, "agent", None) or
                             (rec.tags[0] if rec.tags else "unknown"),
                })
        except (ImportError, AttributeError) as e:
            log.debug("Task scoring skipped: %s", e)

    # --- Phase B1: Process commands from all users ---
    for user_bridge in all_bridges:
        for cmd in user_bridge.poll_commands():
            bridge = user_bridge  # use this user's bridge for item creation
            cmd_type = cmd.get("type", "")
            sender = cmd.get("sender", "user")
            content = cmd.get("content", "")
            title = cmd.get("title", content[:50] if content else "Untitled")
            item_id = cmd.get("item_id", "")
            tags = cmd.get("tags") or []
            log.info("Mira command [%s]: type=%s title=%s", user_bridge.user_id, cmd_type, title[:60])

            # --- Access control: check user permissions ---
            user_cfg = get_user_config(user_bridge.user_id)
            if user_cfg["role"] == "guest":
                log.warning("Unknown user '%s' — restricted to guest access", user_bridge.user_id)
            # Store user context in command for downstream use
            cmd["_user_id"] = user_bridge.user_id
            cmd["_user_role"] = user_cfg["role"]
            cmd["_model_restriction"] = user_cfg.get("model_restriction")
            cmd["_content_filter"] = user_cfg.get("content_filter", False)
            cmd["_allowed_agents"] = user_cfg.get("allowed_agents", ["general"])

            if cmd_type == "new_request":
                task_id = cmd.get("item_id") or f"req_{uuid.uuid4().hex[:8]}"
                quick = cmd.get("quick", False)
                if not _check_inbound_command_safety(bridge, cmd, task_id, title, content):
                    continue
                if not bridge.item_exists(task_id):
                    bridge.create_task(task_id, title, content, sender=sender, tags=tags, origin="user")
                workspace = TASKS_DIR / _talk_slug(content, task_id)
                msg = Message(id=task_id, sender=sender, timestamp=cmd.get("timestamp",""),
                              content=content, thread_id=task_id)
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "new_discussion":
                disc_id = cmd.get("item_id") or f"disc_{uuid.uuid4().hex[:8]}"
                if not _check_inbound_command_safety(bridge, cmd, disc_id, title, content):
                    continue
                if not bridge.item_exists(disc_id):
                    bridge.create_discussion(disc_id, title, content, sender=sender, tags=tags)
                workspace = TASKS_DIR / _talk_slug(content, disc_id)
                msg = Message(id=disc_id, sender=sender, timestamp=cmd.get("timestamp",""),
                              content=content, thread_id=disc_id)
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "reply" and item_id:
                if not _check_inbound_command_safety(bridge, cmd, item_id, title, content):
                    continue
                bridge.append_message(item_id, sender, content)
                # Photo daily feedback — handle inline, no task dispatch needed
                if item_id.startswith("photo_daily_"):
                    try:
                        handle_photo_feedback(item_id, content)
                    except Exception as e:
                        log.error("Photo feedback handler failed: %s", e)
                    continue
                workspace = TASKS_DIR / _talk_slug(content, item_id)
                msg = Message(id=item_id, sender=sender, timestamp=cmd.get("timestamp",""),
                              content=content, thread_id=item_id)
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "comment":
                parent_id = cmd.get("parent_id", "")
                disc_id = f"disc_{uuid.uuid4().hex[:8]}"
                bridge.create_discussion(disc_id, f"Re: {title}", content,
                                         sender=sender, tags=["feed-comment"],
                                         parent_id=parent_id)
            elif cmd_type == "cancel" and item_id:
                bridge.update_status(item_id, "failed",
                                     error={"code": "cancelled", "message": "Cancelled by user", "retryable": False})
            elif cmd_type == "recall":
                query = cmd.get("query", content or "")
                recall_id = f"req_recall_{uuid.uuid4().hex[:8]}"
                if not _check_inbound_command_safety(bridge, cmd, recall_id, f"Recall: {query[:40]}", query):
                    continue
                bridge.create_task(recall_id, f"Recall: {query[:40]}", query,
                                   sender=sender, tags=["recall"], origin="user")
                workspace = TASKS_DIR / _talk_slug(query, recall_id)
                msg = Message(id=recall_id, sender=sender, timestamp=cmd.get("timestamp",""),
                              content=query, thread_id=recall_id)
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "archive" and item_id:
                bridge.archive_thread(item_id)
            elif cmd_type == "pin" and item_id:
                item = bridge._read_item(item_id)
                if item:
                    item["pinned"] = cmd.get("pinned", True)
                    bridge._write_item(item)
                    bridge._update_manifest()
            elif cmd_type == "tag" and item_id:
                bridge.set_tags(item_id, tags)
            elif cmd_type == "share" and item_id:
                bridge.share_item(item_id)
            elif cmd_type == "add_todo":
                prio = cmd.get("priority", "medium")
                bridge.add_todo(title, priority=prio)
                log.info("Todo added for %s: %s (%s)", user_bridge.user_id, title, prio)
            elif cmd_type == "todo_followup":
                todo_id = cmd.get("todo_id", "") or cmd.get("item_id", "")
                if todo_id and content:
                    req_id = f"req_{todo_id}"
                    if not _check_inbound_command_safety(bridge, cmd, req_id, f"Todo: {title}", content):
                        continue
                    # Save user followup to todo
                    bridge.add_followup(todo_id, content, source="user")
                    bridge.update_todo(todo_id, status="working")
                    # Dispatch to worker — build context from all previous followups
                    todo = next((t for t in bridge.load_todos() if t["id"] == todo_id), None)
                    if todo:
                        history = "\n".join(
                            f"[{fu.get('source','?')}] {fu.get('content','')}"
                            for fu in todo.get("followups", [])
                        )
                        full_content = f"Todo: {todo['title']}\n\nConversation so far:\n{history}\n\nUser's latest message:\n{content}"
                        if not bridge.item_exists(req_id):
                            bridge.create_task(req_id, f"Todo: {todo['title']}", full_content,
                                               sender=sender, tags=["todo"], origin="user")
                        else:
                            bridge.append_message(req_id, sender, content)
                            bridge.update_status(req_id, "working")
                        workspace = TASKS_DIR / _talk_slug(content, req_id)
                        workspace.mkdir(parents=True, exist_ok=True)
                        (workspace / ".todo_id").write_text(todo_id)
                        msg = Message(id=req_id, sender=sender, timestamp=cmd.get("timestamp", ""),
                                      content=full_content, thread_id=req_id)
                        result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                        if result == "busy":
                            break
                    log.info("Todo followup for %s/%s dispatched", user_bridge.user_id, todo_id)

        # Process pending todos if agent is idle
        if task_mgr.get_active_count() == 0:
            todo = user_bridge.get_next_todo()
            if todo:
                todo_id = todo["id"]
                todo_title = todo["title"]
                log.info("Picking up todo %s: %s", todo_id, todo_title)
                user_bridge.update_todo(todo_id, status="working")
                # Create a request item for the todo
                req_id = f"req_{todo_id}"
                user_bridge.create_task(req_id, f"Todo: {todo_title}", todo_title,
                                         sender="user", tags=["todo"], origin="user")
                workspace = TASKS_DIR / _talk_slug(todo_title, req_id)
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / ".todo_id").write_text(todo_id)
                msg = Message(id=req_id, sender="user", timestamp="",
                              content=todo_title, thread_id=req_id)
                _dispatch_or_requeue(task_mgr, user_bridge, msg, workspace)

    # --- Phase B2: Dispatch legacy inbox messages to background workers ---
    legacy_messages_found = False
    legacy_busy = False
    for bridge in all_bridges:
        if legacy_busy:
            break
        messages = bridge.poll()
        if not messages:
            continue
        legacy_messages_found = True

        # External input arrived — partial-reset emptiness (external takes priority)
        try:
            from emptiness import on_external_input
            on_external_input(user_id=bridge.user_id)
        except ImportError:
            pass

        for msg, msg_path in messages:
            # Ensure worker/runtime sees the correct user even on legacy inbox flows.
            msg.user_id = getattr(msg, "user_id", bridge.user_id) or bridge.user_id

            # Skip if already dispatched (e.g. from a previous cycle)
            if task_mgr.is_dispatched(msg.id):
                log.info("Mira [%s] already dispatched, skipping", msg.id)
                bridge.mark_processed(msg_path)
                continue

            log.info("Mira [%s] from %s: %s", msg.id, msg.sender, msg.content[:80])

            # Handle meta-commands (archive, status, etc.)
            if _is_meta_command(msg.content):
                _handle_meta_command(bridge, msg, msg_path, task_mgr=task_mgr)
                continue

            # --- Retry / follow-up on existing task ---
            # When iOS sends a follow-up, thread_id = original task_id
            if msg.thread_id:
                old_rec = task_mgr.find_failed_task(msg.thread_id)
                if old_rec:
                    log.info("Mira [%s] is a retry/follow-up for task %s", msg.id, msg.thread_id)
                    if not task_mgr.can_retry(old_rec):
                        retry_msg = "该任务已达到重试上限，请检查失败原因后重新发起新任务。"
                        bridge.reply(msg.id, msg.sender, retry_msg, thread_id=msg.thread_id)
                        bridge.update_task_status(msg.thread_id, "failed")
                        bridge.mark_processed(msg_path)
                        log.warning("Retry ceiling reached for task %s (%d/%d)",
                                    old_rec.task_id,
                                    getattr(old_rec, "attempt_count", 0),
                                    getattr(old_rec, "max_attempts", 0))
                        continue
                    # Reuse the original workspace
                    msg_workspace = Path(old_rec.workspace) if old_rec.workspace else TASKS_DIR / _talk_slug(msg.content, msg.thread_id)
                    # Remove old record so dispatch() won't see it as busy
                    removed = task_mgr.reset_for_retry(msg.thread_id)
                    attempt_count = getattr(removed, "attempt_count", getattr(old_rec, "attempt_count", 1)) + 1
                    max_attempts = getattr(removed, "max_attempts", getattr(old_rec, "max_attempts", 1))
                    bridge.ack(msg.id, "received")
                    bridge.update_task_status(msg.thread_id, "working")
                    # Use original task_id for dispatch (overwrite msg.id)
                    msg.id = msg.thread_id
                    task_id = task_mgr.dispatch(
                        msg,
                        msg_workspace,
                        attempt_count=attempt_count,
                        max_attempts=max_attempts,
                    )
                    if task_id:
                        bridge.ack(msg.id, "processing")
                        bridge.mark_processed(msg_path)
                    elif task_mgr.is_busy():
                        log.info("Mira [%s] retry queued (agent busy)", msg.id)
                        legacy_busy = True
                        break
                    else:
                        bridge.reply(msg.id, msg.sender, "重试分发失败，请稍后再试。",
                                    thread_id=msg.thread_id)
                        bridge.mark_processed(msg_path)
                    continue

            # If iOS already created a task file for this thread, reuse it
            # (thread_id can be "task_xxx" or a hex ID like "a189fed4")
            if msg.thread_id and bridge.task_exists(msg.thread_id):
                effective_task_id = msg.thread_id
            else:
                effective_task_id = msg.id

            # Each message gets its own workspace under Mira/tasks/
            slug = _talk_slug(msg.content, effective_task_id)
            msg_workspace = TASKS_DIR / slug

            bridge.ack(msg.id, "received")

            if effective_task_id == msg.id:
                # No iOS task file — create one
                task_title = msg.content[:50].strip()
                bridge.create_task(
                    task_id=msg.id,
                    title=task_title,
                    first_message=msg.content,
                    sender=msg.sender,
                )
            else:
                # Existing task — append the follow-up message and reopen
                bridge.append_task_message(effective_task_id, msg.sender, msg.content)
                bridge.update_task_status(effective_task_id, "queued")

            # Use the effective task_id for dispatch
            msg.id = effective_task_id

            # Dispatch to background worker (returns immediately)
            # Only one Claude Code instance at a time — if busy, leave message for next cycle
            task_id = task_mgr.dispatch(msg, msg_workspace)
            if task_id:
                bridge.ack(msg.id, "processing")
                bridge.update_task_status(effective_task_id, "working")
                bridge.mark_processed(msg_path)
            elif task_mgr.is_busy():
                # Busy — don't mark processed, will retry next launchd cycle
                log.info("Mira [%s] queued (agent busy)", msg.id)
                legacy_busy = True
                break  # no point trying more messages
            else:
                # Actual dispatch failure
                bridge.reply(msg.id, msg.sender, "任务分发失败，请稍后重试。",
                            thread_id=msg.thread_id)
                bridge.ack(msg.id, "error")
                bridge.mark_processed(msg_path)

    if not legacy_messages_found:
        log.info("Mira: no new messages (active tasks: %d)", task_mgr.get_active_count())

    # Periodic cleanup
    for bridge in all_bridges:
        bridge.cleanup_old(days=CLEANUP_DAYS)
    task_mgr.cleanup_old_records(max_age_days=7)

    # Sweep stuck items — safety net for all other bugs
    for bridge in all_bridges:
        _sweep_stuck_items(bridge, task_mgr)


def _check_pending_publish():
    """Auto-publish approved articles from the manifest."""
    from publish_manifest import get_next_pending, update_manifest, validate_step

    entry = get_next_pending("published")  # finds status="approved"
    if not entry:
        # Legacy fallback: check agent_state.json (one release cycle)
        state = load_state()
        legacy = state.get("pending_publish")
        if legacy:
            # Migrate to manifest
            slug = legacy.get("item_id", "unknown").replace("autowrite_", "").replace("_", "-")
            final_md = legacy.get("final_md", "final.md")
            workspace = legacy.get("workspace", "")
            if not Path(final_md).is_absolute() and workspace:
                final_md = str(Path(workspace) / final_md)
            update_manifest(
                slug,
                title=legacy.get("title", slug),
                status="approved",
                workspace=workspace,
                final_md=final_md,
                item_id=legacy.get("item_id", ""),
                auto_podcast=legacy.get("auto_podcast", True),
            )
            del state["pending_publish"]
            save_state(state)
            log.info("Migrated legacy pending_publish '%s' to manifest", slug)
            entry = get_next_pending("published")
        if not entry:
            return

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import publish_to_substack

        final = Path(entry["final_md"])
        if not final.exists():
            update_manifest(entry["slug"], error=f"final_md not found: {final}")
            return

        workspace = Path(entry.get("workspace", final.parent))
        content = final.read_text(encoding="utf-8")
        result = publish_to_substack(
            title=entry["title"],
            subtitle=entry.get("subtitle", ""),
            article_text=content,
            workspace=workspace,
        )

        if "发布被拦截" in result or "cooldown" in result.lower():
            log.info("Publish cooldown active for '%s': %s", entry["slug"], result[:80])
            return  # still in cooldown, try next cycle

        # Published successfully
        post_url = ""
        for part in result.split():
            if "substack.com" in part:
                post_url = part
                break

        # Post-condition: verify the published URL is reachable
        passed, verify_err = validate_step(entry["slug"], "published",
                                           url=post_url, title=entry["title"])
        if not passed:
            try:
                from failure_log import record_failure
                record_failure(
                    pipeline="publish", step="substack_publish", slug=entry["slug"],
                    error_type="verification_failed", error_message=verify_err,
                    expected_output=f"Accessible article at {post_url}",
                    actual_output=verify_err,
                )
            except Exception:
                pass
            log.warning("Publish verification failed for '%s': %s", entry["title"], verify_err)
            # Don't fail hard — URL may take time to propagate

        update_manifest(entry["slug"], status="published", substack_url=post_url)
        log.info("Auto-published '%s': %s", entry["title"], result[:100])

        # Update item status
        bridge = Mira()
        item_id = entry.get("item_id")
        if item_id:
            bridge.update_status(item_id, "done",
                                agent_message=f"已发布到 Substack: {result[:200]}")

        # Queue notes for the new article
        from notes import queue_notes_for_article
        if post_url:
            queue_notes_for_article(entry["title"], content[:3000], post_url)

        # Tweet about the new article
        try:
            sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
            from twitter import tweet_for_article
            tweet_result = tweet_for_article(
                entry["title"], entry.get("subtitle", ""), post_url,
                soul_context="")
            if tweet_result:
                log.info("Tweeted about '%s'", entry["title"])
        except Exception as tw_e:
            log.warning("Twitter promotion failed for '%s': %s", entry["slug"], tw_e)

    except Exception as e:
        update_manifest(entry["slug"], error=str(e))
        log.warning("Pending publish failed for '%s': %s", entry["slug"], e)


def _check_pending_podcast():
    """Trigger podcast generation for published articles."""
    from publish_manifest import get_next_pending, update_manifest

    # EN podcast
    # EN podcast — dispatch if published and not yet started
    entry = get_next_pending("podcast_en")  # finds status="published"
    if entry and entry.get("auto_podcast"):
        final = Path(entry["final_md"])
        slug = entry["slug"]
        bg_name = f"podcast-en-{slug}"
        if final.exists():
            # _dispatch_background deduplicates by name (PID file check)
            log.info("Triggering EN podcast for '%s'", entry["title"])
            _dispatch_background(bg_name, [
                sys.executable, str(_AGENTS_DIR / "podcast" / "handler.py"),
                "--run", "conversation",
                "--title", entry["title"],
                "--file", str(final),
                "--lang", "en",
                "--slug", slug,
            ])
            # Don't update manifest here — podcast CLI updates it on completion
        else:
            update_manifest(slug, error=f"Podcast: final_md not found: {final}")

    # ZH podcast — dispatch if EN done
    entry_zh = get_next_pending("podcast_zh")  # finds status="podcast_en"
    if entry_zh and entry_zh.get("auto_podcast"):
        final = Path(entry_zh["final_md"])
        slug = entry_zh["slug"]
        bg_name = f"podcast-zh-{slug}"
        if final.exists():
            log.info("Triggering ZH podcast for '%s'", entry_zh["title"])
            _dispatch_background(bg_name, [
                sys.executable, str(_AGENTS_DIR / "podcast" / "handler.py"),
                "--run", "conversation",
                "--title", entry_zh["title"],
                "--file", str(final),
                "--lang", "zh",
                "--slug", slug,
            ])
            # Don't update manifest here — podcast CLI updates it on completion
        else:
            update_manifest(slug, error=f"Podcast: final_md not found: {final}")

    # Check if both podcasts done → mark complete
    from publish_manifest import load_manifest
    manifest = load_manifest()
    for entry in manifest.get("articles", {}).values():
        if entry.get("status") == "podcast_zh":
            # Both podcasts done, advance to complete
            update_manifest(entry["slug"], status="complete")
            log.info("Pipeline complete for '%s'", entry.get("title", entry["slug"]))


def _sweep_publish_pipeline():
    """Check for articles stuck in the pipeline and log warnings.

    If an entry has exhausted MAX_RETRIES, notify the user via bridge.
    """
    from publish_manifest import get_stuck_articles, MAX_RETRIES

    stuck = get_stuck_articles(timeout_minutes=120)
    for entry in stuck:
        log.warning("PIPELINE STUCK: '%s' at status '%s' for >2h",
                    entry.get("title", entry["slug"]), entry.get("status"))

        if entry.get("retry_count", 0) >= MAX_RETRIES:
            log.error("Pipeline STUCK after %d retries: '%s' at '%s'",
                      entry.get("retry_count", 0), entry.get("slug"), entry.get("status"))
            try:
                from datetime import datetime, timezone
                m = Mira()
                m.create_item(
                    item_id=f"stuck_{entry['slug']}",
                    title=f"Pipeline stuck: {entry.get('title', entry['slug'])}",
                    messages=[{
                        "id": f"stuck_{entry['slug']}_alert",
                        "sender": "system",
                        "content": (
                            f"Article '{entry.get('title')}' stuck at {entry['status']} "
                            f"after {entry.get('retry_count', 0)} retries. "
                            f"Last error: {entry.get('error', 'unknown')}. "
                            f"Manual intervention needed."
                        ),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }],
                )
            except Exception as e:
                log.warning("Failed to notify about stuck pipeline: %s", e)


def _run_canonical_writing_pipeline() -> int:
    """Advance canonical writing_workflow projects that are ready to move."""
    advanced = 0
    responses = check_writing_responses()
    for resp in responses:
        phase = resp["project"].get("phase", "")
        title = resp["project"].get("title", "")
        if phase == "plan_ready":
            log.info("Auto-advancing canonical writing project: %s", title)
            advance_project(resp["workspace"])
            advanced += 1
        elif phase == "draft_ready":
            log.info("Writing project awaiting user feedback: %s", title)
    return advanced


def _sweep_stuck_items(bridge, task_mgr):
    """Find items stuck in 'working' with no active task and mark them failed.
    Also auto-dismiss alert-type items (informational, not actionable).
    """
    from datetime import datetime, timezone
    STUCK_THRESHOLD = 1800  # 30 minutes
    for path in bridge.items_dir.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Auto-dismiss queued alerts — they're notifications, not tasks
        if item.get("type") == "alert" and item.get("status") == "queued":
            item_id = item.get("id", path.stem)
            log.info("Auto-dismissing alert: %s", item_id)
            bridge.update_status(item_id, "completed")
            continue
        if item.get("status") != "working":
            continue
        updated = item.get("updated_at", "")
        if not updated:
            continue
        try:
            ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
        except (ValueError, TypeError):
            continue
        if age < STUCK_THRESHOLD:
            continue
        item_id = item.get("id", path.stem)
        if task_mgr.is_dispatched(item_id):
            continue
        log.warning("STATE %s: working -> failed (stuck %ds, no active task)", item_id, int(age))
        bridge.update_status(item_id, "failed",
                             error={"code": "stuck",
                                    "message": "Task lost — please retry",
                                    "retryable": True})


def _format_elapsed(seconds: float) -> str:
    """Format seconds as human-readable elapsed time (Chinese)."""
    if seconds < 60:
        return f"{int(seconds)}秒"
    elif seconds < 3600:
        return f"{int(seconds / 60)}分钟"
    else:
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        return f"{h}小时{m}分钟" if m else f"{h}小时"


def _format_status(task_mgr) -> str:
    """Format agent status as a detailed human-readable string."""
    status = task_mgr.get_status_summary()
    now = datetime.now(timezone.utc)

    if status["busy"]:
        lines = [f"Agent 状态: 忙碌 ({status['active_count']} 个任务运行中)"]
        for t in status["active_tasks"]:
            started = datetime.fromisoformat(t["started_at"].replace("Z", "+00:00"))
            elapsed = (now - started).total_seconds()
            lines.append(f"  - {t['preview'][:40]} (已运行 {_format_elapsed(elapsed)})")
    else:
        lines = ["Agent 状态: 空闲"]
        if status["last_completed"]:
            last = datetime.fromisoformat(status["last_completed"].replace("Z", "+00:00"))
            ago = (now - last).total_seconds()
            lines.append(f"上次完成: {_format_elapsed(ago)}前")

    return "\n".join(lines)


def _status_footer(task_mgr) -> str:
    """Compact status line appended to every reply."""
    status = task_mgr.get_status_summary()
    if status["busy"]:
        return f"\n\n---\nAgent: 忙碌 ({status['active_count']}个任务)"
    return "\n\n---\nAgent: 空闲"


def _is_meta_command(content: str) -> bool:
    """Check if a message is a meta-command (not a regular task)."""
    c = content.strip().lower()
    return (c.startswith("/archive ")
            or c in ("/status", "status", "状态")
            or c.startswith("/status"))


def _handle_meta_command(bridge: Mira, msg, msg_path, task_mgr=None):
    """Handle meta-commands like /archive and /status."""
    content = msg.content.strip()
    content_lower = content.lower()

    if content_lower in ("/status", "status", "状态") or content_lower.startswith("/status"):
        # Inline status reply — no background task needed
        if task_mgr:
            status_text = _format_status(task_mgr)
        else:
            status_text = "Agent 状态: 未知 (task_mgr unavailable)"
        bridge.reply(msg.id, msg.sender, status_text, thread_id=msg.thread_id)
        bridge.ack(msg.id, "done")
    elif content.startswith("/archive "):
        thread_id = content.split(" ", 1)[1].strip()
        bridge.archive_thread(thread_id)
        bridge.reply(msg.id, msg.sender, f"Thread {thread_id} 已归档。",
                    thread_id=msg.thread_id)
        bridge.ack(msg.id, "done")
    else:
        bridge.reply(msg.id, msg.sender, f"未知命令: {content[:50]}",
                    thread_id=msg.thread_id)
        bridge.ack(msg.id, "error")

    bridge.mark_processed(msg_path)


# ---------------------------------------------------------------------------
# RESPOND mode — handle user requests from Apple Notes
# ---------------------------------------------------------------------------

def _is_writing_request(body: str) -> bool:
    """Detect if a request is a writing task (use multiple models for variety)."""
    writing_keywords = [
        "写", "write", "draft", "essay", "blog", "文章", "故事", "story",
        "小说", "散文", "随笔", "翻译", "translate", "rewrite", "改写",
    ]
    lower = body.lower()
    return any(kw in lower for kw in writing_keywords)


# Writing resources (outlines, ideas) and output
_WRITINGS_ROOT = WRITINGS_DIR
_WRITINGS_OUTPUT = WRITINGS_OUTPUT_DIR


def _find_outline(title: str, body: str) -> tuple[str, str] | None:
    """Smart outline detection — figure out which 大纲.md the user wants.

    Strategies (tried in order):
    1. Explicit path:  大纲: projects/等候/大纲.md  or  大纲: /full/path.md
    2. Explicit name:  大纲: 理埠
    3. Title matches a project name that has 大纲.md
    4. Body mentions 大纲/outline + body/title contains a project name
    5. Only one project has 大纲.md → use it

    Returns (resolved_path, writing_type) or None.
    """
    projects_dir = _WRITINGS_ROOT / "projects"

    # --- Detect writing type from body ---
    writing_type = "novel"
    type_map = {"小说": "novel", "散文": "essay", "随笔": "essay",
                "博客": "blog", "技术": "technical", "诗歌": "poetry"}
    for line in body.split("\n"):
        m = re.match(r"^\s*(?:类型|type)[:\uff1a]\s*(\w+)", line, re.IGNORECASE)
        if m:
            from config import WRITING_CRITERIA
            val = m.group(1).strip().lower()
            writing_type = type_map.get(val, val)
            if writing_type not in WRITING_CRITERIA:
                writing_type = "novel"
            break
    # Also detect type from body keywords (e.g. "写小说" → novel)
    if writing_type == "novel":
        combined = title + " " + body
        for cn, en in type_map.items():
            if cn in combined:
                writing_type = en
                break

    # --- Strategy 1 & 2: explicit 大纲: line ---
    explicit_ref = None
    for line in body.split("\n"):
        m = re.match(r"^\s*(?:大纲|plan|outline)[:\uff1a]\s*(.+)", line, re.IGNORECASE)
        if m:
            explicit_ref = m.group(1).strip()
            break

    if explicit_ref:
        p = Path(explicit_ref).expanduser()
        if not p.is_absolute():
            p = _WRITINGS_ROOT / p
        if p.exists():
            return str(p), writing_type
        # Try as project name
        if "/" not in explicit_ref and "\\" not in explicit_ref:
            candidate = projects_dir / explicit_ref / "大纲.md"
            if candidate.exists():
                return str(candidate), writing_type
            for d in projects_dir.iterdir():
                if d.is_dir() and explicit_ref in d.name:
                    c = d / "大纲.md"
                    if c.exists():
                        return str(c), writing_type

    # --- Strategy 3: title matches a project folder name ---
    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if d.is_dir() and (d.name == title or title in d.name or d.name in title):
                c = d / "大纲.md"
                if c.exists():
                    log.info("Matched outline by title '%s' → %s", title, c)
                    return str(c), writing_type

    # --- Strategy 4: body mentions 大纲 + contains a project name ---
    mentions_outline = any(kw in (title + body) for kw in ["大纲", "outline", "提纲"])
    if mentions_outline and projects_dir.exists():
        combined = title + " " + body
        for d in projects_dir.iterdir():
            if d.is_dir() and d.name in combined:
                c = d / "大纲.md"
                if c.exists():
                    log.info("Matched outline by mention '%s' → %s", d.name, c)
                    return str(c), writing_type

    # --- Strategy 5: body mentions 大纲 + only one project has one → use it ---
    if mentions_outline and projects_dir.exists():
        outlines = []
        for d in projects_dir.iterdir():
            c = d / "大纲.md"
            if d.is_dir() and c.exists():
                outlines.append(c)
        if len(outlines) == 1:
            log.info("Only one outline found → %s", outlines[0])
            return str(outlines[0]), writing_type
        elif len(outlines) > 1:
            # Multiple outlines — list them in a reply note so user can clarify
            names = [o.parent.name for o in outlines]
            log.info("Multiple outlines found: %s — asking user to clarify", names)
            log.info("Multiple outlines found — cannot auto-select")
            return None

    return None


def _has_pending_health_exports() -> bool:
    """Return True if any user has an Apple Health export waiting to ingest."""
    users_dir = MIRA_DIR / "users"
    if not users_dir.exists():
        return False

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        export_file = user_dir / "health" / "apple_health_export.json"
        if export_file.exists():
            return True
    return False




def _run_health_check():
    """Daily health pipeline: fetch data → DB → summary → insight → bridge.

    Runs once each morning (7-9 AM). DB is source of truth; bridge items
    are a write-through cache for iOS display using stable IDs (one file
    per type per user, overwritten daily).
    """
    sys.path.insert(0, str(_AGENTS_DIR / "health"))
    from health_store import HealthStore
    from ingest import ingest_all_users
    from monitor import check_all_users, format_alerts
    from summary import write_summary_to_bridge
    from report import generate_daily_insight
    from config import DATABASE_URL, SECRETS_FILE, HEALTH_REPORT_MODEL

    store = HealthStore(DATABASE_URL)
    bridge_path = Path(MIRA_DIR)
    today = datetime.now().date()

    # --- 1. Ingest: Oura API + Apple Health exports → DB ---

    try:
        import yaml
        secrets = yaml.safe_load(SECRETS_FILE.read_text(encoding="utf-8")) or {}
        oura_cfg = secrets.get("api_keys", {}).get("oura", {})
        oura_users = {"ang": oura_cfg} if isinstance(oura_cfg, str) else \
                     oura_cfg if isinstance(oura_cfg, dict) else {}
        from oura import fetch_and_store as oura_fetch
        for uid, token in oura_users.items():
            try:
                count = oura_fetch(store, token, uid, days_back=1)
                log.info("Oura: fetched %d metrics for %s", count, uid)
            except Exception as e:
                log.warning("Oura fetch failed for %s: %s", uid, e)
    except Exception as e:
        log.warning("Oura setup failed: %s", e)

    ingested = ingest_all_users(bridge_path, store)
    if ingested:
        log.info("Health: ingested %d metrics from Apple Health", ingested)

    # --- 2. Discover users ---

    users_dir = bridge_path / "users"
    user_ids = sorted(
        d.name for d in users_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ) if users_dir.exists() else ["ang"]

    # --- 3. Refresh health_summary.json for each user (iOS dashboard) ---

    for uid in user_ids:
        try:
            write_summary_to_bridge(store, bridge_path, uid)
        except Exception as e:
            log.warning("Health summary for %s failed: %s", uid, e)

    # --- 4. Anomaly detection → DB + bridge ---

    all_bridges = Mira.for_all_users()
    bridges_by_user = {b.user_id: b for b in all_bridges}

    alerts_by_user = check_all_users(store, user_ids)
    for uid, alerts in (alerts_by_user or {}).items():
        bridge = bridges_by_user.get(uid)
        if not bridge:
            continue
        message = format_alerts(uid, alerts)
        store.upsert_insight(uid, today, "alert", message)
        _write_health_feed(bridge, f"health_alert_{uid}", "健康提醒",
                           message, ["health", "alert"])
        log.info("Health alert sent to %s: %d alerts", uid, len(alerts))

    if not alerts_by_user:
        log.info("Health check: all clear for all users")

    # --- 5. Daily GPT insight → DB + bridge ---

    for uid in user_ids:
        bridge = bridges_by_user.get(uid)
        if not bridge:
            continue
        # Skip if already generated today (check DB, not bridge)
        existing = store.get_latest_insight(uid, "daily")
        if existing and existing["insight_date"] == today:
            log.info("Health insight for %s already exists today, skipping", uid)
            continue
        try:
            insight = generate_daily_insight(store, uid, model=HEALTH_REPORT_MODEL)
            if not insight:
                continue
            store.upsert_insight(uid, today, "daily", insight, model=HEALTH_REPORT_MODEL)
            _write_health_feed(bridge, f"health_insight_{uid}", "今日健康洞察",
                               insight, ["health", "insight"])
            log.info("Daily health insight sent to %s", uid)
        except Exception as e:
            log.warning("Daily health insight for %s failed: %s", uid, e)

    store.close()


def _write_health_feed(bridge, item_id: str, title: str, content: str,
                       tags: list[str]):
    """Write a health feed item to bridge, overwriting any previous version.

    Uses a stable item_id so there's always exactly one file per type per user.
    Directly uses bridge._write_item + _update_manifest for atomic consistency.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    item = {
        "id": item_id,
        "type": "feed",
        "title": title,
        "status": "done",
        "tags": tags,
        "origin": "agent",
        "pinned": True,
        "quick": False,
        "parent_id": "",
        "created_at": now,
        "updated_at": now,
        "messages": [{
            "id": f"{abs(hash(now + item_id)) % 0xFFFFFFFF:08x}",
            "sender": "health_agent",
            "content": content,
            "timestamp": now,
            "kind": "text",
        }],
        "error": None,
        "result_path": None,
    }
    bridge._write_item(item)
    bridge._update_manifest(item)


def _run_health_weekly_report():
    """Generate weekly health reports → DB + bridge (stable ID)."""
    sys.path.insert(0, str(_AGENTS_DIR / "health"))
    from health_store import HealthStore
    from report import generate_weekly_report
    from config import DATABASE_URL

    store = HealthStore(DATABASE_URL)
    all_bridges = Mira.for_all_users()
    bridges_by_user = {b.user_id: b for b in all_bridges}
    today = datetime.now().date()

    users_dir = Path(MIRA_DIR) / "users"
    user_ids = sorted(
        d.name for d in users_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ) if users_dir.exists() else ["ang"]

    for uid in user_ids:
        report = generate_weekly_report(store, uid)
        if "暂无健康数据" in report:
            continue

        # Store in DB
        store.upsert_insight(uid, today, "weekly", report)

        # Write to iCloud Artifacts
        today_str = today.isoformat()
        artifacts_base = Path(ARTIFACTS_DIR).parent
        health_dir = artifacts_base / uid / "health"
        health_dir.mkdir(parents=True, exist_ok=True)
        (health_dir / f"weekly_{today_str}.md").write_text(report, encoding="utf-8")
        log.info("Health weekly report written for %s", uid)

        # Write to bridge (stable ID — overwrites previous week)
        bridge = bridges_by_user.get(uid)
        if bridge:
            _write_health_feed(bridge, f"health_weekly_{uid}", f"健康周报",
                               report, ["health", "report"])

    store.close()





# ---------------------------------------------------------------------------
# Schedule logic
# ---------------------------------------------------------------------------

def _run_inline_scheduled_job(job, payload):
    """Execute an inline scheduled job immediately in-process."""
    if job.inline_runner == "health-check":
        _run_health_check()
        return
    if job.inline_runner == "log-cleanup":
        log_cleanup()
        return
    raise ValueError(f"No inline runner registered for job '{job.inline_runner or job.name}'")


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
                core_path=str(Path(__file__).resolve()),
                user_id=target_user_id,
            )
            dispatched = _dispatch_background(bg_name, cmd)
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


# ---------------------------------------------------------------------------
# PODCAST mode — generate conversation episode for published articles
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Substack growth cycle — likes, comments, engagement
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Self-repair: detect and retry failed daily tasks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Task contracts: every scheduled task declares what "done" means
# ---------------------------------------------------------------------------
# Each entry: state_key_prefix -> {
#   "dispatch": (bg_name, [cmd_args]),
#   "window": (earliest_hour, latest_hour),  # when to schedule + retry
#   "verify": callable(state, today) -> bool,  # did it actually succeed?
# }
# The verify function checks for real output, not just a state flag.
# A task that set its state flag but produced no output is NOT done.

def _verify_state_key(prefix):
    """Simple verifier: state key exists for today."""
    def check(state, today):
        return bool(state.get(f"{prefix}_{today}"))
    return check

def _verify_analyst(slot):
    """Analyst verifier: state key + briefing file exists."""
    def check(state, today):
        key = f"analyst_{today}_{slot}"
        if not state.get(key):
            return False
        briefing = ARTIFACTS_DIR / "briefings" / f"{today}_analyst_{'pre_market' if slot == '0700' else 'post_market'}.md"
        return briefing.exists()
    return check

def _verify_journal(state, today):
    """Journal verifier: journal file exists in soul/journal/."""
    if not state.get(f"journal_{today}"):
        return False
    journal_dir = MIRA_ROOT / "agents" / "shared" / "soul" / "journal"
    return any(journal_dir.glob(f"{today}*.md"))

def _verify_reflect(state, today):
    """Weekly reflect: just check state key (output goes to worldview/interests)."""
    return bool(state.get("last_reflect") and
                state["last_reflect"][:10] >= today)

def _verify_self_evolve(state, today):
    """Self-evolve verifier: state key set + at least one proposal file exists."""
    if not state.get(f"self_evolve_{today}"):
        return False
    proposals_dir = Path(__file__).resolve().parent / "proposals"
    return any(proposals_dir.glob(f"{today}_*.json"))


_DAILY_TASK_CONTRACTS = {
    "zhesi": {
        "dispatch": ("zhesi", ["zhesi"]),
        "window": (9, 22),
        "verify": _verify_state_key("zhesi"),
        "label": "每日哲思",
    },
    "soul_question": {
        "dispatch": ("soul-question", ["soul-question"]),
        "window": (10, 22),
        "verify": _verify_state_key("soul_question"),
        "label": "灵魂提问",
    },
    "daily_photo": {
        "dispatch": ("daily-photo", ["daily-photo"]),
        "window": (7, 20),
        "verify": _verify_state_key("daily_photo"),
        "label": "每日修图",
    },
    "journal": {
        "dispatch": ("journal", ["journal"]),
        "window": (21, 23),
        "verify": _verify_journal,
        "label": "日记",
    },
    "analyst_pre": {
        "dispatch": ("analyst-0700", ["analyst", "--slot", "0700"]),
        "window": (7, 12),
        "verify": _verify_analyst("0700"),
        "label": "盘前分析",
    },
    "analyst_post": {
        "dispatch": ("analyst-1800", ["analyst", "--slot", "1800"]),
        "window": (18, 22),
        "verify": _verify_analyst("1800"),
        "label": "盘后分析",
    },
    "self_evolve": {
        "dispatch": ("self-evolve", ["self-evolve"]),
        "window": (13, 16),
        "verify": _verify_self_evolve,
        "label": "自我进化",
    },
}


def _self_repair_daily_tasks():
    """Check all task contracts. Retry any that failed verification."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    state = load_state()

    for task_id, contract in _DAILY_TASK_CONTRACTS.items():
        earliest, latest = contract["window"]
        if hour < earliest or hour > latest:
            continue

        # Run the verify function — checks real output, not just flags
        if contract["verify"](state, today):
            continue  # genuinely done

        bg_name, cmd_args = contract["dispatch"]

        # Skip if currently running
        if _is_bg_running(bg_name):
            continue

        # 30-minute cooldown between retries
        retry_key = f"_retry_{task_id}_{today}"
        last_retry = state.get(retry_key, "")
        if last_retry:
            try:
                if (now - datetime.fromisoformat(last_retry)).total_seconds() < 1800:
                    continue
            except ValueError:
                pass

        log.warning("Self-repair: %s (%s) not verified, retrying",
                    task_id, contract["label"])
        state[retry_key] = now.isoformat()
        save_state(state)
        _dispatch_background(bg_name, [
            sys.executable, str(Path(__file__).resolve()), *cmd_args,
        ])


def _daily_task_status_report():
    """At 23:05, send a feed item with verified task completion status."""
    now = datetime.now()
    if now.hour != 23 or now.minute < 5:
        return
    today = now.strftime("%Y-%m-%d")
    today_compact = today.replace("-", "")
    state = load_state()

    report_key = f"task_status_report_{today}"
    if state.get(report_key):
        return

    lines = []
    all_ok = True
    for task_id, contract in _DAILY_TASK_CONTRACTS.items():
        verified = contract["verify"](state, today)
        status = "done" if verified else "MISSED"
        if not verified:
            all_ok = False
        # Look up actor provenance from state (try common key patterns)
        actor_key = f"{task_id}_{today}_actor"
        actor = state.get(actor_key, "")
        actor_suffix = f" [actor: {actor}]" if actor else ""
        lines.append(f"- {contract['label']} ({task_id}): {status}{actor_suffix}")

    if all_ok:
        summary = "今日任务全部完成（已验证产出）。\n\n" + "\n".join(lines)
    else:
        summary = "有任务未完成或产出验证失败：\n\n" + "\n".join(lines)

    try:
        bridge = Mira(MIRA_DIR)
        bridge.create_item(
            item_id=f"task_report_{today_compact}",
            item_type="feed",
            title=f"Daily Status: {today}",
            first_message=summary,
            sender="agent",
            tags=["status", "daily"],
            origin="agent",
        )
    except Exception as e:
        log.warning("Failed to create task status report: %s", e)

    state[report_key] = now.isoformat()
    save_state(state)
    log.info("Daily task status report sent")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def cmd_run():
    """Full cycle: talk → respond → dispatch background work.

    The super agent MUST stay fast (<10s). All long-running work
    (writing pipeline, explore, reflect) runs in background processes
    so heartbeat and Mira polling stay responsive.
    """
    import time as _time
    _cycle_start = _time.monotonic()
    log.info("=== Mira Agent wake ===")

    # Load session context from previous cycles
    _session_ctx = load_session_context()
    _session_new = []  # entries from this cycle
    _phase_times: dict[str, int] = {}
    _model_wait_ms = 0  # blocking model calls in this cycle (bg dispatches don't block)

    # Safety net: ensure today's journal/zhesi are visible to iOS
    _t0 = _time.monotonic()
    try:
        _sync_journals_to_briefings()
    except Exception as e:
        log.error("Journal sync check failed: %s", e)
    _phase_times["sync_journals"] = round((_time.monotonic() - _t0) * 1000)

    # Mira first (lightweight, fast) — CRITICAL PATH
    _t0 = _time.monotonic()
    try:
        do_talk()
    except Exception as e:
        log.error("Mira failed: %s", e)
    _phase_times["talk"] = round((_time.monotonic() - _t0) * 1000)

    if should_shutdown():
        log.info("Shutdown requested — exiting after talk phase")
        return

    # Timing guard: skip non-critical checks if cycle already > 8s
    _elapsed = _time.monotonic() - _cycle_start
    if _elapsed < 8:
        # Auto-advance writing projects stuck in plan_ready (no more Notes approval)
        _t0 = _time.monotonic()
        try:
            _run_canonical_writing_pipeline()
        except Exception as e:
            log.error("Writing response check failed: %s", e)
        _phase_times["writing_responses"] = round((_time.monotonic() - _t0) * 1000)

        # Sync Mira's own status + read all app feeds
        _t0 = _time.monotonic()
        try:
            from app_feeds import read_app_feeds, sync_mira_status
            sync_mira_status()
            feeds = read_app_feeds()
            if feeds:
                log.info("App feeds: %s", ", ".join(f["app"] for f in feeds))
        except Exception as e:
            log.warning("App feed sync/read failed: %s", e)
        _phase_times["app_feeds"] = round((_time.monotonic() - _t0) * 1000)
    else:
        log.info("Cycle > 8s (%.1fs), deferring non-critical checks", _elapsed)

    # --- Harvest background process outcomes & check health ---
    _t0 = _time.monotonic()
    try:
        health_monitor.harvest_all()
        health_monitor.check_anomalies()
    except Exception as e:
        log.error("Health monitor failed: %s", e)
    _phase_times["health"] = round((_time.monotonic() - _t0) * 1000)

    # Reap stale PID files (hourly) — prevents stuck tasks
    _t0 = _time.monotonic()
    _reap_stale_pids()
    _phase_times["reap_pids"] = round((_time.monotonic() - _t0) * 1000)

    # --- Publishing pipeline: publish → podcast → sweep ---
    _t0 = _time.monotonic()
    _check_pending_publish()
    _check_pending_podcast()
    _sweep_publish_pipeline()
    _phase_times["pending_publish"] = round((_time.monotonic() - _t0) * 1000)

    # --- All heavy work below runs through the declarative scheduler ---
    _t0 = _time.monotonic()
    _dispatch_scheduled_jobs(_session_new)

    # Podcast — DISABLED (voice quality check, re-enable after intro-mira regen)
    # _any_audio_running = any(
    #     _is_bg_running(f.stem)
    #     for f in _BG_PID_DIR.glob("podcast-*.pid")
    # )
    # if not _any_audio_running:
    #     podcast_pick = should_podcast()
    #     if podcast_pick:
    #         lang, slug, title = podcast_pick
    #         _dispatch_background(f"podcast-{lang}-{slug}", [
    #             sys.executable, str(Path(__file__).resolve()), "podcast",
    #             "--lang", lang, "--slug", slug, "--title", title,
    #         ])

    # Weekly health report — Monday morning
    if _should_health_weekly_report():
        try:
            _run_health_weekly_report()
        except Exception as e:
            log.error("Health weekly report failed: %s", e)
    _phase_times["dispatch"] = round((_time.monotonic() - _t0) * 1000)

    # -----------------------------------------------------------------------
    # Self-repair: retry critical daily tasks that failed or never completed
    # -----------------------------------------------------------------------
    _t0 = _time.monotonic()
    _self_repair_daily_tasks()
    _daily_task_status_report()
    _phase_times["self_repair"] = round((_time.monotonic() - _t0) * 1000)

    _t0 = _time.monotonic()
    _refresh_operator_dashboards()
    _phase_times["operator_dashboard"] = round((_time.monotonic() - _t0) * 1000)

    # Save session context for next cycle
    if _session_new:
        save_session_context(_session_ctx + _session_new)

    _cycle_ms = round((_time.monotonic() - _cycle_start) * 1000)
    _orch_ms = sum(_phase_times.values())
    log.info("TIMING cycle=%ds orchestration=%dms model_wait=%dms phases=%s",
             round(_cycle_ms / 1000), _orch_ms, _model_wait_ms, json.dumps(_phase_times))
    try:
        from config import TIMING_LOG
        with open(TIMING_LOG, "a", encoding="utf-8") as _tf:
            _tf.write(json.dumps({
                "ts": datetime.now().isoformat(),
                "cycle_ms": _cycle_ms,
                "orchestration_ms": _orch_ms,
                "model_wait_ms": _model_wait_ms,
                "phases": _phase_times,
            }) + "\n")
    except Exception as _te:
        log.debug("Timing log write failed: %s", _te)

    log.info("=== Mira Agent sleep ===")


def _refresh_operator_dashboards():
    """Persist operator dashboard snapshots for each configured user."""
    try:
        from operator_dashboard import write_operator_summary
    except Exception as exc:
        log.warning("Operator dashboard unavailable: %s", exc)
        return

    for user_id in get_known_user_ids():
        try:
            write_operator_summary(user_id=user_id)
        except Exception as exc:
            log.warning("Operator dashboard refresh failed for %s: %s", user_id, exc)











# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    # Set up logging
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log",
                encoding="utf-8",
            ),
        ],
    )

    # Prune old log files (keep 14 days)
    _prune_old_logs(LOGS_DIR)

    # Validate configuration — log errors but don't crash
    if not validate_config():
        log.warning("Config validation failed — some features may not work")

    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    # Set usage agent context for token tracking
    from sub_agent import set_usage_agent
    set_usage_agent(command if command != "run" else "super")

    # Parse optional flags
    args = sys.argv[2:]
    flags = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flags[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1

    if command == "run":
        cmd_run()
    elif command == "talk":
        do_talk()
    elif command == "explore":
        sources = flags.get("sources", "").split(",") if flags.get("sources") else None
        slot = flags.get("slot", "")
        do_explore(source_names=sources, slot_name=slot)
    elif command == "reflect":
        do_reflect(user_id=flags.get("user", "ang"))
    elif command == "journal":
        do_journal(user_id=flags.get("user", "ang"))
    elif command == "analyst":
        do_analyst(slot=flags.get("slot", ""))
    elif command == "research":
        do_research()
    elif command == "zhesi":
        do_zhesi(user_id=flags.get("user", "ang"))
    elif command == "soul-question":
        do_soul_question(user_id=flags.get("user", "ang"))
    elif command == "autowrite-check":
        do_autowrite_check()
    elif command == "autowrite-run":
        task_id = flags.get("task-id", f"autowrite_{datetime.now().strftime('%Y-%m-%d')}")
        title = flags.get("title", "Untitled")
        writing_type = flags.get("type", "essay")
        idea = flags.get("idea", "")
        run_autowrite_pipeline(task_id, title, writing_type, idea)
    elif command == "writing-pipeline":
        advanced = _run_canonical_writing_pipeline()
        log.info("Canonical writing pipeline advanced %d project(s)", advanced)
    elif command == "check-comments":
        do_check_comments()
    elif command == "growth-cycle":
        do_growth_cycle()
    elif command == "notes-cycle":
        do_notes_cycle()
    elif command == "spark-check":
        do_spark_check(user_id=flags.get("user", "ang"))
    elif command == "idle-think":
        do_idle_think(user_id=flags.get("user", "ang"))
    elif command == "daily-report":
        do_daily_report()
    elif command == "assess":
        do_assess()
    elif command == "self-improve":
        _run_self_improve()
    elif command == "self-evolve":
        from self_evolve import run_evolve
        run_evolve(dry_run="--dry-run" in sys.argv)
    elif command == "backlog-executor":
        from backlog_executor import run_once
        run_once(dry_run="--dry-run" in sys.argv)
    elif command == "restore-dry-run":
        from restore_drill import run_latest_restore_dry_run

        report = run_latest_restore_dry_run()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report.get("ok"):
            sys.exit(1)
    elif command == "podcast":
        lang  = flags.get("lang", "zh")
        slug  = flags.get("slug", "")
        title = flags.get("title", slug.replace("-", " ").title())
        run_podcast_episode(lang, slug, title)
    elif command == "book-review":
        do_book_review()
    elif command == "daily-photo":
        do_daily_photo()
    elif command == "skill-study":
        group_idx = int(flags.get("group", "0"))
        do_skill_study(group_idx=group_idx, user_id=flags.get("user", "ang"))
    elif command == "write-check":
        # List active writing projects
        responses = check_writing_responses()
        if responses:
            for r in responses:
                print(f"Active: {r['project']['title']} ({r['project']['phase']})")
        else:
            print("No active writing projects")
    elif command == "write-from-plan":
        if len(sys.argv) < 3:
            print("Usage: core.py write-from-plan <path-to-大纲.md> [--title 标题] [--type novel|essay|blog|technical|poetry]")
            sys.exit(1)
        plan_path = sys.argv[2]
        title = flags.get("title", "")
        writing_type = flags.get("type", "novel")
        start_from_plan(title, plan_path, writing_type)
    else:
        print(f"Usage: {sys.argv[0]} [run|talk|respond|explore|reflect|journal|analyst|zhesi|skill-study|autowrite-check|autowrite-run|writing-pipeline|write-check|write-from-plan|spark-check]")
        sys.exit(1)


def _send_crash_notification(error: str):
    """Send crash notification to default user's items/. Minimal deps."""
    try:
        import json, uuid
        from pathlib import Path
        from datetime import datetime, timezone as tz
        from config import MIRA_DIR
        items_dir = MIRA_DIR / "users" / "ang" / "items"
        items_dir.mkdir(parents=True, exist_ok=True)
        msg_id = uuid.uuid4().hex[:8]
        iso = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        short_err = error[:500] if len(error) > 500 else error
        item = {
            "id": f"req_crash_{msg_id}",
            "type": "request",
            "title": "Agent Crash",
            "status": "failed",
            "tags": ["system", "crash"],
            "origin": "agent",
            "pinned": False,
            "quick": False,
            "parent_id": None,
            "created_at": iso,
            "updated_at": iso,
            "messages": [{"id": msg_id, "sender": "agent", "content": f"Mira crashed.\n\n{short_err}", "timestamp": iso, "kind": "error"}],
            "error": {"code": "crash", "message": short_err, "retryable": False, "timestamp": iso},
            "result_path": None,
        }
        path = items_dir / f"req_crash_{msg_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.rename(path)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # Let sys.exit() propagate normally
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        # Log to file even if logging isn't set up
        try:
            crash_path = Path("/tmp/mira-crash.log")
            with open(crash_path, "a") as f:
                f.write(f"\n{'='*60}\n{datetime.now().isoformat()}\n{tb}\n")
        except Exception:
            pass
        # Try logging if available
        try:
            logging.critical("Unhandled exception in main():\n%s", tb)
        except Exception:
            pass
        # Notify user — but rate-limit to avoid notification spam
        # Only send if no crash notification in the last 10 minutes
        try:
            last_crash_file = Path("/tmp/mira-last-crash-notify")
            should_notify = True
            if last_crash_file.exists():
                age = time.time() - last_crash_file.stat().st_mtime
                should_notify = age > 600  # 10 minutes
            if should_notify:
                _send_crash_notification(str(exc))
                last_crash_file.write_text(str(exc))
        except Exception:
            pass
        sys.exit(1)
