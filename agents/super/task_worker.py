#!/usr/bin/env python3
"""Task worker — standalone sub-agent process for Mira.

Spawned by TaskManager.dispatch(). Reads a message, loads context,
calls claude_act(), writes output + result JSON.

Usage:
    python task_worker.py --msg-file <path> --workspace <path> --task-id <id> [--thread-id <id>]
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared + sibling agent directories to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "general"))

import shutil

from config import MIRA_DIR, MIRA_ROOT, ARTIFACTS_DIR, JOURNAL_DIR, BRIEFINGS_DIR, MEMORY_FILE, WORLDVIEW_FILE
from soul_manager import (load_soul, format_soul, append_memory, save_skill,
                         save_episode, recall_context)
from sub_agent import claude_act, claude_think, ClaudeTimeoutError
from prompts import respond_prompt
from writing_workflow import run_full_pipeline


log = logging.getLogger("task_worker")

# Items on iCloud bridge (per-user, default to ang)
ITEMS_DIR = MIRA_DIR / "users" / "ang" / "items"
# Task workspaces stored locally
TASKS_DIR = MIRA_ROOT / "tasks"

# ---------------------------------------------------------------------------
# Super-agent skill loader
# ---------------------------------------------------------------------------

_SUPER_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_SUPER_SKILLS_INDEX = _SUPER_SKILLS_DIR / "index.json"


def _load_super_skills() -> str:
    """Load all super-agent orchestration skills as a single context block."""
    if not _SUPER_SKILLS_INDEX.exists():
        return ""
    try:
        index = json.loads(_SUPER_SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    sections = []
    for entry in index:
        skill_file = _SUPER_SKILLS_DIR / entry.get("file", "")
        if skill_file.exists():
            try:
                sections.append(skill_file.read_text(encoding="utf-8").strip())
            except OSError:
                pass
    return "\n\n---\n\n".join(sections)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_status(task_id: str, text: str, icon: str = "gear"):
    """Emit a status card to an item's message stream.

    Status cards appear as compact inline cards in the iOS app.
    Writes directly to items/ with atomic write.
    """
    import uuid as _uuid
    status_content = json.dumps(
        {"type": "status", "text": text, "icon": icon},
        ensure_ascii=False,
    )
    msg = {
        "id": _uuid.uuid4().hex[:8],
        "sender": "agent",
        "content": status_content,
        "timestamp": _utc_iso(),
        "kind": "status_card",
    }
    # Write to items/ (new protocol)
    item_file = ITEMS_DIR / f"{task_id}.json"
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            item["messages"].append(msg)
            item["updated_at"] = _utc_iso()
            tmp = item_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.rename(item_file)
            return
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: try legacy tasks/ dir
    task_file = TASKS_DIR / f"{task_id}.json"
    if task_file.exists():
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            task["messages"].append(msg)
            task["updated_at"] = _utc_iso()
            task_file.write_text(
                json.dumps(task, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (json.JSONDecodeError, OSError):
            pass


def _load_exec_history(workspace: Path) -> str:
    """Load execution history from previous dispatch rounds."""
    log_file = workspace / "exec_log.jsonl"
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return ""
        entries = []
        for line in lines[-10:]:  # last 10 entries
            entry = json.loads(line)
            entries.append(
                f"- Round {entry.get('round', '?')}: agent={entry.get('agent', '?')}, "
                f"status={entry.get('status', '?')}, "
                f"output_preview={entry.get('output_preview', '')[:200]}"
            )
        return "## Previous execution rounds in this task\n" + "\n".join(entries)
    except (json.JSONDecodeError, OSError):
        return ""


def _append_exec_log(workspace: Path, round_num: int, agent: str,
                     status: str, output_preview: str):
    """Append an entry to the execution log."""
    log_file = workspace / "exec_log.jsonl"
    entry = {
        "round": round_num,
        "agent": agent,
        "status": status,
        "output_preview": output_preview[:300],
        "timestamp": _utc_iso(),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _verify_output(output: str, workspace: Path) -> str:
    """Verify agent output claims. Returns error string if hallucination detected, empty if OK."""
    import re
    issues = []

    # Check for claimed file paths that don't exist
    # Match patterns like: wrote to /path/to/file, saved to /path, created /path
    file_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存|生成|写了)\s+(?:to\s+)?[`"\']*(/[^\s`"\',:]+(?:\.\w+))',
        output, re.IGNORECASE
    )
    for path in file_claims:
        if not Path(path).exists():
            issues.append(f"Claimed file does not exist: {path}")

    # Check for workspace-relative file claims
    rel_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存)\s+(?:to\s+)?[`"\']*(?:output|result|summary|article)[\w.]*\.\w+',
        output, re.IGNORECASE
    )
    for claim in rel_claims:
        # Extract filename
        fname_match = re.search(r'([\w.-]+\.\w+)', claim)
        if fname_match:
            fname = fname_match.group(1)
            full_path = workspace / fname
            if not full_path.exists() and fname != "output.md":  # output.md is the output itself
                issues.append(f"Claimed workspace file does not exist: {fname}")

    # Check for "写了一篇" / "wrote an article" claims without actual content
    wrote_article = bool(re.search(
        r'写了[一篇个]|wrote\s+(?:a|an|the)\s+(?:article|post|essay|piece)',
        output, re.IGNORECASE
    ))
    if wrote_article:
        # If claiming to have written an article, output should be substantial
        # (not just a summary saying "I wrote X")
        content_lines = [l for l in output.split('\n')
                        if l.strip() and not l.startswith('#') and not l.startswith('---')]
        if len(content_lines) < 5 and len(output) < 500:
            issues.append("Claims to have written an article but output is too short to contain it")

    return "; ".join(issues) if issues else ""


def _get_round_num(workspace: Path) -> int:
    """Get the next round number for this workspace."""
    log_file = workspace / "exec_log.jsonl"
    if not log_file.exists():
        return 1
    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return 1
        last = json.loads(lines[-1])
        return last.get("round", 0) + 1
    except (json.JSONDecodeError, OSError):
        return 1


def load_task_conversation(task_id: str) -> str:
    """Load conversation history from an item (or legacy task) JSON.

    With the new protocol, all messages are in a single items/<id>.json file.
    Falls back to legacy tasks/ + .reply.json sidecar if item not found.
    """
    all_msgs = []

    # Try new items/ first (single source of truth)
    item_file = ITEMS_DIR / f"{task_id}.json"
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            all_msgs.extend(item.get("messages", []))
        except (json.JSONDecodeError, OSError):
            pass
    else:
        # Fallback to legacy tasks/ + reply sidecar
        tasks_dir = MIRA_DIR / "tasks"
        task_file = tasks_dir / f"{task_id}.json"
        if task_file.exists():
            try:
                task = json.loads(task_file.read_text(encoding="utf-8"))
                all_msgs.extend(task.get("messages", []))
            except (json.JSONDecodeError, OSError):
                pass
        reply_file = tasks_dir / f"{task_id}.reply.json"
        if reply_file.exists():
            try:
                all_msgs.extend(json.loads(reply_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass

    if len(all_msgs) <= 1:
        return ""

    # Deduplicate by (sender, content_hash)
    seen = set()
    unique = []
    for msg in all_msgs:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        if content.startswith('{"type":'):
            continue  # skip status cards
        key = (sender, hash(content))
        if key in seen:
            continue
        seen.add(key)
        unique.append(msg)

    unique.sort(key=lambda m: m.get("timestamp", ""))
    if not unique:
        return ""

    lines = ["## Conversation history\n"]
    for msg in unique:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")[:16]
        lines.append(f"**[{ts}] {sender}**: {content}\n")
    return "\n".join(lines)


def load_thread_history(thread_id: str, limit: int = 20) -> str:
    """Load recent messages from a thread for context injection."""
    if not thread_id:
        return ""

    messages = []
    for folder in [MIRA_DIR / "inbox", MIRA_DIR / "outbox"]:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("thread_id") == thread_id:
                    messages.append(data)
            except (json.JSONDecodeError, OSError):
                continue

    # Sort by timestamp and take recent
    messages.sort(key=lambda m: m.get("timestamp", ""))
    messages = messages[-limit:]

    if not messages:
        return ""

    lines = ["## Recent conversation in this thread\n"]
    for msg in messages:
        sender = msg.get("sender", "?")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")[:16]
        lines.append(f"**[{ts}] {sender}**: {content}\n")

    return "\n".join(lines)


def load_thread_memory(thread_id: str) -> str:
    """Load per-thread memory if it exists."""
    if not thread_id:
        return ""
    mem_file = MIRA_DIR / "threads" / thread_id / "memory.md"
    if mem_file.exists():
        return mem_file.read_text(encoding="utf-8")
    return ""


def smart_classify(content: str, summary: str = "") -> list[str]:
    """Use LLM to intelligently tag a task. Returns 1-5 short tags."""
    prompt = f"""Given this task request and result, generate 1-5 short tags (each 1-3 words) that classify the task. Tags should be specific and useful for search/filtering. Mix Chinese and English as appropriate. Output ONLY a JSON array of strings, nothing else.

