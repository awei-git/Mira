"""TALK mode — handle messages from Mira (iPhone <-> Mac).

Dispatches new tasks to background workers, collects completed results,
handles meta-commands, and processes both command-based and legacy inbox flows.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import (
    MIRA_DIR,
    CLEANUP_DAYS,
    WRITINGS_DIR,
    WRITINGS_OUTPUT_DIR,
    MAX_TASKS_PER_CYCLE,
)

try:
    from bridge import Mira, Message
except (ImportError, ModuleNotFoundError):
    Mira = None
    Message = None
from task_manager import TaskManager, TASKS_DIR
from memory.soul import check_prompt_injection
from execution.runtime_contract import normalize_task_status

from state import load_state, save_state

log = logging.getLogger("mira")


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
        log.info("STATE %s: dispatch deferred (all %d slots occupied)", msg.id, task_mgr.get_active_count())
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
        bridge.update_status(
            msg.id,
            "failed",
            error={"code": "dispatch_failed", "message": "Worker process failed to start", "retryable": True},
        )
        log.error("STATE %s: -> failed (dispatch error)", msg.id)
        return "failed"


def _quarantine_inbound_command(bridge, cmd: dict, item_id: str, title: str, content: str, reason: str):
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


def _check_inbound_command_safety(bridge, cmd: dict, item_id: str, title: str, content: str) -> bool:
    """Return True when an inbound command is safe to dispatch."""
    flagged, reason = check_prompt_injection(content)
    if not flagged:
        return True
    _quarantine_inbound_command(bridge, cmd, item_id, title, content, reason)
    return False


def _is_meta_command(content: str) -> bool:
    """Check if a message is a meta-command (not a regular task)."""
    c = content.strip().lower()
    return c.startswith("/archive ") or c in ("/status", "status", "状态") or c.startswith("/status")


def _handle_meta_command(bridge, msg, msg_path, task_mgr=None):
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
        bridge.reply(msg.id, msg.sender, f"Thread {thread_id} 已归档。", thread_id=msg.thread_id)
        bridge.ack(msg.id, "done")
    else:
        bridge.reply(msg.id, msg.sender, f"未知命令: {content[:50]}", thread_id=msg.thread_id)
        bridge.ack(msg.id, "error")

    bridge.mark_processed(msg_path)


def _is_writing_request(body: str) -> bool:
    """Detect if a request is a writing task (use multiple models for variety)."""
    writing_keywords = [
        "写",
        "write",
        "draft",
        "essay",
        "blog",
        "文章",
        "故事",
        "story",
        "小说",
        "散文",
        "随笔",
        "翻译",
        "translate",
        "rewrite",
        "改写",
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
    type_map = {
        "小说": "novel",
        "散文": "essay",
        "随笔": "essay",
        "博客": "blog",
        "技术": "technical",
        "诗歌": "poetry",
    }
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
        bridge.update_status(
            item_id, "failed", error={"code": "stuck", "message": "Task lost — please retry", "retryable": True}
        )


def do_talk():
    """Process Mira messages: dispatch new tasks + collect completed results.

    This is the super agent — it dispatches tasks to background workers
    and collects their results. Each call takes seconds, not minutes.
    Processes commands for ALL registered users.
    """
    from config import get_known_user_ids, get_user_config, is_agent_allowed
    from workflows.daily import handle_photo_feedback

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
        rec.status = normalize_task_status(getattr(rec, "status", ""))
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
        elif rec.status in ("failed", "timeout", "blocked"):
            retryable = task_mgr.can_retry(rec)
            if rec.status == "blocked":
                error_msg = f"处理被阻止: {rec.summary}" if rec.summary else "处理被阻止。"
            else:
                error_msg = f"处理失败: {rec.summary}" if rec.summary else "处理失败，请稍后重试。"
            bridge.update_status(
                rec.task_id, "failed", error={"code": rec.status, "message": error_msg, "retryable": retryable}
            )
            log.warning("STATE %s: working -> failed (%s: %s)", rec.task_id, rec.status, rec.summary)

    # --- Score completed tasks (grounded metrics only) ---
    for rec in completed:
        try:
            from evaluation.scorer import evaluate_task_outcome, record_event

            t_scores = evaluate_task_outcome(
                {
                    "status": rec.status,
                    "summary": rec.summary or "",
                    "workspace": rec.workspace or "",
                }
            )
            if t_scores:
                record_event(
                    "task_complete",
                    t_scores,
                    {
                        "task_id": rec.task_id,
                        "agent": getattr(rec, "agent", None) or (rec.tags[0] if rec.tags else "unknown"),
                    },
                )
        except (ImportError, AttributeError) as e:
            log.debug("Task scoring skipped: %s", e)

    # --- Phase B1: Process commands from all users ---
    for user_bridge in all_bridges:
        all_cmds = user_bridge.poll_commands()
        if len(all_cmds) > MAX_TASKS_PER_CYCLE:
            log.warning(
                "BACKPRESSURE user=%s tasks_pending=%d cap=%d skipped=%d",
                user_bridge.user_id,
                len(all_cmds),
                MAX_TASKS_PER_CYCLE,
                len(all_cmds) - MAX_TASKS_PER_CYCLE,
            )
        for cmd in all_cmds[:MAX_TASKS_PER_CYCLE]:
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
                msg = Message(
                    id=task_id, sender=sender, timestamp=cmd.get("timestamp", ""), content=content, thread_id=task_id
                )
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
                msg = Message(
                    id=disc_id, sender=sender, timestamp=cmd.get("timestamp", ""), content=content, thread_id=disc_id
                )
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
                msg = Message(
                    id=item_id, sender=sender, timestamp=cmd.get("timestamp", ""), content=content, thread_id=item_id
                )
                result = _dispatch_or_requeue(task_mgr, bridge, msg, workspace, cmd)
                if result == "busy":
                    break
            elif cmd_type == "comment":
                parent_id = cmd.get("parent_id", "")
                disc_id = f"disc_{uuid.uuid4().hex[:8]}"
                bridge.create_discussion(
                    disc_id, f"Re: {title}", content, sender=sender, tags=["feed-comment"], parent_id=parent_id
                )
            elif cmd_type == "cancel" and item_id:
                bridge.update_status(
                    item_id, "failed", error={"code": "cancelled", "message": "Cancelled by user", "retryable": False}
                )
            elif cmd_type == "recall":
                query = cmd.get("query", content or "")
                recall_id = f"req_recall_{uuid.uuid4().hex[:8]}"
                if not _check_inbound_command_safety(bridge, cmd, recall_id, f"Recall: {query[:40]}", query):
                    continue
                bridge.create_task(
                    recall_id, f"Recall: {query[:40]}", query, sender=sender, tags=["recall"], origin="user"
                )
                workspace = TASKS_DIR / _talk_slug(query, recall_id)
                msg = Message(
                    id=recall_id, sender=sender, timestamp=cmd.get("timestamp", ""), content=query, thread_id=recall_id
                )
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
                            f"[{fu.get('source','?')}] {fu.get('content','')}" for fu in todo.get("followups", [])
                        )
                        full_content = f"Todo: {todo['title']}\n\nConversation so far:\n{history}\n\nUser's latest message:\n{content}"
                        if not bridge.item_exists(req_id):
                            bridge.create_task(
                                req_id,
                                f"Todo: {todo['title']}",
                                full_content,
                                sender=sender,
                                tags=["todo"],
                                origin="user",
                            )
                        else:
                            bridge.append_message(req_id, sender, content)
                            bridge.update_status(req_id, "working")
                        workspace = TASKS_DIR / _talk_slug(content, req_id)
                        workspace.mkdir(parents=True, exist_ok=True)
                        (workspace / ".todo_id").write_text(todo_id)
                        msg = Message(
                            id=req_id,
                            sender=sender,
                            timestamp=cmd.get("timestamp", ""),
                            content=full_content,
                            thread_id=req_id,
                        )
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
                user_bridge.create_task(
                    req_id, f"Todo: {todo_title}", todo_title, sender="user", tags=["todo"], origin="user"
                )
                workspace = TASKS_DIR / _talk_slug(todo_title, req_id)
                workspace.mkdir(parents=True, exist_ok=True)
                (workspace / ".todo_id").write_text(todo_id)
                msg = Message(id=req_id, sender="user", timestamp="", content=todo_title, thread_id=req_id)
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
            from evaluation.emptiness import on_external_input

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
                        log.warning(
                            "Retry ceiling reached for task %s (%d/%d)",
                            old_rec.task_id,
                            getattr(old_rec, "attempt_count", 0),
                            getattr(old_rec, "max_attempts", 0),
                        )
                        continue
                    # Reuse the original workspace
                    msg_workspace = (
                        Path(old_rec.workspace)
                        if old_rec.workspace
                        else TASKS_DIR / _talk_slug(msg.content, msg.thread_id)
                    )
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
                        bridge.reply(msg.id, msg.sender, "重试分发失败，请稍后再试。", thread_id=msg.thread_id)
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
                bridge.reply(msg.id, msg.sender, "任务分发失败，请稍后重试。", thread_id=msg.thread_id)
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
