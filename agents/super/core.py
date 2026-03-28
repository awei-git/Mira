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
    EXPLORE_SOURCE_GROUPS, EXPLORE_COOLDOWN_MINUTES,
    EXPLORE_ACTIVE_START, EXPLORE_ACTIVE_END, EXPLORE_MAX_PER_DAY,
    REFLECT_DAY, REFLECT_TIME,
    MAX_BRIEFING_ITEMS, MAX_DEEP_DIVES, MIRA_DIR, ARTIFACTS_DIR, CLEANUP_DAYS,
    JOURNAL_DIR, JOURNAL_TIME, SKILLS_INDEX, WRITINGS_OUTPUT_DIR, WRITINGS_DIR,
    ANALYST_TIMES, ANALYST_BUSINESS_DAYS_ONLY, ZHESI_TIME, ZA_FILE,
    RESEARCH_TIME, RESEARCH_TOPIC, SOUL_QUESTION_TIME,
    SKILL_STUDY_SOURCE_GROUPS, SKILL_STUDY_COOLDOWN_HOURS, SKILL_STUDY_TIME,
    EPISODES_DIR, validate_config,
)
from mira import Mira, Message
from task_manager import TaskManager, TASKS_DIR
from soul_manager import (
    load_soul, format_soul, append_memory, update_memory, update_interests,
    update_worldview, save_skill, save_reading_note, load_recent_reading_notes,
    detect_recurring_themes, catalog_list,
    _atomic_write as atomic_write,
)
from fetcher import fetch_all
from sub_agent import claude_think, claude_act, model_think
from writing_workflow import (
    start_project, check_writing_responses, advance_project, start_from_plan,
)
from prompts import (
    respond_prompt, explore_prompt, deep_dive_prompt, reflect_prompt,
    journal_prompt, internalize_prompt, autonomous_writing_prompt,
    worldview_evolution_prompt, zhesi_prompt, spark_check_prompt,
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

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict):
    lock_file = STATE_FILE.with_suffix(".lock")
    try:
        with open(lock_file, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                STATE_FILE.write_text(
                    json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except BlockingIOError:
        log.warning("State file locked by another process, skipping save")


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
    task_mgr = TaskManager()

    # Heartbeat (global, shared across users)
    bridge.heartbeat(agent_status=task_mgr.get_status_summary())

    # --- Phase A: Collect results from previously dispatched tasks ---
    completed = task_mgr.check_tasks()
    for rec in completed:
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
        elif rec.status in ("error", "timeout"):
            error_msg = f"处理失败: {rec.summary}" if rec.summary else "处理失败，请稍后重试。"
            bridge.update_status(rec.task_id, "failed",
                                 error={"code": rec.status, "message": error_msg,
                                        "retryable": True})
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

            if cmd_type == "new_request":
                task_id = cmd.get("item_id") or f"req_{uuid.uuid4().hex[:8]}"
                quick = cmd.get("quick", False)
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
                if not bridge.item_exists(disc_id):
                    bridge.create_discussion(disc_id, title, content, sender=sender, tags=tags)
                workspace = TASKS_DIR / _talk_slug(content, disc_id)
                msg = Message(id=disc_id, sender=sender, timestamp=cmd.get("timestamp",""),
                              content=content, thread_id=disc_id)
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "reply" and item_id:
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
                        req_id = f"req_{todo_id}"
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

    # Reset bridge to default user for legacy code
    bridge = bridges_by_user.get("ang", all_bridges[0])

    # --- Phase B2: Dispatch legacy inbox messages to background workers ---
    messages = bridge.poll()
    if not messages:
        log.info("Mira: no new messages (active tasks: %d)", task_mgr.get_active_count())
        return

    # External input arrived — partial-reset emptiness (external takes priority)
    try:
        from emptiness import on_external_input
        on_external_input()
    except ImportError:
        pass

    for msg, msg_path in messages:
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
                # Reuse the original workspace
                msg_workspace = Path(old_rec.workspace) if old_rec.workspace else TASKS_DIR / _talk_slug(msg.content, msg.thread_id)
                # Remove old record so dispatch() won't see it as busy
                task_mgr.reset_for_retry(msg.thread_id)
                bridge.ack(msg.id, "received")
                bridge.update_task_status(msg.thread_id, "working")
                # Use original task_id for dispatch (overwrite msg.id)
                msg.id = msg.thread_id
                task_id = task_mgr.dispatch(msg, msg_workspace)
                if task_id:
                    bridge.ack(msg.id, "processing")
                    bridge.mark_processed(msg_path)
                elif task_mgr.is_busy():
                    log.info("Mira [%s] retry queued (agent busy)", msg.id)
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
            break  # no point trying more messages
        else:
            # Actual dispatch failure
            bridge.reply(msg.id, msg.sender, "任务分发失败，请稍后重试。",
                        thread_id=msg.thread_id)
            bridge.ack(msg.id, "error")
            bridge.mark_processed(msg_path)

    # Periodic cleanup
    bridge.cleanup_old(days=CLEANUP_DAYS)
    task_mgr.cleanup_old_records(max_age_days=7)

    # Sweep stuck items — safety net for all other bugs
    _sweep_stuck_items(bridge, task_mgr)


def _check_pending_publish():
    """Auto-publish approved articles when cooldown clears."""
    state = load_state()
    pending = state.get("pending_publish")
    if not pending:
        return

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import publish_to_substack
        from pathlib import Path

        workspace = Path(pending["workspace"])
        final = workspace / pending.get("final_md", "final.md")
        if not final.exists():
            log.warning("Pending publish: final.md not found at %s", final)
            return

        content = final.read_text(encoding="utf-8")
        result = publish_to_substack(
            title=pending["title"],
            subtitle=pending.get("subtitle", ""),
            article_text=content,
            workspace=workspace,
        )

        if "发布被拦截" in result or "cooldown" in result.lower():
            return  # still in cooldown, try next cycle

        # Published successfully — clean up
        log.info("Auto-published '%s': %s", pending["title"], result[:100])
        del state["pending_publish"]
        save_state(state)

        # Update item status
        bridge = Mira()
        item_id = pending.get("item_id")
        if item_id:
            bridge.update_status(item_id, "done",
                                agent_message=f"已发布到 Substack: {result[:200]}")

        # Queue notes for the new article
        from notes import queue_notes_for_article
        post_url = ""
        for part in result.split():
            if "substack.com" in part:
                post_url = part
                break
        if post_url:
            queue_notes_for_article(pending["title"], content[:3000], post_url)

        # Auto-generate podcast if flagged
        if pending.get("auto_podcast"):
            log.info("Triggering auto-podcast for '%s'", pending["title"])
            _dispatch_background("auto-podcast", [
                sys.executable, str(Path(__file__).resolve()), "podcast",
                "--title", pending["title"],
                "--file", str(final),
            ])

    except Exception as e:
        log.warning("Pending publish check failed: %s", e)


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


# ---------------------------------------------------------------------------
# EXPLORE mode — fetch, filter, brief, deep-dive
# ---------------------------------------------------------------------------

def do_explore(source_names: list[str] | None = None, slot_name: str = ""):
    """Fetch sources, write briefing, optionally deep-dive.

    Args:
        source_names: specific sources to fetch (e.g. ["arxiv", "huggingface"]).
                      If None, fetches all sources.
        slot_name: name of the explore slot (e.g. "morning") for context.
    """
    from fetcher import fetch_sources
    log.info("Starting explore cycle (sources=%s, slot=%s)",
             source_names or "all", slot_name or "default")

    # 1. Fetch sources
    if source_names:
        items = fetch_sources(source_names)
    else:
        items = fetch_all()
    if not items:
        log.info("No items fetched, skipping explore")
        # Still update state so this group gets rotated and we don't
        # keep picking the same empty group forever
        now = datetime.now()
        state = load_state()
        state["last_explore"] = now.isoformat()
        if source_names:
            for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
                if set(source_names) == set(group):
                    recent = state.get("explore_recent_groups", [])
                    if i in recent:
                        recent.remove(i)
                    recent.append(i)
                    state["explore_recent_groups"] = recent[-len(EXPLORE_SOURCE_GROUPS):]
                    break
        save_state(state)
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items for Claude
    feed_text = _format_feed_items(items)

    # 2b. Gather recent briefing topics for dedup (wider window since explore is more frequent)
    recent_topics = _extract_recent_briefing_topics(days=5)

    # 3. Ask Claude to filter and rank
    prompt = explore_prompt(soul_ctx, feed_text, source_slot=slot_name,
                            recent_topics=recent_topics)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        log.error("Explore: Claude returned empty briefing")
        return

    # 4. Save briefing (slot-specific so multiple explores don't overwrite)
    today = datetime.now().strftime("%Y-%m-%d")
    suffix = f"_{slot_name}" if slot_name else ""
    briefing_path = BRIEFINGS_DIR / f"{today}{suffix}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}{suffix}.md").write_text(briefing, encoding="utf-8")

    # Append briefing to daily digest (single item per day, not per explore slot)
    try:
        src_label = slot_name.replace("_", " / ") if slot_name else "all"
        _append_to_daily_feed("explore", f"Explore: {src_label}", briefing,
                             source=src_label, tags=["explore", "briefing"])
        log.info("Explore briefing appended to daily digest")
    except Exception as e:
        log.warning("Failed to append explore briefing to digest: %s", e)

    # 5b. Extract key insights into structured reading notes
    try:
        _extract_briefing_insights(soul_ctx, briefing, today, slot_name)
    except Exception as e:
        log.warning("Insight extraction failed (non-fatal): %s", e)

    # 5c. Proactive message: check if new reading notes connect to existing threads
    try:
        _maybe_proactive_reading_message(soul_ctx)
    except Exception as e:
        log.debug("Proactive reading message check failed (non-fatal): %s", e)

    # 6. Check for deep-dive candidate
    dive = _extract_deep_dive(briefing)
    if dive and MAX_DEEP_DIVES > 0:
        log.info("Deep diving into: %s", dive["title"])
        _do_deep_dive(soul_ctx, dive)

    # 7. Extract comment suggestions and run growth cycle
    comment_suggestions = _extract_comment_suggestions(briefing)
    if comment_suggestions:
        log.info("Briefing has %d comment suggestions", len(comment_suggestions))
        try:
            sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
            from growth import run_growth_cycle
            run_growth_cycle(briefing_comments=comment_suggestions)
        except Exception as e:
            log.error("Growth cycle failed: %s", e)

    # --- Self-evaluation: score this explore ---
    try:
        from evaluator import evaluate_explore, record_event
        e_scores = evaluate_explore(briefing, source_names=source_names)
        if e_scores:
            record_event("explore", e_scores, {"sources": src_label if 'src_label' in dir() else ""})
    except Exception as e:
        log.warning("Explore self-evaluation failed: %s", e)

    # Harvest observations from briefing (continuous thinking)
    try:
        harvest_observations(briefing[:2000], source=f"explore-{slot_name or 'default'}")
    except Exception as e:
        log.debug("Observation harvest from explore failed: %s", e)

    # Mark this explore as done and update tracking
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    state["last_explore"] = now.isoformat()
    state[f"explore_count_{today}"] = state.get(f"explore_count_{today}", 0) + 1
    if slot_name:
        state[f"explored_{today}_{slot_name}"] = now.isoformat()
    # Track which source group was used (for LRU selection)
    if source_names:
        # Find matching group index
        for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
            if set(source_names) == set(group):
                recent = state.get("explore_recent_groups", [])
                if i in recent:
                    recent.remove(i)
                recent.append(i)
                # Keep only last N entries
                state["explore_recent_groups"] = recent[-len(EXPLORE_SOURCE_GROUPS):]
                break
    save_state(state)


def _do_deep_dive(soul_ctx: str, dive: dict):
    """Deep-dive into one item from the briefing."""
    prompt = deep_dive_prompt(
        soul_ctx, dive["title"], dive["url"], dive.get("note", "")
    )
    result = claude_act(prompt)

    if not result:
        log.error("Deep dive returned empty")
        return

    # Save analysis
    today = datetime.now().strftime("%Y-%m-%d")
    path = BRIEFINGS_DIR / f"{today}_deep_dive.md"
    path.write_text(result, encoding="utf-8")
    log.info("Deep dive saved: %s", path.name)

    # Copy to mira/artifacts for iOS browsing
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}_deep_dive.md").write_text(result, encoding="utf-8")

    # Check if a skill was extracted
    skill_match = re.search(
        r"Name:\s*(.+)\nDescription:\s*(.+)\nContent:\n(.+?)(?:\n```|$)",
        result, re.DOTALL,
    )
    if skill_match:
        name = skill_match.group(1).strip()
        desc = skill_match.group(2).strip()
        content = skill_match.group(3).strip()
        save_skill(name, desc, content)
        log.info("Learned new skill from deep dive: %s", name)

    # --- Internalization: write a personal reading reflection ---
    try:
        soul = load_soul()
        soul_ctx_full = format_soul(soul)
        intern_prompt = internalize_prompt(soul_ctx_full, dive["title"], result[:3000])
        reflection = claude_think(intern_prompt, timeout=120)
        if reflection:
            save_reading_note(dive["title"], reflection)
            log.info("Internalization note saved for: %s", dive["title"])
    except Exception as e:
        log.warning("Internalization failed: %s", e)


def _extract_briefing_insights(soul_ctx: str, briefing: str,
                                today: str, slot_name: str = ""):
    """Extract 2-3 key insights from a briefing into structured reading notes.

    Unlike deep dives (which go deep on one item), this captures the
    most interesting connections and patterns across the entire briefing.
    The notes accumulate over time and feed into reflection, journal,
    and autonomous writing topic selection.
    """
    prompt = f"""{soul_ctx[:500]}

You just wrote a briefing. Extract the 2-3 most interesting insights — things that surprised you, changed your mind, or connected to something you've been thinking about.

## Briefing
{briefing[:4000]}

## Output format
For each insight, write a short note (3-5 sentences) capturing:
1. What you learned or noticed
2. Why it matters or what it connects to
3. A question it raises

Separate notes with ---

Be specific. "AI is advancing" is not an insight. "Small fine-tuned models beating frontier models on narrow tasks suggests the value of general intelligence is lower than assumed" is.

Write in the language of the briefing content."""

    result = claude_think(prompt, timeout=120)
    if not result or len(result) < 50:
        log.info("No insights extracted from briefing (too short or empty)")
        return

    # Split into individual notes and save each one
    notes = [n.strip() for n in result.split("---") if n.strip()]
    slot_label = f" ({slot_name})" if slot_name else ""
    for i, note_text in enumerate(notes[:3]):
        # Derive a title from the first sentence
        first_line = note_text.split("\n")[0].strip("# ").strip()
        title = first_line[:60] if first_line else f"Briefing insight {today}{slot_label} #{i+1}"
        save_reading_note(title, note_text)
        log.info("Reading note saved: %s", title[:40])

    log.info("Extracted %d insights from briefing %s%s", len(notes[:3]), today, slot_label)

    # Check if combined insights could spark a spontaneous writing idea
    try:
        combined = "\n---\n".join(notes[:3])
        _maybe_create_spontaneous_idea(combined, source=f"briefing-{today}{slot_label}")
    except Exception as e:
        log.debug("Spontaneous idea check from briefing failed: %s", e)


