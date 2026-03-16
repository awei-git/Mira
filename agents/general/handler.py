"""General agent — handles non-specialized tasks via claude_act.

Used by task_worker when a message doesn't route to a specialized agent
(writer, explorer, analyst, etc). This is the catch-all.
"""
import logging
import re
from pathlib import Path

from config import MIRA_DIR
from soul_manager import load_soul, format_soul
from sub_agent import claude_act
from prompts import respond_prompt

log = logging.getLogger("general_agent")

# Patterns that suggest the task needs web research
_WEB_HINTS = re.compile(
    r"搜[索一]|查[一找]|search|look\s*up|find\s+out|research|最新|latest|current|"
    r"news|新闻|what\s+is|怎么样|how\s+does|网上|online|url|http|www\.",
    re.IGNORECASE,
)


def _maybe_web_research(content: str, max_chars: int = 6000) -> str:
    """Pre-fetch web research if the task looks like it needs it.

    Returns formatted web context string, or empty string if not needed.
    """
    if not _WEB_HINTS.search(content):
        return ""
    try:
        from web_browser import search_and_read
        # Extract a search query from the content (first 80 chars, cleaned)
        query = re.sub(r"[，。！？\n]", " ", content[:120]).strip()
        log.info("Pre-fetching web research for: %s", query[:60])
        result = search_and_read(query, max_results=3, max_chars_per_page=2000)
        if result and "[No search results" not in result:
            return f"\n\n## Web Research (auto-fetched)\n{result[:max_chars]}"
    except Exception as e:
        log.warning("Web pre-research failed: %s", e)
    return ""


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

    # Auto web research for tasks that look like they need it
    web_ctx = _maybe_web_research(content)
    if web_ctx:
        extra_context += web_ctx
        log.info("Added web research context (%d chars)", len(web_ctx))

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

        return summary

    return None
