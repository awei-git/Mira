#!/usr/bin/env python3
"""Mira Super Agent — orchestrator with soul, memory, and curiosity.

Modes:
    run     — full cycle: check inbox, maybe explore/reflect
    respond — process inbox requests only
    explore — fetch sources and write briefing
    reflect — weekly reflection and memory consolidation
"""
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

# Add shared + sibling agent dirs to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "explorer"))

from config import (
    PLAYGROUND_ROOT, WORKSPACE_DIR, BRIEFINGS_DIR, LOGS_DIR, STATE_FILE,
    NOTES_INBOX_FOLDER, NOTES_BRIEFING_FOLDER, NOTES_OUTPUT_FOLDER,
    EXPLORE_TIMES, EXPLORE_WINDOW_MINUTES, REFLECT_DAY, REFLECT_TIME,
    MAX_BRIEFING_ITEMS, MAX_DEEP_DIVES, MIRA_DIR, CLEANUP_DAYS,
    JOURNAL_DIR, JOURNAL_TIME, SKILLS_INDEX, WRITINGS_OUTPUT_DIR, WRITINGS_DIR,
)
from notes_bridge import check_inbox, create_note
from mira import Mira
from task_manager import TaskManager, TASKS_DIR
from soul_manager import (
    load_soul, format_soul, append_memory, update_memory, update_interests,
    save_skill,
)
from fetcher import fetch_all
from sub_agent import claude_think, claude_act
from writing_workflow import (
    start_project, check_writing_responses, advance_project, start_from_plan,
)
from prompts import (
    respond_prompt, explore_prompt, deep_dive_prompt, reflect_prompt,
    journal_prompt,
)

log = logging.getLogger("mira")


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
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


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


def do_talk():
    """Process Mira messages: dispatch new tasks + collect completed results.

    This is the super agent — it dispatches tasks to background workers
    and collects their results. Each call takes seconds, not minutes.
    """
    bridge = Mira()
    task_mgr = TaskManager()

    # Heartbeat now includes task status so phone can show busy/free
    bridge.heartbeat(agent_status=task_mgr.get_status_summary())

    # --- Phase A: Collect results from previously dispatched tasks ---
    completed = task_mgr.check_tasks()
    for rec in completed:
        content = task_mgr.get_reply_content(rec)
        # Append compact status footer so user always knows agent state
        footer = _status_footer(task_mgr)
        if rec.status == "done":
            bridge.reply(rec.msg_id, rec.sender, content + footer, thread_id=rec.thread_id)
            bridge.ack(rec.msg_id, "done")
            log.info("Mira [%s] task done, reply sent", rec.task_id)
        elif rec.status in ("error", "timeout"):
            error_msg = f"处理失败: {rec.summary}" if rec.summary else "处理失败，请稍后重试。"
            bridge.reply(rec.msg_id, rec.sender, error_msg + footer, thread_id=rec.thread_id)
            bridge.ack(rec.msg_id, "error")
            log.warning("Mira [%s] task %s: %s", rec.task_id, rec.status, rec.summary)

    # --- Phase B: Dispatch new messages to background workers ---
    messages = bridge.poll()
    if not messages:
        log.info("Mira: no new messages (active tasks: %d)", task_mgr.get_active_count())
        return

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

        # Each message gets its own workspace under Mira/tasks/
        slug = _talk_slug(msg.content, msg.id)
        msg_workspace = TASKS_DIR / slug

        bridge.ack(msg.id, "received")

        # Dispatch to background worker (returns immediately)
        # Only one Claude Code instance at a time — if busy, leave message for next cycle
        task_id = task_mgr.dispatch(msg, msg_workspace)
        if task_id:
            bridge.ack(msg.id, "processing")
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
            reply = "我找到了多个大纲，你想用哪个？\n\n"
            for o in outlines:
                reply += f"• {o.parent.name}\n"
            reply += "\n请在这条笔记里回复项目名。"
            create_note(NOTES_INBOX_FOLDER, f"请确认: {title}", reply)
            return None

    return None


def do_respond():
    """Process new requests from the Mira Notes inbox."""
    requests = check_inbox()
    if not requests:
        log.info("No new requests in inbox")
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    for req in requests:
        title = req["title"]
        body = req["body"]
        slug = _slugify(title)

        log.info("Processing request: %s → %s", title, slug)

        # Smart detection: does the user want to write from an existing outline?
        outline_ref = _find_outline(title, body)
        if outline_ref:
            plan_path, writing_type = outline_ref
            log.info("Found outline: %s [%s]", plan_path, writing_type)
            start_from_plan(title, plan_path, writing_type)
        elif _is_writing_request(body):
            # Writing tasks go to artifacts/writings/
            writing_ws = _WRITINGS_OUTPUT / slug
            writing_ws.mkdir(parents=True, exist_ok=True)
            start_project(title, body, writing_ws)
        else:
            # Non-writing: Claude with tools handles it directly
            workspace = WORKSPACE_DIR / slug
            workspace.mkdir(parents=True, exist_ok=True)
            prompt = respond_prompt(soul_ctx, title, body, str(workspace))
            result = claude_act(prompt, cwd=workspace)

            if not result:
                log.error("Sub-agent returned empty for '%s'", title)
                append_memory(f"Failed to process request: {title}")
                continue

            (workspace / "agent_output.md").write_text(result, encoding="utf-8")

            summary_path = workspace / "summary.txt"
            summary = summary_path.read_text(encoding="utf-8").strip() if summary_path.exists() else result[:500]

            create_note(
                NOTES_OUTPUT_FOLDER,
                f"Done: {title}",
                f"{summary}\n\nFull output in: {workspace}",
            )
            append_memory(f"Completed request '{title}' [claude] → {workspace}")

        log.info("Done: %s", title)