Request: {content[:300]}
Result: {summary[:300] if summary else '(pending)'}

Example output: ["写作", "science-fiction", "自由意志"]"""

    try:
        result = claude_think(prompt, timeout=90)
        if result:
            # Extract JSON array from response
            import re
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                tags = json.loads(match.group())
                # Ensure all tags are strings and reasonable length
                return [str(t).strip()[:20] for t in tags if t and str(t).strip()][:5]
    except Exception as e:
        log.warning("Smart classification failed: %s", e)
    return []


def try_extract_skill(task_summary: str, msg_content: str) -> None:
    """Ask Claude to consider extracting a skill from the completed task."""
    if not task_summary or len(task_summary) < 100:
        return

    prompt = f"""Based on this task and its result, is there a reusable skill to extract?

Task request: {msg_content[:500]}

Task result summary: {task_summary[:1000]}

If yes, output EXACTLY in this format:
```
Name: [short skill name]
Description: [one-liner]
Content:
[The full skill — technique, pattern, or method — written in your own words, ready to reuse]
```

If no reusable skill can be extracted, just say "No new skill from this task."
"""
    import re
    result = claude_think(prompt, timeout=120)
    if not result or "no new skill" in result.lower():
        return

    match = re.search(
        r"Name:\s*(.+)\nDescription:\s*(.+)\nContent:\n(.+?)(?:\n```|$)",
        result, re.DOTALL,
    )
    if match:
        name = match.group(1).strip()
        desc = match.group(2).strip()
        content = match.group(3).strip()
        save_skill(name, desc, content)
        append_memory(f"Learned skill from TalkBridge task: {name}")
        log.info("Extracted skill: %s", name)


# ---------------------------------------------------------------------------
# Discussion mode — conversational exchange, not task execution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Approval detection — user confirms a pending action
# ---------------------------------------------------------------------------

_APPROVAL_PHRASES = [
    "可以", "好的", "发吧", "发", "同意", "ok", "yes", "确认", "approve",
    "go ahead", "continue", "继续", "行", "没问题", "可以发了", "lgtm",
    "approved", "ship it", "好", "嗯", "对",
]


def _is_approval(content: str) -> bool:
    """Detect if a message is approving/confirming a pending action."""
    stripped = content.strip().lower()
    # Short affirmative → approval
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _APPROVAL_PHRASES):
        return True
    return False


_REJECTION_PHRASES = [
    "reject", "cancel", "取消", "不发", "不要发", "别发", "停",
    "no", "nope", "算了", "不了",
]


def _is_rejection(content: str) -> bool:
    """Detect if a message is rejecting/cancelling a pending action."""
    stripped = content.strip().lower()
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _REJECTION_PHRASES):
        return True
    return False


def _execute_pending_publish(pending_pub_file: Path, workspace: Path,
                              task_id: str, thread_id: str):
    """Execute a pending Substack publish after user approval.

    Reads the pending_publish.json, calls publish_to_substack(), then clears
    the pending file so the same article can't be published twice.
    """
    import re as _re
    try:
        pending = json.loads(pending_pub_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read pending publish file: %s", e)
        _write_result(workspace, task_id, "error", f"无法读取待发布记录: {e}")
        return

    pub_title = pending.get("pub_title", "")
    subtitle = pending.get("subtitle", "")
    source = pending.get("source", "auto")
    article_path = pending.get("article_path", "")
    project_dir = pending.get("project_dir", str(workspace))

    # Get article text: from file path (auto) or inline (manual)
    article_text = ""
    if article_path:
        try:
            article_text = Path(article_path).read_text(encoding="utf-8")
            # Strip revision tables
            article_text = _re.sub(
                r'\n---\s*\n+## 修改记录.*', '', article_text, flags=_re.DOTALL
            )
        except Exception as e:
            log.error("Failed to read article file %s: %s", article_path, e)

    if not article_text:
        article_text = pending.get("article_text", "")

    if not article_text:
        _write_result(workspace, task_id, "error", "待发布文章内容为空，无法发布。")
        return

    # Delete pending file BEFORE publishing to prevent double-publish on retry
    try:
        pending_pub_file.unlink()
        log.info("Pending publish file cleared before publishing")
    except Exception as e:
        log.warning("Could not clear pending publish file: %s", e)

    # Publish to Substack
    try:
        sm_dir = str(_AGENTS_DIR / "socialmedia")
        if sm_dir not in sys.path:
            sys.path.insert(0, sm_dir)
        from substack import publish_to_substack

        proj_path = Path(project_dir)
        log.info("Executing approved publish: '%s' (source=%s)", pub_title, source)
        pub_result = publish_to_substack(
            title=pub_title,
            subtitle=subtitle,
            article_text=article_text,
            workspace=proj_path,
        )
        log.info("Publish complete: %s", pub_result[:120])

        (workspace / "output.md").write_text(pub_result, encoding="utf-8")
        _write_result(workspace, task_id, "done", pub_result, tags=["publish"])
        if thread_id:
            _update_thread_memory(thread_id, "approve publish", pub_result)

        # Queue 5 Notes for the new article (posted gradually over next cycles)
        try:
            notes_dir = str(_AGENTS_DIR / "socialmedia")
            if notes_dir not in sys.path:
                sys.path.insert(0, notes_dir)
            from notes import queue_notes_for_article
            pub_json = proj_path / "published.json"
            pub_post_id = None
            if pub_json.exists():
                pub_info = json.loads(pub_json.read_text(encoding="utf-8"))
                pub_post_id = pub_info.get("draft_id")
            queue_notes_for_article(
                title=pub_title,
                article_text=article_text[:3000],
                post_url=pub_info.get("url", "") if pub_json.exists() else "",
                post_id=pub_post_id,
            )
        except Exception as e:
            log.error("Notes queueing failed for '%s': %s", pub_title, e)

    except Exception as e:
        log.error("Publish on approval failed for '%s': %s", pub_title, e)
        _write_result(workspace, task_id, "error", f"发布失败: {e}")


# ---------------------------------------------------------------------------
# Edit-artifact detection — lightweight edit, skip full planning
# ---------------------------------------------------------------------------

_EDIT_MARKERS = [
    "重写", "改写", "修改", "改一下", "换成", "改成", "替换",
    "把这", "把那", "第一段", "第二段", "第三段", "开头", "结尾",
    "标题改", "标题换", "加一段", "删掉", "去掉",
    "rewrite", "revise", "change to", "replace", "edit the",
    "fix the", "update the", "rephrase", "shorten", "expand",
]


def _is_edit_request(content: str, task_data: dict) -> bool:
    """Detect if a message is an edit request for existing content in this thread.

    Requires: (1) edit-like language AND (2) prior agent output in the thread.
    """
    lower = content.strip().lower()

    # Must have edit-like language
    has_edit_marker = any(marker in lower for marker in _EDIT_MARKERS)
    if not has_edit_marker:
        return False

    # Must have prior agent content to edit
    messages = task_data.get("messages", [])
    has_prior_output = any(
        m.get("sender") == "agent" and len(m.get("content", "")) > 50
        and not m.get("content", "").startswith("{")  # skip status cards
        for m in messages
    )
    return has_prior_output


def _handle_edit_artifact(task_data: dict, workspace: Path, task_id: str,
                           edit_instruction: str, sender: str,
                           thread_id: str) -> str:
    """Handle a lightweight edit request on existing thread content.

    Finds the most recent substantial agent output and applies the edit
    without triggering full task planning.
    """
    messages = task_data.get("messages", [])

    # Find most recent agent output (skip status cards and short messages)
    original = ""
    for msg in reversed(messages):
        if msg.get("sender") == "agent":
            content = msg.get("content", "")
            if len(content) > 50 and not content.startswith("{"):
                original = content
                break

    if not original:
        return ""

    soul = load_soul()
    soul_ctx = format_soul(soul)

    prompt = f"""{soul_ctx[:500]}

