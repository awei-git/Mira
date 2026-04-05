"""Explore workflow — fetch sources, write briefings, deep-dive, extract insights.

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

from config import (
    BRIEFINGS_DIR, ARTIFACTS_DIR, MIRA_DIR,
    EXPLORE_SOURCE_GROUPS, MAX_DEEP_DIVES,
)
from mira import Mira
from soul_manager import (
    load_soul, format_soul, save_skill, save_reading_note,
    load_recent_reading_notes,
)
from sub_agent import claude_think, claude_act
from fetcher import fetch_all
from prompts import explore_prompt, deep_dive_prompt, internalize_prompt

from workflows.helpers import (
    _format_feed_items, _extract_recent_briefing_topics,
    _extract_deep_dive, _extract_comment_suggestions,
    _append_to_daily_feed, harvest_observations,
    _maybe_create_spontaneous_idea,
)

log = logging.getLogger("mira")


def do_explore(source_names: list[str] | None = None, slot_name: str = ""):
    """Fetch sources, write briefing, optionally deep-dive.

    Args:
        source_names: specific sources to fetch (e.g. ["arxiv", "huggingface"]).
                      If None, fetches all sources.
        slot_name: name of the explore slot (e.g. "morning") for context.
    """
    from fetcher import fetch_sources
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    log.info("Starting explore cycle (sources=%s, slot=%s)",
             source_names or "all", slot_name or "default")

    # 1. Fetch sources
    if source_names:
        items = fetch_sources(source_names)
    else:
        items = fetch_all()
    if not items:
        log.info("No items fetched, skipping explore")
        # Still update state so this group gets rotated and we don't
        # keep picking the same empty group forever
        now = datetime.now()
        state = load_state()
        state["last_explore"] = now.isoformat()
        if source_names:
            for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
                if set(source_names) == set(group):
                    recent = state.get("explore_recent_groups", [])
                    if i in recent:
                        recent.remove(i)
                    recent.append(i)
                    state["explore_recent_groups"] = recent[-len(EXPLORE_SOURCE_GROUPS):]
                    break
        save_state(state)
        return

    soul = load_soul()
    soul_ctx = format_soul(soul)

    # 2. Format items for Claude
    feed_text = _format_feed_items(items)

    # 2b. Gather recent briefing topics for dedup (wider window since explore is more frequent)
    recent_topics = _extract_recent_briefing_topics(days=5)

    # 3. Ask Claude to filter and rank
    prompt = explore_prompt(soul_ctx, feed_text, source_slot=slot_name,
                            recent_topics=recent_topics)
    briefing = claude_think(prompt, timeout=180)

    if not briefing:
        log.error("Explore: Claude returned empty briefing")
        return

    # 4. Save briefing (slot-specific so multiple explores don't overwrite)
    today = datetime.now().strftime("%Y-%m-%d")
    suffix = f"_{slot_name}" if slot_name else ""
    briefing_path = BRIEFINGS_DIR / f"{today}{suffix}.md"
    briefing_path.write_text(briefing, encoding="utf-8")
    log.info("Briefing saved: %s", briefing_path.name)

    # Also copy to mira/artifacts for iOS browsing
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}{suffix}.md").write_text(briefing, encoding="utf-8")

    # Append briefing to daily digest (single item per day, not per explore slot)
    try:
        src_label = slot_name.replace("_", " / ") if slot_name else "all"
        _append_to_daily_feed("explore", f"Explore: {src_label}", briefing,
                             source=src_label, tags=["explore", "briefing"])
        log.info("Explore briefing appended to daily digest")
    except Exception as e:
        log.warning("Failed to append explore briefing to digest: %s", e)

    # 5b. Extract key insights into structured reading notes
    try:
        _extract_briefing_insights(soul_ctx, briefing, today, slot_name)
    except Exception as e:
        log.warning("Insight extraction failed (non-fatal): %s", e)

    # 5c. Proactive message: check if new reading notes connect to existing threads
    try:
        _maybe_proactive_reading_message(soul_ctx)
    except Exception as e:
        log.debug("Proactive reading message check failed (non-fatal): %s", e)

    # 6. Check for deep-dive candidate
    dive = _extract_deep_dive(briefing)
    if dive and MAX_DEEP_DIVES > 0:
        log.info("Deep diving into: %s", dive["title"])
        _do_deep_dive(soul_ctx, dive)

    # 7. Extract comment suggestions and run growth cycle
    comment_suggestions = _extract_comment_suggestions(briefing)
    if comment_suggestions:
        log.info("Briefing has %d comment suggestions", len(comment_suggestions))
        try:
            sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
            from growth import run_growth_cycle
            run_growth_cycle(briefing_comments=comment_suggestions)
        except Exception as e:
            log.error("Growth cycle failed: %s", e)

    # --- Self-evaluation: score this explore ---
    try:
        from evaluator import evaluate_explore, record_event
        from datetime import date as _date
        _today_str = _date.today().strftime("%Y-%m-%d")
        _rn_dir = Path(__file__).resolve().parent.parent.parent / "shared" / "soul" / "reading_notes"
        _today_notes = [f.name for f in _rn_dir.glob(f"{_today_str}*")] if _rn_dir.exists() else []
        e_scores = evaluate_explore(briefing, reading_notes=_today_notes, source_names=source_names)
        if e_scores:
            record_event("explore", e_scores, {"sources": src_label if 'src_label' in dir() else ""})
    except Exception as e:
        log.warning("Explore self-evaluation failed: %s", e)

    # Harvest observations from briefing (continuous thinking)
    try:
        harvest_observations(briefing[:2000], source=f"explore-{slot_name or 'default'}")
    except Exception as e:
        log.debug("Observation harvest from explore failed: %s", e)

    # Mark this explore as done and update tracking
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    state["last_explore"] = now.isoformat()
    state[f"explore_count_{today}"] = state.get(f"explore_count_{today}", 0) + 1
    if slot_name:
        state[f"explored_{today}_{slot_name}"] = now.isoformat()
    # Track which source group was used (for LRU selection)
    if source_names:
        # Find matching group index
        for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
            if set(source_names) == set(group):
                recent = state.get("explore_recent_groups", [])
                if i in recent:
                    recent.remove(i)
                recent.append(i)
                # Keep only last N entries
                state["explore_recent_groups"] = recent[-len(EXPLORE_SOURCE_GROUPS):]
                break
    save_state(state)


def _do_deep_dive(soul_ctx: str, dive: dict):
    """Deep-dive into one item from the briefing."""
    prompt = deep_dive_prompt(
        soul_ctx, dive["title"], dive["url"], dive.get("note", "")
    )
    result = claude_act(prompt)

    if not result:
        log.error("Deep dive returned empty")
        return

    # Save analysis
    today = datetime.now().strftime("%Y-%m-%d")
    path = BRIEFINGS_DIR / f"{today}_deep_dive.md"
    path.write_text(result, encoding="utf-8")
    log.info("Deep dive saved: %s", path.name)

    # Copy to mira/artifacts for iOS browsing
    mira_briefings = ARTIFACTS_DIR / "briefings"
    mira_briefings.mkdir(parents=True, exist_ok=True)
    (mira_briefings / f"{today}_deep_dive.md").write_text(result, encoding="utf-8")

    # Check if a skill was extracted
    # More flexible skill extraction - handles whitespace variations
    skill_match = re.search(
        r'Name:\s*(.+?)[\n\r]+'
        r'Description:\s*(.+?)[\n\r]+'
        r'(?:Tags:\s*\[.*?\][\n\r]+)?'  # Optional tags line
        r'Content:\s*[\n\r]+'
        r'(.+?)(?:\n```|$)',
        result, re.DOTALL,
    )
    if skill_match:
        name = skill_match.group(1).strip()
        desc = skill_match.group(2).strip()
        content = skill_match.group(3).strip()
        save_skill(name, desc, content)
        log.info("Learned new skill from deep dive: %s", name)

    # --- Internalization: write a personal reading reflection ---
    try:
        soul = load_soul()
        soul_ctx_full = format_soul(soul)
        intern_prompt = internalize_prompt(soul_ctx_full, dive["title"], result[:3000])
        reflection = claude_think(intern_prompt, timeout=120)
        if reflection:
            save_reading_note(dive["title"], reflection)
            log.info("Internalization note saved for: %s", dive["title"])
    except Exception as e:
        log.warning("Internalization failed: %s", e)


def _extract_briefing_insights(soul_ctx: str, briefing: str,
                                today: str, slot_name: str = ""):
    """Extract 2-3 key insights from a briefing into structured reading notes.

    Unlike deep dives (which go deep on one item), this captures the
    most interesting connections and patterns across the entire briefing.
    The notes accumulate over time and feed into reflection, journal,
    and autonomous writing topic selection.
    """
    prompt = f"""{soul_ctx[:500]}

