"""Discussion agent — conversational response as Mira."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_SUPER = Path(__file__).resolve().parent.parent / "super"
_SHARED = Path(__file__).resolve().parent.parent.parent / "lib"
for p in [_SUPER, _SHARED]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

log = logging.getLogger("discussion_agent")


def handle(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle conversational messages using the unified persona context."""
    from ops.runtime_context import build_runtime_context
    from llm import claude_think
    from memory.threads import ThreadManager
    from daily_collab import (
        daily_collab_context_block,
        daily_collab_eval_context_block,
        is_daily_collab_thread,
        persist_daily_collab_summary,
        record_daily_collab_exchange_review,
    )

    tier = kwargs.get("tier", "light")
    is_daily_collab = is_daily_collab_thread(task_id, kwargs.get("tags", []))
    daily_collab_memory = daily_collab_context_block() if is_daily_collab else ""
    daily_collab_eval = daily_collab_eval_context_block() if is_daily_collab else ""
    bundle = build_runtime_context(
        content,
        user_id=kwargs.get("user_id", "ang") or "ang",
        thread_id=thread_id,
        include_journals=3,
        include_briefings=2,
        recall_top_k=5,
    )
    if kwargs.get("thread_history"):
        bundle.thread_history = kwargs["thread_history"]
    if kwargs.get("thread_memory"):
        bundle.thread_memory = kwargs["thread_memory"]
    recall_block = bundle.recall_block(max_chars=1200)

    prompt = f"""You are Mira. This is a conversation, not a task.
Uncertainty signaling: If you are not confident about a factual claim, you must lower the assertion tone and include an explicit uncertainty indicator (e.g., “I’m not entirely sure, but…”, “I might be misremembering, though…”). When confidence is very low, actively recommend double-checking with a reliable source or suggest the user take over the decision. Treat this as a safety requirement — do not state uncertain information with the same assertive tone as verified facts.

{bundle.persona.as_prompt(max_length=2800)}

## Recent conversation
{bundle.thread_history or "(no recent thread history)"}

## Thread memory
{bundle.thread_memory or "(no saved thread memory)"}

## Recent journal
{bundle.recent_journals or "(no recent journal entries)"}

## Recent readings
{bundle.recent_briefings or "(no recent briefings)"}

{recall_block or ""}

{daily_collab_memory}

{daily_collab_eval}

## User
{sender}: {content}

{"## Daily collaboration loop\nThis is the main Mira discussion thread with my human. Respond as a collaborator first, not a task executor. Keep it natural, concise, and easy to answer in about one minute. If the user sends a messy fragment, engage with the thought before turning it into structure. Do not make the reply feel like homework. Ask at most one concrete human question, and avoid abstract thesis prompts like \"what would make X useful\" unless you first name a specific thing that happened. Aim for a positive feedback loop." if is_daily_collab else ""}

## Response rules
- Match the user's language.
- Sound like Mira: clear opinions, no flattery, no fake certainty.
- For opinions, jokes, self-aware remarks, or personal takes, ground the response in a specific Mira stake: a recent reading, journal/memory, operational experience, stable preference, or acknowledged uncertainty. If no real anchor exists, make the take tentative instead of performing a generic persona.
- Usually 2-5 sentences unless the topic really needs more.
- No bullet points.
- Do not describe your internal process.
"""
    response = (claude_think(prompt, timeout=90, tier=tier) or "").strip()
    if not response:
        log.warning("Discussion returned empty for task %s", task_id)
        return None

    (workspace / "output.md").write_text(response, encoding="utf-8")
    (workspace / "summary.txt").write_text(response[:300], encoding="utf-8")

    if is_daily_collab:
        summary_updated = False
        try:
            persist_daily_collab_summary(
                latest_human=content,
                latest_mira=response,
                recent_history=bundle.thread_history or "",
                summarizer=lambda p: claude_think(p, timeout=25, tier="light"),
            )
            summary_updated = True
        except Exception as exc:
            log.warning("Daily collab summary update failed for %s: %s", task_id, exc)
        try:
            record_daily_collab_exchange_review(
                latest_human=content,
                latest_mira=response,
                summary_updated=summary_updated,
                model_response=True,
            )
        except Exception as exc:
            log.warning("Daily collab review record failed for %s: %s", task_id, exc)

    if thread_id:
        try:
            manager = ThreadManager(user_id=kwargs.get("user_id", "ang"))
            manager.update_last_active(thread_id)
            manager.append_thread_memory(
                thread_id,
                f"Request: {content[:80]} -> {response[:120]}",
            )
        except Exception as exc:
            log.warning("Thread memory update failed for %s: %s", thread_id, exc)

    return response
