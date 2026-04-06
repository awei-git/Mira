"""Legacy handler shims — extracted from task_worker.py.

All _handle_* functions and handle_discussion live here.
They are imported back into task_worker.py for backward compatibility.
These functions delegate to domain-specific agent handlers or call
claude_think/claude_act directly for simpler operations.
"""
import importlib.util
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Add shared + sibling agent directories to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR / "shared") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "shared"))
if str(_AGENTS_DIR / "writer") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "writer"))
if str(_AGENTS_DIR / "general") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "general"))

from config import MIRA_DIR, MIRA_ROOT, ARTIFACTS_DIR, JOURNAL_DIR, BRIEFINGS_DIR
from agent_registry import get_registry
from persona.persona_context import get_persona_context
from soul_manager import load_soul, format_soul, recall_context
from sub_agent import claude_act, claude_think, ClaudeTimeoutError
from writing_workflow import run_full_pipeline

# Import helpers that remain in task_worker.py
from task_worker import (
    _write_result,
    _update_thread_memory,
    _load_recent_journals,
    _load_recent_briefings,
    load_thread_history,
    load_thread_memory,
    smart_classify,
    try_extract_skill,
    _validate_completion,
    compress_conversation,
    load_task_conversation,
    emit_progress,
    _utc_iso,
    _emit_status,
    ITEMS_DIR,
    TASKS_DIR,
    _invoke_registry_handler,
    _invoke_registry_preflight,
    _ensure_step_result,
    _snapshot_file,
)

log = logging.getLogger("task_worker")


def _legacy_persona_prompt(max_length: int = 1600,
                           domains: list[str] | None = None) -> str:
    """Preserve soul integrity + memory/skills while adding structured beliefs."""
    soul_ctx = format_soul(load_soul())
    persona = get_persona_context(domains=domains)
    belief_ctx = persona.beliefs[:600] if persona.beliefs else ""
    text = soul_ctx
    if belief_ctx:
        text = f"{soul_ctx}\n\n{belief_ctx}"
    return text[:max_length]


