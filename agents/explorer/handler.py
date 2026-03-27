"""Explorer agent — fetch feeds, write briefings, extract insights.

Primarily scheduler-driven (core.py:do_explore), but can be triggered
ad-hoc for specific research queries via task dispatch.
"""
import logging
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parent.parent / "shared"
_EXPLORER = Path(__file__).resolve().parent
for p in [_SHARED, _EXPLORER]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

log = logging.getLogger("explorer_agent")


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle an ad-hoc research/exploration request.

    For scheduled explores, core.py calls do_explore() directly.
    This handler is for user-triggered "go research X" requests.
    """
    from sub_agent import claude_act

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
        log.error("Explorer agent returned empty for task %s", task_id)
        return None

    # Read output.md if written
    output_file = workspace / "output.md"
    if output_file.exists():
        output = output_file.read_text(encoding="utf-8")
        if len(output) > len(result):
            result = output

    return result