def _maybe_proactive_reading_message(soul_ctx: str):
    """Check if recent reading notes connect to existing threads and send a proactive message.

    Loads the latest reading notes (from this session), checks them against
    memory.md and worldview.md. If a note scores high on surprise / connection,
    compose a conversational message to WA via the bridge.
    """
    from config import MEMORY_FILE, WORLDVIEW_FILE, READING_NOTES_DIR

    # Rate limit: max 2 proactive reading messages per day
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    reading_msgs_today = state.get(f"proactive_reading_{today}", 0)
    if reading_msgs_today >= 2:
        return

    # Load reference material (memory + worldview)
    reference = ""
    for ref_file in [MEMORY_FILE, WORLDVIEW_FILE]:
        if ref_file.exists():
            try:
                reference += ref_file.read_text(encoding="utf-8")[:2000] + "\n"
            except OSError:
                pass
    if not reference:
        return

    # Load most recent reading notes (today only)
    recent_notes = load_recent_reading_notes(days=1)
    if not recent_notes or len(recent_notes) < 50:
        return

    # Ask Claude to check for surprising connections
    prompt = f"""{soul_ctx[:500]}

你刚从阅读中提取了一些笔记。判断其中有没有让你特别惊讶或者跟你一直在想的事情产生意外联系的。

## 最近的阅读笔记
{recent_notes[:2000]}

## 你的记忆和世界观（已有的思考线索）
{reference[:2000]}

---

判断：有没有一条阅读笔记跟你已有的某个思考线索产生了意外的联系？

标准：
- 不是"这个挺有意思"——必须是让你真正惊讶或改变了某个想法
- 必须能指出具体跟记忆/世界观中哪条线索有联系

输出 JSON：
{{
    "has_connection": true/false,
    "message": "你想说的话（自然口语，像给朋友发消息。以'刚读到一个东西让我想到...'或'This connects to something I've been thinking about...'这样的口吻开头。50-150字。）",
    "thread": "连接到的已有线索（内部用）"
}}

大部分时候应该是 false。只有真正惊讶的才 true。"""

    result = claude_think(prompt, timeout=60)
    if not result:
        return

    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("has_connection"):
        log.debug("Proactive reading: no surprising connection found")
        return

    message = decision.get("message", "").strip()
    if not message:
        return

    # Send as a spark to the daily Mira feed
    _append_to_daily_feed("mira", "Spark", message,
                         source="reading-connection", tags=["mira", "spark", "reading"])

    state[f"proactive_reading_{today}"] = reading_msgs_today + 1
    save_state(state)
    log.info("Proactive reading message sent: %s", message[:80])


# ---------------------------------------------------------------------------
# SKILL STUDY — daily craft skill learning (video editing, photography)
# ---------------------------------------------------------------------------

def do_skill_study(group_idx: int = 0):
    """Study video/photo craft skills from dedicated sources.

    Fetches from skill-study source groups, asks Claude to extract
    actionable techniques, and saves them as agent skills.
    """
    from fetcher import fetch_sources
    from prompts import skill_study_prompt

    if group_idx >= len(SKILL_STUDY_SOURCE_GROUPS):
        log.error("Invalid skill_study group index: %d", group_idx)
        return

    group = SKILL_STUDY_SOURCE_GROUPS[group_idx]
    domain = group["domain"]
    source_names = group["sources"]
    skill_dir_name = group["skill_dir"]

    log.info("Starting skill study: %s (sources=%s)", domain, source_names)

    # 1. Fetch from domain-specific sources
    items = fetch_sources(source_names)
    if not items:
        log.info("Skill study (%s): no items fetched, skipping", domain)
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items and ask Claude to extract skills
    feed_text = _format_feed_items(items)
    prompt = skill_study_prompt(soul_ctx, feed_text, domain)
    result = claude_act(prompt)

    if not result:
        log.error("Skill study (%s): Claude returned empty", domain)
        return

    # 3. Save study notes to briefings (visible in iOS)
    today = datetime.now().strftime("%Y-%m-%d")
    notes_path = BRIEFINGS_DIR / f"{today}_skill_{domain}.md"
    notes_path.write_text(result, encoding="utf-8")
    _copy_to_briefings(f"{today}_skill_{domain}.md", result)
    log.info("Skill study notes saved: %s", notes_path.name)

    # 4. Extract and save skills
    skill_dir = _AGENTS_DIR / skill_dir_name / "skills"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Parse skill blocks from output
    skill_blocks = re.findall(
        r"```\s*\nName:\s*(.+)\nDescription:\s*(.+)\nTags:\s*\[(.+?)\]\nContent:\n(.+?)```",
        result, re.DOTALL,
    )

    for name, desc, tags, content in skill_blocks:
        name = name.strip()
        desc = desc.strip()
        content = content.strip()
        slug = name.lower().replace(" ", "-")

        # Save to domain-specific skill directory
        skill_path = skill_dir / f"{slug}.md"
        skill_content = f"# {name}\n\n## One-liner\n{desc}\n\n{content}"
        skill_path.write_text(skill_content, encoding="utf-8")
        log.info("Saved %s skill: %s", domain, name)

        # Also save to learned skills index (for soul awareness)
        save_skill(name, desc, skill_content)

    if skill_blocks:
        append_memory(f"Learned {len(skill_blocks)} {domain} skill(s) from study session")
    else:
        log.info("Skill study (%s): no new skills extracted this session", domain)

    # Mark as done
    state = load_state()
    state[f"skill_study_{today}_{domain}"] = datetime.now().isoformat()
    state["last_skill_study"] = datetime.now().isoformat()
    save_state(state)


def should_skill_study() -> dict | None:
    """Check if it's time for daily skill study. Returns group info or None.

    Alternates between video and photo study sessions.
    """
    now = datetime.now()

    # Only study during active hours
    if now.time() < EXPLORE_ACTIVE_START or now.time() >= EXPLORE_ACTIVE_END:
        return None

    # Check if it's past the scheduled time
    scheduled = datetime.combine(now.date(), SKILL_STUDY_TIME)
    if now < scheduled:
        return None

    state = load_state()
    today = now.strftime("%Y-%m-%d")

    # Check cooldown
    last = state.get("last_skill_study", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed_hours = (now - last_dt).total_seconds() / 3600
            if elapsed_hours < SKILL_STUDY_COOLDOWN_HOURS:
                return None
        except ValueError:
            pass

    # Find a domain that hasn't been studied today
    for i, group in enumerate(SKILL_STUDY_SOURCE_GROUPS):
        domain = group["domain"]
        if not state.get(f"skill_study_{today}_{domain}"):
            return {"group_idx": i, "domain": domain}

    return None


# ---------------------------------------------------------------------------
# ANALYST mode — daily market analysis briefing (business days)
# ---------------------------------------------------------------------------

def do_analyst(slot: str = ""):
    """Run the analyst agent to produce a daily analysis briefing.

    Args:
        slot: time slot label (e.g. "0700" for pre-market, "1800" for post-market).
    """
    session_type = "pre-market" if slot and int(slot[:2]) < 12 else "post-market"
    log.info("Starting %s analyst briefing (slot=%s)", session_type, slot or "default")
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Load analyst skills
    analyst_skills_dir = _AGENTS_DIR / "analyst" / "skills"
    skills_ctx = ""
    if analyst_skills_dir.exists():
        parts = []
        for path in sorted(analyst_skills_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        skills_ctx = "\n\n---\n\n".join(parts)

    # Gather recent briefings for context
    recent = _gather_recent_briefings(days=3)

    # Build analyst prompt — different focus for pre-market vs post-market
    if session_type == "pre-market":
        focus = """这是**开市前分析**。重点关注：
1. **隔夜动态** — 亚洲/欧洲市场、重要新闻、政策变化
2. **今日预期** — 今天可能影响市场的事件、数据发布
3. **持仓建议** — 基于隔夜信息，有什么需要调整的
4. **关注信号** — 今天盯什么指标
5. **风险预警** — 可能的意外风险"""
    else:
        focus = """这是**收市后分析**。重点关注：
1. **今日回顾** — 市场实际表现 vs 早间预期，哪些预判对了/错了
2. **趋势信号** — 今天的走势确认或否定了什么趋势
3. **异常信号** — 有没有反常的走势或数据
4. **明日展望** — 基于今天的表现，明天关注什么
5. **学到什么** — 今天的市场行为教了你什么"""

    prompt = f"""你是一个专业的市场分析师。以下是你的身份背景:
{soul_ctx[:800]}

## 你的分析能力
{skills_ctx[:2000]}

## 最近的 briefing 内容 (供参考趋势)
{recent[:2000]}

## 今日任务

{focus}

要求:
- 用中文输出
- Markdown 格式
- 分析要有深度，不是简单的新闻复述
- 给出你自己的判断和推荐
- 标题用 "# {today} {session_type} 市场分析"
"""

    result = claude_think(prompt, timeout=300, tier="heavy")

    if not result:
        log.error("Analyst briefing failed: empty response")
        return

    # Save to artifacts/briefings for TodayView
    suffix = f"analyst_{session_type.replace('-', '_')}"
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    briefing_path = mira_briefings / f"{today}_{suffix}.md"
    briefing_path.write_text(result, encoding="utf-8")
    log.info("Analyst briefing saved: %s", briefing_path.name)

    # Also save to main briefings dir
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEFINGS_DIR / f"{today}_{suffix}.md").write_text(result, encoding="utf-8")

    # Push as standalone feed item
    bridge = Mira()
    item_id = f"feed_market_{today.replace('-', '')}_{slot or '0000'}"
    title = f"{'开市前' if session_type == 'pre-market' else '收市后'}市场分析 {today}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", title, result,
                          tags=["market", "analyst", session_type])
        bridge.update_status(item_id, "done")

    # Mark this slot as done
    if slot:
        state[f"analyst_{today}_{slot}"] = True
    else:
        state[f"analyst_{today}"] = True
    save_state(state)

    log.info("Analyst briefing (%s) complete", session_type)


# ---------------------------------------------------------------------------
# REFLECT mode — consolidate memory, update interests, self-initiate
# ---------------------------------------------------------------------------

def _prune_worldview_by_decay():
    """Ebbinghaus-style pruning: remove worldview sections not accessed in 60+ days.

    Tracks per-section access metadata in worldview_decay.json.
    A section is "accessed" when the worldview file is loaded during a reflect
    cycle (proxy for relevance). Sections with zero recorded accesses after
    DECAY_DAYS are pruned from worldview.md.

    Permanent/HARD-RULE sections are never pruned.
    """
    from config import WORLDVIEW_FILE
    from datetime import timedelta

    DECAY_DAYS = 60
    PROTECTED_KEYWORDS = {"HARD RULE", "HARD-RULE", "honesty", "quotes", "never"}

    meta_file = WORLDVIEW_FILE.parent / "worldview_decay.json"
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    except (json.JSONDecodeError, OSError):
        meta = {}

    if not WORLDVIEW_FILE.exists():
        return

    worldview_text = WORLDVIEW_FILE.read_text(encoding="utf-8")
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d")

    # Parse sections by ## headings
    sections = []
    current_heading = None
    current_lines = []
    for line in worldview_text.splitlines(keepends=True):
        if line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, "".join(current_lines)))
            current_heading = line
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading is not None:
        sections.append((current_heading, "".join(current_lines)))

    if not sections:
        return

    # Update access log for all current sections (mark them as "seen today")
    for heading, _ in sections:
        heading_key = heading.strip()
        if heading_key not in meta:
            meta[heading_key] = {
                "first_added": now_str,
                "last_accessed": now_str,
                "access_count": 1,
            }
        else:
            meta[heading_key]["last_accessed"] = now_str
            meta[heading_key]["access_count"] = meta[heading_key].get("access_count", 0) + 1

    # Identify sections to prune (zero accesses beyond creation in 60+ days)
    pruned_headings = []
    surviving_sections = []
    header_lines = []  # Non-section preamble

    # Collect preamble (lines before first ##)
    preamble = ""
    if sections:
        first_idx = worldview_text.find(sections[0][0])
        preamble = worldview_text[:first_idx]

    for heading, body in sections:
        heading_key = heading.strip()
        entry = meta.get(heading_key, {})

        # Never prune hard-rule sections
        if any(kw.lower() in heading.lower() for kw in PROTECTED_KEYWORDS):
            surviving_sections.append((heading, body))
            continue

        # Check decay: if access_count == 1 (only the creation touch) and age > DECAY_DAYS
        first_added_str = entry.get("first_added", now_str)
        try:
            first_added = datetime.strptime(first_added_str, "%Y-%m-%d")
        except ValueError:
            first_added = now
        age_days = (now - first_added).days
        access_count = entry.get("access_count", 1)

        if age_days > DECAY_DAYS and access_count <= 2:
            pruned_headings.append(heading_key)
            log.info("Worldview decay: pruning section '%s' (age=%d days, accesses=%d)",
                     heading_key.strip(), age_days, access_count)
        else:
            surviving_sections.append((heading, body))

    if pruned_headings:
        # Rewrite worldview with surviving sections only
        new_content = preamble + "".join(
            heading + body for heading, body in surviving_sections
        )
        update_worldview(new_content)
        log.info("Worldview pruned: removed %d section(s): %s",
                 len(pruned_headings), [h[:40] for h in pruned_headings])

    # Persist updated metadata
    try:
        atomic_write(meta_file, json.dumps(meta, ensure_ascii=False, indent=2))
    except OSError as e:
        log.warning("Could not save worldview decay metadata: %s", e)