# ---------------------------------------------------------------------------
# EXPLORE mode — fetch, filter, brief, deep-dive
# ---------------------------------------------------------------------------

def do_explore():
    """Fetch sources, write briefing, optionally deep-dive."""
    log.info("Starting explore cycle")

    # 1. Fetch all sources
    items = fetch_all()
    if not items:
        log.info("No items fetched, skipping explore")
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items for Claude
    feed_text = _format_feed_items(items)

    # 3. Ask Claude to filter and rank
    prompt = explore_prompt(soul_ctx, feed_text)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        log.error("Explore: Claude returned empty briefing")
        return

    # 4. Save briefing
    today = datetime.now().strftime("%Y-%m-%d")
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}.md").write_text(briefing, encoding="utf-8")

    # 5. Push briefing to Apple Notes + Mira
    create_note(NOTES_BRIEFING_FOLDER, f"Briefing {today}", briefing)

    # Post to Mira so user can read on phone
    try:
        bridge = Mira()
        # Truncate for Mira (keep it readable on phone)
        tb_briefing = briefing if len(briefing) < 3000 else briefing[:3000] + "\n\n... (truncated)"
        bridge.post(f"📋 Briefing {today}\n\n{tb_briefing}")
    except Exception as e:
        log.warning("Failed to post briefing to Mira: %s", e)

    # 6. Check for deep-dive candidate
    dive = _extract_deep_dive(briefing)
    if dive and MAX_DEEP_DIVES > 0:
        log.info("Deep diving into: %s", dive["title"])
        _do_deep_dive(soul_ctx, dive)

    append_memory(f"Explored {len(items)} items, wrote briefing {today}")

    # Mark this explore slot as done
    now = datetime.now()
    state = load_state()
    state["last_explore"] = now.isoformat()
    for t in EXPLORE_TIMES:
        scheduled = datetime.combine(now.date(), t)
        if abs((now - scheduled).total_seconds()) / 60 <= EXPLORE_WINDOW_MINUTES:
            slot_key = f"explored_{now.strftime('%Y-%m-%d')}_{t.strftime('%H%M')}"
            state[slot_key] = now.isoformat()
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
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
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
        append_memory(f"Learned new skill from deep dive: {name}")


# ---------------------------------------------------------------------------
# REFLECT mode — consolidate memory, update interests, self-initiate
# ---------------------------------------------------------------------------

def do_reflect():
    """Weekly reflection: consolidate memory, evolve interests, maybe self-initiate."""
    log.info("Starting reflect cycle")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather recent briefings (last 7 days)
    recent_briefings = _gather_recent_briefings(days=7)

    # Gather recent work (from memory)
    recent_work = soul["memory"]  # memory already has work log

    prompt = reflect_prompt(soul_ctx, recent_briefings, recent_work)
    result = claude_think(prompt, timeout=300)

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

    if memory_section:
        update_memory(f"# Memory\n\n{memory_section}")
        log.info("Memory consolidated from reflection")

    if project_section and "nothing right now" not in project_section.lower():
        # The agent wants to start something on its own
        log.info("Self-initiated project proposed: %s", project_section[:100])
        project_slug = f"self-{datetime.now().strftime('%Y%m%d')}"
        project_dir = WORKSPACE_DIR / project_slug
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "proposal.md").write_text(project_section, encoding="utf-8")
        append_memory(f"Proposed self-initiated project: {project_slug}")

        # Execute the project
        self_prompt = (
            f"You are an autonomous AI agent. Here is who you are:\n\n{soul_ctx}\n\n"
            f"---\n\n"
            f"You proposed the following project for yourself:\n\n{project_section}\n\n"
            f"Now execute it. Your workspace is: {project_dir}\n"
            f"Save your output there. Write a summary.txt when done."
        )
        output = claude_act(self_prompt, cwd=project_dir)
        if output:
            (project_dir / "output.md").write_text(output, encoding="utf-8")
            append_memory(f"Completed self-initiated project: {project_slug}")
            create_note(
                NOTES_OUTPUT_FOLDER,
                f"Self: {project_slug}",
                output[:2000],
            )

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

    # --- Ask Claude to write the journal ---
    soul = load_soul()
    soul_ctx = format_soul(soul)

    prompt = journal_prompt(soul_ctx, tasks_summary, skills_summary, briefing_summary)
    journal_text = claude_think(prompt, timeout=120)

    if not journal_text:
        log.error("Journal: Claude returned empty")
        return

    # Save journal
    journal_path.write_text(
        f"# Journal {today}\n\n{journal_text}",
        encoding="utf-8",
    )
    log.info("Journal saved: %s", journal_path.name)

    # Post to Mira
    bridge = Mira()
    bridge.post(f"📔 Daily Journal — {today}\n\n{journal_text}")

    # Update memory
    append_memory(f"Wrote daily journal for {today}")

    # Mark done in state
    state = load_state()
    state[f"journal_{today}"] = datetime.now().isoformat()
    save_state(state)


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


