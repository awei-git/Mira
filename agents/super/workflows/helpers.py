"""Shared helper functions used by multiple workflow modules.

Extracted from core.py — pure extraction, no logic changes.
"""
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from config import (
    BRIEFINGS_DIR, JOURNAL_DIR, SKILLS_INDEX, ARTIFACTS_DIR,
    MIRA_DIR, EPISODES_DIR, WRITINGS_DIR, LOGS_DIR,
    ZA_FILE,
)
from user_paths import artifact_name_for_user, user_journal_dir
try:
    from mira import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None
from soul_manager import (
    append_memory, catalog_list,
    _atomic_write as atomic_write,
)
from sub_agent import model_think

log = logging.getLogger("mira")


PUBLISH_COOLDOWN_DAYS = 2  # minimum days between Substack publications


def _append_to_daily_feed(feed_type: str, section_title: str, content: str,
                          source: str = "", tags: list[str] | None = None,
                          user_id: str = "ang"):
    """Append content to a daily feed item (one item per type per day).

    feed_type: 'explore' or 'mira' — determines which daily item to append to.
      - explore: external sources (briefings from Substack, arxiv, Reddit, etc.)
      - mira: agent's own output (sparks, report, journal, reflections)
    """
    today = datetime.now().strftime("%Y%m%d")
    date_str = datetime.now().strftime("%Y-%m-%d")
    bridge = Mira(MIRA_DIR, user_id=user_id)

    if feed_type == "explore":
        feed_id = f"feed_explore_{today}"
        feed_title = f"Explore Digest {date_str}"
        default_tags = ["explore", "briefing"]
    else:
        feed_id = f"feed_mira_{today}"
        feed_title = f"Mira's Day {date_str}"
        default_tags = ["mira", "digest"]

    # Format section with header
    header = f"## {section_title}"
    if source:
        header += f"  [{source}]"
    section = f"{header}\n\n{content}"

    if bridge.item_exists(feed_id):
        bridge.append_message(feed_id, "agent", section)
    else:
        bridge.create_feed(feed_id, feed_title, section,
                           tags=tags or default_tags)


def _copy_to_briefings(filename: str, content: str):
    """Copy content to artifacts/briefings/ with verification and retry.

    iCloud Drive can evict local files, so we verify the write succeeded
    and log clearly if it doesn't.
    """
    import time
    briefings_dir = ARTIFACTS_DIR / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)
    target = briefings_dir / filename

    for attempt in range(3):
        try:
            target.write_text(content, encoding="utf-8")
            # Verify: read back and check
            time.sleep(0.2)  # brief pause for filesystem sync
            if target.exists() and target.stat().st_size > 0:
                log.info("Copied to briefings: %s (%d bytes)", filename, target.stat().st_size)
                return
            log.warning("Briefing copy verification failed (attempt %d): %s exists=%s",
                        attempt + 1, filename, target.exists())
        except OSError as e:
            log.error("Briefing copy failed (attempt %d): %s — %s", attempt + 1, filename, e)
        time.sleep(1)

    log.error("FAILED to copy %s to briefings after 3 attempts — iOS will not see this content", filename)