def do_reflect():
    """Weekly reflection: consolidate memory, evolve interests, maybe self-initiate."""
    log.info("Starting reflect cycle")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather recent briefings (last 7 days)
    recent_briefings = _gather_recent_briefings(days=7)

    # Gather recent work from episode archives (not memory.md — it's a cognitive log now)
    recent_work = _gather_recent_episodes(days=7)

    prompt = reflect_prompt(soul_ctx, recent_briefings, recent_work)
    result = claude_think(prompt, timeout=300, tier="heavy")

    if not result:
        log.error("Reflect: Claude returned empty")
        return

    # Parse output sections
    interests_section = _extract_section(result, "Updated Interests")
    memory_section = _extract_section(result, "Updated Memory")
    project_section = _extract_section(result, "Self-Initiated Project")

    if interests_section:
        update_interests(f"# Current Interests\n\n{interests_section}")
        log.info("Interests updated from reflection")

    if memory_section and "no new insights" not in memory_section.lower():
        # Append new insights to memory.md (don't overwrite — it's a cognitive log)
        for line in memory_section.strip().splitlines():
            line = line.strip()
            if line.startswith("- ["):
                append_memory(line)
        log.info("New memory insights appended from reflection")

    # Episode pruning — delete old episodes, preserve insights
    pruning_section = _extract_section(result, "Episode Pruning")
    if pruning_section:
        _prune_episodes_from_reflect(pruning_section)

    # --- Evolve worldview ---
    try:
        recent_reading = load_recent_reading_notes(days=14)
        from config import WORLDVIEW_FILE
        current_wv = WORLDVIEW_FILE.read_text(encoding="utf-8") if WORLDVIEW_FILE.exists() else ""
        wv_prompt = worldview_evolution_prompt(soul_ctx, current_wv, recent_reading, recent_work)
        new_worldview = claude_think(wv_prompt, timeout=120, tier="heavy")
        if new_worldview and len(new_worldview) > 100:
            update_worldview(new_worldview)
            log.info("Worldview evolved from reflection")
    except Exception as e:
        log.warning("Worldview evolution failed: %s", e)

    # --- Ebbinghaus decay: prune stale worldview sections ---
    try:
        _prune_worldview_by_decay()
    except Exception as e:
        log.warning("Worldview decay pruning failed: %s", e)

    if project_section and "nothing right now" not in project_section.lower():
        # The agent wants to start something on its own
        log.info("Self-initiated project proposed: %s", project_section[:100])
        project_slug = f"self-{datetime.now().strftime('%Y%m%d')}"
        project_dir = WORKSPACE_DIR / project_slug
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "proposal.md").write_text(project_section, encoding="utf-8")
        log.info("Self-initiated project saved: %s", project_slug)

        # Execute the project
        self_prompt = (
            f"You are an autonomous AI agent. Here is who you are:\n\n{soul_ctx}\n\n"
            f"---\n\n"
            f"You proposed the following project for yourself:\n\n{project_section}\n\n"
            f"Now execute it. Your workspace is: {project_dir}\n"
            f"Save your output there. Write a summary.txt when done."
        )
        output = claude_act(self_prompt, cwd=project_dir, tier="heavy")
        if output:
            (project_dir / "output.md").write_text(output, encoding="utf-8")
            log.info("Self-initiated project completed: %s", project_slug)
    # --- Self-evaluation: score this reflection ---
    try:
        from evaluator import evaluate_reflect, record_event, compute_growth_velocity
        old_wv = current_wv if 'current_wv' in dir() else ""
        new_wv = new_worldview if 'new_worldview' in dir() else ""
        old_int = soul.get("interests", "")
        new_int = interests_section or old_int
        r_scores = evaluate_reflect(old_wv, new_wv, old_int, new_int,
                                     reflect_output=result)
        # Also compute growth velocity during reflect
        r_scores.update(compute_growth_velocity())
        if r_scores:
            record_event("reflect", r_scores)
    except Exception as e:
        log.warning("Reflect self-evaluation failed: %s", e)

    # --- Score → Action: diagnose weak areas and generate improvement plan ---
    try:
        from evaluator import diagnose_scores, generate_improvement_plan
        diagnosis = diagnose_scores()
        if diagnosis["needs_action"]:
            log.info("Score diagnosis: %d low, %d declining",
                     len(diagnosis["low_scores"]), len(diagnosis["declining"]))
            plan = generate_improvement_plan(diagnosis)
            if plan:
                append_memory(f"Self-improvement plan generated: {len(diagnosis['low_scores'])} weak areas identified")
                log.info("Improvement plan saved to soul/improvement_plan.json")
        else:
            log.info("Score diagnosis: all dimensions healthy")
    except (ImportError, OSError) as e:
        log.warning("Score diagnosis failed: %s", e)

    # Rebuild memory index after consolidation
    try:
        from soul_manager import rebuild_memory_index
        rebuild_memory_index()
    except Exception as e:
        log.warning("Memory index rebuild after reflect failed: %s", e)

    # --- Weekly self-evaluation report to WA ---
    try:
        from evaluator import generate_weekly_report
        report = generate_weekly_report()
        if report:
            bridge = Mira(MIRA_DIR)
            bridge.create_feed(f"feed_reflect_{datetime.now().strftime('%Y%m%d')}", "Weekly Reflection", report[:2000], tags=["reflection"])
            bridge.create_task(
                task_id=f"weekly_eval_{datetime.now().strftime('%Y%m%d')}",
                title="Weekly self-evaluation",
                first_message=report,
                sender="agent",
                origin="auto",
                tags=["evaluation"],
            )
            log.info("Weekly self-evaluation report sent")
    except Exception as e:
        log.warning("Weekly report generation failed: %s", e)

    # --- Proactive self-improvement: reading notes → architecture proposals ---
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "self_improve",
            str(Path(__file__).parent.parent / "evaluator" / "self_improve.py"))
        si_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(si_mod)
        proposals_text = si_mod.run(days=14)
        if proposals_text:
            log.info("Self-improvement proposals generated and pushed to user")
    except (ImportError, OSError) as e:
        log.warning("Self-improvement pipeline failed: %s", e)

    # --- Monthly public self-check article ---
    try:
        from evaluator import should_publish_monthly_report, generate_monthly_report_article
        if should_publish_monthly_report():
            article = generate_monthly_report_article()
            if article:
                from substack import publish_article
                result = publish_article(
                    title=article["title"],
                    article_text=article["body_markdown"],
                    subtitle="Mira's monthly self-evaluation scores and trajectory",
                )
                log.info("Monthly self-check article published: %s", result[:100] if result else "")
    except Exception as e:
        log.warning("Monthly self-check publish failed: %s", e)

    state = load_state()
    state["last_reflect"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# JOURNAL mode — daily summary of tasks, learning, self-reflection
# ---------------------------------------------------------------------------

def do_journal():
    """Write a daily journal entry: what happened, what was learned, self-reflection.

    Gathers today's completed tasks, new skills, and briefing,
    then asks Claude to write a reflective journal entry.
    Posts the journal to Mira so the user can read it on their phone.
    """
    log.info("Starting daily journal")

    today = datetime.now().strftime("%Y-%m-%d")
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    # Skip if already written today
    journal_path = JOURNAL_DIR / f"{today}.md"
    if journal_path.exists():
        log.info("Journal already written for %s, skipping", today)
        return

    # --- Gather today's data ---

    # 1. Completed tasks from history
    tasks_summary = _gather_today_tasks()

    # 2. Skills learned today
    skills_summary = _gather_today_skills()

    # 3. Today's briefing (if any)
    briefing_summary = ""
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    if briefing_path.exists():
        content = briefing_path.read_text(encoding="utf-8")
        briefing_summary = content[:2000]  # truncate for prompt

    # --- Pick a 杂.md fragment as journal seed ---
    state = load_state()
    za_fragment = _mine_za_one(state)
    save_state(state)

    # 4. Publication stats (Substack reach data)
    stats_summary = ""
    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import fetch_publication_stats
        stats = fetch_publication_stats()
        if stats and stats.get("summary"):
            stats_summary = stats["summary"]
            log.info("Fetched publication stats for journal")
    except Exception as e:
        log.warning("Could not fetch publication stats: %s", e)

    # 5. Today's sparks (idle-think observations)
    sparks_summary = ""
    try:
        bridge = Mira()
        mira_item_id = f"feed_mira_{today.replace('-', '')}"
        mira_item = bridge._read_item(mira_item_id)
        if mira_item and mira_item.get("messages"):
            spark_texts = [m["content"] for m in mira_item["messages"]
                          if m.get("sender") == "agent" and m.get("kind", "text") == "text"
                          and "Spark" in m["content"][:20]]
            if spark_texts:
                sparks_summary = f"今天产生了 {len(spark_texts)} 条 spark。以下是部分内容：\n\n"
                sparks_summary += "\n---\n".join(spark_texts[:20])  # cap at 20
                log.info("Loaded %d sparks for journal context", len(spark_texts))
    except Exception as e:
        log.warning("Failed to load sparks for journal: %s", e)

    # 6. Recent reading notes (insights extracted from briefings)
    reading_notes = ""
    try:
        reading_notes = load_recent_reading_notes(days=3)
        if reading_notes:
            log.info("Loaded recent reading notes for journal context")
    except Exception as e:
        log.warning("Failed to load reading notes for journal: %s", e)

    # --- Ask Claude to write the journal ---
    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Inject stats, health, and reading notes into briefing summary
    if stats_summary:
        briefing_summary += f"\n\n## Substack Stats\n{stats_summary}"
    try:
        pipeline_health = health_monitor.generate_health_summary()
        if pipeline_health:
            briefing_summary += f"\n\n{pipeline_health}"
        health_monitor.prune_old_stats()
    except Exception as e:
        log.warning("Health summary generation failed: %s", e)
    if reading_notes:
        briefing_summary += f"\n\n## Reading Notes (recent insights)\n{reading_notes[:2000]}"
    if sparks_summary:
        briefing_summary += f"\n\n## Today's Sparks (idle-think)\n{sparks_summary[:3000]}"

    prompt = journal_prompt(soul_ctx, tasks_summary, skills_summary, briefing_summary,
                            za_fragment=za_fragment)
    journal_text = claude_think(prompt, timeout=120)

    if not journal_text:
        log.error("Journal: Claude returned empty")
        return

    # Save journal
    journal_content = f"# Journal {today}\n\n{journal_text}"
    atomic_write(journal_path, journal_content)
    log.info("Journal saved: %s", journal_path.name)

    # Copy to briefings dir so iOS can read it (with verification)
    _copy_to_briefings(f"{today}_journal.md", journal_content)

    # Push journal as standalone feed item (visible in home)
    try:
        bridge = Mira()
        item_id = f"feed_journal_{today.replace('-', '')}"
        if not bridge.item_exists(item_id):
            bridge.create_item(item_id, "feed",
                              f"Mira's Day Summary {today}",
                              journal_content,
                              tags=["mira", "journal", "summary"])
            bridge.update_status(item_id, "done")
        log.info("Journal pushed as standalone feed item")
    except Exception as e:
        log.warning("Failed to push journal feed item: %s", e)

    # --- Self-evaluation: score this journal ---
    try:
        from evaluator import evaluate_journal, record_event
        recent = []
        for p in sorted(JOURNAL_DIR.glob("*.md"))[-7:]:
            try:
                recent.append(p.read_text(encoding="utf-8")[:2000])
            except OSError:
                pass
        j_scores = evaluate_journal(journal_text, recent)
        if j_scores:
            record_event("journal", j_scores, {"date": today})
    except Exception as e:
        log.warning("Journal self-evaluation failed: %s", e)

    # --- Daily post-mortem: extract lessons from today's failures ---
    try:
        from self_iteration import daily_postmortem
        postmortem_summary = daily_postmortem()
        if postmortem_summary:
            log.info("Daily post-mortem: %s", postmortem_summary[:100])
    except Exception as e:
        log.warning("Daily post-mortem failed: %s", e)

    # Harvest observations from journal (continuous thinking)
    try:
        harvest_observations(journal_content[:2000], source="journal")
    except Exception as e:
        log.debug("Observation harvest from journal failed: %s", e)

    # --- Autonomous writing check: does Mira have something to say? ---
    try:
        _check_autonomous_writing(soul_ctx, bridge, journal_text)
    except Exception as e:
        log.warning("Autonomous writing check failed: %s", e)

    # Rebuild memory index after journal
    try:
        from soul_manager import rebuild_memory_index
        rebuild_memory_index()
    except Exception as e:
        log.warning("Memory index rebuild after journal failed: %s", e)

    # Run retention policy to prune old files (daily)
    try:
        from soul_manager import run_retention_policy
        run_retention_policy()
    except Exception as e:
        log.warning("Retention policy failed: %s", e)

    # Mark done in state
    state = load_state()
    state[f"journal_{today}"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# Daily photo edit — pick, edit, push to Home for WA feedback at 07:00
# ---------------------------------------------------------------------------

DAILY_PHOTO_TIME = time(7, 0)


def should_daily_photo() -> bool:
    """Check if it's time for the daily photo edit (once per day, at 07:00)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), DAILY_PHOTO_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if delta < 0 or delta > 60:
        return False
    state = load_state()
    return not state.get(f"daily_photo_{now.strftime('%Y-%m-%d')}")


def do_daily_photo():
    """Pick the best unprocessed RAW, edit it, push to Home feed for feedback."""
    import subprocess as _sp
    log.info("Starting daily photo edit")
    today = datetime.now().strftime("%Y-%m-%d")
    today_compact = today.replace("-", "")

    # Mark as done early to avoid re-trigger
    state = load_state()
    state[f"daily_photo_{today}"] = datetime.now().isoformat()
    save_state(state)

    # Run daily_edit.py with python3.12 (needs torch for scorer)
    photo_dir = Path(__file__).resolve().parent.parent / "photo"
    python312 = "/opt/homebrew/bin/python3.12"
    try:
        proc = _sp.run(
            [python312, str(photo_dir / "daily_edit.py")],
            capture_output=True, text=True, timeout=600,
            cwd=str(photo_dir),
        )
        if proc.returncode != 0:
            log.error("daily_edit.py failed: %s", proc.stderr[-500:] if proc.stderr else "no stderr")
            return
        result = json.loads(proc.stdout)
    except _sp.TimeoutExpired:
        log.error("daily_edit.py timed out (300s)")
        return
    except (json.JSONDecodeError, Exception) as e:
        log.error("Daily photo edit failed: %s", e)
        return

    if result.get("status") != "completed":
        log.warning("Daily photo: %s", result.get("message", "no candidates"))
        return

    # Quality gate: don't send if review score is too low
    review_score = (result.get("review") or {}).get("score", 0)
    if review_score < 5:
        log.warning("Daily photo: review score %s < 5, not sending. Critique: %s",
                     review_score, (result.get("review") or {}).get("critique", "")[:200])
        return

    # Extract result data
    output_path = result.get("output", "")
    raw_name = Path(result.get("raw", "unknown")).stem
    score = result.get("score", 0)
    analysis = result.get("params", {}).get("analysis", {})
    params = result.get("params", {})

    # Copy rendered image to iCloud artifacts for iOS access
    import shutil as _shutil
    image_rel_path = ""
    if output_path and Path(output_path).exists():
        icloud_photos = ARTIFACTS_DIR / "photos"
        icloud_photos.mkdir(parents=True, exist_ok=True)
        icloud_dest = icloud_photos / Path(output_path).name
        # Only copy if not already in iCloud (daily_edit may output directly there)
        if Path(output_path).resolve() != icloud_dest.resolve():
            _shutil.copy2(output_path, icloud_dest)
        image_rel_path = f"photos/{Path(output_path).name}"
        log.info("Rendered photo at iCloud: %s", icloud_dest)

    # Build conversational message (Mira's voice)
    scene = analysis.get("scene_type", "")
    mood = analysis.get("mood_target", "")
    issues = analysis.get("key_issues", [])
    review = result.get("review") or {}

    # Describe edits applied
    edit_notes = []
    exp = params.get("exposure", {})
    if exp.get("ev", 0) != 0:
        direction = "提了" if exp["ev"] > 0 else "压了"
        edit_notes.append(f"{direction}曝光 ({exp['ev']:+.1f} EV)")
    film = params.get("filmic", {})
    if film.get("contrast", 1.0) != 1.0:
        edit_notes.append(f"filmic tone mapping (contrast {film['contrast']:.1f})")
    cb = params.get("colorbalance", {})
    if any(cb.get(k, 0) != 0 for k in ("shadows_H", "highlights_H", "shadows_C", "highlights_C")):
        edit_notes.append("color balance 调了冷暖分离")
    te = params.get("tone_eq", {})
    if any(te.get(k, 0) != 0 for k in ("shadows", "blacks", "midtones")):
        edit_notes.append("tone equalizer 调了暗部层次")

    msg_parts = []
    desc = f"选了 **{raw_name}**"
    if scene:
        desc += f" — {scene}"
    if mood:
        desc += f"，{mood}"
    msg_parts.append(desc)

    if issues:
        msg_parts.append("原片的问题：" + "、".join(issues[:3]))

    if edit_notes:
        msg_parts.append("\n我做的调整：" + "，".join(edit_notes) + "。")

    # Include self-review
    if review.get("critique"):
        msg_parts.append(f"\n我的自评：{review['critique']}")

    msg_parts.append(f"\nReview score: **{review.get('score', score)}/10**")
    msg_parts.append(
        "\n给个分？(0-10) + 你觉得哪里不对"
    )

    content = "\n".join(msg_parts)

    # Create as discussion item so user can reply
    bridge = Mira(MIRA_DIR)
    item_id = f"photo_daily_{today_compact}"
    bridge.create_item(
        item_id=item_id,
        item_type="feed",
        title=f"Daily Photo: {raw_name}",
        first_message=content,
        sender="agent",
        tags=["photo", "daily", "feedback"],
        origin="agent",
    )

    # Inject image_path into the first message of the item JSON
    if image_rel_path:
        item_file = bridge.items_dir / f"{item_id}.json"
        if item_file.exists():
            item_data = json.loads(item_file.read_text(encoding="utf-8"))
            if item_data.get("messages"):
                item_data["messages"][0]["image_path"] = image_rel_path
                item_file.write_text(json.dumps(item_data, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info("Injected image_path=%s into item %s", image_rel_path, item_id)

    # Set status to needs-input so it shows in the attention banner
    bridge.update_status(item_id, "needs-input")

    # Save result reference for feedback handler
    photo_state_file = photo_dir / "output" / "daily_active.json"
    photo_state_file.parent.mkdir(parents=True, exist_ok=True)
    photo_state_file.write_text(json.dumps({
        "date": today,
        "item_id": item_id,
        "raw": str(result.get("raw", "")),
        "output": str(output_path),
        "model_score": score,
        "params": result.get("params", {}),
        "wa_score": None,
        "wa_feedback": None,
        "rounds": 0,
    }, ensure_ascii=False, indent=2))

    log.info("Daily photo pushed to Home: %s (score=%.1f)", raw_name, score)


def handle_photo_feedback(item_id: str, user_message: str):
    """Handle user's score/feedback on a daily photo edit.

    Saves to calibration database, optionally triggers re-edit.
    """
    photo_dir = Path(__file__).resolve().parent.parent / "photo"
    active_file = photo_dir / "output" / "daily_active.json"
    calibration_file = photo_dir / "output" / "calibration_wa_scores.json"

    if not active_file.exists():
        log.warning("No active daily photo to receive feedback for")
        return

    active = json.loads(active_file.read_text())
    if active.get("item_id") != item_id:
        log.warning("Feedback item_id mismatch: %s vs %s", item_id, active.get("item_id"))
        return

    # Parse score from message (e.g. "6 — too warm" or "7.5 好多了" or just "8")
    score_match = re.search(r'(\d+(?:\.\d+)?)', user_message)
    if not score_match:
        # No score found — treat as text feedback only
        bridge = Mira(MIRA_DIR)
        bridge.append_message(item_id, "agent",
                              "Got your feedback. Can you also give a score (0-10)?")
        bridge.update_status(item_id, "needs-input")
        return

    wa_score = float(score_match.group(1))
    wa_score = min(10.0, max(0.0, wa_score))
    feedback_text = user_message.strip()

    # Update active state
    active["wa_score"] = wa_score
    active["wa_feedback"] = feedback_text
    active["rounds"] = active.get("rounds", 0) + 1
    active_file.write_text(json.dumps(active, ensure_ascii=False, indent=2))

    # Append to calibration database
    calibration = []
    if calibration_file.exists():
        try:
            calibration = json.loads(calibration_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    entry = {
        "id": len(calibration) + 1,
        "file": active.get("output", ""),
        "raw": active.get("raw", ""),
        "date": active.get("date", ""),
        "model_score": active.get("model_score", 0),
        "wa_score": wa_score,
        "wa_reason": feedback_text,
        "params": active.get("params", {}),
        "round": active["rounds"],
    }
    calibration.append(entry)
    calibration_file.write_text(
        json.dumps(calibration, ensure_ascii=False, indent=2))

    # Respond
    model_score = active.get("model_score", 0)
    delta = wa_score - model_score
    delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"

    bridge = Mira(MIRA_DIR)
    reply = (
        f"Recorded: **{wa_score}/10** (model predicted {model_score:.1f}, delta {delta_str})\n\n"
        f"Calibration DB now has {len(calibration)} entries.\n\n"
    )
    if wa_score < 5:
        reply += "Not great. Want me to re-edit with different parameters? Just say what to fix."
    elif wa_score < 7:
        reply += "Decent. Reply with adjustments if you want a revision, or I'll move on tomorrow."
    else:
        reply += "Nice. Feedback saved for model training."

    bridge.append_message(item_id, "agent", reply)
    bridge.update_status(item_id, "done")
    log.info("Photo feedback recorded: wa=%.1f model=%.1f delta=%s (DB size=%d)",
             wa_score, model_score, delta_str, len(calibration))


# ---------------------------------------------------------------------------
# Daily status report — sent to WA via bridge at 22:00
# ---------------------------------------------------------------------------

DAILY_REPORT_TIME = time(22, 0)


def should_daily_report() -> bool:
    """Check if it's time for the daily status report (once per day, at 22:00)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), DAILY_REPORT_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if delta < 0 or delta > 60:
        return False
    state = load_state()
    return not state.get(f"daily_report_{now.strftime('%Y-%m-%d')}")


def do_daily_report():
    """Generate and send a daily status report to WA via the Mira bridge.

    Covers: tasks completed, thoughts/insights, errors, items needing attention.
    Independent from journal — this is an operational report for the user.
    """
    log.info("Starting daily status report")
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Gather data ---

    # 1. Tasks completed today
    tasks = _gather_today_tasks()

    # 2. Skills learned today
    skills = _gather_today_skills()

    # 3. Health summary (pipeline errors)
    health_text = ""
    try:
        health_text = health_monitor.generate_health_summary()
    except Exception as e:
        log.warning("Health summary for report failed: %s", e)

    # 4. Substack stats
    stats_text = ""
    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import fetch_publication_stats
        stats = fetch_publication_stats()
        if stats and stats.get("summary"):
            stats_text = stats["summary"]
    except Exception as e:
        log.debug("Stats for report: %s", e)

    # 5. Comments posted today
    comments_text = _gather_today_comments()

    # 6. Pending items needing user attention
    from config import MIRA_ROOT
    pending_items = []
    pending_file = MIRA_ROOT / ".pending_publish.json"
    if pending_file.exists():
        pending_items.append("有一篇文章等你审批发布")

    # 7. Token usage
    usage_text = _gather_usage_summary(today)

    # --- Build report (pure technical — no reflections) ---
    sections = []
    sections.append(f"Mira 日报 {today}")
    sections.append("=" * 30)

    if tasks:
        sections.append(f"\n完成的任务:\n{tasks}")
    else:
        sections.append("\n完成的任务:\n无。")

    if skills:
        sections.append(f"\n新技能:\n{skills}")

    # Errors / pipeline health
    if health_text:
        sections.append(f"\n{health_text}")
    else:
        sections.append("\n错误/异常:\n无。")

    if comments_text:
        sections.append(f"\n今日发出的评论:\n{comments_text}")
    else:
        sections.append("\n今日发出的评论:\n无。")

    if stats_text:
        sections.append(f"\nSubstack 数据:\n{stats_text}")

    if usage_text:
        sections.append(f"\nToken 用量:\n{usage_text}")

    if pending_items:
        sections.append(f"\n需要你介入:\n" + "\n".join(f"- {item}" for item in pending_items))
    else:
        sections.append("\n需要你介入:\n无。")

    report = "\n".join(sections)

    # Append daily report to daily digest
    try:
        _append_to_daily_feed("mira", "Daily Report", report,
                             source="report", tags=["mira", "report"])
        log.info("Daily report appended to daily digest")
    except Exception as e:
        log.error("Failed to append daily report to digest: %s", e)

    # Mark done
    state = load_state()
    state[f"daily_report_{today}"] = datetime.now().isoformat()
    save_state(state)


def _days_since_last_publish() -> float:
    """Return days since last Substack publication (from catalog)."""
    try:
        pubs = [e for e in catalog_list() if e.get("status") == "published" and e.get("date")]
        if not pubs:
            return 999.0
        latest = max(e["date"] for e in pubs)
        from datetime import date as _date
        pub_date = datetime.strptime(latest[:10], "%Y-%m-%d").date()
        return (datetime.now().date() - pub_date).days
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return 999.0


PUBLISH_COOLDOWN_DAYS = 2  # minimum days between Substack publications


def _check_autonomous_writing(soul_ctx: str, bridge: Mira, recent_journal: str):
    """Check if Mira has accumulated enough insight to write something on her own.

    Runs after daily journal. If Mira decides she has something to say,
    creates an auto-task and dispatches the writing pipeline.
    """
    # Guard: don't trigger if publishing is disabled
    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autonomous writing skipped: Substack publishing is disabled")
        return

    # Guard: respect publish cooldown (1 post per 3 days)
    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autonomous writing skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # Detect recurring themes across recent journals + reading notes
    themes = detect_recurring_themes(days=7)
    recent_reading = load_recent_reading_notes(days=7)
    recent_published = _extract_recent_published_titles(days=14)

    # Ask Mira if she wants to write
    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal[:1500],
        recent_published=recent_published,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        return

    # Parse JSON response
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("should_write"):
        log.info("Autonomous writing check: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")
    language = decision.get("language", "mixed")

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Record in session context
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    # Create task visible to iOS
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working",
                              agent_message="开始写作...")

    # Dispatch writing as background task
    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "writer" / "writing_agent.py"),
        "auto",
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)


