"""Discussion agent — conversational response as Mira.

Handles chat messages where the user wants a conversation, not a task.
Uses soul context (identity, worldview, memory, journal) to respond as Mira.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent / "super"
_SHARED = Path(__file__).resolve().parent.parent / "shared"
_WRITER = Path(__file__).resolve().parent.parent / "writer"
_EXPLORER = Path(__file__).resolve().parent.parent / "explorer"
for p in [_SUPER, _SHARED, _WRITER, _EXPLORER]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

log = logging.getLogger("discussion_agent")


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle conversational messages. Returns response text."""
    from task_worker import handle_discussion

    task_data = {"content": content, "sender": sender}
    tier = kwargs.get("tier", "light")

    response = handle_discussion(task_data, workspace, task_id, thread_id, tier=tier)
    if response:
        return response

    log.warning("Discussion returned empty for task %s", task_id)
    return None