def _sync_journals_to_briefings():
    """Ensure today's journal and zhesi are in artifacts/briefings/.

    Called during each agent cycle as a safety net — if the initial copy
    failed or iCloud evicted the file, this will restore it.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    briefings_dir = ARTIFACTS_DIR / "briefings"
    briefings_dir.mkdir(parents=True, exist_ok=True)

    try:
        from config import get_known_user_ids
        user_ids = get_known_user_ids()
    except Exception:
        user_ids = ["ang"]

    for user_id in user_ids:
        journal_dir = user_journal_dir(user_id)

        journal_src = journal_dir / f"{today}.md"
        journal_dst = briefings_dir / artifact_name_for_user(f"{today}_journal.md", user_id)
        if journal_src.exists() and not journal_dst.exists():
            try:
                journal_dst.write_text(journal_src.read_text(encoding="utf-8"), encoding="utf-8")
                log.info("Restored journal to briefings: %s", journal_dst.name)
            except OSError as e:
                log.error("Failed to restore journal to briefings: %s", e)

        zhesi_src = journal_dir / f"{today}_zhesi.md"
        zhesi_dst = briefings_dir / artifact_name_for_user(f"{today}_zhesi.md", user_id)
        if zhesi_src.exists() and not zhesi_dst.exists():
            try:
                zhesi_dst.write_text(zhesi_src.read_text(encoding="utf-8"), encoding="utf-8")
                log.info("Restored zhesi to briefings: %s", zhesi_dst.name)
            except OSError as e:
                log.error("Failed to restore zhesi to briefings: %s", e)


def _slugify(title: str) -> str:
    """Simple slug from title."""
    import unicodedata
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")[:50]
    return slug or "untitled"


def _format_feed_items(items: list[dict]) -> str:
    """Format feed items as text for Claude."""
    lines = []
    for i, item in enumerate(items, 1):
        lines.append(f"[{i}] {item.get('source', '?')} | {item.get('title', '?')}")
        if item.get("summary"):
            lines.append(f"    {item['summary'][:200]}")
        if item.get("url"):
            lines.append(f"    {item['url']}")
        lines.append("")
    return "\n".join(lines)


def _extract_deep_dive(briefing: str) -> dict | None:
    """Extract the deep-dive candidate from a briefing."""
    match = re.search(
        r"Deep Dive Candidate\s*\n+(.+?)(?:\n##|\Z)",
        briefing, re.DOTALL,
    )
    if not match:
        return None

    text = match.group(1).strip()
    if "none" in text.lower():
        return None

    # Try to extract title and URL
    url_match = re.search(r"(https?://\S+)", text)
    title = text.split("\n")[0].strip("*[] ")

    if not url_match:
        return None

    return {
        "title": title,
        "url": url_match.group(1),
        "note": text,
    }


def _extract_comment_suggestions(briefing: str) -> list[dict]:
    """Extract comment suggestions from the '值得去聊两句' section of a briefing.

    Returns list of dicts with {url, comment_draft, reason}.
    """
    # Match the section header (emoji or text variants)
    match = re.search(
        r"(?:💬\s*)?值得去聊两句\s*\n+(.+?)(?:\n##|\n---|\Z)",
        briefing, re.DOTALL,
    )
    if not match:
        return []

    text = match.group(1).strip()
    suggestions = []

    # Split by list items (- or *)
    items = re.split(r"\n[-*]\s+", "\n" + text)
    for item in items:
        item = item.strip()
        if not item:
            continue
        url_match = re.search(r"(https?://\S+)", item)
        if url_match:
            draft = re.sub(r"—\s*我想说：\s*", "— ", item)
            suggestions.append({
                "url": url_match.group(1).rstrip(")"),
                "comment_draft": draft,
                "reason": "",
            })

    return suggestions[:3]  # Max 3 suggestions


def _extract_section(text: str, header: str) -> str:
    """Extract content under a ### header."""
    pattern = rf"###\s*{re.escape(header)}\s*\n(.+?)(?=\n###|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_recent_briefing_topics(days: int = 3) -> str:
    """Extract topic titles/URLs from recent briefings for dedup.

    Returns a concise list of what's been covered so the explore prompt
    can skip repeats.
    """
    cutoff = datetime.now() - timedelta(days=days)
    topics = []
    for path in sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
        except ValueError:
            continue
        # Skip journals, zhesi, deep_dives — only briefings
        stem = path.stem[11:]  # after YYYY-MM-DD_
        if any(x in stem for x in ("journal", "zhesi", "deep_dive", "analyst")):
            continue
        content = path.read_text(encoding="utf-8")
        # Extract markdown links as topic indicators
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
        for title, url in links[:15]:
            topics.append(f"- {title} ({url})")
        # Also grab any lines that look like topic headers
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("##") or (line.startswith("**") and line.endswith("**")):
                topics.append(f"- {line}")
    # Dedup and limit
    seen = set()
    unique = []
    for t in topics:
        key = t.lower()[:80]
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return "\n".join(unique[:30]) if unique else ""