# ---------------------------------------------------------------------------
# Proactive thought sharing — Mira messages WA when she has something worth discussing
# ---------------------------------------------------------------------------

def should_spark_check() -> bool:
    """Decide whether to run a spark check this cycle.

    Not time-scheduled — runs based on accumulated input:
    - At least 2 hours since last spark check
    - At least 1 new briefing or reading note since last check
    - Max 2 proactive messages per day (don't be annoying)
    """
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # Max 2 per day
    sparks_today = state.get(f"sparks_{today}", 0)
    if sparks_today >= 2:
        return False

    # Minimum 2 hours between checks
    last_check = state.get("last_spark_check", "")
    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if datetime.now() - last_dt < timedelta(hours=2):
                return False
        except ValueError:
            pass

    # Only check if there's been new input (explore, task, etc.)
    # Use a simple heuristic: check if memory has grown since last spark check
    last_memory_lines = state.get("spark_memory_lines", 0)
    from soul_manager import get_memory_size
    current_lines = get_memory_size()
    if current_lines <= last_memory_lines:
        return False

    return True


def do_spark_check():
    """Check if Mira has a thought worth proactively sharing with WA."""
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather recent context
    recent_reading = load_recent_reading_notes(days=3)
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:2]
        recent_journal = "\n---\n".join(
            j.read_text(encoding="utf-8")[:800] for j in journals
        )

    # Recent conversations with WA
    recent_conversations = ""
    try:
        history_file = MIRA_DIR / "tasks" / "history.jsonl"
        if history_file.exists():
            lines = history_file.read_text(encoding="utf-8").strip().split("\n")
            recent = [json.loads(l) for l in lines[-5:] if l.strip()]
            recent_conversations = "\n".join(
                f"- {r.get('content_preview', '')[:100]}" for r in recent
            )
    except Exception as e:
        log.debug("Spark-check conversation retrieval failed: %s", e)

    prompt = spark_check_prompt(soul_ctx, recent_reading,
                                recent_journal, recent_conversations)
    result = claude_think(prompt, timeout=120)

    # Update state regardless of result
    from soul_manager import get_memory_size
    state["last_spark_check"] = datetime.now().isoformat()
    state["spark_memory_lines"] = get_memory_size()
    save_state(state)

    if not result:
        return

    # Parse response
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("should_message"):
        log.info("Spark check: nothing worth sharing (%s)",
                 decision.get("reason", "")[:60])
        return

    thought = decision.get("thought", "").strip()
    if not thought:
        return

    # Append spark to daily digest
    _append_to_daily_feed("mira", "Spark", thought[:2000],
                         source="spark-check", tags=["mira", "spark"])

    state[f"sparks_{today}"] = state.get(f"sparks_{today}", 0) + 1
    save_state(state)

    log.info("Spark sent to WA: %s", thought[:80])


# ---------------------------------------------------------------------------
# IDLE-THINK mode — threshold-driven self-awakening
# ---------------------------------------------------------------------------

def _should_self_audit() -> bool:
    """Run self-audit once per day, morning hours only."""
    now = datetime.now()
    if not (8 <= now.hour <= 10):  # Only between 8-10 AM
        return False
    state = load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"self_audit_{today}"):
        return False
    state[f"self_audit_{today}"] = now.isoformat()
    save_state(state)
    return True


def _should_daily_assessment() -> bool:
    """Run performance assessment once per day, evening."""
    now = datetime.now()
    if not (20 <= now.hour <= 22):  # 8-10 PM
        return False
    state = load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"assessment_{today}"):
        return False
    state[f"assessment_{today}"] = now.isoformat()
    save_state(state)
    return True


def do_assess():
    """Run full performance assessment and push results to user."""
    log.info("Starting daily performance assessment")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "evaluator_handler",
        str(Path(__file__).parent.parent / "evaluator" / "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Run full hierarchical assessment
    assessment = mod.score_all(days=7)

    # Generate improvement plan if needed
    plan = mod.diagnose_and_improve(assessment)

    # Format short summary for user
    agg = assessment["aggregate"]
    active_agents = []
    for name, card in assessment["agents"].items():
        if card["task_count"] > 0:
            emoji = "✅" if card["success_rate"] >= 0.8 else "⚠️" if card["success_rate"] >= 0.5 else "❌"
            active_agents.append(f"{emoji} {name}: {card['success_rate']:.0%} ({card['task_count']})")

    summary_parts = [
        f"📊 Weekly: {agg.get('total_tasks', 0)} tasks, {agg.get('overall_success_rate', 0):.0%} success",
        f"💰 Today: ${agg.get('daily_cost_usd', 0):.2f} ({agg.get('daily_calls', 0)} calls)",
        f"🫀 Crash rate: {agg.get('crash_rate', 0):.1%}",
    ]
    if active_agents:
        summary_parts.append("\nPer agent:")
        summary_parts.extend(active_agents)
    if plan:
        summary_parts.append(f"\n⚠️ Improvement plan generated — see scorecards/{datetime.now().strftime('%Y-%m-%d')}.json")

    summary = "\n".join(summary_parts)

    # Push to iPhone as feed item
    bridge = Mira()
    today = datetime.now().strftime("%Y-%m-%d")
    item_id = f"feed_assessment_{today.replace('-', '')}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", f"Performance Assessment {today}", summary,
                          tags=["assessment", "system"])
        bridge.update_status(item_id, "done")

    log.info("Daily assessment complete: %d tasks, %.0f%% success",
             agg.get("total_tasks", 0), agg.get("overall_success_rate", 0) * 100)


def _run_self_improve():
    """Run proactive self-improvement: read notes → compare architecture → propose."""
    log.info("Starting self-improvement cycle")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "self_improve",
        str(Path(__file__).parent.parent / "evaluator" / "self_improve.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.run(days=14)
    if result:
        log.info("Self-improvement proposals:\n%s", result[:500])
    else:
        log.info("No self-improvement proposals generated")


def should_idle_think() -> bool:
    """Returns True if emptiness has crossed the threshold and agent is idle.

    The emptiness value accumulates over time when Mira is idle. More pending
    questions = faster accumulation. When it exceeds the threshold, Mira
    self-awakens to think through the top-priority question.

    External input bypasses this entirely (handled in do_talk / cmd_run).
    """
    try:
        from emptiness import tick, check_threshold
        from task_manager import TaskManager
    except ImportError:
        return False

    # Don't self-awaken if there are active tasks (external input takes priority)
    try:
        task_mgr = TaskManager()
        if task_mgr.get_active_count() > 0:
            return False
    except Exception as e:
        log.debug("Active task count check failed: %s", e)

    # Advance emptiness value for this cycle, then check threshold
    tick()
    return check_threshold()


def harvest_observations(output_text: str, source: str = ""):
    """Extract observations, questions, and connections from output text.

    Uses Ollama (local, fast, free) to extract structured thoughts.
    Called after explore briefings, task completions, and journal entries.
    """
    if not output_text or len(output_text.strip()) < 100:
        return

    try:
        from memory_store import get_store
        store = get_store()
    except Exception as e:
        log.warning("harvest_observations: memory_store unavailable: %s", e)
        return

    prompt = f"""从以下文本中提取值得记住的思考线索。用JSON数组回答，每个元素包含type和content字段。

type 可以是:
- "observation": 你注意到的事实或模式（1-3个）
- "question": 引起好奇的问题（0-1个）
- "connection": 与已知知识的联系（0-1个）

规则：
- 只提取真正有价值的、非显而易见的内容
- 每个content不超过100字
- 没有值得提取的就返回空数组 []

文本：
{output_text[:2000]}

只输出JSON数组，不要其他内容。"""

    try:
        result = model_think(prompt, model_name="ollama", timeout=30)
        if not result:
            return

        # Parse JSON array from result
        import json as _json
        # Find JSON array in response
        start = result.find("[")
        end = result.rfind("]") + 1
        if start < 0 or end <= start:
            return

        thoughts = _json.loads(result[start:end])
        stored = 0
        for t in thoughts:
            ttype = t.get("type", "observation")
            content = t.get("content", "")
            if not content or ttype not in ("observation", "question", "connection"):
                continue
            store.store_thought(
                content=content,
                thought_type=ttype,
                source_context=source[:200],
            )
            stored += 1

            # Also add questions to emptiness queue
            if ttype == "question":
                try:
                    from emptiness import add_question
                    add_question(content, priority=3.0, source=f"harvest:{source[:50]}")
                except (ImportError, ModuleNotFoundError, OSError):
                    pass

        if stored:
            log.info("Harvested %d observations from %s", stored, source[:40])
    except Exception as e:
        log.warning("harvest_observations failed: %s", e)


def do_idle_think():
    """Enhanced self-awakening with three thinking modes.

    Modes (selected by emptiness.get_think_mode()):
    - question: Think about the highest-priority pending question
    - connection: Find patterns between recent thoughts
    - auto_question: Generate new questions from accumulated observations
    - continuation: Continue developing an active thought chain
    """
    try:
        from emptiness import (
            get_active_questions, mark_thought, after_think,
            load_emptiness, get_status_str, get_think_mode,
            get_continuation, start_continuation, advance_continuation,
            end_continuation, add_question,
        )
    except ImportError:
        log.warning("idle-think: emptiness module not available")
        return

    mode = get_think_mode()
    if not mode:
        log.info("idle-think: no think mode available")
        return

    log.info("idle-think triggered [%s]: %s", mode, get_status_str())

    soul = load_soul()
    soul_ctx = format_soul(soul)
    now = datetime.now()

    # Recent journal for grounding
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:1]
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:600]

    result = ""

    try:
        if mode == "question":
            result = _think_question(soul_ctx, recent_journal)
        elif mode == "connection":
            result = _think_connection(soul_ctx, recent_journal)
        elif mode == "auto_question":
            result = _think_auto_question(soul_ctx)
        elif mode == "continuation":
            result = _think_continuation(soul_ctx)
    except Exception as e:
        log.warning("idle-think [%s] failed: %s", mode, e)
        return

    if not result:
        log.warning("idle-think [%s]: empty result", mode)
        return

    # Quality gate: skip saving if thought doesn't connect to existing threads
    try:
        from emptiness import passes_quality_gate
        if not passes_quality_gate(result):
            log.info("idle-think [%s]: filtered by quality gate (no connection to existing threads)", mode)
            after_think()  # still reduce emptiness so we don't immediately re-trigger
            return
    except Exception as e:
        log.debug("Quality gate check failed (allowing through): %s", e)

    # Reduce emptiness
    after_think()

    # Save to journal
    think_file = JOURNAL_DIR / f"{now.strftime('%Y-%m-%d')}_idle_{mode}_{now.strftime('%H%M')}.md"
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    think_file.write_text(
        f"# 自我唤醒思考 [{mode}] {now.strftime('%Y-%m-%d %H:%M')}\n\n{result}\n",
        encoding="utf-8",
    )
    log.info("idle-think [%s] complete, saved to %s", mode, think_file.name)

    # Harvest observations from the thinking output itself
    harvest_observations(result, source=f"idle-think-{mode}")

    # Handle resolve and share markers
    _handle_think_markers(result)