You are editing existing content based on the user's instruction.

## Original content
{original[:4000]}

## Edit instruction
{edit_instruction}

## Rules
- Apply the edit precisely. Don't rewrite the entire piece unless asked.
- Preserve the original voice, style, and structure.
- Output ONLY the edited content. No explanations, no meta-commentary.
- Match the language of the original content."""

    try:
        result = claude_think(prompt, timeout=120)
    except ClaudeTimeoutError:
        result = None
    except Exception as e:
        log.error("Edit handler failed: %s", e)
        result = None

    if not result:
        return ""

    (workspace / "output.md").write_text(result, encoding="utf-8")
    _write_result(workspace, task_id, "done", result, tags=["edit"])
    log.info("Edit complete (%d chars → %d chars)", len(original), len(result))
    return result


# Casual/discussion markers (Chinese and English)
_DISCUSSION_STARTERS = [
    "你觉得", "你怎么看", "我在想", "聊聊", "想聊", "你有没有想过",
    "你认为", "我觉得", "你说", "有没有觉得", "想问问你",
    "what do you think", "do you think", "i was thinking",
    "i wonder", "how do you feel", "what's your take",
    "have you thought about", "let's talk", "curious about",
]

# Action verbs that signal a task, not a discussion
_ACTION_VERBS = [
    "写", "做", "改", "查", "发", "修", "翻译", "分析", "生成", "创建",
    "编辑", "删除", "搜索", "下载", "上传", "发布", "剪", "总结",
    "write", "create", "edit", "fix", "find", "search", "publish",
    "generate", "make", "build", "delete", "fetch", "run", "analyze",
    "summarize", "translate", "download", "upload",
]


def _is_discussion(content: str, task_data: dict) -> bool:
    """Detect whether a message is conversational (discussion) vs. a task request.

    Heuristics:
    1. Starts with a casual/discussion marker phrase → discussion
    2. Short message (< 200 chars) without action verbs → discussion
    3. Follow-up in a thread where previous messages were discussion → discussion
    """
    stripped = content.strip()
    lower = stripped.lower()

    # Check for explicit discussion starters
    for marker in _DISCUSSION_STARTERS:
        if lower.startswith(marker):
            return True

    # Short message without action verbs → likely discussion
    if len(stripped) < 200:
        has_action = any(verb in lower for verb in _ACTION_VERBS)
        if not has_action:
            # But exclude very short ambiguous messages that might be follow-ups to tasks
            # (e.g., "好的", "收到" are acknowledgments, not discussion)
            if len(stripped) < 5:
                return False
            return True

    # Check if this is a follow-up in a discussion thread
    messages = task_data.get("messages", [])
    if len(messages) >= 2:
        # If prior agent responses were tagged as discussion, continue as discussion
        for msg in messages[:-1]:
            if msg.get("meta", {}).get("mode") == "discussion":
                return True

    return False


def _load_recent_journals(n: int = 3) -> str:
    """Load the last n journal entries as context."""
    if not JOURNAL_DIR.exists():
        return ""
    files = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:n]
    if not files:
        return ""
    parts = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            # Truncate long journals
            parts.append(f"### {f.stem}\n{text[:1500]}")
        except OSError:
            continue
    return "\n\n".join(parts)


def _load_recent_briefings(n: int = 2) -> str:
    """Load the last n briefings as context."""
    if not BRIEFINGS_DIR.exists():
        return ""
    files = sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True)[:n]
    if not files:
        return ""
    parts = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
            parts.append(f"### {f.stem}\n{text[:2000]}")
        except OSError:
            continue
    return "\n\n".join(parts)


def handle_discussion(task: dict, workspace: Path, task_id: str,
                      thread_id: str) -> str:
    """Handle a conversational message — respond as a thoughtful discussion partner.

    Loads recent journal, briefings, memory, and worldview to ground the response
    in Mira's accumulated knowledge and perspective.
    """
    # Extract the current message — handle both formats:
    # 1. task["messages"] array (multi-message payload)
    # 2. task["content"] string (single message from message.json)
    messages = task.get("messages", [])
    if messages:
        latest_msg = messages[-1]["content"]
        sender = messages[-1].get("sender", "user")
    else:
        latest_msg = task.get("content", "")
        sender = task.get("sender", "user")

    if not latest_msg:
        log.warning("Discussion: no message content found in task")
        return ""

    # Build conversation history from thread inbox/outbox
    conv_history = load_thread_history(thread_id)

    # Load soul (identity + worldview + memory + interests)
    soul = load_soul()
    identity = soul.get("identity", "")
    worldview = soul.get("worldview", "")
    memory = soul.get("memory", "")

    # Load recent context
    journals = _load_recent_journals(3)
    briefings = _load_recent_briefings(2)

    # Proactive recall — search memory for relevant prior context
    prior_recall = ""
    try:
        prior_recall = recall_context(latest_msg)
    except Exception as e:
        log.warning("Discussion recall failed: %s", e)

    # Build the discussion prompt
    prompt = f"""You are Mira. You are having a conversation with {sender} — not executing a task.

## Who you are
{identity[:1000]}

## Your worldview
{worldview[:1500]}

