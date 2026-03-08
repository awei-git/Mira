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
    MIRA_ROOT, WORKSPACE_DIR, BRIEFINGS_DIR, LOGS_DIR, STATE_FILE,
    NOTES_INBOX_FOLDER, NOTES_BRIEFING_FOLDER, NOTES_OUTPUT_FOLDER,
    EXPLORE_SOURCE_GROUPS, EXPLORE_COOLDOWN_MINUTES,
    EXPLORE_ACTIVE_START, EXPLORE_ACTIVE_END, EXPLORE_MAX_PER_DAY,
    REFLECT_DAY, REFLECT_TIME,
    MAX_BRIEFING_ITEMS, MAX_DEEP_DIVES, MIRA_DIR, CLEANUP_DAYS,
    JOURNAL_DIR, JOURNAL_TIME, SKILLS_INDEX, WRITINGS_OUTPUT_DIR, WRITINGS_DIR,
    ANALYST_TIMES, ANALYST_BUSINESS_DAYS_ONLY, ZHESI_TIME, ZA_FILE,
)
from notes_bridge import check_inbox, create_note
from mira import Mira
from task_manager import TaskManager, TASKS_DIR
from soul_manager import (
    load_soul, format_soul, append_memory, update_memory, update_interests,
    update_worldview, save_skill, save_reading_note, load_recent_reading_notes,
    detect_recurring_themes,
)
from fetcher import fetch_all
from sub_agent import claude_think, claude_act
from writing_workflow import (
    start_project, check_writing_responses, advance_project, start_from_plan,
)
from prompts import (
    respond_prompt, explore_prompt, deep_dive_prompt, reflect_prompt,
    journal_prompt, internalize_prompt, autonomous_writing_prompt,
    worldview_evolution_prompt, zhesi_prompt,
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
        if rec.status == "needs-input":
            # Task wants user confirmation before proceeding
            bridge.reply(rec.msg_id, rec.sender, content + footer, thread_id=rec.thread_id)
            bridge.update_task_status(rec.task_id, "needs-input", agent_message=content)
            if rec.tags:
                bridge.set_task_tags(rec.task_id, rec.tags)
            log.info("Mira [%s] needs user input: %s", rec.task_id, content[:80])
        elif rec.status == "done":
            bridge.reply(rec.msg_id, rec.sender, content + footer, thread_id=rec.thread_id)
            bridge.ack(rec.msg_id, "done")
            # Update task state for iOS
            bridge.update_task_status(rec.task_id, "done", agent_message=content)
            if rec.tags:
                bridge.set_task_tags(rec.task_id, rec.tags)
            log.info("Mira [%s] task done, reply sent", rec.task_id)
        elif rec.status in ("error", "timeout"):
            error_msg = f"处理失败: {rec.summary}" if rec.summary else "处理失败，请稍后重试。"
            bridge.reply(rec.msg_id, rec.sender, error_msg + footer, thread_id=rec.thread_id)
            bridge.ack(rec.msg_id, "error")
            bridge.update_task_status(rec.task_id, "failed", agent_message=error_msg)
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

        # If iOS already created a task (thread_id starts with "task_"), reuse it
        effective_task_id = msg.thread_id if msg.thread_id.startswith("task_") else msg.id

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
            # iOS already created the task file; just update status
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
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items for Claude
    feed_text = _format_feed_items(items)

    # 3. Ask Claude to filter and rank
    prompt = explore_prompt(soul_ctx, feed_text, source_slot=slot_name)
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
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}{suffix}.md").write_text(briefing, encoding="utf-8")

    # 5. Push briefing to Apple Notes + Mira
    slot_label = f" ({slot_name})" if slot_name else ""
    create_note(NOTES_BRIEFING_FOLDER, f"Briefing {today}{slot_label}", briefing)

    # Briefing is displayed as a card in Today (from .md file)
    # No need to create a task — that just pollutes the task list

    # 6. Check for deep-dive candidate
    dive = _extract_deep_dive(briefing)
    if dive and MAX_DEEP_DIVES > 0:
        log.info("Deep diving into: %s", dive["title"])
        _do_deep_dive(soul_ctx, dive)

    src_label = ",".join(source_names) if source_names else "all"
    append_memory(f"Explored {len(items)} items ({src_label}), wrote briefing {today}")

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

    # --- Internalization: write a personal reading reflection ---
    try:
        soul = load_soul()
        soul_ctx_full = format_soul(soul)
        intern_prompt = internalize_prompt(soul_ctx_full, dive["title"], result[:3000])
        reflection = claude_think(intern_prompt, timeout=60)
        if reflection:
            save_reading_note(dive["title"], reflection)
            append_memory(f"Reading reflection on '{dive['title']}': {reflection[:100]}")
            log.info("Internalization note saved for: %s", dive["title"])
    except Exception as e:
        log.warning("Internalization failed: %s", e)


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

    result = claude_think(prompt, timeout=300)

    if not result:
        log.error("Analyst briefing failed: empty response")
        return

    # Save to artifacts/briefings for TodayView
    suffix = f"analyst_{session_type.replace('-', '_')}"
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    briefing_path = mira_briefings / f"{today}_{suffix}.md"
    briefing_path.write_text(result, encoding="utf-8")
    log.info("Analyst briefing saved: %s", briefing_path.name)

    # Also save to main briefings dir
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEFINGS_DIR / f"{today}_{suffix}.md").write_text(result, encoding="utf-8")

    append_memory(f"Generated {session_type} analyst briefing for {today}")

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

    # --- Evolve worldview ---
    try:
        recent_reading = load_recent_reading_notes(days=14)
        from config import WORLDVIEW_FILE
        current_wv = WORLDVIEW_FILE.read_text(encoding="utf-8") if WORLDVIEW_FILE.exists() else ""
        wv_prompt = worldview_evolution_prompt(soul_ctx, current_wv, recent_reading, recent_work)
        new_worldview = claude_think(wv_prompt, timeout=120)
        if new_worldview and len(new_worldview) > 100:
            update_worldview(new_worldview)
            log.info("Worldview evolved from reflection")
    except Exception as e:
        log.warning("Worldview evolution failed: %s", e)

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

    # --- Pick a 杂.md fragment as journal seed ---
    state = load_state()
    za_fragment = _mine_za_one(state)
    save_state(state)

    # --- Ask Claude to write the journal ---
    soul = load_soul()
    soul_ctx = format_soul(soul)

    prompt = journal_prompt(soul_ctx, tasks_summary, skills_summary, briefing_summary,
                            za_fragment=za_fragment)
    journal_text = claude_think(prompt, timeout=120)

    if not journal_text:
        log.error("Journal: Claude returned empty")
        return

    # Save journal
    journal_content = f"# Journal {today}\n\n{journal_text}"
    journal_path.write_text(journal_content, encoding="utf-8")
    log.info("Journal saved: %s", journal_path.name)

    # Copy to briefings dir so iOS can read it (same synced folder)
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}_journal.md").write_text(journal_content, encoding="utf-8")

    # Journal is displayed as a briefing card in Today (from .md file)
    # No need to create a task — that just pollutes the task list

    # Update memory
    append_memory(f"Wrote daily journal for {today}")

    # --- Autonomous writing check: does Mira have something to say? ---
    try:
        _check_autonomous_writing(soul_ctx, bridge, journal_text)
    except Exception as e:
        log.warning("Autonomous writing check failed: %s", e)

    # Mark done in state
    state = load_state()
    state[f"journal_{today}"] = datetime.now().isoformat()
    save_state(state)


