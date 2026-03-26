"""Discussion agent — conversational response as Mira."""
from __future__ import annotations

import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent / "super"
_SHARED = Path(__file__).resolve().parent.parent / "shared"
if str(_SUPER) not in sys.path:
    sys.path.insert(0, str(_SUPER))
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs):
    """Handle conversational messages via discussion mode."""
    from task_worker import _handle_discussion_agent
    _handle_discussion_agent(workspace, task_id, content, sender, thread_id)