def _is_duplicate_topic(title: str, thesis: str) -> bool:
    """Check if a similar topic already exists in ideas/ (any state) or published.

    Uses keyword overlap to detect duplicates. Threshold: >50% shared keywords.
    """
    # Build keyword set from new topic
    import re as _re
    stop = {"the","a","an","is","are","was","were","in","on","of","to","for","and","or","but","with","this","that","it","not","from","by","as","at","how","why","when","what"}
    def keywords(text):
        words = set(_re.findall(r'[a-z]{3,}', text.lower()))
        return words - stop

    new_kw = keywords(f"{title} {thesis}")
    if len(new_kw) < 3:
        return False

    # Check existing idea files
    ideas_dir = Path(__file__).resolve().parent.parent.parent / "writer" / "ideas"
    if ideas_dir.exists():
        for f in ideas_dir.glob("*.md"):
            if f.name.startswith("_"):
                continue
            try:
                content = f.read_text(encoding="utf-8")[:500]
                existing_kw = keywords(content)
                if not existing_kw:
                    continue
                overlap = len(new_kw & existing_kw) / max(len(new_kw), 1)
                if overlap > 0.5:
                    log.debug("Duplicate topic: '%s' overlaps %.0f%% with %s", title, overlap*100, f.name)
                    return True
            except OSError:
                continue

    # Check published titles
    published = _extract_recent_published_titles(days=30)
    if published:
        pub_kw = keywords(published)
        overlap = len(new_kw & pub_kw) / max(len(new_kw), 1)
        if overlap > 0.6:
            return True

    return False


def _extract_recent_published_titles(days: int = 14) -> str:
    """Extract titles of recently published articles for autowrite dedup.

    Reads filenames from artifacts/writings/_published/ to build a list
    of what Mira has already written, so she doesn't repeat topics.
    """
    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    if not published_dir.exists():
        return ""
    cutoff = datetime.now() - timedelta(days=days)
    titles = []
    for path in sorted(published_dir.glob("*.md"), reverse=True):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
        except ValueError:
            continue
        # Extract title from first heading or filename
        try:
            content = path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    titles.append(f"- [{date_str}] {line[2:]}")
                    break
            else:
                # Fallback to filename
                slug = path.stem[11:]  # after YYYY-MM-DD_
                titles.append(f"- [{date_str}] {slug.replace('-', ' ').title()}")
        except Exception:
            slug = path.stem[11:]
            titles.append(f"- [{date_str}] {slug.replace('-', ' ').title()}")
    return "\n".join(titles) if titles else ""


def _gather_recent_briefings(days: int = 7) -> str:
    """Read recent briefing files."""
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(BRIEFINGS_DIR.glob("*.md")):
        # Parse date from filename
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                texts.append(f"--- {path.stem} ---\n{content[:1000]}\n")
        except ValueError:
            continue
    return "\n".join(texts) if texts else "No recent briefings."


def _gather_recent_episodes(days: int = 7) -> str:
    """Read recent episode archives for reflect cycle."""
    cutoff = datetime.now() - timedelta(days=days)
    texts = []
    for path in sorted(EPISODES_DIR.glob("*.md")):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date >= cutoff:
                content = path.read_text(encoding="utf-8")
                # Include title + first 500 chars as summary
                texts.append(f"--- {path.stem} ---\n{content[:500]}\n")
        except (ValueError, OSError):
            continue
    return "\n".join(texts) if texts else "No recent episodes."