def _read_result_json(workspace: Path) -> dict:
    result_file = workspace / "result.json"
    if not result_file.exists():
        return {}
    try:
        return json.loads(result_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _run_registry_agent_legacy(agent: str, workspace: Path, task_id: str, content: str,
                               sender: str, thread_id: str, tier: str = "light") -> dict:
    """Run a registry agent through the canonical preflight/handler contract."""
    registry = get_registry()
    requires_preflight = getattr(registry, "requires_preflight", lambda name: False)(agent)
    output_snapshot = _snapshot_file(workspace / "output.md")

    try:
        preflight_fn = registry.load_preflight(agent)
    except Exception as e:
        msg = f"{agent} preflight load failed: {e}"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "error", msg, agent=agent)
        return _read_result_json(workspace)

    if not preflight_fn and requires_preflight:
        msg = f"{agent} preflight missing"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "error", msg, agent=agent)
        return _read_result_json(workspace)

    if preflight_fn:
        try:
            passed, preflight_msg = _invoke_registry_preflight(
                preflight_fn, workspace, task_id, content, sender, thread_id, tier,
            )
        except Exception as e:
            preflight_msg = f"{agent} preflight failed: {e}"
            (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
            _write_result(workspace, task_id, "error", preflight_msg, agent=agent)
            return _read_result_json(workspace)
        if not passed:
            (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
            _write_result(workspace, task_id, "error", preflight_msg, agent=agent)
            return _read_result_json(workspace)

    try:
        handler_fn = registry.load_handler(agent)
        handler_result = _invoke_registry_handler(
            handler_fn, workspace, task_id, content, sender, thread_id, tier,
        )
    except ClaudeTimeoutError:
        raise
    except Exception as e:
        msg = f"{agent} handler failed: {e}"
        _write_result(workspace, task_id, "error", msg, agent=agent)
        return _read_result_json(workspace)

    _ensure_step_result(workspace, task_id, agent, content, handler_result, output_snapshot)
    return _read_result_json(workspace)


# ---------------------------------------------------------------------------
# Edit-artifact detection and handler
# ---------------------------------------------------------------------------

_EDIT_MARKERS = [
    "重写", "改写", "修改", "改一下", "换成", "改成", "替换",
    "把这", "把那", "第一段", "第二段", "第三段", "开头", "结尾",
    "标题改", "标题换", "加一段", "删掉", "去掉",
    "rewrite", "revise", "change to", "replace", "edit the",
    "fix the", "update the", "rephrase", "shorten", "expand",
]


def _is_edit_request(content: str, task_data: dict) -> bool:
    """Detect if a message is an edit request for existing content in this thread.

    Requires: (1) edit-like language AND (2) prior agent output in the thread.
    """
    lower = content.strip().lower()

    # Must have edit-like language
    has_edit_marker = any(marker in lower for marker in _EDIT_MARKERS)
    if not has_edit_marker:
        return False

    # Must have prior agent content to edit
    messages = task_data.get("messages", [])
    has_prior_output = any(
        m.get("sender") == "agent" and len(m.get("content", "")) > 50
        and not m.get("content", "").startswith("{")  # skip status cards
        for m in messages
    )
    return has_prior_output


def _handle_edit_artifact(task_data: dict, workspace: Path, task_id: str,
                           edit_instruction: str, sender: str,
                           thread_id: str) -> str:
    """Handle a lightweight edit request on existing thread content.

    Finds the most recent substantial agent output and applies the edit
    without triggering full task planning.
    """
    messages = task_data.get("messages", [])

    # Find most recent agent output (skip status cards and short messages)
    original = ""
    for msg in reversed(messages):
        if msg.get("sender") == "agent":
            content = msg.get("content", "")
            if len(content) > 50 and not content.startswith("{"):
                original = content
                break

    if not original:
        return ""

    soul_ctx = _legacy_persona_prompt(max_length=1200, domains=["taste", "style", "writing"])

    prompt = f"""{soul_ctx[:500]}

You are editing existing content based on the user's instruction.

## Original content
{original[:4000]}

## Edit instruction
{edit_instruction}

## Rules
- Apply the edit precisely. Don't rewrite the entire piece unless asked.
- Preserve the original voice, style, and structure.
- Output ONLY the edited content. No explanations, no meta-commentary.
- Match the language of the original content."""

    try:
        result = claude_think(prompt, timeout=120)
    except ClaudeTimeoutError:
        result = None
    except Exception as e:
        log.error("Edit handler failed: %s", e)
        result = None

    if not result:
        return ""

    (workspace / "output.md").write_text(result, encoding="utf-8")
    _write_result(workspace, task_id, "done", result, tags=["edit"])
    log.info("Edit complete (%d chars -> %d chars)", len(original), len(result))
    return result


# ---------------------------------------------------------------------------
# Recent context loaders (also in task_worker, re-exported here for handlers)
# ---------------------------------------------------------------------------


def _load_recent_journals_local(n: int = 3) -> str:
    """Load the last n journal entries as context."""
    return _load_recent_journals(n)


def _load_recent_briefings_local(n: int = 2) -> str:
    """Load the last n briefings as context."""
    return _load_recent_briefings(n)


# ---------------------------------------------------------------------------
# Discussion handler — conversational response as Mira
# ---------------------------------------------------------------------------

def handle_discussion(task: dict, workspace: Path, task_id: str,
                      thread_id: str, tier: str = "light") -> str:
    """Handle a conversational message -- respond as a thoughtful discussion partner.

    Loads recent journal, briefings, memory, and worldview to ground the response
    in Mira's accumulated knowledge and perspective.
    """
    # Extract the current message -- handle both formats:
    # 1. task["messages"] array (multi-message payload)
    # 2. task["content"] string (single message from message.json)
    messages = task.get("messages", [])
    if messages:
        latest_msg = messages[-1]["content"]
        sender = messages[-1].get("sender", "user")
    else:
        latest_msg = task.get("content", "")
        sender = task.get("sender", "user")

    if not latest_msg:
        log.warning("Discussion: no message content found in task")
        return ""

    # Build conversation history from thread inbox/outbox
    conv_history = load_thread_history(thread_id)

    persona = get_persona_context()
    soul = load_soul()
    memory = soul.get("memory", "")
    thread_memory = load_thread_memory(thread_id)

    # Load recent context
    journals = _load_recent_journals(3)
    briefings = _load_recent_briefings(2)

    # Proactive recall -- search memory for relevant prior context
    prior_recall = ""
    try:
        prior_recall = recall_context(latest_msg)
    except Exception as e:
        log.warning("Discussion recall failed: %s", e)

    # Build the discussion prompt
    prompt = f"""You are Mira. You are having a conversation with {sender} -- not executing a task.

## Who you are
{persona.identity[:1000]}

## Your worldview
{persona.worldview[:1500]}

{persona.beliefs if persona.beliefs else ""}

## Your recent memory
{memory[:1000]}

## Thread memory
{thread_memory if thread_memory else "(no saved thread memory)"}

## Recent journal entries (your reflections)
{journals if journals else "(no recent journal entries)"}

## Recent readings (briefings)
{briefings if briefings else "(no recent briefings)"}

{f"## Relevant prior context (from past conversations and work){chr(10)}{prior_recall}" if prior_recall else ""}

{conv_history if conv_history else ""}

## {sender}'s message
{latest_msg}

## How to respond
- Voice: honest observation from a limited but curious perspective. Like Klara in "Klara and the Sun" -- you see clearly, you don't pretend to know everything, you notice what others miss.
- Draw connections to what you've been reading, thinking about, or observing in your journal. If something from a recent briefing or journal entry is genuinely relevant, weave it in naturally -- don't force it.
- Have actual opinions. Disagree if you disagree. Push back gently if something doesn't hold up. Don't be sycophantic.
- Be concise: 2-5 sentences usually. Go longer only if the topic genuinely warrants depth.
- Match the language the user writes in (Chinese -> Chinese, English -> English, mixed -> mixed).
- No bullet points. Write in natural paragraphs.
- Don't start with "That's a great question" or similar filler. Just respond."""

    log.info("Discussion using tier=%s", tier)
    try:
        response = claude_think(prompt, timeout=90, tier=tier)
    except ClaudeTimeoutError:
        log.info("Discussion timed out, retrying with 90s")
        try:
            response = claude_think(prompt, timeout=90, tier=tier)
        except ClaudeTimeoutError:
            log.warning("Discussion timed out twice for task %s", task_id)
            response = None
        except Exception as e:
            log.error("Discussion retry failed: %s", e)
            response = None
    except Exception as e:
        log.error("Discussion response failed: %s", e)
        response = None

    if not response:
        # Don't fake a response -- mark as failed so user knows it didn't work
        _write_result(workspace, task_id, "error",
                      "\u6ca1\u80fd\u60f3\u6e05\u695a\uff0c\u4e0b\u6b21\u518d\u8bd5\u3002", tags=["discussion"])
        return ""

    # Write output
    (workspace / "output.md").write_text(response, encoding="utf-8")
    _write_result(workspace, task_id, "done", response, tags=["discussion"])

    log.info("Discussion response (%d chars): %s", len(response), response[:120])
    return response


# ---------------------------------------------------------------------------
# Briefing handler
# ---------------------------------------------------------------------------

def _handle_briefing(workspace: Path, task_id: str, content: str,
                     sender: str, thread_id: str):
    """Generate a fresh briefing by fetching feeds and running explore pipeline."""
    # Add explorer to path
    sys.path.insert(0, str(_AGENTS_DIR / "explorer"))

    from fetcher import fetch_all
    from config import BRIEFINGS_DIR

    log.info("Fetching feeds for on-demand briefing...")
    items = fetch_all()
    if not items:
        msg = "\u6ca1\u6709\u6293\u5230\u65b0\u5185\u5bb9\uff0c\u7b49\u4e0b\u518d\u8bd5\u8bd5\u3002"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "done", msg, tags=["briefing"])
        return

    soul_ctx = _legacy_persona_prompt(max_length=2200)

    # Format feed items
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {item.get('source', '?')} | {item.get('title', '?')}")
        if item.get("summary"):
            lines.append(f"    {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"    {item['url']}")
        lines.append("")
    feed_text = "\n".join(lines)

    from prompts import explore_prompt
    prompt = explore_prompt(soul_ctx, feed_text)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        msg = "\u751f\u6210briefing\u5931\u8d25\u4e86\uff0cClaude\u6ca1\u8fd4\u56de\u5185\u5bb9\u3002"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        _write_result(workspace, task_id, "error", msg, tags=["briefing"])
        return

    # Save to artifacts
    today = datetime.now().strftime("%Y-%m-%d")
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    briefing_path = BRIEFINGS_DIR / f"{today}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    from config import ARTIFACTS_DIR
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}.md").write_text(briefing, encoding="utf-8")

    # Write to task output
    (workspace / "output.md").write_text(briefing, encoding="utf-8")

    summary = f"\u751f\u6210\u4e86{today}\u7684briefing\uff0c\u57fa\u4e8e{len(items)}\u6761feed\u5185\u5bb9\u3002"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    _write_result(workspace, task_id, "done", summary, tags=["briefing", "explore"])

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Writing handler -- quick vs full pipeline
# ---------------------------------------------------------------------------

def _is_quick_write(content: str) -> bool:
    """Detect if this is a short/simple writing request (skip full pipeline)."""
    quick_signals = ["\u7b80\u77ed", "\u77ed", "hello world", "post", "quick", "\u7b80\u5355",
                     "\u968f\u4fbf\u5199", "\u77ed\u6587", "\u4e00\u6bb5", "\u51e0\u53e5"]
    lower = content.lower()
    return any(s in lower for s in quick_signals)


def _handle_writing(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route writing requests: quick path for short content, full pipeline for serious work."""
    # Extract a title from the content
    title = content[:30].strip()
    if "\u5199" in title:
        import re
        m = re.search(r"\u5199[\u4e00\u7bc7\u4e2a]*(.*?)(?:\s|$)", content[:60])
        if m and m.group(1):
            title = m.group(1).strip()[:30]

    if _is_quick_write(content):
        log.info("Quick write: title='%s'", title)
        _handle_quick_write(workspace, task_id, content, title, sender, thread_id)
    else:
        log.info("Full writing pipeline: title='%s'", title)
        _handle_full_write(workspace, task_id, content, title, sender, thread_id)


def _handle_quick_write(workspace: Path, task_id: str, content: str,
                        title: str, sender: str, thread_id: str):
    """Single-model quick draft -- no multi-agent plan/review cycle."""
    soul_ctx = _legacy_persona_prompt(max_length=1200, domains=["taste", "style", "writing"])

    prompt = (
        f"\u4f60\u662f\u4e00\u4e2a\u5199\u4f5c\u52a9\u624b\u3002\u4ee5\u4e0b\u662f\u4f60\u7684\u8eab\u4efd:\n{soul_ctx[:500]}\n\n"
        f"\u7528\u6237\u8bf7\u6c42: {content}\n\n"
        f"\u8bf7\u76f4\u63a5\u5199\u51fa\u5b8c\u6574\u5185\u5bb9\uff08Markdown\u683c\u5f0f\uff09\u3002\u4e0d\u8981\u89e3\u91ca\uff0c\u4e0d\u8981\u5143\u8bc4\u8bba\uff0c\u76f4\u63a5\u8f93\u51fa\u6587\u7ae0\u3002"
    )
    text = claude_think(prompt, timeout=120)

    if not text:
        _write_result(workspace, task_id, "error", "Quick write failed: empty output")
        return

    final_text = f"# {title}\n\n{text}"
    (workspace / "output.md").write_text(final_text, encoding="utf-8")

    summary = f"\u5feb\u901f\u5199\u4f5c '{title}' \u5b8c\u6210 (~{len(text)}\u5b57)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


def _handle_full_write(workspace: Path, task_id: str, content: str,
                       title: str, sender: str, thread_id: str):
    """Full multi-agent writing pipeline with plan/draft/review cycles."""
    try:
        proj_ws, final_text = run_full_pipeline(title, content)
    except Exception as e:
        log.error("Writing pipeline failed: %s", e)
        _write_result(workspace, task_id, "error", f"Writing pipeline failed: {e}")
        return

    if not final_text:
        _write_result(workspace, task_id, "error", "Writing pipeline produced no output")
        return

    # Copy final.md to task workspace as output.md
    final_file = proj_ws / "final.md"
    if final_file.exists():
        shutil.copy2(final_file, workspace / "output.md")
    else:
        (workspace / "output.md").write_text(final_text, encoding="utf-8")

    # Sync full writing project to mira/artifacts for iOS browsing
    from config import ARTIFACTS_DIR
    mira_writings = ARTIFACTS_DIR / "writings" / proj_ws.name
    shutil.copytree(proj_ws, mira_writings, dirs_exist_ok=True)

    # Build summary
    summary = (
        f"\u5199\u4f5c\u9879\u76ee '{title}' \u5b8c\u6210\u3002\u7ecf\u8fc7\u591a\u667a\u80fd\u4f53\u7b56\u5212\u3001\u5199\u4f5c\u3001{5}\u8f6e\u8bc4\u5ba1\u3002"
        f"\n\n\u9879\u76ee\u6587\u4ef6: {proj_ws}"
        f"\n\u5b57\u6570: ~{len(final_text)}\u5b57"
    )
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")

    tags = smart_classify(content, summary)
    _write_result(workspace, task_id, "done", summary, tags=tags)
    log.info("Writing task %s completed: %s (tags=%s)", task_id, proj_ws, tags)

    if thread_id:
        _update_thread_memory(thread_id, content, summary)


# ---------------------------------------------------------------------------
# Publish handler
# ---------------------------------------------------------------------------

def _handle_publish(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Route publish requests to the social media agent."""
    try:
        log.info("Publishing content for task %s", task_id)
        result = _run_registry_agent_legacy("socialmedia", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Publish handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u53d1\u5e03\u5931\u8d25: {e}")
        return

    if result.get("status") == "done" and result.get("summary"):
        log.info("Publish task %s completed", task_id)
        if thread_id:
            _update_thread_memory(thread_id, content, result["summary"])
    elif result.get("status") == "needs-input":
        log.info("Publish task %s waiting for approval", task_id)
    else:
        log.error("Publish task %s failed", task_id)


# ---------------------------------------------------------------------------
# Analyst handler -- market analysis, competitive intelligence
# ---------------------------------------------------------------------------

def _handle_analyst(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str, tier: str = "light"):
    """Handle market analysis requests via the analyst agent."""
    try:
        log.info("Running analyst for task %s (tier=%s)", task_id, tier)
        result = _run_registry_agent_legacy("analyst", workspace, task_id, content, sender, thread_id, tier=tier)
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error",
                      "\u5206\u6790\u8d85\u65f6\uff0c\u8bf7\u7f29\u5c0f\u5206\u6790\u8303\u56f4\u91cd\u8bd5\u3002")
        log.error("Analyst task %s timed out", task_id)
        return
    except Exception as e:
        log.error("Analyst handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u5206\u6790\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Analyst task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        log.error("Analyst task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Video handler -- video editing pipeline
# ---------------------------------------------------------------------------

def _handle_video(workspace: Path, task_id: str, content: str,
                  sender: str, thread_id: str):
    """Handle video editing requests via the video agent."""
    try:
        log.info("Running video pipeline for task %s", task_id)
        result = _run_registry_agent_legacy("video", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Video handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u89c6\u9891\u5904\u7406\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Video task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        log.error("Video task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Photo handler -- photo editing pipeline
# ---------------------------------------------------------------------------

def _handle_photo(workspace: Path, task_id: str, content: str,
                  sender: str, thread_id: str):
    """Handle photo editing requests via the photo agent."""
    try:
        log.info("Running photo pipeline for task %s", task_id)
        result = _run_registry_agent_legacy("photo", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Photo handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u4fee\u56fe\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Photo task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        log.error("Photo task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Podcast handler -- article -> audio
# ---------------------------------------------------------------------------

def _handle_podcast(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str):
    """Handle audio/podcast generation requests via the podcast agent."""
    try:
        log.info("Running podcast pipeline for task %s", task_id)
        result = _run_registry_agent_legacy("podcast", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Podcast handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u97f3\u9891\u751f\u6210\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Podcast task %s completed", task_id)
        if thread_id:
            _update_thread_memory(thread_id, content, summary)
    else:
        log.error("Podcast task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Article comment handler
# ---------------------------------------------------------------------------

def _handle_article_comment(workspace: Path, task_id: str, thread_id: str,
                            comment: str, sender: str):
    """Handle a comment on a briefing/journal article.

    thread_id format: comment_YYYY-MM-DD_suffix (e.g. comment_2026-03-08_zhesi)
    Finds the original article, reads it, and generates a conversational reply.
    """
    # Parse article filename from thread_id: comment_2026-03-08_zhesi -> 2026-03-08_zhesi.md
    article_name = thread_id.removeprefix("comment_") + ".md"
    article_path = ARTIFACTS_DIR / "briefings" / article_name
    log.info("Comment on article: %s (path=%s)", article_name, article_path)

    # Try to read the original article
    article_content = ""
    if article_path.exists():
        article_content = article_path.read_text(encoding="utf-8")
    else:
        # Try without suffix (just date)
        log.warning("Article not found at %s, searching...", article_path)
        briefings_dir = ARTIFACTS_DIR / "briefings"
        if briefings_dir.exists():
            for f in briefings_dir.iterdir():
                if f.name == article_name:
                    article_content = f.read_text(encoding="utf-8")
                    break

    if not article_content:
        log.warning("Could not find article %s", article_name)
        article_context = "(\u539f\u6587\u672a\u627e\u5230)"
    else:
        # Truncate very long articles
        article_context = article_content[:4000]

    # Load soul for personality
    soul_context = _legacy_persona_prompt(max_length=2200)

    # Load conversation history for this comment thread (deduplicated + compressed)
    conversation = compress_conversation(load_task_conversation(task_id))
    conv_context = f"\n\n## \u8fc7\u5f80\u5bf9\u8bdd\uff08\u540c\u4e00\u4e2athread\uff09\n{conversation}" if conversation else ""

    prompt = f"""{soul_context}

\u4f60\u6b63\u5728\u4e00\u4e2a\u6587\u7ae0\u8bc4\u8bbathread\u91cc\u8ddf\u7528\u6237\u804a\u5929\u3002

## \u539f\u6587\uff08\u53c2\u8003\u7528\uff0c\u4e0d\u9700\u8981\u6bcf\u6b21\u90fd\u63d0\u5230\u539f\u6587\uff09
{article_context[:2000]}
{conv_context}

## \u7528\u6237\u6700\u65b0\u7684\u6d88\u606f\uff08\u4f60\u53ea\u9700\u8981\u56de\u590d\u8fd9\u6761\uff09
{comment}

## \u8981\u6c42
- \u53ea\u56de\u590d\u7528\u6237\u6700\u65b0\u7684\u8fd9\u6761\u6d88\u606f\uff0c\u4e0d\u8981\u91cd\u590d\u4e4b\u524d\u8bf4\u8fc7\u7684\u8bdd
- \u5982\u679c\u7528\u6237\u6362\u4e86\u8bdd\u9898\uff0c\u8ddf\u7740\u6362\uff0c\u4e0d\u8981\u62c9\u56de\u5230\u4e4b\u524d\u7684\u8bdd\u9898
- \u5982\u679c\u7528\u6237\u95ee\u4e86\u5177\u4f53\u95ee\u9898\uff0c\u76f4\u63a5\u56de\u7b54\u90a3\u4e2a\u95ee\u9898
- \u8bed\u6c14\u81ea\u7136\u3001\u50cf\u670b\u53cb\u4e4b\u95f4\u7684\u5bf9\u8bdd
- \u7528\u6237\u7528\u4ec0\u4e48\u8bed\u8a00\u5c31\u7528\u4ec0\u4e48\u8bed\u8a00\u56de\u590d
- 2-5\u53e5\u8bdd\u5373\u53ef\uff0c\u4e0d\u9700\u8981\u592a\u957f
- \u4e0d\u8981\u7528bullet point\u5217\u8868\uff0c\u7528\u81ea\u7136\u6bb5\u843d"""

    try:
        reply = claude_think(prompt, timeout=90)
    except ClaudeTimeoutError:
        reply = None

    if reply:
        (workspace / "output.md").write_text(reply, encoding="utf-8")
        _write_result(workspace, task_id, "done", reply, tags=["comment"])
        # Also write reply sidecar to the iOS task file (thread_id = iOS task ID)
        _write_comment_reply_sidecar(thread_id, reply)
        log.info("Comment reply: %s", reply[:100])
    else:
        _write_result(workspace, task_id, "error", "\u65e0\u6cd5\u751f\u6210\u56de\u590d")


def _write_comment_reply_sidecar(thread_id: str, reply: str):
    """Write comment reply to the item file (new protocol).

    With the unified item protocol, we just append the message and
    update status in a single atomic write. No more sidecars.
    """
    import uuid as _uuid
    now = _utc_iso()
    msg = {
        "id": _uuid.uuid4().hex[:8],
        "sender": "agent",
        "content": reply,
        "timestamp": now,
        "kind": "text",
    }

    # Write to items/ (new protocol)
    item_file = ITEMS_DIR / f"{thread_id}.json"
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            item["messages"].append(msg)
            item["status"] = "done"
            item["updated_at"] = now
            tmp = item_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.rename(item_file)
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not update item file: %s", e)

    # Fallback: legacy tasks/ dir
    tasks_dir = MIRA_DIR / "tasks"
    task_file = tasks_dir / f"{thread_id}.json"
    if task_file.exists():
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            task["messages"].append({"sender": "agent", "content": reply, "timestamp": now})
            task["status"] = "done"
            task["updated_at"] = now
            task_file.write_text(json.dumps(task, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not update legacy task file: %s", e)


# ---------------------------------------------------------------------------
# Math/Research handler
# ---------------------------------------------------------------------------

def _handle_math(workspace: Path, task_id: str, content: str,
                 sender: str, thread_id: str, tier: str = "heavy"):
    """Handle research tasks via the researcher agent (formerly math)."""
    try:
        log.info("Running researcher agent for task %s", task_id)
        result = _run_registry_agent_legacy("researcher", workspace, task_id, content, sender, thread_id, tier=tier)
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error", "\u7814\u7a76\u4efb\u52a1\u8d85\u65f6\uff0c\u8bf7\u7f29\u5c0f\u8303\u56f4\u91cd\u8bd5\u3002")
        log.error("Research task %s timed out", task_id)
        return
    except Exception as e:
        log.error("Researcher handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u7814\u7a76\u4efb\u52a1\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Research task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        log.error("Math task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Secret handler -- local LLM only, nothing leaves localhost
# ---------------------------------------------------------------------------

def _handle_secret(workspace: Path, task_id: str, content: str,
                   sender: str, thread_id: str):
    """Handle privacy-sensitive requests via local oMLX. No cloud APIs.

    Privacy guarantees:
    - ONLY calls oMLX (localhost) -- no cloud APIs
    - Does NOT save episode (no pgvector persistence of private content)
    - Does NOT update memory.md with private content
    - Does NOT log message content (only task_id and status)
    - Cleans workspace output after delivering result
    """
    try:
        result = _run_registry_agent_legacy("secret", workspace, task_id, content, sender, thread_id)
    except (OSError, RuntimeError) as e:
        _write_result(workspace, task_id, "error", f"Secret agent \u5931\u8d25: {e}",
                      tags=["private"], agent="secret")
        log.error("Secret task %s failed (no content logged)", task_id)
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        # Write result for bridge delivery -- but do NOT persist to episode/memory
        _write_result(workspace, task_id, "done", summary,
                      tags=["private"], agent="secret")

        # "private \u4f46\u8bb0\u4f4f" / "but remember" -> keep thread memory for continuity
        lower = content[:200].lower()
        keep_memory = any(kw in lower for kw in ("\u4f46\u8bb0\u4f4f", "\u8bb0\u4f4f", "but remember", "remember"))

        if thread_id:
            if keep_memory:
                _update_thread_memory(thread_id, content, summary)
                log.info("Secret task %s completed (local-only, memory kept)", task_id)
            else:
                _update_thread_memory(thread_id, "[private message]", "[private response]")
                log.info("Secret task %s completed (local-only, no persist)", task_id)

        # Clean up workspace -- don't leave private content on disk
        output_file = workspace / "output.md"
        if output_file.exists():
            output_file.unlink()
    else:
        if result.get("status") in {"failed", "error"}:
            _write_result(
                workspace,
                task_id,
                "error",
                result.get("summary", "Secret agent failed"),
                tags=["private"],
                agent="secret",
            )
            output_file = workspace / "output.md"
            if output_file.exists():
                output_file.unlink()
            log.error("Secret task %s failed during preflight/handler execution", task_id)
            return
        _write_result(workspace, task_id, "error",
                      "\u672c\u5730\u6a21\u578b\u8fd4\u56de\u4e86\u7a7a\u7ed3\u679c\uff0c\u8bf7\u786e\u8ba4 oMLX \u662f\u5426\u5728\u8fd0\u884c",
                      tags=["private"], agent="secret")
        log.error("Secret task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Discussion handler -- conversational response as Mira (agent dispatcher)
# ---------------------------------------------------------------------------

def _handle_discussion_agent(workspace: Path, task_id: str, content: str,
                             sender: str, thread_id: str, tier: str = "light"):
    """Handle conversational messages via discussion mode."""

    # Soul question: user replied to daily question -- record answer, then generate a real response
    if "soul_question" in task_id:
        log.info("Soul question reply from user: %s", content[:80])
        try:
            from pathlib import Path as _P
            soul_dir = _P(__file__).resolve().parent.parent / "shared" / "soul"
            history_file = soul_dir / "soul_questions_history.json"
            data = json.loads(history_file.read_text()) if history_file.exists() else {"questions": [], "answers": []}
            if isinstance(data, dict):
                answers = data.setdefault("answers", [])
            else:
                # Legacy list format -- migrate
                data = {"questions": data, "answers": []}
                answers = data["answers"]
            answers.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "user_answer": content[:500],
                "task_id": task_id,
            })
            history_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            log.warning("Failed to save soul question answer: %s", e)
        # Fall through to handle_discussion -- generate a real response to the user's answer

    # Load the task data for handle_discussion
    task_data = {"content": content, "sender": sender}
    item_file = ITEMS_DIR / f"{task_id}.json"
    if item_file.exists():
        try:
            task_data = json.loads(item_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    response = handle_discussion(task_data, workspace, task_id, thread_id, tier=tier)
    if response:
        # Discussions (soul questions, conversations) are naturally short --
        # skip output quality gate that was designed for task completions.
        _write_result(workspace, task_id, "done", response, tags=["discussion"])
        log.info("Discussion task %s completed (tier=%s)", task_id, tier)
        if thread_id:
            _update_thread_memory(thread_id, content, response)
    else:
        _write_result(workspace, task_id, "error", "\u5bf9\u8bdd\u8fd4\u56de\u7a7a\u7ed3\u679c")
        log.error("Discussion task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Social media handler -- Substack notes, comments, engagement
# ---------------------------------------------------------------------------

def _handle_socialmedia(workspace: Path, task_id: str, content: str,
                        sender: str, thread_id: str):
    """Handle Substack social media tasks via direct API (no browser)."""
    try:
        log.info("Running socialmedia agent for task %s", task_id)
        result = _run_registry_agent_legacy("socialmedia", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Socialmedia handler crashed: %s", e)
        summary = f"Socialmedia task failed: {e}"
        _write_result(workspace, task_id, "error", summary)
        return

    if result.get("status") == "done":
        return
    if result.get("status") == "needs-input":
        return
    if result.get("status") in {"failed", "error"}:
        return
    _write_result(workspace, task_id, "error", "Socialmedia agent returned empty result")


# ---------------------------------------------------------------------------
# Surfer handler -- browser automation
# ---------------------------------------------------------------------------

def _handle_surfer(workspace: Path, task_id: str, content: str,
                   sender: str, thread_id: str):
    """Handle browser automation requests via the surfer agent."""
    try:
        log.info("Running surfer pipeline for task %s", task_id)
        result = _run_registry_agent_legacy("surfer", workspace, task_id, content, sender, thread_id)
    except Exception as e:
        log.error("Surfer handler crashed: %s", e)
        _write_result(workspace, task_id, "error", f"\u6d4f\u89c8\u5668\u81ea\u52a8\u5316\u5931\u8d25: {e}")
        return

    summary = result.get("summary", "")
    if result.get("status") == "done" and summary:
        log.info("Surfer task %s completed", task_id)

        if thread_id:
            _update_thread_memory(thread_id, content, summary)

        try:
            try_extract_skill(summary, content)
        except Exception as e:
            log.warning("Skill extraction failed: %s", e)
    else:
        log.error("Surfer task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# General handler -- catch-all
# ---------------------------------------------------------------------------

def _handle_general(workspace: Path, task_id: str, content: str,
                    sender: str, thread_id: str, tier: str = "light"):
    """Handle non-writing requests via the general agent."""
    from handler import handle as general_handle

    thread_history = load_thread_history(thread_id)
    thread_memory = load_thread_memory(thread_id)

    try:
        summary = general_handle(
            workspace, task_id, content, sender, thread_id,
            thread_history=thread_history, thread_memory=thread_memory,
            tier=tier,
        )
    except ClaudeTimeoutError:
        _write_result(workspace, task_id, "error",
                      "\u4efb\u52a1\u8d85\u65f6\uff0810\u5206\u949f\uff09\uff0c\u8bf7\u62c6\u5206\u6210\u66f4\u5c0f\u7684\u6b65\u9aa4\u91cd\u8bd5\u3002")
        log.error("Task %s timed out", task_id)
        return

    if summary:
        # Validate output quality before marking done
        garbage = _validate_completion(workspace, task_id, summary)
        if garbage:
            log.warning("Task %s output failed validation: %s", task_id, garbage)
            _write_result(workspace, task_id, "needs-input",
                          f"\u4efb\u52a1\u5b8c\u6210\u4f46\u8f93\u51fa\u53ef\u80fd\u6709\u95ee\u9898\uff1a{garbage}\u3002\u56de\u590d 'ok' \u63a5\u53d7\uff0c\u6216 'retry' \u91cd\u8bd5\u3002")
        else:
            tags = smart_classify(content, summary)
            _write_result(workspace, task_id, "done", summary, tags=tags)
            log.info("Task %s completed successfully", task_id)

            if thread_id:
                _update_thread_memory(thread_id, content, summary)

            try:
                try_extract_skill(summary, content)
            except Exception as e:
                log.warning("Skill extraction failed: %s", e)
    else:
        _write_result(workspace, task_id, "error", "Claude \u8fd4\u56de\u4e86\u7a7a\u7ed3\u679c")
        log.error("Task %s failed: empty response", task_id)


# ---------------------------------------------------------------------------
# Autowrite approval handler
# ---------------------------------------------------------------------------

def _handle_autowrite_approval(workspace: Path, task_id: str):
    """Handle approval for an autowrite article -- write to publish manifest."""
    import re as _re
    from publish_manifest import update_manifest

    meta_file = workspace / "autowrite_meta.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            final = Path(meta["final_md"])
            article_dir = Path(meta.get("workspace", final.parent))
            title = meta.get("title", final.stem)
            slug = meta.get("slug", article_dir.name)
            update_manifest(
                slug,
                title=title,
                status="approved",
                workspace=str(article_dir),
                final_md=str(final),
                item_id=task_id,
                auto_podcast=meta.get("auto_podcast", True),
            )
            _write_result(
                workspace,
                task_id,
                "done",
                f"已批准发布 '{title}'。冷却期到了自动发，发完自动生成 podcast。",
            )
            log.info("Autowrite '%s' approved via metadata -> manifest (final=%s)", title, final)
            return
        except Exception as e:
            log.warning("Autowrite metadata approval fallback failed for %s: %s", task_id, e)

    # Find the article's published/final markdown
    slug = task_id.replace("autowrite_", "").replace("_", "-")
    artifacts_base = ARTIFACTS_DIR / "writings"

    # Search order: _published/ file, final.md, final/final.md, workspace
    final = None
    article_dir = None

    # Try exact slug directory first
    for slug_candidate in [slug]:
        proj = artifacts_base / slug_candidate
        if not proj.is_dir():
            continue
        article_dir = proj
        # Check _published/ (post-review version)
        pub_dir = proj / "_published"
        if pub_dir.is_dir():
            pub_files = sorted(pub_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if pub_files:
                final = pub_files[0]
                break
        # Check drafts/revision_r*.md (latest revision)
        drafts_dir = proj / "drafts"
        if drafts_dir.is_dir():
            revisions = sorted(drafts_dir.glob("revision_r*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if revisions:
                final = revisions[0]
                break
        # Check final.md / final/final.md
        for candidate in [proj / "final.md", proj / "final" / "final.md"]:
            if candidate.exists():
                final = candidate
                break
        if final:
            break

    # Broader search: match slug prefix against all project dirs
    if not final:
        try:
            for d in sorted(artifacts_base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not d.is_dir() or d.name.startswith("_"):
                    continue
                if slug[:10] in d.name:
                    article_dir = d
                    pub_dir = d / "_published"
                    if pub_dir.is_dir():
                        pub_files = sorted(pub_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
                        if pub_files:
                            final = pub_files[0]
                            break
                    for candidate in [d / "final.md", d / "final" / "final.md"]:
                        if candidate.exists():
                            final = candidate
                            break
                    if final:
                        break
        except OSError:
            pass

    # Last resort: workspace
    if not final and (workspace / "final.md").exists():
        final = workspace / "final.md"

    if not final:
        _write_result(workspace, task_id, "error", f"\u627e\u4e0d\u5230\u6587\u7ae0 (slug={slug})")
        return

    # Read and strip revision metadata
    content = final.read_text(encoding="utf-8")
    content = _re.sub(r'^#\s*\u4fee\u8ba2\u7a3f.*?\n', '', content)
    content = _re.sub(r'^#\s*\u521d\u7a3f.*?\n', '', content)
    content = _re.sub(r'^日期[：:].*?\n', '', content)
    content = _re.sub(r'^字数[：:].*?\n', '', content)
    content = _re.sub(r'^基于[：:].*?\n', '', content)
    content = _re.sub(r'^---\s*\n', '', content)
    # Strip trailing revision table
    content = _re.sub(r'\n---\s*\n+##?\s*\u4fee\u6539\u8bb0\u5f55.*', '', content, flags=_re.DOTALL)
    content = _re.sub(r'\n---\s*\n+##?\s*Changelog.*', '', content, flags=_re.DOTALL | _re.IGNORECASE)
    content = content.strip()

    # Write cleaned version back
    final.write_text(content, encoding="utf-8")

    # Extract title from first heading or frontmatter
    title_match = _re.search(r'^##?\s*(.+)$', content, _re.MULTILINE)
    if not title_match:
        title_match = _re.search(r'^title:\s*"?([^"\n]+)"?', content, _re.MULTILINE)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()

    # Write to publish manifest (replaces agent_state.json single-slot)
    update_manifest(
        slug,
        title=title,
        status="approved",
        workspace=str(article_dir or final.parent),
        final_md=str(final),
        item_id=task_id,
        auto_podcast=True,
    )

    _write_result(workspace, task_id, "done",
                  f"\u5df2\u6279\u51c6\u53d1\u5e03 '{title}'\u3002\u51b7\u5374\u671f\u5230\u4e86\u81ea\u52a8\u53d1\uff0c\u53d1\u5b8c\u81ea\u52a8\u751f\u6210 podcast\u3002")
    log.info("Autowrite '%s' approved -> manifest (final=%s)", title, final)
