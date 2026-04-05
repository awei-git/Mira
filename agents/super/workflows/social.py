"""Social media workflows — Substack comments, growth, notes, spark checks.

Extracted from core.py — pure extraction, no logic changes.
"""
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import JOURNAL_DIR, MIRA_DIR
try:
    from mira import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from soul_manager import (
    load_soul, format_soul, load_recent_reading_notes,
    get_memory_size,
)
from sub_agent import claude_think
from prompts import spark_check_prompt

from workflows.helpers import _append_to_daily_feed

log = logging.getLogger("mira")


def do_check_comments():
    """Check Substack posts for new comments and reply as Mira.

    Two loops:
    1. Replies to Mira's own articles (existing)
    2. Replies to Mira's outbound comments on other publications (new)
    """
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting Substack comment check")

    state = load_state()
    state["last_comment_check"] = datetime.now().isoformat()
    save_state(state)

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import check_and_reply_comments, sync_posts_for_ios
        # Sync posts list for iOS app display
        try:
            sync_posts_for_ios()
        except Exception as e:
            log.warning("sync_posts_for_ios failed (non-fatal): %s", e)
        replies = check_and_reply_comments()
        if replies:
            log.info("Replied to %d comments on own posts", len(replies))
            for r in replies:
                log.info("  %s on '%s': %s",
                         r["comment_name"], r["post_title"], r["reply"][:80])
        else:
            log.info("No new comments on own posts")

        # Also check Note replies
        from notes import check_and_reply_note_comments
        note_replies = check_and_reply_note_comments()
        if note_replies:
            log.info("Replied to %d Note comments", len(note_replies))
            for r in note_replies:
                log.info("  %s on note %s: %s",
                         r["commenter"], r["note_id"], r["reply"][:80])
        else:
            log.info("No new Note comments")
    except Exception as e:
        log.error("Comment check failed: %s", e)

    # Also run the growth cycle's reply follow-up (replies to Mira's outbound comments)
    try:
        from growth import _follow_up_on_replies
        soul = load_soul()
        soul_ctx = format_soul(soul)[:500]
        _follow_up_on_replies(soul_ctx)
    except Exception as e:
        log.error("Outbound reply follow-up failed: %s", e)


def do_growth_cycle():
    """Run the Substack growth cycle: likes + proactive comments."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting standalone growth cycle")

    state = load_state()
    state["last_growth_cycle"] = datetime.now().isoformat()
    save_state(state)

    try:
        sm_dir = str(Path(__file__).resolve().parent.parent.parent / "socialmedia")
        shared_dir = str(Path(__file__).resolve().parent.parent.parent / "shared")
        import sys as _sys
        for d in (sm_dir, shared_dir):
            if d not in _sys.path:
                _sys.path.insert(0, d)

        from growth import run_growth_cycle
        run_growth_cycle()
    except Exception as e:
        log.error("Growth cycle failed: %s", e)

    # Collect pending twitter metrics after engagement cycle
    try:
        from twitter import collect_pending_metrics
        import json as _json
        _tw_state_path = Path(__file__).resolve().parent.parent.parent / "socialmedia" / "twitter_state.json"
        if _tw_state_path.exists():
            _tw_state = _json.loads(_tw_state_path.read_text())
            collected = collect_pending_metrics(_tw_state)
            if collected:
                log.info("Collected metrics for %d tweets", len(collected))
                _tw_state_path.write_text(_json.dumps(_tw_state, indent=2, ensure_ascii=False))
    except Exception as e:
        log.debug("Twitter metrics collection failed: %s", e)


def do_notes_cycle():
    """Run the Substack Notes cycle: backfill + standalone Notes."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting Substack Notes cycle")

    state = load_state()
    state["last_notes_cycle"] = datetime.now().isoformat()
    save_state(state)

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from notes import run_notes_cycle

        # Load soul context for voice consistency
        soul = load_soul()
        soul_ctx = format_soul(soul)

        summary = run_notes_cycle(soul_context=soul_ctx)

        if summary.get("backfilled") or summary.get("standalone_posted"):
            parts = []
            if summary["backfilled"]:
                parts.append(f"backfilled {summary['backfilled']} articles")
            if summary["standalone_posted"]:
                parts.append("posted standalone Note")
            log.info("Notes cycle complete: %s", summary)
        else:
            log.info("Notes cycle: nothing to post")
    except Exception as e:
        log.error("Notes cycle failed: %s", e)


def do_spark_check():
    """Check if Mira has a thought worth proactively sharing with WA."""
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # Gather recent context
    recent_reading = load_recent_reading_notes(days=3)
    recent_journal = ""
    if JOURNAL_DIR.exists():
        journals = sorted(JOURNAL_DIR.glob("*.md"), reverse=True)[:2]
        recent_journal = "\n---\n".join(
            j.read_text(encoding="utf-8")[:800] for j in journals
        )

    # Recent conversations with WA
    recent_conversations = ""
    try:
        history_file = MIRA_DIR / "tasks" / "history.jsonl"
        if history_file.exists():
            lines = history_file.read_text(encoding="utf-8").strip().split("\n")
            recent = [json.loads(l) for l in lines[-5:] if l.strip()]
            recent_conversations = "\n".join(
                f"- {r.get('content_preview', '')[:100]}" for r in recent
            )
    except Exception as e:
        log.debug("Spark-check conversation retrieval failed: %s", e)

    prompt = spark_check_prompt(soul_ctx, recent_reading,
                                recent_journal, recent_conversations)
    result = claude_think(prompt, timeout=120)

    # Update state regardless of result
    state["last_spark_check"] = datetime.now().isoformat()
    state["spark_memory_lines"] = get_memory_size()
    save_state(state)

    if not result:
        return

    # Parse response
    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("should_message"):
        log.info("Spark check: nothing worth sharing (%s)",
                 decision.get("reason", "")[:60])
        return

    thought = decision.get("thought", "").strip()
    if not thought:
        return

    # Append spark to daily digest
    _append_to_daily_feed("mira", "Spark", thought[:2000],
                         source="spark-check", tags=["mira", "spark"])

    state[f"sparks_{today}"] = state.get(f"sparks_{today}", 0) + 1
    save_state(state)

    log.info("Spark sent to WA: %s", thought[:80])
