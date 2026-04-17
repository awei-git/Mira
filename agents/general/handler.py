"""General agent — handles non-specialized tasks via claude_act.

Used by task_worker when a message doesn't route to a specialized agent
(writer, explorer, analyst, etc). This is the catch-all.
"""

import logging
import re
from pathlib import Path

from publish.preflight import preflight_check
from ops.runtime_context import build_runtime_context
from memory.soul import load_skills_for_task
from llm import claude_act, claude_think
from prompts import respond_prompt

log = logging.getLogger("general_agent")

# Patterns that suggest the task needs web research
_WEB_HINTS = re.compile(
    r"搜[索一]|查[一找]|search|look\s*up|find\s+out|research|最新|latest|current|"
    r"news|新闻|what\s+is|怎么样|how\s+does|网上|online|url|http|www\.",
    re.IGNORECASE,
)
_EFFECTFUL_HINTS = re.compile(
    r"publish|post|tweet|send\s+email|email\s+this|delete|remove|rm\s|unlink|"
    r"overwrite|save\s+to|write\s+to\s+file|edit\s+the\s+file|modify\s+the\s+file|"
    r"发布|发帖|发推|发送邮件|删除|移除|覆盖|写入文件|修改文件|保存到",
    re.IGNORECASE,
)


def preflight(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """General agent only handles low-risk tasks; effectful intents must route elsewhere."""
    instruction = (content or "").strip()
    if not instruction:
        return False, "PREFLIGHT BLOCKED [general]: empty instruction"
    if _EFFECTFUL_HINTS.search(instruction):
        return (
            False,
            "PREFLIGHT BLOCKED [general]: effectful request should use a specialized agent",
        )
    if _WEB_HINTS.search(instruction):
        result = preflight_check(
            "external_api",
            {
                "instruction": instruction,
                "endpoint": "auto:web_research",
                "method": "search",
            },
        )
        if not result.passed:
            return False, result.summary()
    return True, ""


def _maybe_web_research(content: str, max_chars: int = 6000) -> str:
    """Pre-fetch web research if the task looks like it needs it.

    Returns formatted web context string, or empty string if not needed.
    """
    if not _WEB_HINTS.search(content):
        return ""
    try:
        from tools.web_browser import search_and_read

        # Extract a search query from the content (first 80 chars, cleaned)
        query = re.sub(r"[，。！？\n]", " ", content[:120]).strip()
        log.info("Pre-fetching web research for: %s", query[:60])
        result = search_and_read(query, max_results=3, max_chars_per_page=2000)
        if result and "[No search results" not in result:
            return f"\n\n## Web Research (auto-fetched)\n{result[:max_chars]}"
    except Exception as e:
        log.warning("Web pre-research failed: %s", e)
    return ""


def handle(
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    thread_history: str = "",
    thread_memory: str = "",
    tier: str = "light",
    agent_id: str = "general",
) -> str | None:
    """Handle a general request. Returns output text or None on failure."""
    bundle = build_runtime_context(
        content,
        user_id="ang",
        thread_id=thread_id,
    )
    # Respect explicitly supplied thread state from the caller.
    if thread_history:
        bundle.thread_history = thread_history
    if thread_memory:
        bundle.thread_memory = thread_memory

    extra_context = ""
    if bundle.thread_history:
        extra_context += f"\n\n{bundle.thread_history}"
    if bundle.thread_memory:
        extra_context += f"\n\n## Thread Memory\n{bundle.thread_memory}"
    recall_block = bundle.recall_block(max_chars=1000)
    if recall_block:
        extra_context += f"\n\n{recall_block}"

    # Inject relevant skills for this task
    skills_ctx = load_skills_for_task(content, agent_type="general")
    if skills_ctx:
        extra_context += f"\n\n## Relevant Skills\n{skills_ctx}"
        log.info("Injected %d chars of relevant skills", len(skills_ctx))

    # Auto web research for tasks that look like they need it
    web_ctx = _maybe_web_research(content)
    if web_ctx:
        extra_context += web_ctx
        log.info("Added web research context (%d chars)", len(web_ctx))

    prompt = respond_prompt(
        bundle.persona.as_prompt(max_length=2600),
        f"Mira:{sender}",
        content + extra_context,
        str(workspace),
    )

    log.info("Calling claude_act for task %s (tier=%s, agent=%s)", task_id, tier, agent_id)
    result = claude_act(prompt, cwd=workspace, tier=tier, agent_id=agent_id)

    if not result:
        log.warning("General agent tool path unavailable for task %s — using think-only fallback", task_id)
        fallback_prompt = (
            prompt + "\n\nTool execution is unavailable. Answer directly using only the context above. "
            "Do not claim you edited files, ran commands, or fetched additional sources unless they are already included."
        )
        result = claude_think(fallback_prompt, timeout=120, tier=tier)

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