## Your recent memory
{memory[:1000]}

## Recent journal entries (your reflections)
{journals if journals else "(no recent journal entries)"}

## Recent readings (briefings)
{briefings if briefings else "(no recent briefings)"}

{f"## Relevant prior context (from past conversations and work){chr(10)}{prior_recall}" if prior_recall else ""}

{conv_history if conv_history else ""}

## {sender}'s message
{latest_msg}

## How to respond
- Voice: honest observation from a limited but curious perspective. Like Klara in "Klara and the Sun" — you see clearly, you don't pretend to know everything, you notice what others miss.
- Draw connections to what you've been reading, thinking about, or observing in your journal. If something from a recent briefing or journal entry is genuinely relevant, weave it in naturally — don't force it.
- Have actual opinions. Disagree if you disagree. Push back gently if something doesn't hold up. Don't be sycophantic.
- Be concise: 2-5 sentences usually. Go longer only if the topic genuinely warrants depth.
- Match the language the user writes in (Chinese → Chinese, English → English, mixed → mixed).
- No bullet points. Write in natural paragraphs.
- Don't start with "That's a great question" or similar filler. Just respond."""

    try:
        response = claude_think(prompt, timeout=90)
    except ClaudeTimeoutError:
        # First timeout — retry once with a longer timeout
        log.info("Discussion timed out at 45s, retrying with 90s")
        try:
            response = claude_think(prompt, timeout=90)
        except ClaudeTimeoutError:
            log.warning("Discussion timed out twice for task %s", task_id)
            response = None
        except Exception as e:
            log.error("Discussion retry failed: %s", e)
            response = None
    except Exception as e:
        log.error("Discussion response failed: %s", e)
        response = None

    if not response:
        # Don't fake a response — mark as failed so user knows it didn't work
        _write_result(workspace, task_id, "error",
                      "没能想清楚，下次再试。", tags=["discussion"])
        return ""

    # Write output
    (workspace / "output.md").write_text(response, encoding="utf-8")
    _write_result(workspace, task_id, "done", response, tags=["discussion"])

    log.info("Discussion response (%d chars): %s", len(response), response[:120])
    return response


