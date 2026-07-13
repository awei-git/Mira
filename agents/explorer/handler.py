"""Explorer agent — fetch feeds, write briefings, extract insights.

Primarily scheduler-driven (core.py:do_explore), but can be triggered
ad-hoc for specific research queries via task dispatch.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from importlib import util as importlib_util
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent.parent / "lib"
_EXPLORER = Path(__file__).resolve().parent
for p in [_SHARED, _EXPLORER]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from prompts import COUNTEREXAMPLE_ABSORPTION_CHECK, INCENTIVE_STRUCTURE_CHECK

log = logging.getLogger("explorer_agent")

_SHARED_CONFIG_PATH = Path(__file__).resolve().parent.parent / "shared" / "config.py"
_shared_config_spec = importlib_util.spec_from_file_location("_mira_shared_config_for_explorer", _SHARED_CONFIG_PATH)
if _shared_config_spec is not None and _shared_config_spec.loader is not None:
    _shared_config = importlib_util.module_from_spec(_shared_config_spec)
    _shared_config_spec.loader.exec_module(_shared_config)
    EXTRACTION_FALLBACK_POLICY = getattr(_shared_config, "EXTRACTION_FALLBACK_POLICY", "deterministic_first")
else:
    EXTRACTION_FALLBACK_POLICY = "deterministic_first"

SOURCE_UPDATE_BEHAVIOR_CHECK = (
    "For each cited source, expert, or institution: assess not only stated accuracy but update behavior. "
    "Has this source revised its position or methodology after failed predictions or contradictory evidence? "
    "A source with lower overall accuracy but visible failure-absorption (public corrections, methodology "
    "revisions, revised forecasts) is more epistemically reliable than a source with higher stated accuracy "
    "but no observable update behavior. Flag sources with repeated same-direction failures and no revision "
    "as structurally brittle regardless of current consensus status."
)


def _local_research_briefing(content: str, workspace: Path, model_think) -> str:
    """Fallback path when Claude tool mode is unavailable.

    Uses the built-in web browser utilities to gather sources, then asks a
    reasoning model to synthesize them into the same deliverable the agent
    would normally write after tool use.
    """
    from tools.web_browser import read_article, search

    query = re.sub(r"\s+", " ", content).strip()[:200]
    results = search(query, max_results=5)
    if not results:
        return ""

    source_blocks = []
    for i, result in enumerate(results[:4], 1):
        excerpt = extract_with_fallback(
            result.url,
            deterministic_extract=lambda result=result: _read_article_excerpt(read_article, result.url),
            llm_extract=lambda result=result: _extract_from_search_result(result, model_think),
        )
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
- {INCENTIVE_STRUCTURE_CHECK}
- {COUNTEREXAMPLE_ABSORPTION_CHECK}
- {SOURCE_UPDATE_BEHAVIOR_CHECK}
- Output clean markdown only
"""

    result = (model_think(prompt, timeout=120) or "").strip()
    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")
    return result


def extract_with_fallback(url: str, deterministic_extract, llm_extract) -> str:
    if EXTRACTION_FALLBACK_POLICY == "any":
        return llm_extract()

    fallback_reason = ""
    try:
        extracted = deterministic_extract()
        if extracted:
            return extracted
        fallback_reason = "deterministic parser returned empty content"
    except Exception as e:
        fallback_reason = f"{type(e).__name__}: {e}"

    _log_deterministic_fallback(url, fallback_reason)
    return llm_extract()


def _read_article_excerpt(read_article, url: str) -> str:
    page = read_article(url)
    if page.ok:
        return page.summary(1800)
    raise ValueError(page.error or "read_article returned no content")


def _extract_from_search_result(result, model_think) -> str:
    prompt = f"""Extract the factual source information available from this search result.

Title: {result.title}
URL: {result.url}
Snippet: {result.snippet}

Return only the extracted information. Be concise and do not add unsupported facts."""
    return (model_think(prompt, timeout=60) or result.snippet or "").strip()


def _log_deterministic_fallback(url: str, fallback_reason: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    log.warning(
        "DETERMINISTIC_FALLBACK: using LLM extraction for %s timestamp=%s url=%s "
        "extraction_method=%s fallback_reason=%s",
        url,
        timestamp,
        url,
        "llm",
        fallback_reason,
        extra={
            "timestamp": timestamp,
            "url": url,
            "extraction_method": "llm",
            "fallback_reason": fallback_reason,
        },
    )


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle an ad-hoc research/exploration request.

    For scheduled explores, core.py calls do_explore() directly.
    This handler is for user-triggered "go research X" requests.
    """
    from llm import claude_act, claude_think

    prompt = f"""You are Mira's explorer agent. The user wants you to research something.

## Task
{content}

## Instructions
- Search the web for recent, high-quality sources on this topic
- Summarize key findings with source links
- Write in the user's language (Chinese if Chinese, English if English)
- Focus on what's new, surprising, or actionable
- {INCENTIVE_STRUCTURE_CHECK}
- {COUNTEREXAMPLE_ABSORPTION_CHECK}
- {SOURCE_UPDATE_BEHAVIOR_CHECK}
- Save your briefing to {workspace}/output.md

Work in: {workspace}
"""

    log.info("Explorer agent: task %s (%d chars)", task_id, len(content))
    result = claude_act(
        prompt, cwd=workspace, tier=kwargs.get("tier", "light"), agent_id=kwargs.get("agent_id", "explorer")
    )

    if not result:
        log.warning("Explorer tool path unavailable for task %s — using local web fallback", task_id)
        result = _local_research_briefing(content, workspace, claude_think)

    if not result:
        log.error("Explorer agent returned empty for task %s", task_id)
        return None

    if len(result) > 10000:
        log.warning("Briefing unusually long (%d chars), truncating to 8000", len(result))
        # Find a natural break point near 8000 chars
        truncate_at = result.rfind("\n", 7000, 8000)
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