def _prune_episodes_from_reflect(pruning_text: str):
    """Delete old episodes listed in reflect output, preserve insights in memory."""
    import re as _re
    for line in pruning_text.strip().splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        # Parse: "- filename.md → insight" or "- filename.md → prune, no insight"
        match = _re.match(r"^- (.+?\.md)\s*[→->]+\s*(.+)$", line)
        if not match:
            continue
        filename = match.group(1).strip()
        insight = match.group(2).strip()
        ep_path = EPISODES_DIR / filename
        if not ep_path.exists():
            continue
        # Save insight to memory if it's worth keeping
        if "no insight" not in insight.lower() and "prune" not in insight.lower():
            date_str = filename[:10] if len(filename) >= 10 else datetime.now().strftime("%Y-%m-%d")
            append_memory(f"- [{date_str}] {insight}")
        # Delete the episode file
        try:
            ep_path.unlink()
            log.info("Pruned episode: %s", filename)
        except OSError as e:
            log.warning("Failed to prune episode %s: %s", filename, e)


def _gather_today_tasks() -> str:
    """Read today's completed tasks from history.jsonl."""
    history_file = MIRA_DIR / "tasks" / "history.jsonl"
    if not history_file.exists():
        return ""

    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        for line in history_file.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            rec = json.loads(line)
            completed = rec.get("completed_at", "")
            if completed and completed[:10] == today:
                sender = rec.get("sender", "?")
                preview = rec.get("content_preview", "")
                status = rec.get("status", "?")
                summary = rec.get("summary", "")[:200]
                lines.append(f"- [{sender}] {preview}\n  Status: {status}\n  Result: {summary}")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read task history: %s", e)

    # Also check current status.json for tasks completed today not yet in history
    status_file = MIRA_DIR / "tasks" / "status.json"
    if status_file.exists():
        try:
            records = json.loads(status_file.read_text(encoding="utf-8"))
            for rec in records:
                completed = rec.get("completed_at", "")
                if completed and completed[:10] == today and rec.get("status") == "done":
                    preview = rec.get("content_preview", "")
                    summary = rec.get("summary", "")[:200]
                    # Avoid duplicates
                    if not any(preview in l for l in lines):
                        lines.append(f"- [{rec.get('sender', '?')}] {preview}\n  Result: {summary}")
        except (json.JSONDecodeError, OSError):
            pass

    return "\n".join(lines)


def _gather_today_skills() -> str:
    """Find skills added today from the skills index."""
    if not SKILLS_INDEX.exists():
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
        for skill in index:
            created = skill.get("created", skill.get("added", ""))
            if created and created[:10] == today:
                lines.append(f"- **{skill['name']}**: {skill.get('description', '')}")
    except (json.JSONDecodeError, OSError):
        pass
    return "\n".join(lines)


def _gather_usage_summary(date_str: str) -> str:
    """Aggregate token usage from daily JSONL log into a readable summary."""
    usage_file = LOGS_DIR / f"usage_{date_str}.jsonl"
    if not usage_file.exists():
        return ""
    try:
        # Aggregate by agent x provider x model
        from collections import defaultdict
        by_agent = defaultdict(lambda: defaultdict(lambda: {"prompt": 0, "completion": 0, "calls": 0}))
        by_provider = defaultdict(lambda: {"prompt": 0, "completion": 0, "calls": 0})
        total = {"prompt": 0, "completion": 0, "calls": 0}

        for line in usage_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            agent = r.get("agent", "unknown")
            provider = r.get("provider", "?")
            model = r.get("model", "?")
            pt = r.get("prompt_tokens", 0)
            ct = r.get("completion_tokens", 0)

            key = f"{provider}/{model}"
            by_agent[agent][key]["prompt"] += pt
            by_agent[agent][key]["completion"] += ct
            by_agent[agent][key]["calls"] += 1
            by_provider[key]["prompt"] += pt
            by_provider[key]["completion"] += ct
            by_provider[key]["calls"] += 1
            total["prompt"] += pt
            total["completion"] += ct
            total["calls"] += 1

        lines = []
        # Per-model totals
        lines.append(f"总计: {total['calls']}次调用, {total['prompt']+total['completion']:,} tokens")
        for key, v in sorted(by_provider.items(), key=lambda x: -(x[1]["prompt"]+x[1]["completion"])):
            lines.append(f"  {key}: {v['calls']}次, {v['prompt']+v['completion']:,} tok (in:{v['prompt']:,} out:{v['completion']:,})")

        # Per-agent breakdown
        lines.append("")
        for agent, models in sorted(by_agent.items()):
            agent_total = sum(m["prompt"] + m["completion"] for m in models.values())
            agent_calls = sum(m["calls"] for m in models.values())
            lines.append(f"{agent}: {agent_calls}次, {agent_total:,} tokens")
            for key, v in sorted(models.items(), key=lambda x: -(x[1]["prompt"]+x[1]["completion"])):
                lines.append(f"  {key}: {v['calls']}次, {v['prompt']+v['completion']:,}")

        return "\n".join(lines)
    except Exception as e:
        log.warning("Usage summary failed: %s", e)
        return ""


