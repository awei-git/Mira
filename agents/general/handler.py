"""General agent — handles non-specialized tasks via claude_act.

Used by task_worker when a message doesn't route to a specialized agent
(writer, explorer, analyst, etc). This is the catch-all.
"""
import logging
from pathlib import Path

from config import MIRA_DIR
from soul_manager import load_soul, format_soul, append_memory
from sub_agent import claude_act
from prompts import respond_prompt

log = logging.getLogger("general_agent")


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a general request. Returns output text or None on failure."""
    soul = load_soul()
    soul_ctx = format_soul(soul)

    extra_context = ""
    if thread_history:
        extra_context += f"\n\n{thread_history}"
    if thread_memory:
        extra_context += f"\n\n## Thread Memory\n{thread_memory}"

    prompt = respond_prompt(
        soul_ctx,
        f"Mira:{sender}",
        content + extra_context,
        str(workspace),
    )

    log.info("Calling claude_act for task %s", task_id)
    result = claude_act(prompt, cwd=workspace, tier="light")

    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")

        summary_file = workspace / "summary.txt"
        summary = ""
        if summary_file.exists():
            summary = summary_file.read_text(encoding="utf-8").strip()
        if not summary:
            summary = result[:300]

        append_memory(f"Completed Mira task from {sender}: {content[:60]}")
        return summary

    return None
