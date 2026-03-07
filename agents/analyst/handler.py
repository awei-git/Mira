"""Analyst agent — handles market analysis, trend detection, competitive intelligence.

Uses specialized skills (quantitative reasoning, trend signals, competitive mapping,
synthesis) loaded from agents/analyst/skills/ to provide structured, decision-ready analysis.
"""
import logging
from pathlib import Path

from config import MIRA_DIR
from soul_manager import load_soul, format_soul, append_memory
from sub_agent import claude_act
from prompts import analyst_prompt

log = logging.getLogger("analyst_agent")

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _load_analyst_skills() -> str:
    """Load all analyst skill files as context."""
    if not _SKILLS_DIR.exists():
        return ""
    parts = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
    return "\n\n---\n\n".join(parts)


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a market analysis request. Returns output text or None on failure."""
    soul = load_soul()
    soul_ctx = format_soul(soul)
    skills_ctx = _load_analyst_skills()

    extra_context = ""
    if thread_history:
        extra_context += f"\n\n{thread_history}"
    if thread_memory:
        extra_context += f"\n\n## Thread Memory\n{thread_memory}"

    prompt = analyst_prompt(
        soul_ctx,
        skills_ctx,
        f"Mira:{sender}",
        content + extra_context,
        str(workspace),
    )

    log.info("Calling claude_act for analyst task %s", task_id)
    result = claude_act(prompt, cwd=workspace)

    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")

        summary_file = workspace / "summary.txt"
        summary = ""
        if summary_file.exists():
            summary = summary_file.read_text(encoding="utf-8").strip()
        if not summary:
            summary = result[:300]

        append_memory(f"Completed analyst task from {sender}: {content[:60]}")
        return summary

    return None
