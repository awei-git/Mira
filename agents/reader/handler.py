"""Reader agent runtime handler.

The reader package primarily owns scheduled book-review workflows. This
handler gives the manifest registry a safe runtime entry point without
triggering the long scheduled workflow for arbitrary app-submitted tasks.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("reader_agent")


def _write_output(workspace: Path, text: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "output.md").write_text(text, encoding="utf-8")
    (workspace / "summary.txt").write_text(text.splitlines()[0].strip() or text[:120], encoding="utf-8")


def handle(
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    thread_history: str = "",
    thread_memory: str = "",
    tier: str = "light",
    agent_id: str = "reader",
    **kwargs,
) -> str:
    """Handle app-submitted reader tasks with a durable artifact.

    Scheduled reading reports still run through ``daily_book_review.main()``.
    For ad-hoc task routing, fail closed into a clear planning artifact instead
    of silently launching a network/LLM-heavy scheduled job.
    """
    del thread_history, thread_memory, tier, agent_id, kwargs

    request = content.strip() or "(empty request)"
    summary = (
        "Reader request captured for manual/scheduled workflow review.\n\n"
        f"- task_id: {task_id}\n"
        f"- sender: {sender}\n"
        f"- thread_id: {thread_id or '(none)'}\n"
        f"- request: {request}\n\n"
        "The reader agent is currently scheduled-workflow only. Route book "
        "report generation through the daily book-review scheduler or promote "
        "this request to a declared reader workflow before marking it done."
    )
    _write_output(workspace, summary)
    log.info("Reader handler captured task %s without running scheduled workflow", task_id)
    return summary