# ---------------------------------------------------------------------------
# Schedule logic
# ---------------------------------------------------------------------------

def should_explore() -> bool:
    """Check if it's time to explore based on schedule. Exactly once per slot."""
    now = datetime.now()
    state = load_state()

    for t in EXPLORE_TIMES:
        scheduled = datetime.combine(now.date(), t)
        delta = abs((now - scheduled).total_seconds()) / 60
        if delta <= EXPLORE_WINDOW_MINUTES:
            # Check if we already explored for THIS specific slot today
            slot_key = f"explored_{now.strftime('%Y-%m-%d')}_{t.strftime('%H%M')}"
            if state.get(slot_key):
                return False  # already done this slot
            return True

    return False


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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def cmd_run():
    """Full cycle: talk → respond → dispatch background work.

    The super agent MUST stay fast (<10s). All long-running work
    (writing pipeline, explore, reflect) runs in background processes
    so heartbeat and Mira polling stay responsive.
    """
    log.info("=== Mira Agent wake ===")

    # Mira first (lightweight, fast)
    try:
        do_talk()
    except Exception as e:
        log.error("Mira failed: %s", e)

    # Apple Notes inbox — lightweight check only
    do_respond()

    # Check if user approved plans or gave feedback on writing projects
    try:
        responses = check_writing_responses()
        for resp in responses:
            advance_project(resp["workspace"], user_input=resp["content"])
    except Exception as e:
        log.error("Writing response check failed: %s", e)

    # --- All heavy work below runs in background processes ---

    # Writing pipeline
    _dispatch_background("writing-pipeline", [
        sys.executable,
        str(_AGENTS_DIR / "writer" / "writing_agent.py"),
        "run",
    ])

    # Explore / Reflect (scheduled)
    if should_explore():
        _dispatch_background("explore", [
            sys.executable, str(Path(__file__).resolve()), "explore",
        ])

    if should_reflect():
        _dispatch_background("reflect", [
            sys.executable, str(Path(__file__).resolve()), "reflect",
        ])

    if should_journal():
        _dispatch_background("journal", [
            sys.executable, str(Path(__file__).resolve()), "journal",
        ])

    log.info("=== Mira Agent sleep ===")


# ---------------------------------------------------------------------------
# Background dispatch for long-running tasks
# ---------------------------------------------------------------------------

_BG_PID_DIR = PLAYGROUND_ROOT / "agents" / ".bg_pids"


def _dispatch_background(name: str, cmd: list[str]):
    """Spawn a background process if one isn't already running for this name.

    Tracks PID to avoid duplicate runs. Fire-and-forget.
    """
    _BG_PID_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _BG_PID_DIR / f"{name}.pid"

    # Check if a previous run is still active
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)  # check if alive
            log.info("Background '%s' still running (PID %d), skipping", name, old_pid)
            return
        except (OSError, ValueError):
            pass  # process gone, safe to start new one

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=open(LOGS_DIR / f"bg-{name}.log", "a"),
            start_new_session=True,
            cwd=str(PLAYGROUND_ROOT / "agents" / "super"),
        )
        pid_file.write_text(str(proc.pid))
        log.info("Background '%s' dispatched (PID %d)", name, proc.pid)
    except Exception as e:
        log.error("Failed to dispatch background '%s': %s", name, e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _extract_section(text: str, header: str) -> str:
    """Extract content under a ### header."""
    pattern = rf"###\s*{re.escape(header)}\s*\n(.+?)(?=\n###|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


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

    command = sys.argv[1] if len(sys.argv) > 1 else "run"

    if command == "run":
        cmd_run()
    elif command == "talk":
        do_talk()
    elif command == "respond":
        do_respond()
    elif command == "explore":
        do_explore()
    elif command == "reflect":
        do_reflect()
    elif command == "journal":
        do_journal()
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
        # Start writing from an existing outline/plan file
        # Usage: core.py write-from-plan <plan_path> [--title <title>] [--type <type>]
        if len(sys.argv) < 3:
            print("Usage: core.py write-from-plan <path-to-大纲.md> [--title 标题] [--type novel|essay|blog|technical|poetry]")
            sys.exit(1)
        plan_path = sys.argv[2]
        title = ""
        writing_type = "novel"
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--title" and i + 1 < len(sys.argv):
                title = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--type" and i + 1 < len(sys.argv):
                writing_type = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        start_from_plan(title, plan_path, writing_type)
    else:
        print(f"Usage: {sys.argv[0]} [run|talk|respond|explore|reflect|journal|write-check|write-from-plan]")
        sys.exit(1)


if __name__ == "__main__":
    main()