def _gather_today_comments() -> str:
    """Read comments posted today from growth_state.json."""
    growth_file = _AGENTS_DIR / "socialmedia" / "growth_state.json"
    if not growth_file.exists():
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    try:
        data = json.loads(growth_file.read_text(encoding="utf-8"))
        for entry in data.get("comment_history", []):
            if entry.get("date", "")[:10] == today:
                url = entry.get("url", "")
                text = entry.get("text", "")[:120].replace("\n", " ")
                lines.append(f"- {url}\n  \"{text}...\"")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read comment history: %s", e)
    return "\n".join(lines)


def _mine_za_ideas(count: int = 3) -> list[str]:
    """Extract random philosophical fragments from 杂.md, organized by @topic sections."""
    import random

    if not ZA_FILE.exists():
        return []

    text = ZA_FILE.read_text(encoding="utf-8")
    # Split into @topic sections
    sections = re.split(r"\n@", text)
    fragments = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        # Get topic name (first line) and content lines
        lines = section.split("\n")
        topic = lines[0].strip().lstrip("@").strip()
        # Collect non-empty content lines as individual fragments
        for line in lines[1:]:
            line = line.strip()
            if line and len(line) > 15:  # skip very short lines
                fragments.append(f"[{topic}] {line}")

    if not fragments:
        return []

    return random.sample(fragments, min(count, len(fragments)))


def _mine_za_one(state: dict | None = None) -> str:
    """Pick one fragment from 杂.md, avoiding recently used ones."""
    import hashlib
    fragments = _mine_za_ideas(count=50)  # get many, then filter
    if not fragments:
        return ""

    used = set()
    if state:
        used = set(state.get("zhesi_used", []))

    # Prefer unused fragments
    available = [f for f in fragments if hashlib.md5(f.encode()).hexdigest()[:8] not in used]
    if not available:
        # All used, reset
        available = fragments
        if state is not None:
            state["zhesi_used"] = []

    import random
    chosen = random.choice(available)

    # Track usage
    if state is not None:
        h = hashlib.md5(chosen.encode()).hexdigest()[:8]
        state.setdefault("zhesi_used", []).append(h)

    return chosen


def _days_since_last_publish() -> float:
    """Return days since last Substack publication (from catalog)."""
    try:
        pubs = [e for e in catalog_list() if e.get("status") == "published" and e.get("date")]
        if not pubs:
            return 999.0
        latest = max(e["date"] for e in pubs)
        from datetime import date as _date
        pub_date = datetime.strptime(latest[:10], "%Y-%m-%d").date()
        return (datetime.now().date() - pub_date).days
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return 999.0