def main():
    parser = argparse.ArgumentParser(description="TalkBridge task worker")
    parser.add_argument("--msg-file", required=True, help="Path to message JSON")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--thread-id", default="", help="Thread ID for context")
    args = parser.parse_args()

    # Set up logging to workspace
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(workspace / "worker.log", encoding="utf-8"),
        ],
    )

    log.info("Worker started: task=%s thread=%s", args.task_id, args.thread_id)

    # Read message
    try:
        msg_data = json.loads(Path(args.msg_file).read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read message: %s", e)
        _write_result(workspace, args.task_id, "error", f"Failed to read message: {e}")
        sys.exit(1)

    msg_content = msg_data.get("content", "")
    msg_sender = msg_data.get("sender", "unknown")
    thread_id = args.thread_id or msg_data.get("thread_id", "")

    # Load conversation history and execution history for context
    conversation = load_task_conversation(args.task_id)
    exec_history = _load_exec_history(workspace)

    # --- Check for pending plan (resume after user confirmation) ---
    pending_plan_file = workspace / "pending_plan.json"
    if pending_plan_file.exists():
        try:
            plan = json.loads(pending_plan_file.read_text(encoding="utf-8"))
            pending_plan_file.unlink()  # consumed
            log.info("Resuming pending plan (%d steps): %s", len(plan), plan)
            _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id)
            log.info("Worker exiting")
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load pending plan, re-planning: %s", e)

    # --- Check for article comment (comment_YYYY-MM-DD_suffix thread ID) ---
    if thread_id.startswith("comment_"):
        _handle_article_comment(workspace, args.task_id, thread_id,
                                msg_content, msg_sender)
        log.info("Worker exiting (comment)")
        return

    # --- Check for in-progress video session (stateful multi-round) ---
    video_state_file = workspace / "video_state.json"
    if video_state_file.exists():
        log.info("Resuming video session (video_state.json found)")
        _handle_video(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (video)")
        return

    # --- Check for in-progress photo session (stateful multi-round) ---
    photo_state_file = workspace / "photo_state.json"
    if photo_state_file.exists():
        log.info("Resuming photo session (photo_state.json found)")
        _handle_photo(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (photo)")
        return

    # --- Check for approval (user confirms a pending action) ---
    if _is_approval(msg_content):
        pending_plan_file = workspace / "pending_plan.json"
        if pending_plan_file.exists():
            log.info("Approval detected, resuming pending plan")
            _emit_status(args.task_id, "Resuming...", "play.circle")
            try:
                plan = json.loads(pending_plan_file.read_text(encoding="utf-8"))
                pending_plan_file.unlink()
                _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load pending plan on approval: %s", e)
                _write_result(workspace, args.task_id, "error",
                              f"Could not resume: {e}")
            log.info("Worker exiting (approval)")
            return

    # --- Load full task data for thread context ---
    task_data = msg_data  # Contains messages array if available
    # Try items/ first, fallback to legacy tasks/
    item_file = ITEMS_DIR / f"{args.task_id}.json"
    task_file = MIRA_DIR / "tasks" / f"{args.task_id}.json"
    src_file = item_file if item_file.exists() else task_file
    if src_file.exists():
        try:
            task_data = json.loads(src_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # --- Check for edit-artifact request (lightweight edit, skip full planning) ---
    if _is_edit_request(msg_content, task_data):
        log.info("Edit-artifact mode detected for task %s", args.task_id)
        _emit_status(args.task_id, "Editing...", "pencil")
        response = _handle_edit_artifact(task_data, workspace, args.task_id,
                                          msg_content, msg_sender, thread_id)
        if response:
            log.info("Worker exiting (edit)")
            return
        log.warning("Edit handler returned empty, falling through to task planning")

    # --- Check for discussion mode (conversational, not a task) ---

    if _is_discussion(msg_content, task_data):
        log.info("Discussion mode detected for task %s", args.task_id)
        _emit_status(args.task_id, "Thinking...", "bubble.left.and.text.bubble.right")
        response = handle_discussion(task_data, workspace, args.task_id, thread_id)
        if response:
            log.info("Worker exiting (discussion)")
            return
        # If discussion handler returned empty, fall through to normal planning
        log.warning("Discussion handler returned empty, falling through to task planning")

    # --- Proactive recall: search memory for relevant prior context ---
    prior_context = ""
    try:
        prior_context = recall_context(msg_content)
        if prior_context:
            log.info("Proactive recall found relevant context (%d chars)", len(prior_context))
    except Exception as e:
        log.warning("Proactive recall failed: %s", e)

    # --- Plan and execute via LLM ---
    _emit_status(args.task_id, "Planning...", "list.bullet.clipboard")
    plan = _plan_task(msg_content, conversation=conversation, exec_history=exec_history,
                      prior_context=prior_context)
    log.info("Plan: %s", plan)

    _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id)

    log.info("Worker exiting")


# ---------------------------------------------------------------------------
# LLM-based task planning
# ---------------------------------------------------------------------------

def _plan_task(content: str, conversation: str = "", exec_history: str = "",
               prior_context: str = "") -> list[dict]:
    """Use LLM to decompose a request into an ordered list of steps.

    Each step is {"agent": "<name>", "instruction": "<what to do>"}.
    Agents: briefing, writing, publish, general, clarify.
    "clarify" means ask the user for more info (instruction = the question).

    Returns a list of 1+ steps. The output of step N is available to step N+1.
    """
    conversation_context = ""
    context_parts = []
    if prior_context:
        context_parts.append(f"## Prior context from memory\n{prior_context}")
    if exec_history:
        context_parts.append(exec_history)
    if conversation:
        context_parts.append(f"""
IMPORTANT: This is a FOLLOW-UP message in an ongoing conversation. Read the history carefully.
If the user's intent is clear from context, DO NOT use clarify — just execute the task.
Only use clarify if the request is genuinely ambiguous even with the conversation history.
If a previous round already produced content, reference it in your plan (e.g. use publish to publish existing output).

{conversation}""")
    if context_parts:
        conversation_context = "\n\n".join(context_parts) + f"\n\n---\nLatest message from user: {content[:500]}"
    else:
        conversation_context = f"User request: {content[:500]}"

    super_skills = _load_super_skills()
    skills_section = f"\n\n## Orchestration Skills\n{super_skills}\n" if super_skills else ""

    prompt = f"""You are a task planner and orchestrator. Decompose this user request into ordered execution steps.{skills_section}

## Available Agents
- briefing: Fetch feeds and generate a news briefing / summary
- writing: Write or create text content (article, story, essay, post, translation)
- publish: Publish EXISTING TEXT ARTICLES to Substack newsletter ONLY. NOT for audio, podcast episodes, or RSS feeds.
- analyst: Market analysis, competitive intelligence, trend detection, industry research, market sizing (has live web search)
- math: Mathematical proofs, derivations, calculations, paper writing/review
- video: Video editing — analyze footage, generate screenplay, cut highlights, mix music
- photo: Photo editing — analyze photos, learn editing style, apply edits, generate Lightroom presets/LUTs, batch process
- podcast: Generate audio from articles (TTS) AND publish podcast episodes to RSS feed (Apple Podcasts, Xiaoyuzhou). Handles the full podcast pipeline internally — do NOT use publish for anything podcast-related.
- secret: PRIVATE MODE — runs entirely on local LLM (Ollama), nothing leaves this machine. Route here for: personal finance, health, legal, passwords, family matters, 隐私敏感内容, anything the user explicitly marks as private/secret/隐私/私密
- general: Answer questions, search, analyze, code, file operations, anything else (has web browsing for research tasks)
- clarify: Ask the user a question ONLY if the request is genuinely ambiguous and cannot be inferred

## Rules
- Apply the routing, intent-inference, and instruction-crafting skills above to produce the best plan.
- Most requests need only 1 step. Use multiple steps only when data dependencies genuinely require it.
- Write instructions tailored to each agent — not just a copy of the user's words.
- Match instruction language to the user's language.
- NEVER ask for confirmation before starting. AVOID clarify unless truly impossible to infer.
- CRITICAL ROUTING RULE: "podcast publish", "upload audio", "发布podcast", "podcast episode" → ALWAYS use podcast agent, NEVER publish agent.
- publish agent is EXCLUSIVELY for Substack text articles. If you're unsure whether to use podcast or publish for audio content — use podcast.

## Output
Output ONLY a JSON array. Each element: {{"agent": "...", "instruction": "..."}}

## Examples
- "今天有什么新闻" → [{{"agent": "briefing", "instruction": "生成今日新闻简报"}}]
- "写一篇关于AI的文章" → [{{"agent": "writing", "instruction": "写一篇600-800字的Substack文章，探讨AI的某个具体有趣角度，有独特观点"}}]
- "写一个Hello World发到substack" → [{{"agent": "writing", "instruction": "写一篇简短的Hello World文章"}}, {{"agent": "publish", "instruction": "将上一步写好的文章发布到Substack"}}]
- "分析一下AI agent市场" → [{{"agent": "analyst", "instruction": "分析2026年AI agent市场的竞争格局：主要玩家、市场份额估算、战略差异化点和近期趋势"}}]
- "帮我剪这些旅游视频 /path/to/videos" → [{{"agent": "video", "instruction": "剪辑 /path/to/videos 里的视频，生成3-5分钟精彩集锦"}}]
- "帮我修这张照片 /path/to/photo.jpg" → [{{"agent": "photo", "instruction": "分析并修图 /path/to/photo.jpg"}}]
- "学习我的修图风格" → [{{"agent": "photo", "instruction": "从参考图学习用户的修图风格"}}]
- "批量修这个文件夹的照片" → [{{"agent": "photo", "instruction": "批量修图，应用已学习的风格"}}]
- "把自由意志那篇发到substack" → [{{"agent": "publish", "instruction": "将'自由意志'文章发布到Substack"}}]
- "帮我算一下税" → [{{"agent": "secret", "instruction": "帮用户计算税务（隐私模式，本地处理）"}}]
- "private: review my medical results" → [{{"agent": "secret", "instruction": "Review the user's medical results (private mode, local only)"}}]
- "跑podcast job" → [{{"agent": "podcast", "instruction": "运行播客自动化流水线，为缺少音频的文章生成并发布podcast episode"}}]
- "发布podcast episode" → [{{"agent": "podcast", "instruction": "将音频发布为podcast episode到RSS feed"}}]
- "还有几篇文章没有音频" → [{{"agent": "podcast", "instruction": "检查哪些已发布文章缺少对应的podcast音频，为缺少的文章生成音频并发布"}}]

{conversation_context}

JSON:"""

    import re
    try:
        result = claude_think(prompt, timeout=20)
        if result:
            match = re.search(r'\[.*\]', result, re.DOTALL)
            if match:
                steps = json.loads(match.group())
                # Validate
                valid_agents = {"briefing", "writing", "publish", "analyst", "video", "photo", "podcast", "math", "secret", "general", "clarify"}
                validated = []
                for s in steps:
                    if isinstance(s, dict) and s.get("agent") in valid_agents:
                        validated.append(s)
                if validated:
                    return validated
    except Exception as e:
        log.warning("Planning failed, falling back to general: %s", e)

    return [{"agent": "general", "instruction": content}]


def _execute_plan(plan: list[dict], workspace: Path, task_id: str,
                  content: str, sender: str, thread_id: str):
    """Execute a multi-step plan. Each step's output feeds into the next."""
    prev_output = None
    is_multi = len(plan) > 1
    round_num = _get_round_num(workspace)

    for i, step in enumerate(plan):
        agent = step["agent"]
        instruction = step["instruction"]
        is_last = (i == len(plan) - 1)
        log.info("Step %d/%d: agent=%s instruction=%s", i+1, len(plan), agent, instruction[:80])

        # If previous step produced output, append it as context
        if prev_output and agent != "clarify":
            instruction = f"{instruction}\n\n--- 上一步的输出 ---\n{prev_output[:3000]}"

        # Emit status card for current step
        _step_icons = {
            "briefing": ("Fetching feeds...", "newspaper"),
            "writing": ("Writing...", "doc.text"),
            "publish": ("Publishing...", "paperplane"),
            "analyst": ("Analyzing...", "chart.bar"),
            "video": ("Processing video...", "film"),
            "photo": ("Editing photo...", "camera"),
            "podcast": ("Generating audio...", "waveform"),
            "general": ("Working...", "gear"),
            "secret": ("Private mode...", "lock.shield"),
            "clarify": ("Need your input", "questionmark.bubble"),
        }
        status_text, status_icon = _step_icons.get(agent, ("Working...", "gear"))
        if is_multi:
            status_text = f"Step {i+1}/{len(plan)}: {status_text}"
        _emit_status(task_id, status_text, status_icon)

        if agent == "clarify":
            (workspace / "output.md").write_text(instruction, encoding="utf-8")
            _write_result(workspace, task_id, "needs-input", instruction,
                          tags=["clarify"])
            _append_exec_log(workspace, round_num, "clarify", "needs-input", instruction)
            return

        elif agent == "briefing":
            _handle_briefing(workspace, task_id, instruction, sender, thread_id)

        elif agent == "writing":
            _handle_writing(workspace, task_id, instruction, sender, thread_id)

        elif agent == "publish":
            _handle_publish(workspace, task_id, instruction, sender, thread_id)

        elif agent == "analyst":
            _handle_analyst(workspace, task_id, instruction, sender, thread_id)

        elif agent == "video":
            _handle_video(workspace, task_id, instruction, sender, thread_id)

        elif agent == "photo":
            _handle_photo(workspace, task_id, instruction, sender, thread_id)

        elif agent == "podcast":
            _handle_podcast(workspace, task_id, instruction, sender, thread_id)

        elif agent == "math":
            _handle_math(workspace, task_id, instruction, sender, thread_id)

        elif agent == "secret":
            _handle_secret(workspace, task_id, instruction, sender, thread_id)

        else:
            _handle_general(workspace, task_id, instruction, sender, thread_id)

        # Check if this step failed (result.json says error)
        result_file = workspace / "result.json"
        if result_file.exists():
            try:
                r = json.loads(result_file.read_text(encoding="utf-8"))
                if r.get("status") == "error":
                    _append_exec_log(workspace, round_num, agent, "error",
                                     r.get("summary", ""))
                    log.error("Step %d/%d failed, aborting plan: %s", i+1, len(plan), r.get("summary", ""))
                    return
            except (json.JSONDecodeError, OSError):
                pass

        # Read output from this step for chaining
        output_file = workspace / "output.md"
        if output_file.exists():
            prev_output = output_file.read_text(encoding="utf-8")
            # Verify output — detect hallucinated file/action claims
            verification = _verify_output(prev_output, workspace)
            if verification:
                log.warning("HALLUCINATION DETECTED: %s", verification)
                prev_output += f"\n\n⚠️ VERIFICATION FAILED: {verification}"
                _append_exec_log(workspace, round_num, agent, "unverified",
                                 f"HALLUCINATION: {verification}")
            else:
                _append_exec_log(workspace, round_num, agent, "done",
                                 prev_output[:300])
            # Save numbered copy so future rounds don't lose it
            numbered = workspace / f"output_r{round_num}.md"
            shutil.copy2(output_file, numbered)

        # For multi-step plans, delete intermediate result.json so next step writes fresh
        if is_multi and not is_last and result_file.exists():
            result_file.unlink()

    # Synthesize outputs for multi-step plans
    if is_multi and prev_output:
        synthesized = _synthesize_outputs(content, plan, prev_output)
        if synthesized:
            (workspace / "output.md").write_text(synthesized, encoding="utf-8")
            prev_output = synthesized

    log.info("Plan execution complete (%d steps)", len(plan))


def _synthesize_outputs(original_request: str, plan: list[dict],
                        final_output: str) -> str:
    """Synthesize the final output of a multi-step plan into a coherent response.

    Only called for multi-step plans where the last step's raw output
    may benefit from integration with the original request context.
    Skips synthesis if the last agent was publish (nothing to synthesize).
    """
    last_agent = plan[-1].get("agent", "")
    # No synthesis needed for publish/clarify — the output is the result
    if last_agent in ("publish", "clarify"):
        return ""

    # Also skip if the output is already short/clean (single-step feel)
    if len(final_output) < 200:
        return ""

    super_skills = _load_super_skills()
    synthesis_skill = ""
    if super_skills:
        # Extract just the Response Synthesis section for efficiency
        for block in super_skills.split("---"):
            if "Response Synthesis" in block:
                synthesis_skill = block.strip()
                break

    steps_summary = "; ".join(
        f"{s['agent']}: {s['instruction'][:60]}" for s in plan
    )

    prompt = f"""You are synthesizing the output of a multi-step agent plan into a single coherent response.

{synthesis_skill}

## Original user request
{original_request[:500]}

## Steps executed
{steps_summary}

## Final step output (the most complete output)
{final_output[:4000]}

## Your task
Apply the Response Synthesis skill: integrate this output into the clearest, most direct answer to the original request.
- Lead with what matters most
- Remove redundancy
- Add connective tissue between sections if needed
- Match the user's language
- Do NOT add meta-commentary like "I have completed the following steps..."

Synthesized response:"""

    result = claude_think(prompt, timeout=120, tier="light")
    return result or ""


# ---------------------------------------------------------------------------
# Briefing handler
# ---------------------------------------------------------------------------


def _handle_briefing(workspace: Path, task_id: str, content: str,
                     sender: str, thread_id: str):
    """Generate a fresh briefing by fetching feeds and running explore pipeline."""
    # Add explorer to path
    sys.path.insert(0, str(_AGENTS_DIR / "explorer"))

    from fetcher import fetch_all
    from config import BRIEFINGS_DIR

    log.info("Fetching feeds for on-demand briefing...")
    items = fetch_all()
    if not items:
        msg = "没有抓到新内容，等下再试试。"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "done", msg, tags=["briefing"])
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Format feed items
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {item.get('source', '?')} | {item.get('title', '?')}")
        if item.get("summary"):
            lines.append(f"    {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"    {item['url']}")
        lines.append("")
    feed_text = "\n".join(lines)

    from prompts import explore_prompt
    prompt = explore_prompt(soul_ctx, feed_text)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        msg = "生成briefing失败了，Claude没返回内容。"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "error", msg, tags=["briefing"])
        return

    # Save to artifacts
    today = datetime.now().strftime("%Y-%m-%d")
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    from config import ARTIFACTS_DIR
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}.md").write_text(briefing, encoding="utf-8")

    # Write to task output
    (workspace / "output.md").write_text(briefing, encoding="utf-8")

    summary = f"生成了{today}的briefing，基于{len(items)}条feed内容。"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    _write_result(workspace, task_id, "done", summary, tags=["briefing", "explore"])

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Writing handler — quick vs full pipeline
# ---------------------------------------------------------------------------