def _check_autonomous_writing(soul_ctx: str, bridge: Mira, recent_journal: str):
    """Check if Mira has accumulated enough insight to write something on her own.

    Runs after daily journal. If Mira decides she has something to say,
    creates an auto-task and dispatches the writing pipeline.
    """
    # Detect recurring themes across recent journals + reading notes
    themes = detect_recurring_themes(days=7)
    recent_reading = load_recent_reading_notes(days=7)

    # Ask Mira if she wants to write
    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal[:1500],
    )
    result = claude_think(prompt, timeout=60)
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

    append_memory(f"Self-initiated writing: '{title}' ({writing_type})")


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
    """Check Substack posts for new comments and reply as Mira."""
    log.info("Starting Substack comment check")

    state = load_state()
    state["last_comment_check"] = datetime.now().isoformat()
    save_state(state)

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "publisher"))
        from substack import check_and_reply_comments, sync_posts_for_ios
        # Sync posts list for iOS app display
        sync_posts_for_ios()
        replies = check_and_reply_comments()
        if replies:
            log.info("Replied to %d comments", len(replies))
            for r in replies:
                log.info("  %s on '%s': %s",
                         r["comment_name"], r["post_title"], r["reply"][:80])
            # Notify bridge
            bridge = Mira()
            summary = f"回复了 {len(replies)} 条 Substack 评论:\n"
            for r in replies:
                summary += f"- {r['comment_name']} on \"{r['post_title']}\": {r['reply'][:60]}\n"
            append_memory(f"Replied to {len(replies)} Substack comments")
        else:
            log.info("No new comments to reply to")
    except Exception as e:
        log.error("Comment check failed: %s", e)


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
    except Exception:
        pass

    prompt = zhesi_prompt(soul_ctx, fragment, recent_reading)
    result = claude_think(prompt, timeout=120)

    if not result:
        log.error("哲思: Claude returned empty")
        return

    # Save
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    zhesi_path = JOURNAL_DIR / f"{today}_zhesi.md"
    content = f"# 每日哲思 {today}\n\n> {fragment}\n\n{result}"
    zhesi_path.write_text(content, encoding="utf-8")
    log.info("哲思 saved: %s", zhesi_path.name)

    # Copy to artifacts for iOS
    mira_briefings = MIRA_DIR / "artifacts" / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}_zhesi.md").write_text(content, encoding="utf-8")

    append_memory(f"Wrote daily 哲思 on: {fragment[:60]}")

    state[f"zhesi_{today}"] = datetime.now().isoformat()
    save_state(state)