def harvest_observations(output_text: str, source: str = "", user_id: str = "ang"):
    """Extract observations, questions, and connections from output text.

    Uses local LLM (oMLX, fast, free) to extract structured thoughts.
    Called after explore briefings, task completions, and journal entries.
    """
    if not output_text or len(output_text.strip()) < 100:
        return

    try:
        from memory_store import get_store
        store = get_store()
    except Exception as e:
        log.warning("harvest_observations: memory_store unavailable: %s", e)
        return

    prompt = f"""从以下文本中提取值得记住的思考线索。用JSON数组回答，每个元素包含type和content字段。

type 可以是:
- "observation": 你注意到的事实或模式（1-3个）
- "question": 引起好奇的问题（0-1个）
- "connection": 与已知知识的联系（0-1个）

规则：
- 只提取真正有价值的、非显而易见的内容
- 每个content不超过100字
- 没有值得提取的就返回空数组 []

文本：
{output_text[:2000]}

只输出JSON数组，不要其他内容。"""

    try:
        result = model_think(prompt, model_name="omlx", timeout=30)
        if not result:
            return

        # Parse JSON array from result
        import json as _json
        # Find JSON array in response
        start = result.find("[")
        end = result.rfind("]") + 1
        if start < 0 or end <= start:
            return

        thoughts = _json.loads(result[start:end])
        stored = 0
        for t in thoughts:
            ttype = t.get("type", "observation")
            content = t.get("content", "")
            if not content or ttype not in ("observation", "question", "connection"):
                continue
            store.store_thought(
                content=content,
                thought_type=ttype,
                source_context=source[:200],
                user_id=user_id,
            )
            stored += 1

            # Also add questions to emptiness queue
            if ttype == "question":
                try:
                    from emptiness import add_question
                    add_question(content, priority=3.0, source=f"harvest:{source[:50]}", user_id=user_id)
                except (ImportError, ModuleNotFoundError, OSError):
                    pass

        if stored:
            log.info("Harvested %d observations from %s", stored, source[:40])
    except Exception as e:
        log.warning("harvest_observations failed: %s", e)


def _maybe_create_spontaneous_idea(thought_text: str, source: str = "",
                                   user_id: str | None = None):
    """Create a writing idea if a thought connects to 2+ existing threads.

    Checks the thought against memory.md, worldview.md, and recent reading
    notes. If it references concepts from at least 2 distinct threads,
    auto-creates a new idea file in the ideas folder.
    """
    from config import MEMORY_FILE, WORLDVIEW_FILE, READING_NOTES_DIR

    if not thought_text or len(thought_text.strip()) < 100:
        return

    # Lazy import to avoid circular dependency
    from core import load_state, save_state

    # Rate limit: max 1 spontaneous idea per day
    state = load_state(user_id=user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get(f"spontaneous_idea_{today}"):
        return

    # Load reference threads as labeled sections
    threads = {}
    if MEMORY_FILE.exists():
        try:
            for line in MEMORY_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("## ") or (line.startswith("- ") and len(line) > 20):
                    threads[line[:80]] = line
        except OSError:
            pass
    if WORLDVIEW_FILE.exists():
        try:
            for line in WORLDVIEW_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("## ") or (line.startswith("- ") and len(line) > 20):
                    threads[line[:80]] = line
        except OSError:
            pass

    if len(threads) < 2:
        return

    # Use local LLM (oMLX, fast) to check connections
    thread_list = "\n".join(f"- {k}" for k in list(threads.keys())[:30])
    prompt = f"""判断以下思考片段是否同时关联了至少2条不同的已有思考线索。

思考片段：
{thought_text[:1500]}

已有线索：
{thread_list}

输出 JSON：
{{
    "connected_threads": 2,  // 关联的线索数量
    "threads": ["线索1简述", "线索2简述"],
    "title": "基于这个连接可以写的文章标题（中文或英文，15字以内）",
    "thesis": "核心论点（一句话）",
    "skip": false  // 如果连接很弱或牵强，设为 true
}}

只输出JSON。如果关联不足2条或连接牵强，connected_threads 设为实际数量，skip 设为 true。"""

    try:
        result = model_think(prompt, model_name="omlx", timeout=30)
        if not result:
            return

        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return
        decision = json.loads(match.group())
    except Exception as e:
        log.debug("Spontaneous idea check failed: %s", e)
        return

    if decision.get("skip") or decision.get("connected_threads", 0) < 2:
        return

    title = decision.get("title", "").strip()
    thesis = decision.get("thesis", "").strip()
    if not title or not thesis:
        return

    # Check for duplicates
    try:
        if _is_duplicate_topic(title, thesis):
            log.info("Spontaneous idea skipped (duplicate): %s", title)
            return
    except Exception:
        pass

    # Create idea file
    slug = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '-',
                  title.lower().replace(" ", "-"))[:50].strip("-")
    idea_path = WRITINGS_DIR / "ideas" / f"{slug}.md"

    if idea_path.exists():
        log.info("Spontaneous idea skipped (file exists): %s", slug)
        return

    connected = ", ".join(decision.get("threads", []))
    idea_content = f"""# {title}

- **type**: essay
- **language**: 中文
- **platform**: Substack
- **target_words**: 2000
- **deadline**:

## Theme

{thesis}

## Key Points

- Connection: {connected}
- Source: {source}

## Notes

Spontaneous idea — emerged from connecting 2+ existing threads.
Original thought: {thought_text[:500]}

## Feedback



---
<!-- AUTO-MANAGED BELOW — DO NOT EDIT -->
## Status

- **state**: new
- **project_dir**:
- **created**: {today}
- **scaffolded**:
- **round_1_draft**:
- **round_1_critique**:
- **round_1_revision**:
- **feedback_detected**:
- **round_2_draft**:
- **round_2_critique**:
- **round_2_revision**:
- **current_round**: 0
- **idea_hash**:
- **last_error**:
"""

    try:
        idea_path.write_text(idea_content, encoding="utf-8")
        state[f"spontaneous_idea_{today}"] = title
        save_state(state, user_id=user_id)
        log.info("Spontaneous writing idea created: %s (%s)", title, idea_path.name)
    except OSError as e:
        log.warning("Failed to save spontaneous idea: %s", e)


