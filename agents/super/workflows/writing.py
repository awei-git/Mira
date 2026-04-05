"""Writing workflow orchestration — autowrite check, skill study writing triggers.

Extracted from core.py — pure extraction, no logic changes.
Note: This is the super-agent's writing orchestration, NOT the writing_workflow.py
in agents/writer/.
"""
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import JOURNAL_DIR, WRITINGS_DIR
try:
    from mira import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from soul_manager import (
    load_soul, format_soul, load_recent_reading_notes,
    detect_recurring_themes,
)
from sub_agent import claude_think
from prompts import autonomous_writing_prompt

from workflows.helpers import (
    _mine_za_ideas, _days_since_last_publish,
    _extract_recent_published_titles, _is_duplicate_topic,
    PUBLISH_COOLDOWN_DAYS,
)

log = logging.getLogger("mira")


def _check_autonomous_writing(soul_ctx: str, bridge: Mira, recent_journal: str):
    """Check if Mira has accumulated enough insight to write something on her own.

    Runs after daily journal. If Mira decides she has something to say,
    creates an auto-task and dispatches the writing pipeline.
    """
    # Guard: don't trigger if publishing is disabled
    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autonomous writing skipped: Substack publishing is disabled")
        return

    # Guard: respect publish cooldown (1 post per 3 days)
    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autonomous writing skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # Detect recurring themes across recent journals + reading notes
    themes = detect_recurring_themes(days=7)
    recent_reading = load_recent_reading_notes(days=7)
    recent_published = _extract_recent_published_titles(days=14)

    # Ask Mira if she wants to write
    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal[:1500],
        recent_published=recent_published,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        return

    # Parse JSON response
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("should_write"):
        log.info("Autonomous writing check: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")
    language = decision.get("language", "mixed")

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Lazy imports from core to avoid circular deps
    from core import (
        load_session_context, save_session_context, session_record,
    )
    from runtime.dispatcher import _dispatch_background

    # Record in session context
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    # Create task visible to iOS
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working",
                              agent_message="开始写作...")

    # Dispatch writing as background task
    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent.parent / "writer" / "writing_agent.py"),
        "auto",
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)