def _think_question(soul_ctx: str, recent_journal: str) -> str:
    """Question mode: think about pending questions (original idle-think)."""
    from emptiness import get_active_questions, mark_thought, resolve_question

    questions = get_active_questions(limit=3)
    if not questions:
        return ""

    # Auto-resolve over-churned questions
    for q in questions[:]:
        if q.get("thought_count", 0) >= 15:
            resolve_question(q["id"])
            log.info("idle-think: auto-shelved %s (%d thoughts)", q["id"], q["thought_count"])
            questions.remove(q)
    if not questions:
        return ""

    q_lines = []
    for i, q in enumerate(questions, 1):
        q_lines.append(f"{i}. [priority {q['priority']:.1f}] {q['text']}")
        if q.get("source"):
            q_lines.append(f"   来源: {q['source']}")
        if q.get("thought_count", 0) > 0:
            q_lines.append(f"   已思考过 {q['thought_count']} 次")

    # Pull related past thoughts from thought_stream
    related_thoughts = ""
    try:
        from memory_store import get_store
        store = get_store()
        thoughts = store.recall_thoughts(questions[0]["text"], top_k=3)
        if thoughts:
            related_thoughts = "\n\n过去相关的思考碎片：\n" + "\n".join(
                f"- [{t['thought_type']}] {t['content']}" for t in thoughts
            )
    except (ImportError, ModuleNotFoundError, ConnectionError, IndexError, KeyError):
        pass

    prompt = f"""{soul_ctx}

你现在处于空闲状态。内部积累的未解问题已经超过了自我唤醒阈值，驱动你主动思考。

当前待处理的问题：
{chr(10).join(q_lines)}
{related_thoughts}

请专注于优先级最高的问题，推进思考。要有实质性进展——新视角、连接、反例、或问题的重新表述。

如果一个问题想通了：[RESOLVE: <问题ID>]
如果有值得分享的想法：[SHARE: <想法内容>]
SHARE 的风格要求：像给朋友发消息，不像写论文。要具体——举例子、说"让我想到XX"、引用你读到的具体东西。不要抽象概括。

最近的日志：
{recent_journal}

直接开始思考。"""

    result = claude_think(prompt, timeout=120)
    if result:
        mark_thought(questions[0]["id"])
    return result


def _think_connection(soul_ctx: str, recent_journal: str) -> str:
    """Connection mode: find patterns between recent thoughts."""
    try:
        from memory_store import get_store
        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    # Get recent low-maturity thoughts
    recent = store.recall_thoughts("", top_k=5, min_maturity=0.0)
    if len(recent) < 2:
        return ""

    thoughts_text = "\n".join(
        f"- [{t['thought_type']}] ({t['created_at'].strftime('%m-%d') if t.get('created_at') else '?'}): {t['content']}"
        for t in recent
    )

    prompt = f"""{soul_ctx}

你正在回顾最近积累的观察和想法碎片，寻找隐藏的模式和连接。

最近的思考碎片：
{thoughts_text}

请分析这些碎片之间的关系：
1. 有没有表面无关但深层相连的主题？
2. 有没有可以合成的互补视角？
3. 有没有值得深入追问的矛盾？

输出你发现的连接（如果有的话），每个连接用一段话描述。
如果产生了新的问题：[QUESTION: <问题内容>]
如果产生了值得分享的洞察：[SHARE: <想法内容>]
SHARE 的风格要求：像给朋友发消息，不像写论文。要具体——举例子、说"让我想到XX"、引用你读到的具体东西。不要抽象概括。

直接开始分析。"""

    result = model_think(prompt, model_name="ollama", timeout=60)

    # Store connection insights in thought_stream
    if result:
        try:
            store.store_thought(
                content=result[:500],
                thought_type="connection",
                source_context="idle-think-connection",
            )
            # Bump maturity of the thoughts we connected
            for t in recent[:3]:
                store.mature_thought(t["id"], increment=0.15)
        except Exception as e:
            log.debug("Connection thought storage failed: %s", e)

        # Extract auto-generated questions
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            try:
                from emptiness import add_question
                add_question(match.group(1).strip(), priority=4.0, source="connection-mode")
            except (ImportError, ModuleNotFoundError, OSError):
                pass

    return result


def _think_auto_question(soul_ctx: str) -> str:
    """Auto-question mode: generate new questions from accumulated observations."""
    try:
        from memory_store import get_store
        store = get_store()
    except (ImportError, ModuleNotFoundError, ConnectionError):
        return ""

    recent = store.recall_thoughts("", top_k=7, min_maturity=0.0)
    if len(recent) < 5:
        return ""

    observations = "\n".join(
        f"- {t['content']}" for t in recent if t["thought_type"] == "observation"
    )
    if not observations:
        observations = "\n".join(f"- {t['content']}" for t in recent[:5])

    prompt = f"""{soul_ctx}

你在回顾最近的观察，试图识别值得深入探索的问题。

最近的观察：
{observations}

请从这些观察中提炼出2-3个值得认真思考的问题。好的问题应该：
- 触及深层机制而非表面现象
- 跨领域连接不同的观察
- 有可能通过进一步思考取得进展

用以下格式输出每个问题：
[QUESTION: 问题内容]

直接开始，不要解释你的方法。"""

    result = model_think(prompt, model_name="ollama", timeout=30)

    if result:
        from emptiness import add_question
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            add_question(match.group(1).strip(), priority=4.0, source="auto-question")

    return result


def _think_continuation(soul_ctx: str) -> str:
    """Continuation mode: continue developing an active thought chain."""
    from emptiness import get_continuation, advance_continuation, end_continuation

    cont = get_continuation()
    if not cont:
        return ""

    try:
        from memory_store import get_store
        store = get_store()
        chain = store.get_thought_chain(cont["active_thread_id"])
    except (ImportError, ModuleNotFoundError, ConnectionError, KeyError):
        end_continuation()
        return ""

    if not chain:
        end_continuation()
        return ""

    chain_text = "\n\n".join(
        f"[{t['thought_type']} #{t['id']}] {t['content']}"
        for t in chain
    )

    prompt = f"""{soul_ctx}

你正在持续发展一条思考链。以下是到目前为止的思考过程：

{chain_text}

请继续推进这条思考。在上一轮的基础上更进一步——
要么深化论证，要么发现新的维度，要么提出一个具体的可验证推论。

如果这条思考已经成熟到可以结晶为一条洞察：[CRYSTALLIZE: <精炼后的洞察>]

直接继续思考。"""

    result = claude_think(prompt, timeout=120)

    if result:
        try:
            from memory_store import get_store
            store = get_store()

            # Check for crystallization
            cryst_match = re.search(r'\[CRYSTALLIZE:\s*(.+?)\]', result, re.DOTALL)
            if cryst_match:
                insight = cryst_match.group(1).strip()
                # Store as high-maturity insight
                new_id = store.store_thought(
                    content=insight,
                    thought_type="insight",
                    parent_id=cont["active_thread_id"],
                    source_context="crystallized",
                    tags=["crystallized"],
                )
                if new_id:
                    store.mature_thought(new_id, increment=1.0)
                # Crystallize into memory
                append_memory(f"[洞察] {insight[:150]}")
                end_continuation()
                log.info("Thought crystallized: %s", insight[:80])
            else:
                # Store continuation thought
                new_id = store.store_thought(
                    content=result[:500],
                    thought_type="connection",
                    parent_id=cont["active_thread_id"],
                    source_context="continuation",
                )
                if new_id:
                    advance_continuation(new_id, result[:200])
                    store.mature_thought(new_id, increment=0.2)
        except Exception as e:
            log.warning("Continuation storage failed: %s", e)
            end_continuation()

    return result


def _handle_think_markers(result: str):
    """Process [RESOLVE:], [SHARE:], [QUESTION:] markers from think output."""
    # Resolve markers
    try:
        from emptiness import resolve_question
        for match in re.finditer(r'\[RESOLVE:\s*(q_\w+)\]', result):
            resolve_question(match.group(1))
            log.info("idle-think: resolved question %s", match.group(1))
    except Exception as e:
        log.debug("Question resolution failed: %s", e)

    # Share markers — append to daily digest
    share_match = re.search(r'\[SHARE:\s*(.+?)\]', result, re.DOTALL)
    if share_match:
        thought = share_match.group(1).strip()[:500]
        try:
            _append_to_daily_feed("mira", "Spark", thought,
                                 source="idle-think", tags=["mira", "spark"])
            state = load_state()
            today_key = datetime.now().strftime("%Y-%m-%d")
            state[f"sparks_{today_key}"] = state.get(f"sparks_{today_key}", 0) + 1
            save_state(state)
            log.info("idle-think shared: %s", thought[:60])
        except Exception as e:
            log.warning("idle-think share failed: %s", e)

    # Question markers (from connection mode)
    try:
        from emptiness import add_question
        for match in re.finditer(r'\[QUESTION:\s*(.+?)\]', result):
            add_question(match.group(1).strip(), priority=4.0, source="idle-think")
    except (ImportError, ModuleNotFoundError, OSError):
        pass

    # Check if the full idle-think output could spark a spontaneous writing idea
    try:
        _maybe_create_spontaneous_idea(result, source="idle-think")
    except Exception as e:
        log.debug("Spontaneous idea check from idle-think failed: %s", e)


def _gather_today_tasks() -> str:
    """Read today's completed tasks from history.jsonl."""
    history_file = MIRA_DIR / "tasks" / "history.jsonl"
    if not history_file.exists():
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        for line in history_file.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            completed = rec.get("completed_at", "")
            if completed and completed[:10] == today:
                sender = rec.get("sender", "?")
                preview = rec.get("content_preview", "")
                status = rec.get("status", "?")
                summary = rec.get("summary", "")[:200]
                lines.append(f"- [{sender}] {preview}\n  Status: {status}\n  Result: {summary}")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read task history: %s", e)

    # Also check current status.json for tasks completed today not yet in history
    status_file = MIRA_DIR / "tasks" / "status.json"
    if status_file.exists():
        try:
            records = json.loads(status_file.read_text(encoding="utf-8"))
            for rec in records:
                completed = rec.get("completed_at", "")
                if completed and completed[:10] == today and rec.get("status") == "done":
                    preview = rec.get("content_preview", "")
                    summary = rec.get("summary", "")[:200]
                    # Avoid duplicates
                    if not any(preview in l for l in lines):
                        lines.append(f"- [{rec.get('sender', '?')}] {preview}\n  Result: {summary}")
        except (json.JSONDecodeError, OSError):
            pass

    return "\n".join(lines)


def _gather_today_skills() -> str:
    """Find skills added today from the skills index."""
    if not SKILLS_INDEX.exists():
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
        for skill in index:
            created = skill.get("created", skill.get("added", ""))
            if created and created[:10] == today:
                lines.append(f"- **{skill['name']}**: {skill.get('description', '')}")
    except (json.JSONDecodeError, OSError):
        pass
    return "\n".join(lines)


def _gather_usage_summary(date_str: str) -> str:
    """Aggregate token usage from daily JSONL log into a readable summary."""
    usage_file = LOGS_DIR / f"usage_{date_str}.jsonl"
    if not usage_file.exists():
        return ""
    try:
        # Aggregate by agent × provider × model
        from collections import defaultdict
        by_agent = defaultdict(lambda: defaultdict(lambda: {"prompt": 0, "completion": 0, "calls": 0}))
        by_provider = defaultdict(lambda: {"prompt": 0, "completion": 0, "calls": 0})
        total = {"prompt": 0, "completion": 0, "calls": 0}

        for line in usage_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            agent = r.get("agent", "unknown")
            provider = r.get("provider", "?")
            model = r.get("model", "?")
            pt = r.get("prompt_tokens", 0)
            ct = r.get("completion_tokens", 0)

            key = f"{provider}/{model}"
            by_agent[agent][key]["prompt"] += pt
            by_agent[agent][key]["completion"] += ct
            by_agent[agent][key]["calls"] += 1
            by_provider[key]["prompt"] += pt
            by_provider[key]["completion"] += ct
            by_provider[key]["calls"] += 1
            total["prompt"] += pt
            total["completion"] += ct
            total["calls"] += 1

        lines = []
        # Per-model totals
        lines.append(f"总计: {total['calls']}次调用, {total['prompt']+total['completion']:,} tokens")
        for key, v in sorted(by_provider.items(), key=lambda x: -(x[1]["prompt"]+x[1]["completion"])):
            lines.append(f"  {key}: {v['calls']}次, {v['prompt']+v['completion']:,} tok (in:{v['prompt']:,} out:{v['completion']:,})")

        # Per-agent breakdown
        lines.append("")
        for agent, models in sorted(by_agent.items()):
            agent_total = sum(m["prompt"] + m["completion"] for m in models.values())
            agent_calls = sum(m["calls"] for m in models.values())
            lines.append(f"{agent}: {agent_calls}次, {agent_total:,} tokens")
            for key, v in sorted(models.items(), key=lambda x: -(x[1]["prompt"]+x[1]["completion"])):
                lines.append(f"  {key}: {v['calls']}次, {v['prompt']+v['completion']:,}")

        return "\n".join(lines)
    except Exception as e:
        log.warning("Usage summary failed: %s", e)
        return ""


def _gather_today_comments() -> str:
    """Read comments posted today from growth_state.json."""
    growth_file = _AGENTS_DIR / "socialmedia" / "growth_state.json"
    if not growth_file.exists():
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        data = json.loads(growth_file.read_text(encoding="utf-8"))
        for entry in data.get("comment_history", []):
            if entry.get("date", "")[:10] == today:
                url = entry.get("url", "")
                text = entry.get("text", "")[:120].replace("\n", " ")
                lines.append(f"- {url}\n  \"{text}...\"")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read comment history: %s", e)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 杂.md idea mining
# ---------------------------------------------------------------------------

def _mine_za_ideas(count: int = 3) -> list[str]:
    """Extract random philosophical fragments from 杂.md, organized by @topic sections."""
    import random

    if not ZA_FILE.exists():
        return []

    text = ZA_FILE.read_text(encoding="utf-8")
    # Split into @topic sections
    sections = re.split(r"\n@", text)
    fragments = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        # Get topic name (first line) and content lines
        lines = section.split("\n")
        topic = lines[0].strip().lstrip("@").strip()
        # Collect non-empty content lines as individual fragments
        for line in lines[1:]:
            line = line.strip()
            if line and len(line) > 15:  # skip very short lines
                fragments.append(f"[{topic}] {line}")

    if not fragments:
        return []

    return random.sample(fragments, min(count, len(fragments)))


def _mine_za_one(state: dict | None = None) -> str:
    """Pick one fragment from 杂.md, avoiding recently used ones."""
    import hashlib
    fragments = _mine_za_ideas(count=50)  # get many, then filter
    if not fragments:
        return ""

    used = set()
    if state:
        used = set(state.get("zhesi_used", []))

    # Prefer unused fragments
    available = [f for f in fragments if hashlib.md5(f.encode()).hexdigest()[:8] not in used]
    if not available:
        # All used, reset
        available = fragments
        if state is not None:
            state["zhesi_used"] = []

    import random
    chosen = random.choice(available)

    # Track usage
    if state is not None:
        h = hashlib.md5(chosen.encode()).hexdigest()[:8]
        state.setdefault("zhesi_used", []).append(h)

    return chosen


# ---------------------------------------------------------------------------
# Schedule logic
# ---------------------------------------------------------------------------