def _prune_old_logs(logs_dir: Path, keep_days: int = 14):
    """Remove old log files, compress mid-age logs, and truncate oversized ones.

    - Daily logs (YYYY-MM-DD.log): keep 14 days, gzip after 3 days
    - Background logs (bg-*.log): keep 7 days, gzip after 3 days
    - Oversized logs: truncate to last 2MB
    - Old .gz files: remove after keep_days
    """
    import gzip as _gzip

    _LOG_MAX_BYTES = 5 * 1024 * 1024  # 5MB per log file

    try:
        now = datetime.now()
        cutoff = now - timedelta(days=keep_days)
        bg_cutoff = now - timedelta(days=7)
        compress_cutoff = now - timedelta(days=3)

        # Clean old .gz files
        for gz_file in logs_dir.glob("*.log.gz"):
            try:
                if gz_file.stat().st_mtime < cutoff.timestamp():
                    gz_file.unlink()
            except OSError:
                continue

        for log_file in logs_dir.glob("*.log"):
            try:
                name = log_file.stem
                is_bg = name.startswith("bg-")
                file_cutoff = bg_cutoff if is_bg else cutoff

                # Determine file age
                if name[:4].isdigit():
                    try:
                        file_date = datetime.strptime(name[:10], "%Y-%m-%d")
                    except ValueError:
                        file_date = datetime.fromtimestamp(log_file.stat().st_mtime)
                else:
                    file_date = datetime.fromtimestamp(log_file.stat().st_mtime)

                # Remove old files
                if file_date < file_cutoff:
                    log_file.unlink()
                    continue

                # Truncate oversized logs (keep tail)
                if log_file.stat().st_size > _LOG_MAX_BYTES:
                    content = log_file.read_bytes()
                    log_file.write_bytes(content[-2 * 1024 * 1024:])

                # Compress logs older than 3 days (skip today's active log)
                if file_date < compress_cutoff:
                    gz_path = log_file.with_suffix(".log.gz")
                    if not gz_path.exists():
                        with open(log_file, "rb") as f_in:
                            with _gzip.open(gz_path, "wb") as f_out:
                                f_out.writelines(f_in)
                        log_file.unlink()
            except (ValueError, OSError):
                continue
    except OSError:
        pass