def do_autowrite_check():
    """Standalone check: does Mira have something she wants to write?

    Draws from 杂.md ideas + recent readings + recurring themes.
    More proactive than the journal-only trigger.
    """
    # Lazy imports from core to avoid circular deps
    from core import (
        load_state, save_state,
        load_session_context, save_session_context, session_record,
        session_has_recent,
    )
    from runtime.dispatcher import _dispatch_background

    # Guard: don't bother if publishing is disabled
    from config import SUBSTACK_PUBLISHING_DISABLED
    if SUBSTACK_PUBLISHING_DISABLED:
        log.info("Autowrite check skipped: Substack publishing is disabled")
        return

    # Guard: respect publish cooldown (1 post per 3 days)
    days = _days_since_last_publish()
    if days < PUBLISH_COOLDOWN_DAYS:
        log.info("Autowrite check skipped: last publish %.0f days ago (cooldown: %d days)",
                 days, PUBLISH_COOLDOWN_DAYS)
        return

    # Check session context: don't re-trigger if we recently decided to write or skip
    if session_has_recent("autowrite_triggered", hours=4):
        log.info("Autowrite check skipped: already triggered recently (session context)")
        return
    if session_has_recent("autowrite_skip", hours=2):
        log.info("Autowrite check skipped: recently decided not to write (session context)")
        return

    log.info("Starting autonomous writing check")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather context
    za_fragments = "\n".join(f"- {f}" for f in _mine_za_ideas(count=5))
    themes = detect_recurring_themes(days=7)
    recent_reading = ""
    try:
        recent_reading = load_recent_reading_notes(days=7)
    except Exception as e:
        log.warning("Failed to load reading notes for autowrite: %s", e)

    # Get most recent journal
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("????-??-??.md"), reverse=True)
        if journals:
            recent_journal = journals[0].read_text(encoding="utf-8")[:1500]

    # Gather recent idle-think [SHARE] sparks — these are Mira's most personal,
    # first-person observations and should be the primary source for writing
    recent_sparks = ""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        spark_files = sorted(JOURNAL_DIR.glob(f"{today}_idle_question_*.md"), reverse=True)
        # Also check yesterday
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        spark_files += sorted(JOURNAL_DIR.glob(f"{yesterday}_idle_question_*.md"), reverse=True)

        share_thoughts = []
        for sf in spark_files[:30]:  # cap file reads
            content = sf.read_text(encoding="utf-8")
            share_match = re.search(r'\[SHARE:\s*(.+?)\]', content, re.DOTALL)
            if share_match:
                share_thoughts.append(share_match.group(1).strip())
        if share_thoughts:
            recent_sparks = "\n\n---\n\n".join(share_thoughts[:10])
            log.info("Loaded %d SHARE sparks for autowrite context", len(share_thoughts))
    except Exception as e:
        log.warning("Failed to load idle-think sparks for autowrite: %s", e)

    recent_published = _extract_recent_published_titles(days=14)

    prompt = autonomous_writing_prompt(
        soul_ctx,
        recurring_themes="\n".join(f"- {t}" for t in themes) if themes else "",
        recent_reading=recent_reading[:2000],
        recent_journal=recent_journal,
        za_fragments=za_fragments,
        recent_published=recent_published,
        recent_sparks=recent_sparks,
    )
    result = claude_think(prompt, timeout=120)
    if not result:
        log.info("Autonomous writing check: empty response")
        state = load_state()
        state["last_autowrite_check"] = datetime.now().isoformat()
        save_state(state)
        return

    # Parse decision
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    state = load_state()
    state["last_autowrite_check"] = datetime.now().isoformat()

    if not decision.get("should_write"):
        log.info("Autonomous writing: Mira chose not to write (%s)",
                 decision.get("reason", "")[:80])
        # Record decision in session context so next cycle knows
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip",
                                  decision.get("reason", "")[:100]))
        save_session_context(ctx)
        save_state(state)
        return

    # Mira wants to write!
    title = decision.get("title", "Untitled")
    thesis = decision.get("thesis", "")
    outline = decision.get("outline", "")
    writing_type = decision.get("type", "essay")

    # Dedup: check if a similar topic already exists in ideas/ or published
    if _is_duplicate_topic(title, thesis):
        log.info("Autonomous writing: skipped '%s' — similar topic already exists", title)
        ctx = load_session_context()
        ctx.append(session_record("autowrite_skip", f"duplicate: {title}"))
        save_session_context(ctx)
        save_state(state)
        return

    log.info("Autonomous writing triggered: '%s' [%s]", title, writing_type)

    # Record in session context — prevents duplicate triggers in subsequent cycles
    ctx = load_session_context()
    ctx.append(session_record("autowrite_triggered", title, topic=title))
    save_session_context(ctx)

    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"autowrite_{today}"

    bridge = Mira()
    content = f"{title}\n\n{thesis}\n\n{outline}"
    bridge.create_task(
        task_id=task_id,
        title=f"Mira writes: {title}",
        first_message=f"我想写一篇关于 {title} 的文章。\n\n核心论点: {thesis}\n\n{outline}",
        sender="agent",
        tags=["writing", "autonomous", "auto", writing_type],
        origin="auto",
    )
    bridge.update_task_status(task_id, "working", agent_message="开始写作...")

    _dispatch_background(f"autowrite-{today}", [
        sys.executable,
        str(Path(__file__).resolve().parent.parent.parent / "writer" / "writing_agent.py"),
        "auto",
        "--title", title,
        "--type", writing_type,
        "--idea", content,
    ])

    log.info("Self-initiated writing: '%s' (%s)", title, writing_type)
    save_state(state)
