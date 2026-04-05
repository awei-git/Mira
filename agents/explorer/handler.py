"""Explorer agent — fetch feeds, write briefings, extract insights.

Primarily scheduler-driven (core.py:do_explore), but can be triggered
ad-hoc for specific research queries via task dispatch.
"""
import logging
import re
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent / "shared"
_EXPLORER = Path(__file__).resolve().parent
for p in [_SHARED, _EXPLORER]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

log = logging.getLogger("explorer_agent")


def _local_research_briefing(content: str, workspace: Path, model_think) -> str:
    """Fallback path when Claude tool mode is unavailable.

    Uses the built-in web browser utilities to gather sources, then asks a
    reasoning model to synthesize them into the same deliverable the agent
    would normally write after tool use.
    """
    from web_browser import read_article, search

    query = re.sub(r"\s+", " ", content).strip()[:200]
    results = search(query, max_results=5)
    if not results:
        return ""

    source_blocks = []
    for i, result in enumerate(results[:4], 1):
        page = read_article(result.url)
        excerpt = page.summary(1800) if page.ok else result.snippet
        source_blocks.append(
            f"""## Source {i}
Title: {result.title}
URL: {result.url}
Snippet: {result.snippet}

Excerpt:
{excerpt}"""
        )

    prompt = f"""You are Mira's explorer agent. Claude tool mode is unavailable, so another system has already gathered source material for you.

## User Task
{content}

## Gathered Sources
{chr(10).join(source_blocks)}

## Instructions
- Write in the user's language (Chinese if Chinese, English if English)
- Focus on what is recent, surprising, or actionable
- Cite sources inline as markdown links using the provided titles/URLs
- If sources conflict, say so explicitly
- Output clean markdown only
"""

    result = (model_think(prompt, timeout=120) or "").strip()
    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")
    return result


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle an ad-hoc research/exploration request.

    For scheduled explores, core.py calls do_explore() directly.
    This handler is for user-triggered "go research X" requests.
    """
    from sub_agent import claude_act, claude_think

    prompt = f"""You are Mira's explorer agent. The user wants you to research something.

## Task
{content}

## Instructions
- Search the web for recent, high-quality sources on this topic
- Summarize key findings with source links
- Write in the user's language (Chinese if Chinese, English if English)
- Focus on what's new, surprising, or actionable
- Save your briefing to {workspace}/output.md

Work in: {workspace}
"""

    log.info("Explorer agent: task %s (%d chars)", task_id, len(content))
    result = claude_act(prompt, cwd=workspace, tier=kwargs.get("tier", "light"))

    if not result:
        log.warning("Explorer tool path unavailable for task %s — using local web fallback", task_id)
        result = _local_research_briefing(content, workspace, claude_think)

    if not result:
        log.error("Explorer agent returned empty for task %s", task_id)
        return None

    if len(result) > 10000:
        log.warning("Briefing unusually long (%d chars), truncating to 8000", len(result))
        # Find a natural break point near 8000 chars
        truncate_at = result.rfind('\n', 7000, 8000)
        if truncate_at == -1:
            truncate_at = 8000
        result = result[:truncate_at]

    if len(result) < 100:
        log.warning("Briefing suspiciously short (%d chars)", len(result))

    # Read output.md if written
    output_file = workspace / "output.md"
    if output_file.exists():
        output = output_file.read_text(encoding="utf-8")
        if len(output) > len(result):
            result = output

    return result