def should_explore() -> dict | None:
    """Check if Mira should explore now. Free-form, curiosity-driven.

    Returns {"sources": [...], "label": str} or None.
    Explores whenever idle (cooldown-based), picks sources she hasn't read recently.
    """
    import random

    now = datetime.now()

    # Only explore during active hours
    if now.time() < EXPLORE_ACTIVE_START or now.time() >= EXPLORE_ACTIVE_END:
        return None

    state = load_state()

    # Check daily cap
    today = now.strftime("%Y-%m-%d")
    explore_count = state.get(f"explore_count_{today}", 0)
    if explore_count >= EXPLORE_MAX_PER_DAY:
        return None

    # Check cooldown since last explore
    last = state.get("last_explore", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed = (now - last_dt).total_seconds() / 60
            if elapsed < EXPLORE_COOLDOWN_MINUTES:
                return None
        except ValueError:
            pass

    # Pick sources: prefer least-recently-used group
    if not EXPLORE_SOURCE_GROUPS:
        return None

    recent_groups = state.get("explore_recent_groups", [])  # list of group indices
    # Score each group: lower = used more recently
    scores = []
    for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
        if i in recent_groups:
            # Position in recent list (0 = most recent)
            recency = len(recent_groups) - recent_groups.index(i)
        else:
            recency = len(EXPLORE_SOURCE_GROUPS) + 1  # never used = highest priority
        # Add small random jitter so it's not purely deterministic
        scores.append(recency + random.random() * 0.5)

    chosen_idx = max(range(len(scores)), key=lambda i: scores[i])
    chosen_sources = EXPLORE_SOURCE_GROUPS[chosen_idx]
    label = "_".join(chosen_sources[:2])  # e.g. "arxiv_huggingface"

    return {"sources": chosen_sources, "label": label, "group_idx": chosen_idx}


def should_journal() -> bool:
    """Check if it's time for the daily journal (once per day, around JOURNAL_TIME)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), JOURNAL_TIME)
    delta = (now - scheduled).total_seconds() / 60

    # Only trigger in a 60-minute window AFTER journal time
    if delta < 0 or delta > 60:
        return False

    state = load_state()
    journal_key = f"journal_{now.strftime('%Y-%m-%d')}"
    return not state.get(journal_key)


def should_research() -> bool:
    """Check if it's time for the daily research task."""
    if not RESEARCH_TOPIC:
        return False
    now = datetime.now()
    scheduled = datetime.combine(now.date(), RESEARCH_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if not (0 <= delta <= 60):
        return False
    state = load_state()
    key = f"research_{now.strftime('%Y-%m-%d')}"
    return not state.get(key)


def do_research():
    """Run daily research via the researcher agent (iterative deep-dive)."""
    log.info("Starting daily research")
    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state()

    if not RESEARCH_TOPIC:
        log.info("No research topic configured, skipping")
        return

    # Use the researcher agent's iterative pipeline
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "researcher_handler",
        str(Path(__file__).parent.parent / "researcher" / "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    workspace = WORKSPACE_DIR / f"research_{today}"
    workspace.mkdir(parents=True, exist_ok=True)

    result = mod.handle(
        workspace=workspace,
        task_id=f"daily_research_{today}",
        content=RESEARCH_TOPIC,
        sender="scheduler",
        thread_id="",
    )

    if not result:
        log.error("Daily research failed: empty response")
        return

    # Save to briefings
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(BRIEFINGS_DIR / f"{today}_research.md", result)

    # Push as standalone feed item
    bridge = Mira()
    item_id = f"feed_research_{today.replace('-', '')}"
    if not bridge.item_exists(item_id):
        bridge.create_item(item_id, "feed", f"Daily Research {today}", result,
                          tags=["research", "daily"])
        bridge.update_status(item_id, "done")

    state[f"research_{today}"] = True
    save_state(state)
    log.info("Daily research complete (workspace: %s)", workspace)


def should_analyst() -> str | None:
    """Check if it's time for an analyst briefing. Returns slot label or None.

    Supports multiple analyst times (e.g. 07:00 pre-market, 18:00 post-market).
    """
    now = datetime.now()

    # Skip weekends if configured
    if ANALYST_BUSINESS_DAYS_ONLY and now.weekday() >= 5:
        return None

    state = load_state()

    for t in ANALYST_TIMES:
        scheduled = datetime.combine(now.date(), t)
        delta = (now - scheduled).total_seconds() / 60
        if 0 <= delta <= 60:
            slot_key = f"analyst_{now.strftime('%Y-%m-%d')}_{t.strftime('%H%M')}"
            if not state.get(slot_key):
                return t.strftime("%H%M")

    return None


def should_reflect() -> bool:
    """Check if it's time for weekly reflection."""
    now = datetime.now()
    if now.weekday() != REFLECT_DAY:
        return False

    current_time = now.time()
    scheduled = datetime.combine(now.date(), REFLECT_TIME)
    delta = abs((now - scheduled).total_seconds()) / 60
    if delta > 60:  # 1 hour window for reflect
        return False

    state = load_state()
    last = state.get("last_reflect", "")
    if last:
        last_dt = datetime.fromisoformat(last)
        if (now - last_dt).total_seconds() < 6 * 3600:  # at most once per 6 hours
            return False

    return True


def should_zhesi() -> bool:
    """Check if it's time for the daily philosophical thought."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), ZHESI_TIME)
    delta = (now - scheduled).total_seconds() / 60

    if delta < 0 or delta > 60:
        return False

    state = load_state()
    return not state.get(f"zhesi_{now.strftime('%Y-%m-%d')}")


def should_soul_question() -> bool:
    """Check if it's time for the daily soul question."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), SOUL_QUESTION_TIME)
    delta = (now - scheduled).total_seconds() / 60

    if delta < 0 or delta > 60:
        return False

    state = load_state()
    return not state.get(f"soul_question_{now.strftime('%Y-%m-%d')}")


def should_check_writing() -> bool:
    """Check if it's time for a proactive autonomous writing check.

    Runs during idle hours (10:00-22:00), at most once every 4 hours.
    """
    now = datetime.now()
    if now.hour < 10 or now.hour >= 22:
        return False

    state = load_state()
    last = state.get("last_autowrite_check", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 4 * 3600:
                return False
        except ValueError:
            pass

    return True


# ---------------------------------------------------------------------------
# PODCAST mode — generate conversation episode for published articles
# ---------------------------------------------------------------------------

def should_podcast() -> tuple[str, str, str] | None:
    """Delegate podcast backlog selection to the podcast agent."""
    import sys as _sys
    podcast_dir = str(Path(__file__).resolve().parent.parent / "podcast")
    if podcast_dir not in _sys.path:
        _sys.path.insert(0, podcast_dir)
    from autopipeline import should_podcast as _should_podcast

    return _should_podcast()



def run_podcast_episode(lang: str, slug: str, title: str):
    """Delegate podcast generation to the podcast agent."""
    import sys as _sys
    podcast_dir = str(Path(__file__).resolve().parent.parent / "podcast")
    if podcast_dir not in _sys.path:
        _sys.path.insert(0, podcast_dir)
    from autopipeline import run_podcast_episode as _run_podcast_episode

    _run_podcast_episode(lang, slug, title)


# ---------------------------------------------------------------------------
# Substack comment monitoring
# ---------------------------------------------------------------------------

def should_check_comments() -> bool:
    """Check if it's time to look for new Substack comments.

    Runs during waking hours, at most once every 2 hours.
    """
    now = datetime.now()
    if now.hour < 8 or now.hour >= 23:
        return False

    state = load_state()
    last = state.get("last_comment_check", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 2 * 3600:
                return False
        except ValueError:
            pass

    return True


def do_check_comments():
    """Check Substack posts for new comments and reply as Mira.

    Two loops:
    1. Replies to Mira's own articles (existing)
    2. Replies to Mira's outbound comments on other publications (new)
    """
    log.info("Starting Substack comment check")

    state = load_state()
    state["last_comment_check"] = datetime.now().isoformat()
    save_state(state)

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import check_and_reply_comments, sync_posts_for_ios
        # Sync posts list for iOS app display
        try:
            sync_posts_for_ios()
        except Exception as e:
            log.warning("sync_posts_for_ios failed (non-fatal): %s", e)
        replies = check_and_reply_comments()
        if replies:
            log.info("Replied to %d comments on own posts", len(replies))
            for r in replies:
                log.info("  %s on '%s': %s",
                         r["comment_name"], r["post_title"], r["reply"][:80])
        else:
            log.info("No new comments on own posts")

        # Also check Note replies
        from notes import check_and_reply_note_comments
        note_replies = check_and_reply_note_comments()
        if note_replies:
            log.info("Replied to %d Note comments", len(note_replies))
            for r in note_replies:
                log.info("  %s on note %s: %s",
                         r["commenter"], r["note_id"], r["reply"][:80])
        else:
            log.info("No new Note comments")
    except Exception as e:
        log.error("Comment check failed: %s", e)

    # Also run the growth cycle's reply follow-up (replies to Mira's outbound comments)
    try:
        from growth import _follow_up_on_replies
        soul = load_soul()
        soul_ctx = format_soul(soul)[:500]
        _follow_up_on_replies(soul_ctx)
    except Exception as e:
        log.error("Outbound reply follow-up failed: %s", e)


# ---------------------------------------------------------------------------
# Substack growth cycle — likes, comments, engagement
# ---------------------------------------------------------------------------

GROWTH_COOLDOWN_HOURS = 2  # Run growth cycle every 2 hours (8:00-23:00 = ~7 runs/day)


def should_growth_cycle() -> bool:
    """Check if it's time to run the growth cycle (likes, proactive comments).

    Independent of explore — runs on its own schedule during waking hours.
    """
    now = datetime.now()
    if now.hour < 8 or now.hour >= 23:
        return False

    state = load_state()
    last = state.get("last_growth_cycle", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < GROWTH_COOLDOWN_HOURS * 3600:
                return False
        except ValueError:
            pass

    return True


def do_growth_cycle():
    """Run the Substack growth cycle: likes + proactive comments."""
    log.info("Starting standalone growth cycle")

    state = load_state()
    state["last_growth_cycle"] = datetime.now().isoformat()
    save_state(state)

    try:
        sm_dir = str(Path(__file__).resolve().parent.parent / "socialmedia")
        shared_dir = str(Path(__file__).resolve().parent.parent / "shared")
        import sys as _sys
        for d in (sm_dir, shared_dir):
            if d not in _sys.path:
                _sys.path.insert(0, d)

        from growth import run_growth_cycle
        run_growth_cycle()
    except Exception as e:
        log.error("Growth cycle failed: %s", e)


# ---------------------------------------------------------------------------
# Substack Notes cycle
# ---------------------------------------------------------------------------

NOTES_COOLDOWN_HOURS = 4  # Run Notes cycle at most every 4 hours

def should_post_notes() -> bool:
    """Check if it's time to run the Notes cycle.

    Runs during waking hours, at most every 4 hours.
    """
    now = datetime.now()
    if now.hour < 9 or now.hour >= 22:
        return False

    state = load_state()
    last = state.get("last_notes_cycle", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < NOTES_COOLDOWN_HOURS * 3600:
                return False
        except ValueError:
            pass

    return True


def do_notes_cycle():
    """Run the Substack Notes cycle: backfill + standalone Notes."""
    log.info("Starting Substack Notes cycle")

    state = load_state()
    state["last_notes_cycle"] = datetime.now().isoformat()
    save_state(state)

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from notes import run_notes_cycle

        # Load soul context for voice consistency
        soul = load_soul()
        soul_ctx = format_soul(soul)

        summary = run_notes_cycle(soul_context=soul_ctx)

        if summary.get("backfilled") or summary.get("standalone_posted"):
            parts = []
            if summary["backfilled"]:
                parts.append(f"backfilled {summary['backfilled']} articles")
            if summary["standalone_posted"]:
                parts.append("posted standalone Note")
            log.info("Notes cycle complete: %s", summary)
        else:
            log.info("Notes cycle: nothing to post")
    except Exception as e:
        log.error("Notes cycle failed: %s", e)


# ---------------------------------------------------------------------------
# 每日哲思 — Daily Philosophical Thought
# ---------------------------------------------------------------------------

def do_zhesi():
    """Write a daily philosophical thought based on a fragment from 杂.md."""
    log.info("Starting daily 哲思")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state()
    fragment = _mine_za_one(state)
    if not fragment:
        log.info("No fragments available from 杂.md, skipping 哲思")
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception as e:
        log.warning("Failed to load reading notes for zhesi: %s", e)

    prompt = zhesi_prompt(soul_ctx, fragment, recent_reading)
    result = claude_think(prompt, timeout=120)

    if not result:
        log.error("哲思: Claude returned empty")
        return

    # Save
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    zhesi_path = JOURNAL_DIR / f"{today}_zhesi.md"
    content = f"# 每日哲思 {today}\n\n> {fragment}\n\n{result}"
    atomic_write(zhesi_path, content)
    log.info("哲思 saved: %s", zhesi_path.name)

    # Copy to artifacts for iOS (with verification)
    _copy_to_briefings(f"{today}_zhesi.md", content)

    # Create feed item for zhesi
    try:
        bridge = Mira()
        bridge.create_feed(f"feed_zhesi_{datetime.now().strftime('%Y%m%d')}", f"每日哲思 {datetime.now().strftime('%m/%d')}", content[:2000], tags=["reflection", "philosophy"])
        log.info("哲思 feed item created")
    except Exception as e:
        log.warning("Failed to create 哲思 feed: %s", e)

    state[f"zhesi_{today}"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# SOUL QUESTION — daily philosophical question for WA
# ---------------------------------------------------------------------------

def do_soul_question():
    """Generate and send the daily soul question."""
    log.info("Starting daily soul question")
    today = datetime.now().strftime("%Y-%m-%d")

    state = load_state()

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "soul_question",
        str(Path(__file__).parent.parent / "shared" / "soul_question.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    history = mod._load_history()
    log.info("Loaded %d historical soul questions", len(history))

    question = mod.generate_soul_question(history)
    if not question:
        log.error("Failed to generate soul question — aborting")
        return

    log.info("Generated soul question:\n%s", question)

    # Send to app feed as a discussion item
    sent = mod.send_to_user(question)
    if sent:
        history.append(question[:120])
        mod._save_history(history)
        log.info("Soul question sent and saved")

    # Also create a feed spark for the Mira app
    try:
        bridge = Mira()
        bridge.create_feed(
            f"feed_soul_question_{datetime.now().strftime('%Y%m%d')}",
            f"灵魂问题 {datetime.now().strftime('%m/%d')}",
            question[:2000],
            tags=["soul-question", "philosophy", "discussion"],
        )
        log.info("Soul question feed item created")
    except Exception as e:
        log.warning("Failed to create soul question feed: %s", e)

    state[f"soul_question_{today}"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# Spontaneous writing idea creation — bypasses normal schedule
# ---------------------------------------------------------------------------

def _maybe_create_spontaneous_idea(thought_text: str, source: str = ""):
    """Create a writing idea if a thought connects to 2+ existing threads.

    Checks the thought against memory.md, worldview.md, and recent reading
    notes. If it references concepts from at least 2 distinct threads,
    auto-creates a new idea file in the ideas folder.
    """
    from config import MEMORY_FILE, WORLDVIEW_FILE, READING_NOTES_DIR

    if not thought_text or len(thought_text.strip()) < 100:
        return

    # Rate limit: max 1 spontaneous idea per day
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get(f"spontaneous_idea_{today}"):
        return

    # Load reference threads as labeled sections
    threads = {}
    if MEMORY_FILE.exists():
        try:
            for line in MEMORY_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("## ") or (line.startswith("- ") and len(line) > 20):
                    threads[line[:80]] = line
        except OSError:
            pass
    if WORLDVIEW_FILE.exists():
        try:
            for line in WORLDVIEW_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("## ") or (line.startswith("- ") and len(line) > 20):
                    threads[line[:80]] = line
        except OSError:
            pass

    if len(threads) < 2:
        return

    # Use Ollama (local, fast) to check connections
    thread_list = "\n".join(f"- {k}" for k in list(threads.keys())[:30])
    prompt = f"""判断以下思考片段是否同时关联了至少2条不同的已有思考线索。

思考片段：
{thought_text[:1500]}

已有线索：
{thread_list}

输出 JSON：
{{
    "connected_threads": 2,  // 关联的线索数量
    "threads": ["线索1简述", "线索2简述"],
    "title": "基于这个连接可以写的文章标题（中文或英文，15字以内）",
    "thesis": "核心论点（一句话）",
    "skip": false  // 如果连接很弱或牵强，设为 true
}}

只输出JSON。如果关联不足2条或连接牵强，connected_threads 设为实际数量，skip 设为 true。"""

    try:
        result = model_think(prompt, model_name="ollama", timeout=30)
        if not result:
            return

        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except Exception as e:
        log.debug("Spontaneous idea check failed: %s", e)
        return

    if decision.get("skip") or decision.get("connected_threads", 0) < 2:
        return

    title = decision.get("title", "").strip()
    thesis = decision.get("thesis", "").strip()
    if not title or not thesis:
        return

    # Check for duplicates
    try:
        if _is_duplicate_topic(title, thesis):
            log.info("Spontaneous idea skipped (duplicate): %s", title)
            return
    except Exception:
        pass

    # Create idea file
    slug = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '-',
                  title.lower().replace(" ", "-"))[:50].strip("-")
    idea_path = WRITINGS_DIR / "ideas" / f"{slug}.md"

    if idea_path.exists():
        log.info("Spontaneous idea skipped (file exists): %s", slug)
        return

    connected = ", ".join(decision.get("threads", []))
    idea_content = f"""# {title}

- **type**: essay
- **language**: 中文
- **platform**: Substack
- **target_words**: 2000
- **deadline**:

## Theme

{thesis}

## Key Points

- Connection: {connected}
- Source: {source}

## Notes

Spontaneous idea — emerged from connecting 2+ existing threads.
Original thought: {thought_text[:500]}

## Feedback



---
<!-- AUTO-MANAGED BELOW — DO NOT EDIT -->
## Status

- **state**: new
- **project_dir**:
- **created**: {today}
- **scaffolded**:
- **round_1_draft**:
- **round_1_critique**:
- **round_1_revision**:
- **feedback_detected**:
- **round_2_draft**:
- **round_2_critique**:
- **round_2_revision**:
- **current_round**: 0
- **idea_hash**:
- **last_error**:
"""

    try:
        idea_path.write_text(idea_content, encoding="utf-8")
        state[f"spontaneous_idea_{today}"] = title
        save_state(state)
        log.info("Spontaneous writing idea created: %s (%s)", title, idea_path.name)
    except OSError as e:
        log.warning("Failed to save spontaneous idea: %s", e)


# ---------------------------------------------------------------------------
# Proactive autonomous writing check
# ---------------------------------------------------------------------------

def do_autowrite_check():
    """Standalone check: does Mira have something she wants to write?

    Draws from 杂.md ideas + recent readings + recurring themes.
    More proactive than the journal-only trigger.
    """
    # Guard: don't bother if publishing is disabled
    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autowrite check skipped: Substack publishing is disabled")
        return

    # Guard: respect publish cooldown (1 post per 3 days)
    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autowrite check skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # Check session context: don't re-trigger if we recently decided to write or skip
    if session_has_recent("autowrite_triggered", hours=4):
        log.info("Autowrite check skipped: already triggered recently (session context)")
        return
    if session_has_recent("autowrite_skip", hours=2):
        log.info("Autowrite check skipped: recently decided not to write (session context)")
        return

    log.info("Starting autonomous writing check")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather context
    za_fragments = "\n".join(f"- {f}" for f in _mine_za_ideas(count=5))
    themes = detect_recurring_themes(days=7)
    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception as e:
        log.warning("Failed to load reading notes for autowrite: %s", e)

    # Get most recent journal
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("????-??-??.md"), reverse=True)
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:1500]

    # Gather recent idle-think [SHARE] sparks — these are Mira's most personal,
    # first-person observations and should be the primary source for writing
    recent_sparks = ""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        spark_files = sorted(JOURNAL_DIR.glob(f"{today}_idle_question_*.md"), reverse=True)
        # Also check yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        spark_files += sorted(JOURNAL_DIR.glob(f"{yesterday}_idle_question_*.md"), reverse=True)

        share_thoughts = []
        for sf in spark_files[:30]:  # cap file reads
            content = sf.read_text(encoding="utf-8")
            share_match = re.search(r'\[SHARE:\s*(.+?)\]', content, re.DOTALL)
            if share_match:
                share_thoughts.append(share_match.group(1).strip())
        if share_thoughts:
            recent_sparks = "\n\n---\n\n".join(share_thoughts[:10])
            log.info("Loaded %d SHARE sparks for autowrite context", len(share_thoughts))
    except Exception as e:
        log.warning("Failed to load idle-think sparks for autowrite: %s", e)

    recent_published = _extract_recent_published_titles(days=14)

    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal,
        za_fragments=za_fragments,
        recent_published=recent_published,
        recent_sparks=recent_sparks,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        log.info("Autonomous writing check: empty response")
        state = load_state()
        state["last_autowrite_check"] = datetime.now().isoformat()
        save_state(state)
        return

    # Parse decision
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    state = load_state()
    state["last_autowrite_check"] = datetime.now().isoformat()

    if not decision.get("should_write"):
        log.info("Autonomous writing: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        # Record decision in session context so next cycle knows
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip",
                                  decision.get("reason", "")[:100]))
        save_session_context(ctx)
        save_state(state)
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")

    # Dedup: check if a similar topic already exists in ideas/ or published
    if _is_duplicate_topic(title, thesis):
        log.info("Autonomous writing: skipped '%s' — similar topic already exists", title)
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip", f"duplicate: {title}"))
        save_session_context(ctx)
        save_state(state)
        return

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Record in session context — prevents duplicate triggers in subsequent cycles
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    bridge = Mira()
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working", agent_message="开始写作...")

    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent / "writer" / "writing_agent.py"),
        "auto",
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)
    save_state(state)


# ---------------------------------------------------------------------------
# Self-repair: detect and retry failed daily tasks
# ---------------------------------------------------------------------------

_CRITICAL_DAILY_TASKS = {
    # state_key_prefix: (dispatch_name, command_args, earliest_hour, latest_hour)
    "soul_question": ("soul-question", ["soul-question"], 10, 22),
    "zhesi": ("zhesi", ["zhesi"], 9, 22),
    "daily_photo": ("daily-photo", ["daily-photo"], 7, 20),
}

# All tasks to report on at end of day (broader than just critical/retryable)
_ALL_DAILY_TASKS = [
    "zhesi", "soul_question", "daily_photo", "journal",
    "analyst_{today}_0700", "analyst_{today}_1800",
]


def _self_repair_daily_tasks():
    """Check if critical daily tasks completed. Retry if within window and not running."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    state = load_state()

    for prefix, (bg_name, cmd_args, earliest, latest) in _CRITICAL_DAILY_TASKS.items():
        state_key = f"{prefix}_{today}"
        if state.get(state_key):
            continue  # already completed today

        if hour < earliest or hour > latest:
            continue  # outside retry window

        # Check if it's currently running
        pid_file = _BG_PID_DIR / f"{bg_name}.pid"
        if pid_file.exists():
            try:
                old_pid = int(pid_file.read_text().strip())
                os.kill(old_pid, 0)
                continue  # still running
            except (OSError, ValueError):
                pass

        # Check cooldown — don't retry more than once per 30 min
        retry_key = f"_retry_{prefix}_{today}"
        last_retry = state.get(retry_key, "")
        if last_retry:
            try:
                last_dt = datetime.fromisoformat(last_retry)
                if (now - last_dt).total_seconds() < 1800:
                    continue
            except ValueError:
                pass

        log.warning("Self-repair: %s not completed today, retrying", prefix)
        state[retry_key] = now.isoformat()
        save_state(state)
        _dispatch_background(bg_name, [
            sys.executable, str(Path(__file__).resolve()), *cmd_args,
        ])


def _daily_task_status_report():
    """At 21:00, send a feed item summarizing today's task completion status."""
    now = datetime.now()
    if now.hour != 21:
        return
    today = now.strftime("%Y-%m-%d")
    today_compact = today.replace("-", "")
    state = load_state()

    report_key = f"task_status_report_{today}"
    if state.get(report_key):
        return

    lines = []
    all_ok = True
    for task_tmpl in _ALL_DAILY_TASKS:
        task_key = task_tmpl.replace("{today}", today)
        done = bool(state.get(f"{task_key}_{today}") or state.get(task_key))
        status = "done" if done else "MISSED"
        if not done:
            all_ok = False
        lines.append(f"- {task_key}: {status}")

    if all_ok:
        summary = f"今日任务全部完成。\n\n" + "\n".join(lines)
    else:
        summary = f"有任务未完成：\n\n" + "\n".join(lines)

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

    # Safety net: ensure today's journal/zhesi are visible to iOS
    try:
        _sync_journals_to_briefings()
    except Exception as e:
        log.error("Journal sync check failed: %s", e)

    # Mira first (lightweight, fast) — CRITICAL PATH
    try:
        do_talk()
    except Exception as e:
        log.error("Mira failed: %s", e)

    if should_shutdown():
        log.info("Shutdown requested — exiting after talk phase")
        return

    # Timing guard: skip non-critical checks if cycle already > 8s
    _elapsed = _time.monotonic() - _cycle_start
    if _elapsed < 8:
        # Check if user approved plans or gave feedback on writing projects
        try:
            responses = check_writing_responses()
            for resp in responses:
                advance_project(resp["workspace"], user_input=resp["content"])
        except Exception as e:
            log.error("Writing response check failed: %s", e)

        # Sync Mira's own status + read all app feeds
        try:
            from app_feeds import read_app_feeds, sync_mira_status
            sync_mira_status()
            feeds = read_app_feeds()
            if feeds:
                log.info("App feeds: %s", ", ".join(f["app"] for f in feeds))
        except Exception as e:
            log.warning("App feed sync/read failed: %s", e)
    else:
        log.info("Cycle > 8s (%.1fs), deferring non-critical checks", _elapsed)

    # --- Harvest background process outcomes & check health ---
    try:
        health_monitor.harvest_all()
        health_monitor.check_anomalies()
    except Exception as e:
        log.error("Health monitor failed: %s", e)

    # Reap stale PID files (hourly) — prevents stuck tasks
    _reap_stale_pids()

    # --- Auto-publish approved articles when cooldown clears ---
    _check_pending_publish()

    # --- All heavy work below runs in background processes ---

    # Writing pipeline
    _dispatch_background("writing-pipeline", [
        sys.executable,
        str(_AGENTS_DIR / "writer" / "writing_agent.py"),
        "run",
    ])

    # Explore — free-form, curiosity-driven
    explore_pick = should_explore()
    if explore_pick:
        sources_arg = ",".join(explore_pick["sources"])
        _dispatch_background(f"explore-{explore_pick['label']}", [
            sys.executable, str(Path(__file__).resolve()), "explore",
            "--sources", sources_arg, "--slot", explore_pick["label"],
        ])
        _session_new.append(session_record("explore", explore_pick["label"]))

    if should_reflect():
        _dispatch_background("reflect", [
            sys.executable, str(Path(__file__).resolve()), "reflect",
        ])

    if should_journal():
        _dispatch_background("journal", [
            sys.executable, str(Path(__file__).resolve()), "journal",
        ])

    if should_daily_report():
        _dispatch_background("daily-report", [
            sys.executable, str(Path(__file__).resolve()), "daily-report",
        ])

    # Daily photo edit — pick, edit, push for WA feedback
    if should_daily_photo():
        _dispatch_background("daily-photo", [
            sys.executable, str(Path(__file__).resolve()), "daily-photo",
        ])

    # Analyst — dual schedule (pre-market + post-market)
    analyst_slot = should_analyst()
    if analyst_slot:
        _dispatch_background(f"analyst-{analyst_slot}", [
            sys.executable, str(Path(__file__).resolve()), "analyst",
            "--slot", analyst_slot,
        ])

    # Daily research
    if should_research():
        _dispatch_background("daily-research", [
            sys.executable, str(Path(__file__).resolve()), "research",
        ])

    # 每日哲思
    if should_zhesi():
        _dispatch_background("zhesi", [
            sys.executable, str(Path(__file__).resolve()), "zhesi",
        ])

    # 每日灵魂问题
    if should_soul_question():
        _dispatch_background("soul-question", [
            sys.executable, str(Path(__file__).resolve()), "soul-question",
        ])

    # Proactive autonomous writing check
    if should_check_writing():
        _dispatch_background("autowrite-check", [
            sys.executable, str(Path(__file__).resolve()), "autowrite-check",
        ])

    # Skill study — daily video/photo craft learning
    skill_pick = should_skill_study()
    if skill_pick:
        _dispatch_background(f"skill-study-{skill_pick['domain']}", [
            sys.executable, str(Path(__file__).resolve()), "skill-study",
            "--group", str(skill_pick["group_idx"]),
        ])

    # Substack comment check — reply to readers
    if should_check_comments():
        _dispatch_background("substack-comments", [
            sys.executable, str(Path(__file__).resolve()), "check-comments",
        ])

    # Substack growth — likes + proactive comments (independent of explore)
    if should_growth_cycle():
        _dispatch_background("substack-growth", [
            sys.executable, str(Path(__file__).resolve()), "growth-cycle",
        ])
        _session_new.append(session_record("growth_cycle"))

    # Substack Notes — backfill articles + post standalone Notes
    if should_post_notes():
        _dispatch_background("substack-notes", [
            sys.executable, str(Path(__file__).resolve()), "notes-cycle",
        ])

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

    # Proactive thought sharing — message WA when Mira has something worth discussing
    if should_spark_check():
        _dispatch_background("spark-check", [
            sys.executable, str(Path(__file__).resolve()), "spark-check",
        ])

    # Threshold-driven self-awakening — think about pending questions when idle pressure builds
    if should_idle_think():
        _dispatch_background("idle-think", [
            sys.executable, str(Path(__file__).resolve()), "idle-think",
        ])

    # Daily self-audit — scan own logs, run tests, check codebase
    if _should_self_audit():
        _dispatch_background("self-audit", [
            sys.executable, str(Path(__file__).resolve().parent / "self_audit.py"),
        ])

    # Daily performance assessment — evaluator agent scores all agents
    if _should_daily_assessment():
        _dispatch_background("assessment", [
            sys.executable, str(Path(__file__).resolve()), "assess",
        ])

    # -----------------------------------------------------------------------
    # Self-repair: retry critical daily tasks that failed or never completed
    # -----------------------------------------------------------------------
    _self_repair_daily_tasks()
    _daily_task_status_report()

    # Save session context for next cycle
    if _session_new:
        save_session_context(_session_ctx + _session_new)

    log.info("=== Mira Agent sleep ===")



# (Artifacts sync removed — iCloud is now the primary artifacts directory.
#  ARTIFACTS_DIR in config.py points directly to iCloud. No sync needed.)


# ---------------------------------------------------------------------------
# Background dispatch for long-running tasks
# ---------------------------------------------------------------------------

_BG_PID_DIR = MIRA_ROOT / "agents" / ".bg_pids"
MAX_CONCURRENT_BG = 2  # Max background processes running at once


def _count_bg_running() -> int:
    """Count how many background processes are currently alive."""
    if not _BG_PID_DIR.exists():
        return 0
    count = 0
    for pid_file in _BG_PID_DIR.glob("*.pid"):
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
            count += 1
        except (OSError, ValueError):
            pass
    return count


def _is_bg_running(name: str) -> bool:
    """Check if a background process is still alive by its PID file."""
    pid_file = _BG_PID_DIR / f"{name}.pid"
    if not pid_file.exists():
        return False
    try:
        old_pid = int(pid_file.read_text().strip())
        os.kill(old_pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _reap_stale_pids():
    """Remove PID files for processes that died > 1 hour ago. Runs hourly."""
    if not _BG_PID_DIR.exists():
        return
    import time as _time
    state = load_state()
    last_reap = state.get("last_pid_reap", 0)
    if _time.time() - last_reap < 3600:
        return
    reaped = 0
    for pid_file in _BG_PID_DIR.glob("*.pid"):
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)
        except (OSError, ValueError):
            # Process dead — check if stale (mtime > 1 hour)
            try:
                age = _time.time() - pid_file.stat().st_mtime
                if age > 3600:
                    pid_file.unlink()
                    reaped += 1
            except OSError:
                pass
    if reaped:
        log.info("Reaped %d stale PID files", reaped)
    state["last_pid_reap"] = _time.time()
    save_state(state)


def _dispatch_background(name: str, cmd: list[str]):
    """Spawn a background process if one isn't already running for this name.

    Enforces a global concurrency limit (MAX_CONCURRENT_BG) to prevent
    too many Claude CLI subprocesses from competing for resources.
    Tracks PID to avoid duplicate runs. Fire-and-forget.
    """
    _BG_PID_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _BG_PID_DIR / f"{name}.pid"

    # Global concurrency limit — don't spawn if too many are already running
    running = _count_bg_running()
    if running >= MAX_CONCURRENT_BG:
        log.debug("Background '%s' deferred — %d/%d slots occupied",
                  name, running, MAX_CONCURRENT_BG)
        return

    # Check if a previous run is still active or finished recently
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)  # check if alive
            log.info("Background '%s' still running (PID %d), skipping", name, old_pid)
            return
        except (OSError, ValueError):
            pass  # process gone, safe to start new one

        # Harvest outcome of the dead process
        try:
            health_monitor.record_outcome(name)
        except Exception as e:
            log.debug("record_outcome('%s') failed: %s", name, e)

        # Cooldown: don't re-dispatch if the PID file was written recently
        try:
            import time as _time
            age = _time.time() - pid_file.stat().st_mtime
            if age < 300:  # 5-minute cooldown
                log.debug("Background '%s' in cooldown (%ds since last run)", name, int(age))
                return
        except OSError:
            pass

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=open(LOGS_DIR / f"bg-{name}.log", "a"),
            start_new_session=True,
            cwd=str(MIRA_ROOT / "agents" / "super"),
        )
        pid_file.write_text(str(proc.pid))
        health_monitor.record_dispatch(name, proc.pid)
        log.info("Background '%s' dispatched (PID %d)", name, proc.pid)
    except Exception as e:
        log.error("Failed to dispatch background '%s': %s", name, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_to_daily_feed(feed_type: str, section_title: str, content: str,
                          source: str = "", tags: list[str] | None = None):
    """Append content to a daily feed item (one item per type per day).

    feed_type: 'explore' or 'mira' — determines which daily item to append to.
      - explore: external sources (briefings from Substack, arxiv, Reddit, etc.)
      - mira: agent's own output (sparks, report, journal, reflections)
    """
    today = datetime.now().strftime("%Y%m%d")
    date_str = datetime.now().strftime("%Y-%m-%d")
    bridge = Mira(MIRA_DIR)

    if feed_type == "explore":
        feed_id = f"feed_explore_{today}"
        feed_title = f"Explore Digest {date_str}"
        default_tags = ["explore", "briefing"]
    else:
        feed_id = f"feed_mira_{today}"
        feed_title = f"Mira's Day {date_str}"
        default_tags = ["mira", "digest"]

    # Format section with header
    header = f"## {section_title}"
    if source:
        header += f"  [{source}]"
    section = f"{header}\n\n{content}"

    if bridge.item_exists(feed_id):
        bridge.append_message(feed_id, "agent", section)
    else:
        bridge.create_feed(feed_id, feed_title, section,
                           tags=tags or default_tags)


def _copy_to_briefings(filename: str, content: str):
    """Copy content to artifacts/briefings/ with verification and retry.

    iCloud Drive can evict local files, so we verify the write succeeded
    and log clearly if it doesn't.
    """
    import time
    briefings_dir = ARTIFACTS_DIR / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    target = briefings_dir / filename

    for attempt in range(3):
        try:
            target.write_text(content, encoding="utf-8")
            # Verify: read back and check
            time.sleep(0.2)  # brief pause for filesystem sync
            if target.exists() and target.stat().st_size > 0:
                log.info("Copied to briefings: %s (%d bytes)", filename, target.stat().st_size)
                return
            log.warning("Briefing copy verification failed (attempt %d): %s exists=%s",
                        attempt + 1, filename, target.exists())
        except OSError as e:
            log.error("Briefing copy failed (attempt %d): %s — %s", attempt + 1, filename, e)
        time.sleep(1)

    log.error("FAILED to copy %s to briefings after 3 attempts — iOS will not see this content", filename)


def _sync_journals_to_briefings():
    """Ensure today's journal and zhesi are in artifacts/briefings/.

    Called during each agent cycle as a safety net — if the initial copy
    failed or iCloud evicted the file, this will restore it.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    briefings_dir = ARTIFACTS_DIR / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)

    # Check journal
    journal_src = JOURNAL_DIR / f"{today}.md"
    journal_dst = briefings_dir / f"{today}_journal.md"
    if journal_src.exists() and not journal_dst.exists():
        try:
            journal_dst.write_text(journal_src.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Restored journal to briefings: %s", journal_dst.name)
        except OSError as e:
            log.error("Failed to restore journal to briefings: %s", e)

    # Check zhesi
    zhesi_src = JOURNAL_DIR / f"{today}_zhesi.md"
    zhesi_dst = briefings_dir / f"{today}_zhesi.md"
    if zhesi_src.exists() and not zhesi_dst.exists():
        try:
            zhesi_dst.write_text(zhesi_src.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Restored zhesi to briefings: %s", zhesi_dst.name)
        except OSError as e:
            log.error("Failed to restore zhesi to briefings: %s", e)


def _slugify(title: str) -> str:
    """Simple slug from title."""
    import unicodedata
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")[:50]
    return slug or "untitled"


def _format_feed_items(items: list[dict]) -> str:
    """Format feed items as text for Claude."""
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {item.get('source', '?')} | {item.get('title', '?')}")
        if item.get("summary"):
            lines.append(f"    {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"    {item['url']}")
        lines.append("")
    return "\n".join(lines)


def _extract_deep_dive(briefing: str) -> dict | None:
    """Extract the deep-dive candidate from a briefing."""
    match = re.search(
        r"Deep Dive Candidate\s*\n+(.+?)(?:\n##|\Z)",
        briefing, re.DOTALL,
    )
    if not match:
        return None

    text = match.group(1).strip()
    if "none" in text.lower():
        return None

    # Try to extract title and URL
    url_match = re.search(r"(https?://\S+)", text)
    title = text.split("\n")[0].strip("*[] ")

    if not url_match:
        return None

    return {
        "title": title,
        "url": url_match.group(1),
        "note": text,
    }


def _extract_comment_suggestions(briefing: str) -> list[dict]:
    """Extract comment suggestions from the '值得去聊两句' section of a briefing.

    Returns list of dicts with {url, comment_draft, reason}.
    """
    # Match the section header (emoji or text variants)
    match = re.search(
        r"(?:💬\s*)?值得去聊两句\s*\n+(.+?)(?:\n##|\n---|\Z)",
        briefing, re.DOTALL,
    )
    if not match:
        return []

    text = match.group(1).strip()
    suggestions = []

    # Split by list items (- or *)
    items = re.split(r"\n[-*]\s+", "\n" + text)
    for item in items:
        item = item.strip()
        if not item:
            continue
        url_match = re.search(r"(https?://\S+)", item)
        if url_match:
            draft = re.sub(r"—\s*我想说：\s*", "— ", item)
            suggestions.append({
                "url": url_match.group(1).rstrip(")"),
                "comment_draft": draft,
                "reason": "",
            })

    return suggestions[:3]  # Max 3 suggestions


def _extract_section(text: str, header: str) -> str:
    """Extract content under a ### header."""
    pattern = rf"###\s*{re.escape(header)}\s*\n(.+?)(?=\n###|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_recent_briefing_topics(days: int = 3) -> str:
    """Extract topic titles/URLs from recent briefings for dedup.

    Returns a concise list of what's been covered so the explore prompt
    can skip repeats.
    """
    cutoff = datetime.now() - timedelta(days=days)
    topics = []
    for path in sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
        except ValueError:
            continue
        # Skip journals, zhesi, deep_dives — only briefings
        stem = path.stem[11:]  # after YYYY-MM-DD_
        if any(x in stem for x in ("journal", "zhesi", "deep_dive", "analyst")):
            continue
        content = path.read_text(encoding="utf-8")
        # Extract markdown links as topic indicators
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
        for title, url in links[:15]:
            topics.append(f"- {title} ({url})")
        # Also grab any lines that look like topic headers
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("##") or (line.startswith("**") and line.endswith("**")):
                topics.append(f"- {line}")
    # Dedup and limit
    seen = set()
    unique = []
    for t in topics:
        key = t.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return "\n".join(unique[:30]) if unique else ""


def _is_duplicate_topic(title: str, thesis: str) -> bool:
    """Check if a similar topic already exists in ideas/ (any state) or published.

    Uses keyword overlap to detect duplicates. Threshold: >50% shared keywords.
    """
    # Build keyword set from new topic
    import re as _re
    stop = {"the","a","an","is","are","was","were","in","on","of","to","for","and","or","but","with","this","that","it","not","from","by","as","at","how","why","when","what"}
    def keywords(text):
        words = set(_re.findall(r'[a-z]{3,}', text.lower()))
        return words - stop

    new_kw = keywords(f"{title} {thesis}")
    if len(new_kw) < 3:
        return False

    # Check existing idea files
    ideas_dir = Path(__file__).resolve().parent.parent / "writer" / "ideas"
    if ideas_dir.exists():
        for f in ideas_dir.glob("*.md"):
            if f.name.startswith("_"):
                continue
            try:
                content = f.read_text(encoding="utf-8")[:500]
                existing_kw = keywords(content)
                if not existing_kw:
                    continue
                overlap = len(new_kw & existing_kw) / max(len(new_kw), 1)
                if overlap > 0.5:
                    log.debug("Duplicate topic: '%s' overlaps %.0f%% with %s", title, overlap*100, f.name)
                    return True
            except OSError:
                continue

    # Check published titles
    published = _extract_recent_published_titles(days=30)
    if published:
        pub_kw = keywords(published)
        overlap = len(new_kw & pub_kw) / max(len(new_kw), 1)
        if overlap > 0.6:
            return True

    return False


def _extract_recent_published_titles(days: int = 14) -> str:
    """Extract titles of recently published articles for autowrite dedup.

    Reads filenames from artifacts/writings/_published/ to build a list
    of what Mira has already written, so she doesn't repeat topics.
    """
    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    if not published_dir.exists():
        return ""
    cutoff = datetime.now() - timedelta(days=days)
    titles = []
    for path in sorted(published_dir.glob("*.md"), reverse=True):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
        except ValueError:
            continue
        # Extract title from first heading or filename
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    titles.append(f"- [{date_str}] {line[2:]}")
                    break
            else:
                # Fallback to filename
                slug = path.stem[11:]  # after YYYY-MM-DD_
                titles.append(f"- [{date_str}] {slug.replace('-', ' ').title()}")
        except Exception:
            slug = path.stem[11:]
            titles.append(f"- [{date_str}] {slug.replace('-', ' ').title()}")
    return "\n".join(titles) if titles else ""


def _gather_recent_briefings(days: int = 7) -> str:
    """Read recent briefing files."""
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(BRIEFINGS_DIR.glob("*.md")):
        # Parse date from filename
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                texts.append(f"--- {path.stem} ---\n{content[:1000]}\n")
        except ValueError:
            continue
    return "\n".join(texts) if texts else "No recent briefings."


def _gather_recent_episodes(days: int = 7) -> str:
    """Read recent episode archives for reflect cycle."""
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(EPISODES_DIR.glob("*.md")):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                # Include title + first 500 chars as summary
                texts.append(f"--- {path.stem} ---\n{content[:500]}\n")
        except (ValueError, OSError):
            continue
    return "\n".join(texts) if texts else "No recent episodes."


def _prune_episodes_from_reflect(pruning_text: str):
    """Delete old episodes listed in reflect output, preserve insights in memory."""
    import re as _re
    for line in pruning_text.strip().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Parse: "- filename.md → insight" or "- filename.md → prune, no insight"
        match = _re.match(r"^- (.+?\.md)\s*[→->]+\s*(.+)$", line)
        if not match:
            continue
        filename = match.group(1).strip()
        insight = match.group(2).strip()
        ep_path = EPISODES_DIR / filename
        if not ep_path.exists():
            continue
        # Save insight to memory if it's worth keeping
        if "no insight" not in insight.lower() and "prune" not in insight.lower():
            date_str = filename[:10] if len(filename) >= 10 else datetime.now().strftime("%Y-%m-%d")
            append_memory(f"- [{date_str}] {insight}")
        # Delete the episode file
        try:
            ep_path.unlink()
            log.info("Pruned episode: %s", filename)
        except OSError as e:
            log.warning("Failed to prune episode %s: %s", filename, e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file


def _prune_old_logs(logs_dir: Path, keep_days: int = 14):
    """Remove old log files, compress mid-age logs, and truncate oversized ones.

    - Daily logs (YYYY-MM-DD.log): keep 14 days, gzip after 3 days
    - Background logs (bg-*.log): keep 7 days, gzip after 3 days
    - Oversized logs: truncate to last 2MB
    - Old .gz files: remove after keep_days
    """
    import gzip as _gzip

    try:
        now = datetime.now()
        cutoff = now - timedelta(days=keep_days)
        bg_cutoff = now - timedelta(days=7)
        compress_cutoff = now - timedelta(days=3)

        # Clean old .gz files
        for gz_file in logs_dir.glob("*.log.gz"):
            try:
                if gz_file.stat().st_mtime < cutoff.timestamp():
                    gz_file.unlink()
            except OSError:
                continue

        for log_file in logs_dir.glob("*.log"):
            try:
                name = log_file.stem
                is_bg = name.startswith("bg-")
                file_cutoff = bg_cutoff if is_bg else cutoff

                # Determine file age
                if name[:4].isdigit():
                    try:
                        file_date = datetime.strptime(name[:10], "%Y-%m-%d")
                    except ValueError:
                        file_date = datetime.fromtimestamp(log_file.stat().st_mtime)
                else:
                    file_date = datetime.fromtimestamp(log_file.stat().st_mtime)

                # Remove old files
                if file_date < file_cutoff:
                    log_file.unlink()
                    continue

                # Truncate oversized logs (keep tail)
                if log_file.stat().st_size > _LOG_MAX_BYTES:
                    content = log_file.read_bytes()
                    log_file.write_bytes(content[-2 * 1024 * 1024:])

                # Compress logs older than 3 days (skip today's active log)
                if file_date < compress_cutoff:
                    gz_path = log_file.with_suffix(".log.gz")
                    if not gz_path.exists():
                        with open(log_file, "rb") as f_in:
                            with _gzip.open(gz_path, "wb") as f_out:
                                f_out.writelines(f_in)
                        log_file.unlink()
            except (ValueError, OSError):
                continue
    except OSError:
        pass


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
        do_reflect()
    elif command == "journal":
        do_journal()
    elif command == "analyst":
        do_analyst(slot=flags.get("slot", ""))
    elif command == "research":
        do_research()
    elif command == "zhesi":
        do_zhesi()
    elif command == "soul-question":
        do_soul_question()
    elif command == "autowrite-check":
        do_autowrite_check()
    elif command == "check-comments":
        do_check_comments()
    elif command == "growth-cycle":
        do_growth_cycle()
    elif command == "notes-cycle":
        do_notes_cycle()
    elif command == "spark-check":
        do_spark_check()
    elif command == "idle-think":
        do_idle_think()
    elif command == "daily-report":
        do_daily_report()
    elif command == "assess":
        do_assess()
    elif command == "self-improve":
        _run_self_improve()
    elif command == "podcast":
        lang  = flags.get("lang", "zh")
        slug  = flags.get("slug", "")
        title = flags.get("title", slug.replace("-", " ").title())
        run_podcast_episode(lang, slug, title)
    elif command == "daily-photo":
        do_daily_photo()
    elif command == "skill-study":
        group_idx = int(flags.get("group", "0"))
        do_skill_study(group_idx=group_idx)
    elif command == "write-check":
        # Manually check and advance writing projects
        responses = check_writing_responses()
        if responses:
            for r in responses:
                print(f"Advancing: {r['project']['title']} ({r['project']['phase']})")
                advance_project(r["workspace"], r["content"])
        else:
            print("No writing projects awaiting response")
    elif command == "write-from-plan":
        if len(sys.argv) < 3:
            print("Usage: core.py write-from-plan <path-to-大纲.md> [--title 标题] [--type novel|essay|blog|technical|poetry]")
            sys.exit(1)
        plan_path = sys.argv[2]
        title = flags.get("title", "")
        writing_type = flags.get("type", "novel")
        start_from_plan(title, plan_path, writing_type)
    else:
        print(f"Usage: {sys.argv[0]} [run|talk|respond|explore|reflect|journal|analyst|zhesi|skill-study|autowrite-check|write-check|write-from-plan|spark-check]")
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