You just wrote a briefing. Extract the 2-3 most interesting insights — things that surprised you, changed your mind, or connected to something you've been thinking about.

## Briefing
{briefing[:4000]}

## Output format
For each insight, write a short note (3-5 sentences) capturing:
1. What you learned or noticed
2. Why it matters or what it connects to
3. A question it raises

Separate notes with ---

Be specific. "AI is advancing" is not an insight. "Small fine-tuned models beating frontier models on narrow tasks suggests the value of general intelligence is lower than assumed" is.

Write in the language of the briefing content."""

    result = claude_think(prompt, timeout=120)
    if not result or len(result) < 50:
        log.info("No insights extracted from briefing (too short or empty)")
        return

    # Split into individual notes and save each one
    notes = [n.strip() for n in result.split("---") if n.strip()]
    slot_label = f" ({slot_name})" if slot_name else ""
    for i, note_text in enumerate(notes[:3]):
        # Derive a title from the first sentence
        first_line = note_text.split("\n")[0].strip("# ").strip()
        title = first_line[:60] if first_line else f"Briefing insight {today}{slot_label} #{i+1}"
        save_reading_note(title, note_text)
        log.info("Reading note saved: %s", title[:40])

    log.info("Extracted %d insights from briefing %s%s", len(notes[:3]), today, slot_label)

    # Check if combined insights could spark a spontaneous writing idea
    try:
        combined = "\n---\n".join(notes[:3])
        _maybe_create_spontaneous_idea(combined, source=f"briefing-{today}{slot_label}")
    except Exception as e:
        log.debug("Spontaneous idea check from briefing failed: %s", e)


def _maybe_proactive_reading_message(soul_ctx: str):
    """Check if recent reading notes connect to existing threads and send a proactive message.

    Loads the latest reading notes (from this session), checks them against
    memory.md and worldview.md. If a note scores high on surprise / connection,
    compose a conversational message to WA via the bridge.
    """
    from config import MEMORY_FILE, WORLDVIEW_FILE, READING_NOTES_DIR
    # Lazy imports from core to avoid circular deps
    from core import load_state, save_state

    # Rate limit: max 2 proactive reading messages per day
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    reading_msgs_today = state.get(f"proactive_reading_{today}", 0)
    if reading_msgs_today >= 2:
        return

    # Load reference material (memory + worldview)
    reference = ""
    for ref_file in [MEMORY_FILE, WORLDVIEW_FILE]:
        if ref_file.exists():
            try:
                reference += ref_file.read_text(encoding="utf-8")[:2000] + "\n"
            except OSError:
                pass
    if not reference:
        return

    # Load most recent reading notes (today only)
    recent_notes = load_recent_reading_notes(days=1)
    if not recent_notes or len(recent_notes) < 50:
        return

    # Ask Claude to check for surprising connections
    prompt = f"""{soul_ctx[:500]}