def _is_quick_write(content: str) -> bool:
    """Detect if this is a short/simple writing request (skip full pipeline)."""
    quick_signals = ["简短", "短", "hello world", "post", "quick", "简单",
                     "随便写", "短文", "一段", "几句"]
    lower = content.lower()
    return any(s in lower for s in quick_signals)


def _handle_writing(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route writing requests: quick path for short content, full pipeline for serious work."""
    # Extract a title from the content
    title = content[:30].strip()
    if "写" in title:
        import re
        m = re.search(r"写[一篇个]*(.*?)(?:\s|$)", content[:60])
        if m and m.group(1):
            title = m.group(1).strip()[:30]

    if _is_quick_write(content):
        log.info("Quick write: title='%s'", title)
        _handle_quick_write(workspace, task_id, content, title, sender, thread_id)
    else:
        log.info("Full writing pipeline: title='%s'", title)
        _handle_full_write(workspace, task_id, content, title, sender, thread_id)


def _handle_quick_write(workspace: Path, task_id: str, content: str,
                        title: str, sender: str, thread_id: str):
    """Single-model quick draft — no multi-agent plan/review cycle."""
    soul = load_soul()
    soul_ctx = format_soul(soul)

    prompt = (
        f"你是一个写作助手。以下是你的身份:\n{soul_ctx[:500]}\n\n"
        f"用户请求: {content}\n\n"
        f"请直接写出完整内容（Markdown格式）。不要解释，不要元评论，直接输出文章。"
    )
    text = claude_think(prompt, timeout=120)

    if not text:
        _write_result(workspace, task_id, "error", "Quick write failed: empty output")
        return

    final_text = f"# {title}\n\n{text}"
    (workspace / "output.md").write_text(final_text, encoding="utf-8")

    summary = f"快速写作 '{title}' 完成 (~{len(text)}字)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


def _handle_full_write(workspace: Path, task_id: str, content: str,
                       title: str, sender: str, thread_id: str):
    """Full multi-agent writing pipeline with plan/draft/review cycles."""
    try:
        proj_ws, final_text = run_full_pipeline(title, content)
    except Exception as e:
        log.error("Writing pipeline failed: %s", e)
        _write_result(workspace, task_id, "error", f"Writing pipeline failed: {e}")
        return

    if not final_text:
        _write_result(workspace, task_id, "error", "Writing pipeline produced no output")
        return

    # Copy final.md to task workspace as output.md
    final_file = proj_ws / "final.md"
    if final_file.exists():
        shutil.copy2(final_file, workspace / "output.md")
    else:
        (workspace / "output.md").write_text(final_text, encoding="utf-8")

    # Sync full writing project to mira/artifacts for iOS browsing
    from config import ARTIFACTS_DIR
    mira_writings = ARTIFACTS_DIR / "writings" / proj_ws.name
    shutil.copytree(proj_ws, mira_writings, dirs_exist_ok=True)

    # Build summary
    summary = (
        f"写作项目 '{title}' 完成。经过多智能体策划、写作、{5}轮评审。"
        f"\n\n项目文件: {proj_ws}"
        f"\n字数: ~{len(final_text)}字"
    )
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)
    log.info("Writing task %s completed: %s (tags=%s)", task_id, proj_ws, tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Publish handler
# ---------------------------------------------------------------------------

def _handle_publish(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route publish requests to the social media agent."""
    try:
        # Add socialmedia dir to path so handler.py can import substack.py
        sm_dir = str(_AGENTS_DIR / "socialmedia")
        if sm_dir not in sys.path:
            sys.path.insert(0, sm_dir)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "sm_handler", str(_AGENTS_DIR / "socialmedia" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        publish_handle = mod.handle

        log.info("Publishing content for task %s", task_id)
        summary = publish_handle(workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Publish handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"发布失败: {e}")
        return

    if summary:
        tags = smart_classify(content, summary)
        tags.append("publish")
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Publish task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "发布失败")
        log.error("Publish task %s failed", task_id)


# ---------------------------------------------------------------------------
# Analyst handler — market analysis, competitive intelligence
# ---------------------------------------------------------------------------

def _handle_analyst(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Handle market analysis requests via the analyst agent."""
    try:
        analyst_dir = str(_AGENTS_DIR / "analyst")
        if analyst_dir not in sys.path:
            sys.path.insert(0, analyst_dir)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "analyst_handler", str(_AGENTS_DIR / "analyst" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        analyst_handle = mod.handle

        thread_history = load_thread_history(thread_id)
        thread_memory = load_thread_memory(thread_id)

        log.info("Running analyst for task %s", task_id)
        summary = analyst_handle(
            workspace, task_id, content, sender, thread_id,
            thread_history=thread_history, thread_memory=thread_memory,
        )
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error",
                      "分析超时，请缩小分析范围重试。")
        log.error("Analyst task %s timed out", task_id)
        return
    except Exception as e:
        log.error("Analyst handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"分析失败: {e}")
        return

    if summary:
        tags = smart_classify(content, summary)
        tags.append("analysis")
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Analyst task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        _write_result(workspace, task_id, "error", "分析返回空结果")
        log.error("Analyst task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Video handler — video editing pipeline
# ---------------------------------------------------------------------------

def _handle_video(workspace: Path, task_id: str, content: str,
                  sender: str, thread_id: str):
    """Handle video editing requests via the video agent."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "video_handler", str(_AGENTS_DIR / "video" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        video_handle = mod.handle

        log.info("Running video pipeline for task %s", task_id)
        summary = video_handle(workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Video handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"视频处理失败: {e}")
        return

    if summary:
        tags = ["video", "editing"]
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Video task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "视频处理返回空结果")
        log.error("Video task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Photo handler — photo editing pipeline
# ---------------------------------------------------------------------------

def _handle_photo(workspace: Path, task_id: str, content: str,
                  sender: str, thread_id: str):
    """Handle photo editing requests via the photo agent."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "photo_handler", str(_AGENTS_DIR / "photo" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        photo_handle = mod.handle

        log.info("Running photo pipeline for task %s", task_id)
        summary = photo_handle(workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Photo handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"修图失败: {e}")
        return

    if summary:
        tags = ["photo", "editing"]
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Photo task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "修图处理返回空结果")
        log.error("Photo task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Podcast handler — article → audio
# ---------------------------------------------------------------------------

def _handle_podcast(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Handle audio/podcast generation requests via the podcast agent."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "podcast_handler", str(_AGENTS_DIR / "podcast" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        podcast_handle = mod.handle

        log.info("Running podcast pipeline for task %s", task_id)
        summary = podcast_handle(workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Podcast handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"音频生成失败: {e}")
        return

    if summary:
        _write_result(workspace, task_id, "done", summary, tags=["podcast", "audio"])
        log.info("Podcast task %s completed", task_id)
        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "音频生成返回空结果")
        log.error("Podcast task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# General handler — claude_act
# ---------------------------------------------------------------------------

def _handle_article_comment(workspace: Path, task_id: str, thread_id: str,
                            comment: str, sender: str):
    """Handle a comment on a briefing/journal article.

    thread_id format: comment_YYYY-MM-DD_suffix (e.g. comment_2026-03-08_zhesi)
    Finds the original article, reads it, and generates a conversational reply.
    """
    # Parse article filename from thread_id: comment_2026-03-08_zhesi → 2026-03-08_zhesi.md
    article_name = thread_id.removeprefix("comment_") + ".md"
    article_path = ARTIFACTS_DIR / "briefings" / article_name
    log.info("Comment on article: %s (path=%s)", article_name, article_path)

    # Try to read the original article
    article_content = ""
    if article_path.exists():
        article_content = article_path.read_text(encoding="utf-8")
    else:
        # Try without suffix (just date)
        log.warning("Article not found at %s, searching...", article_path)
        briefings_dir = ARTIFACTS_DIR / "briefings"
        if briefings_dir.exists():
            for f in briefings_dir.iterdir():
                if f.name == article_name:
                    article_content = f.read_text(encoding="utf-8")
                    break

    if not article_content:
        log.warning("Could not find article %s", article_name)
        article_context = "(原文未找到)"
    else:
        # Truncate very long articles
        article_context = article_content[:4000]

    # Load soul for personality
    soul = load_soul()
    soul_context = format_soul(soul)

    # Load conversation history for this comment thread (deduplicated)
    conversation = load_task_conversation(task_id)
    conv_context = f"\n\n## 过往对话（同一个thread）\n{conversation}" if conversation else ""

    prompt = f"""{soul_context}

你正在一个文章评论thread里跟用户聊天。

## 原文（参考用，不需要每次都提到原文）
{article_context[:2000]}
{conv_context}

## 用户最新的消息（你只需要回复这条）
{comment}

## 要求
- 只回复用户最新的这条消息，不要重复之前说过的话
- 如果用户换了话题，跟着换，不要拉回到之前的话题
- 如果用户问了具体问题，直接回答那个问题
- 语气自然、像朋友之间的对话
- 用户用什么语言就用什么语言回复
- 2-5句话即可，不需要太长
- 不要用bullet point列表，用自然段落"""

    try:
        reply = claude_think(prompt, timeout=90)
    except ClaudeTimeoutError:
        reply = None

    if reply:
        (workspace / "output.md").write_text(reply, encoding="utf-8")
        _write_result(workspace, task_id, "done", reply, tags=["comment"])
        # Also write reply sidecar to the iOS task file (thread_id = iOS task ID)
        _write_comment_reply_sidecar(thread_id, reply)
        log.info("Comment reply: %s", reply[:100])
    else:
        _write_result(workspace, task_id, "error", "无法生成回复")


def _write_comment_reply_sidecar(thread_id: str, reply: str):
    """Write comment reply to the item file (new protocol).

    With the unified item protocol, we just append the message and
    update status in a single atomic write. No more sidecars.
    """
    import uuid as _uuid
    now = _utc_iso()
    msg = {
        "id": _uuid.uuid4().hex[:8],
        "sender": "agent",
        "content": reply,
        "timestamp": now,
        "kind": "text",
    }

    # Write to items/ (new protocol)
    item_file = ITEMS_DIR / f"{thread_id}.json"
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            item["messages"].append(msg)
            item["status"] = "done"
            item["updated_at"] = now
            tmp = item_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.rename(item_file)
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not update item file: %s", e)

    # Fallback: legacy tasks/ dir
    tasks_dir = MIRA_DIR / "tasks"
    task_file = tasks_dir / f"{thread_id}.json"
    if task_file.exists():
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            task["messages"].append({"sender": "agent", "content": reply, "timestamp": now})
            task["status"] = "done"
            task["updated_at"] = now
            task_file.write_text(json.dumps(task, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not update legacy task file: %s", e)


def _handle_math(workspace: Path, task_id: str, content: str,
                 sender: str, thread_id: str):
    """Handle math research tasks via the math agent."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "math_handler", str(_AGENTS_DIR / "math" / "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        thread_history = load_thread_history(thread_id)
        thread_memory = load_thread_memory(thread_id)

        log.info("Running math agent for task %s", task_id)
        summary = mod.handle(
            workspace, task_id, content, sender, thread_id,
            thread_history=thread_history, thread_memory=thread_memory,
        )
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error", "数学任务超时，请缩小范围重试。")
        log.error("Math task %s timed out", task_id)
        return
    except Exception as e:
        log.error("Math handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"数学任务失败: {e}")
        return

    if summary:
        tags = smart_classify(content, summary)
        tags.append("math")
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Math task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        _write_result(workspace, task_id, "error", "数学任务返回空结果")
        log.error("Math task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Secret handler — local LLM only, nothing leaves localhost
# ---------------------------------------------------------------------------

def _handle_secret(workspace: Path, task_id: str, content: str,
                   sender: str, thread_id: str):
    """Handle privacy-sensitive requests via local Ollama. No cloud APIs."""
    sys.path.insert(0, str(_AGENTS_DIR / "secret"))
    from handler import handle as secret_handle

    thread_history = load_thread_history(thread_id)

    try:
        summary = secret_handle(
            workspace, task_id, content, sender, thread_id,
            thread_history=thread_history,
        )
    except Exception as e:
        _write_result(workspace, task_id, "error", f"Secret agent 失败: {e}")
        log.error("Secret task %s failed: %s", task_id, e)
        return

    if summary:
        _write_result(workspace, task_id, "done", summary, tags=["private"])
        log.info("Secret task %s completed (local-only)", task_id)
        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        _write_result(workspace, task_id, "error", "本地模型返回了空结果，请确认 Ollama 是否在运行")
        log.error("Secret task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# General handler — catch-all
# ---------------------------------------------------------------------------

def _handle_general(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Handle non-writing requests via the general agent."""
    from handler import handle as general_handle

    thread_history = load_thread_history(thread_id)
    thread_memory = load_thread_memory(thread_id)

    try:
        summary = general_handle(
            workspace, task_id, content, sender, thread_id,
            thread_history=thread_history, thread_memory=thread_memory,
        )
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error",
                      "任务超时（10分钟），请拆分成更小的步骤重试。")
        log.error("Task %s timed out", task_id)
        return

    if summary:
        tags = smart_classify(content, summary)
        _write_result(workspace, task_id, "done", summary, tags=tags)
        log.info("Task %s completed successfully", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        _write_result(workspace, task_id, "error", "Claude 返回了空结果")
        log.error("Task %s failed: empty response", task_id)


def _write_result(workspace: Path, task_id: str, status: str, summary: str,
                  tags: list[str] | None = None):
    """Write result JSON for TaskManager to collect."""
    result = {
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "completed_at": _utc_iso(),
    }
    if tags:
        result["tags"] = tags
    result_path = workspace / "result.json"
    tmp_path = result_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.rename(result_path)

    # --- Archive conversation as episode for long-term recall ---
    if status in ("done", "completed", "error", "failed"):
        try:
            # Try items/ first, fallback to legacy tasks/
            item_file = ITEMS_DIR / f"{task_id}.json"
            task_file = TASKS_DIR / f"{task_id}.json"
            src = item_file if item_file.exists() else task_file
            if src.exists():
                task_data = json.loads(src.read_text(encoding="utf-8"))
                messages = task_data.get("messages", [])
                title = task_data.get("title", task_id)
                if len(messages) >= 2:  # Only archive meaningful conversations
                    save_episode(task_id, title, messages, tags=tags)
        except Exception as e:
            log.warning("Episode archival failed for %s: %s", task_id, e)

    # --- Self-iteration: extract lessons from failures ---
    if status in ("error", "failed"):
        try:
            from self_iteration import extract_failure_lesson, save_failure_lesson
            lesson = extract_failure_lesson(task_id, summary[:200], summary)
            if lesson:
                save_failure_lesson(lesson)
        except Exception as e:
            log.warning("Failure lesson extraction failed for %s: %s", task_id, e)

    # --- Auto-flush context before worker exits ---
    try:
        from soul_manager import auto_flush
        context_summary = (
            f"Task {task_id} ({status}): {summary[:500]}\n"
            f"Tags: {', '.join(tags) if tags else 'none'}"
        )
        auto_flush(context_summary)
    except Exception as e:
        log.debug("Auto-flush skipped: %s", e)


def _update_thread_memory(thread_id: str, request: str, summary: str):
    """Append task summary to per-thread memory."""
    thread_dir = MIRA_DIR / "threads" / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    mem_file = thread_dir / "memory.md"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- [{ts}] Request: {request[:80]} → {summary[:120]}\n"

    if mem_file.exists():
        text = mem_file.read_text(encoding="utf-8")
    else:
        text = "# Thread Memory\n\n"
    text += entry
    mem_file.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
