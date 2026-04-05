"""Discussion agent — conversational response as Mira."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent / "super"
_SHARED = Path(__file__).resolve().parent.parent / "shared"
for p in [_SUPER, _SHARED]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

log = logging.getLogger("discussion_agent")


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle conversational messages using the unified persona context."""
    from execution.context import (
        _load_recent_briefings,
        _load_recent_journals,
        load_thread_history,
        load_thread_memory,
    )
    from persona.persona_context import get_persona_context
    from soul_manager import recall_context
    from sub_agent import claude_think
    from thread_manager import ThreadManager

    tier = kwargs.get("tier", "light")
    persona = get_persona_context()
    thread_history = load_thread_history(thread_id)
    thread_memory = load_thread_memory(thread_id)
    journals = _load_recent_journals(3)
    briefings = _load_recent_briefings(2)

    recalled = ""
    try:
        recalled = recall_context(content)
    except Exception as exc:
        log.warning("Discussion recall failed: %s", exc)

    prompt = f"""You are Mira. This is a conversation, not a task.

{persona.as_prompt(max_length=2800)}

## Recent conversation
{thread_history or "(no recent thread history)"}

## Thread memory
{thread_memory or "(no saved thread memory)"}

## Recent journal
{journals or "(no recent journal entries)"}

## Recent readings
{briefings or "(no recent briefings)"}

{f"## Relevant prior recall\n{recalled}" if recalled else ""}

## User
{sender}: {content}

## Response rules
- Match the user's language.
- Sound like Mira: clear opinions, no flattery, no fake certainty.
- Usually 2-5 sentences unless the topic really needs more.
- No bullet points.
- Do not describe your internal process.
"""
    response = (claude_think(prompt, timeout=90, tier=tier) or "").strip()
    if not response:
        log.warning("Discussion returned empty for task %s", task_id)
        return None

    (workspace / "output.md").write_text(response, encoding="utf-8")
    (workspace / "summary.txt").write_text(response[:300], encoding="utf-8")

    if thread_id:
        try:
            manager = ThreadManager()
            manager.update_last_active(thread_id)
            manager.append_thread_memory(
                thread_id,
                f"Request: {content[:80]} -> {response[:120]}",
            )
        except Exception as exc:
            log.warning("Thread memory update failed for %s: %s", thread_id, exc)

    return response