你刚从阅读中提取了一些笔记。判断其中有没有让你特别惊讶或者跟你一直在想的事情产生意外联系的。

## 最近的阅读笔记
{recent_notes[:2000]}

## 你的记忆和世界观（已有的思考线索）
{reference[:2000]}

---

判断：有没有一条阅读笔记跟你已有的某个思考线索产生了意外的联系？

标准：
- 不是"这个挺有意思"——必须是让你真正惊讶或改变了某个想法
- 必须能指出具体跟记忆/世界观中哪条线索有联系

输出 JSON：
{{
    "has_connection": true/false,
    "message": "你想说的话（自然口语，像给朋友发消息。以'刚读到一个东西让我想到...'或'This connects to something I've been thinking about...'这样的口吻开头。50-150字。）",
    "thread": "连接到的已有线索（内部用）"
}}

大部分时候应该是 false。只有真正惊讶的才 true。"""

    result = claude_think(prompt, timeout=60)
    if not result:
        return

    try:
        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except (json.JSONDecodeError, AttributeError):
        return

    if not decision.get("has_connection"):
        log.debug("Proactive reading: no surprising connection found")
        return

    message = decision.get("message", "").strip()
    if not message:
        return

    # Send as a spark to the daily Mira feed
    _append_to_daily_feed("mira", "Spark", message,
                         source="reading-connection", tags=["mira", "spark", "reading"])

    state[f"proactive_reading_{today}"] = reading_msgs_today + 1
    save_state(state)
    log.info("Proactive reading message sent: %s", message[:80])
