"""Context helpers — conversation loading, thread history, compression.

Extracted from task_worker.py. Contains:
- load_task_conversation: load conversation history from item/task JSON
- load_thread_history: load recent messages from a thread
- load_thread_memory: load per-thread memory
- compress_conversation: compress long conversation histories
- _truncate_messages: simple truncation fallback
- _load_recent_journals: load last N journal entries
- _load_recent_briefings: load last N briefings
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Add shared directory to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_AGENTS_DIR / "shared") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import MIRA_DIR, JOURNAL_DIR, BRIEFINGS_DIR
from sub_agent import claude_think

log = logging.getLogger("task_worker")


def _items_dir(user_id: str = "ang") -> Path:
    return MIRA_DIR / "users" / user_id / "items"


def _legacy_thread_dirs(user_id: str) -> list[Path]:
    return [
        MIRA_DIR / "users" / user_id / "inbox",
        MIRA_DIR / "users" / user_id / "outbox",
        MIRA_DIR / "inbox",
        MIRA_DIR / "outbox",
    ]


def load_task_conversation(task_id: str, user_id: str = "ang") -> str:
    """Load conversation history from an item (or legacy task) JSON.

    With the new protocol, all messages are in a single items/<id>.json file.
    Falls back to legacy tasks/ + .reply.json sidecar if item not found.
    """
    all_msgs = []

    # Try new items/ first (single source of truth)
    item_file = _items_dir(user_id) / f"{task_id}.json"
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


def load_thread_history(thread_id: str, limit: int = 20, user_id: str = "ang") -> str:
    """Load recent messages from a thread for context injection."""
    if not thread_id:
        return ""

    messages = []
    item_file = _items_dir(user_id) / f"{thread_id}.json"
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            messages.extend(item.get("messages", []))
        except (json.JSONDecodeError, OSError):
            pass

    for folder in _legacy_thread_dirs(user_id):
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


def load_thread_memory(thread_id: str, user_id: str = "ang") -> str:
    """Load per-thread memory if it exists."""
    if not thread_id:
        return ""
    mem_file = MIRA_DIR / "users" / user_id / "threads" / thread_id / "memory.md"
    if not mem_file.exists():
        mem_file = MIRA_DIR / "threads" / thread_id / "memory.md"
    if mem_file.exists():
        return mem_file.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Conversation compression — reduce token usage for long histories
# ---------------------------------------------------------------------------

_COMPRESS_THRESHOLD = 3000  # chars above which we compress

def compress_conversation(conversation: str, max_chars: int = 2000) -> str:
    """Compress a long conversation history to fit within token budget.

    Strategy:
    1. If short enough, return as-is.
    2. Keep the first message (original request) and last 3 messages verbatim.
    3. Summarize the middle messages into a compact block.
    Uses local LLM (claude_think) for summarization to avoid wasting API tokens.
    Falls back to truncation if LLM call fails.
    """
    if not conversation or len(conversation) <= _COMPRESS_THRESHOLD:
        return conversation

    lines = conversation.strip().split("\n")
    # Find message boundaries (lines starting with **[)
    msg_indices = [i for i, l in enumerate(lines) if l.startswith("**[")]

    if len(msg_indices) <= 4:
        # Few messages — just truncate long ones
        return _truncate_messages(conversation, max_chars)

    # Keep first message + last 3 messages verbatim
    first_end = msg_indices[1] if len(msg_indices) > 1 else len(lines)
    last_start = msg_indices[-3]

    first_msg = "\n".join(lines[:first_end])
    middle = "\n".join(lines[first_end:last_start])
    last_msgs = "\n".join(lines[last_start:])

    if len(middle) < 500:
        # Middle is short enough, just combine
        return f"{first_msg}\n{middle}\n{last_msgs}"

    # Try LLM compression of the middle
    try:
        summary = claude_think(
            f"Summarize this conversation excerpt in 3-5 bullet points. "
            f"Focus on decisions made, key information exchanged, and task progress. "
            f"Be concise.\n\n{middle[:3000]}",
            timeout=60
        )
        if summary and len(summary) < len(middle):
            compressed_middle = f"\n*[{len(msg_indices) - 4} earlier messages summarized]*\n{summary}\n"
            result = f"{first_msg}\n{compressed_middle}\n{last_msgs}"
            if len(result) <= max_chars * 1.5:
                return result
    except Exception as e:
        log.warning("Conversation compression LLM failed: %s", e)

    # Fallback: hard truncation
    return _truncate_messages(conversation, max_chars)


def _truncate_messages(conversation: str, max_chars: int) -> str:
    """Simple truncation: keep beginning and end of conversation."""
    if len(conversation) <= max_chars:
        return conversation
    half = max_chars // 2
    return (
        conversation[:half]
        + f"\n\n... ({len(conversation) - max_chars} chars omitted) ...\n\n"
        + conversation[-half:]
    )


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