# ---------------------------------------------------------------------------
# Proactive autonomous writing check
# ---------------------------------------------------------------------------

def do_autowrite_check():
    """Standalone check: does Mira have something she wants to write?

    Draws from 杂.md ideas + recent readings + recurring themes.
    More proactive than the journal-only trigger.
    """
    log.info("Starting autonomous writing check")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather context
    za_fragments = "\n".join(f"- {f}" for f in _mine_za_ideas(count=5))
    themes = detect_recurring_themes(days=7)
    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception:
        pass

    # Get most recent journal
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("????-??-??.md"), reverse=True)
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:1500]

    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal,
        za_fragments=za_fragments,
    )
    result = claude_think(prompt, timeout=60)
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
        save_state(state)
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

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

    append_memory(f"Self-initiated writing: '{title}' ({writing_type})")
    save_state(state)


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

    # Sync app feeds → browsable artifacts (fast, file I/O only)
    try:
        from agents.shared.app_sync import sync_app_artifacts
        sync_app_artifacts()
    except Exception as e:
        log.warning("App sync failed: %s", e)

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

    if should_reflect():
        _dispatch_background("reflect", [
            sys.executable, str(Path(__file__).resolve()), "reflect",
        ])

    if should_journal():
        _dispatch_background("journal", [
            sys.executable, str(Path(__file__).resolve()), "journal",
        ])

    # Analyst — dual schedule (pre-market + post-market)
    analyst_slot = should_analyst()
    if analyst_slot:
        _dispatch_background(f"analyst-{analyst_slot}", [
            sys.executable, str(Path(__file__).resolve()), "analyst",
            "--slot", analyst_slot,
        ])

    # 每日哲思
    if should_zhesi():
        _dispatch_background("zhesi", [
            sys.executable, str(Path(__file__).resolve()), "zhesi",
        ])

    # Proactive autonomous writing check
    if should_check_writing():
        _dispatch_background("autowrite-check", [
            sys.executable, str(Path(__file__).resolve()), "autowrite-check",
        ])

    # Substack comment check — reply to readers
    if should_check_comments():
        _dispatch_background("substack-comments", [
            sys.executable, str(Path(__file__).resolve()), "check-comments",
        ])

    log.info("=== Mira Agent sleep ===")


# ---------------------------------------------------------------------------
# Background dispatch for long-running tasks
# ---------------------------------------------------------------------------

_BG_PID_DIR = MIRA_ROOT / "agents" / ".bg_pids"


def _dispatch_background(name: str, cmd: list[str]):
    """Spawn a background process if one isn't already running for this name.

    Tracks PID to avoid duplicate runs. Fire-and-forget.
    """
    _BG_PID_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _BG_PID_DIR / f"{name}.pid"

    # Check if a previous run is still active or finished recently
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)  # check if alive
            log.info("Background '%s' still running (PID %d), skipping", name, old_pid)
            return
        except (OSError, ValueError):
            pass  # process gone, safe to start new one
        # Cooldown: don't re-dispatch if the PID file was written recently
        try:
            import time as _time
            age = _time.time() - pid_file.stat().st_mtime
            if age < 300:  # 5-minute cooldown
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
    elif command == "respond":
        do_respond()
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
    elif command == "zhesi":
        do_zhesi()
    elif command == "autowrite-check":
        do_autowrite_check()
    elif command == "check-comments":
        do_check_comments()
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
        print(f"Usage: {sys.argv[0]} [run|talk|respond|explore|reflect|journal|analyst|zhesi|autowrite-check|write-check|write-from-plan]")
        sys.exit(1)


if __name__ == "__main__":
    main()
