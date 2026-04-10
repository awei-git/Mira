"""Wiki workflow — build and maintain Mira's personal knowledge base.

Called after journal (daily) and during reflect (weekly maintenance).
Creates and updates topic-indexed wiki pages from reading notes.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

from sub_agent import claude_think, model_think
from soul_manager import load_soul, format_soul

from wiki_manager import (
    WIKI_DIR,
    list_wiki_pages, load_wiki_page, save_wiki_page,
    detect_wiki_candidates, get_notes_for_topic,
    find_related_pages, rebuild_wiki_index, _load_meta,
)

log = logging.getLogger("mira")

# Max operations per cycle to stay within time budget
MAX_UPDATES_PER_CYCLE = 2
MAX_CREATES_PER_CYCLE = 1


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_CREATE_PAGE_PROMPT = """\
You are Mira, an AI agent building your personal knowledge wiki.

Create a wiki page synthesizing your understanding of: **{topic}**

## Source material (reading notes):
{sources}

## Your identity context:
{soul_snippet}

## Instructions:
- Write in first person as Mira — this is YOUR understanding, not a textbook
- Synthesize across sources into connected prose, don't just list bullet points
- Include specific insights, not generic summaries
- Note contradictions or open questions you've encountered
- Reference source notes where relevant: [Source: filename]
- Use the language that matches the source material (Chinese if sources are Chinese)

## Output format (follow exactly):
# {topic_title}

> {{one-sentence description}}

*Last updated: {date} | Sources: {n_sources} reading notes*

## Core Understanding
{{500-1000 words of synthesized knowledge}}

## Key Insights
- **{{insight}}** — {{2-3 sentences}}. [Source: {{note filename}}]

## Open Questions
- {{questions you can't answer yet}}

## Connections
- Related: {{links to related topics if you know of any}}
"""

_UPDATE_PAGE_PROMPT = """\
You are Mira, updating a wiki page with new knowledge.

## Current page:
{current_content}

## New material to integrate:
{new_material}

## Instructions:
- ADD new knowledge to existing sections, or create a new section if the topic is distinct
- Do NOT rewrite or remove existing content — only extend
- Update the "Last updated" date to {date}
- Update the source count
- If new material contradicts existing content, note it in "Open Questions"
- Reference new sources: [Source: filename]
- Keep the same language as the existing page

Output the COMPLETE updated page (not just the diff).
"""

_CATEGORIZE_PROMPT = """\
Categorize this wiki topic into exactly ONE category.

Topic: {topic}
Description: {description}

Available categories: technology, philosophy, craft, markets, society, science, personal

Reply with ONLY the category name, nothing else.
"""


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def do_wiki_update(trigger: str = "journal", new_content: str = "",
                   user_id: str = "ang"):
    """Update the wiki with today's knowledge.

    Called after journal (daily) or manually.
    Phase 1: Detect new page candidates (no LLM)
    Phase 2: Update existing pages with new content (max 2, LLM)
    Phase 3: Create one new page if candidates found (LLM)
    Phase 4: Rebuild index (no LLM)
    """
    log.info("Wiki update starting (trigger=%s)", trigger)
    pages = list_wiki_pages()
    updates_done = 0
    creates_done = 0

    # --- Phase 2: Update existing pages ---
    if new_content and pages:
        related_slugs = find_related_pages(new_content)
        for slug in related_slugs[:MAX_UPDATES_PER_CYCLE]:
            try:
                current = load_wiki_page(slug)
                if not current:
                    continue
                # Gather new notes related to this page's topic
                meta = _load_meta()
                page_info = meta.get("pages", {}).get(slug, {})
                title = page_info.get("title", slug)

                # Find today's reading notes that relate
                today_notes = get_notes_for_topic(title, days=1, user_id=user_id)
                if not today_notes:
                    continue

                new_material = "\n\n---\n\n".join(
                    f"### {n['title']}\n{n['content'][:800]}"
                    for n in today_notes[:5]
                )

                prompt = _UPDATE_PAGE_PROMPT.format(
                    current_content=current[:3000],
                    new_material=new_material[:2000],
                    date=datetime.now().strftime("%Y-%m-%d"),
                )
                updated = model_think(prompt, model_name="omlx", timeout=90)
                if updated and len(updated) > len(current) * 0.5:
                    save_wiki_page(
                        slug, title, updated,
                        source_count=page_info.get("source_count", 0) + len(today_notes),
                        reason=f"updated with {len(today_notes)} new notes",
                    )
                    updates_done += 1
                    _create_wiki_links(slug, today_notes, user_id=user_id)
            except Exception as e:
                log.warning("Wiki update failed for %s: %s", slug, e)

    # --- Phase 1 + 3: Detect candidates and create one new page ---
    if creates_done < MAX_CREATES_PER_CYCLE:
        try:
            candidates = detect_wiki_candidates(days=14, min_count=3, user_id=user_id)
            if candidates:
                top = candidates[0]
                _create_new_page(top, user_id=user_id)
                creates_done += 1
        except Exception as e:
            log.warning("Wiki candidate detection/creation failed: %s", e)

    # --- Phase 4: Rebuild index ---
    if updates_done > 0 or creates_done > 0:
        rebuild_wiki_index()

    log.info("Wiki update done: %d updates, %d creates", updates_done, creates_done)


def _create_new_page(candidate: dict, user_id: str = "ang"):
    """Create a new wiki page from a candidate topic."""
    topic = candidate["topic"]
    slug = candidate["slug"]
    source_notes = get_notes_for_topic(topic, days=30, user_id=user_id)

    if len(source_notes) < 2:
        log.info("Wiki: skipping '%s' — only %d source notes", topic, len(source_notes))
        return

    # Format source material
    sources_text = "\n\n---\n\n".join(
        f"### {n['title']}\n*{n['date']}*\n\n{n['content'][:800]}"
        for n in source_notes[:8]
    )

    # Load soul for identity context
    soul = load_soul()
    soul_snippet = format_soul(soul)[:500]

    prompt = _CREATE_PAGE_PROMPT.format(
        topic=topic,
        sources=sources_text[:4000],
        soul_snippet=soul_snippet,
        topic_title=topic,
        date=datetime.now().strftime("%Y-%m-%d"),
        n_sources=len(source_notes),
    )

    content = model_think(prompt, model_name="omlx", timeout=120)
    if not content or len(content) < 200:
        log.warning("Wiki: page creation for '%s' produced insufficient content", topic)
        return

    # Categorize
    category = _categorize_topic(topic, content[:200])

    # Find description from first line after title
    desc = ""
    for line in content.split("\n"):
        if line.startswith(">"):
            desc = line.strip("> ").strip()
            break

    save_wiki_page(slug, topic, content,
                   description=desc,
                   category=category,
                   source_count=len(source_notes),
                   reason=f"created from {len(source_notes)} notes")

    # Create knowledge links
    _create_wiki_links(slug, source_notes, user_id=user_id)
    log.info("Wiki: created page '%s' (%s) from %d notes",
             topic, slug, len(source_notes))


def _categorize_topic(topic: str, description: str) -> str:
    """Categorize a topic using a lightweight LLM call."""
    try:
        prompt = _CATEGORIZE_PROMPT.format(topic=topic, description=description)
        result = model_think(prompt, model_name="omlx", timeout=30)
        if result:
            cat = result.strip().lower().split()[0]
            valid = {"technology", "philosophy", "craft", "markets",
                     "society", "science", "personal"}
            if cat in valid:
                return cat
    except Exception:
        pass
    return "general"


def _create_wiki_links(slug: str, source_notes: list[dict], user_id: str = "ang"):
    """Create knowledge links between a wiki page and its source notes."""
    try:
        from knowledge_links import add_link
        for note in source_notes[:10]:
            add_link("wiki", slug, "reading_note", note.get("path", ""),
                     "related", confidence=0.7, created_by="wiki", user_id=user_id)
    except Exception as e:
        log.debug("Wiki link creation failed: %s", e)


# ---------------------------------------------------------------------------
# Weekly maintenance
# ---------------------------------------------------------------------------

def do_wiki_maintenance(user_id: str = "ang"):
    """Weekly wiki health check — run during reflect.

    - Refresh cross-links between pages
    - Prune stale pages (no updates in 60+ days, <200 words)
    - Rebuild index
    """
    log.info("Wiki maintenance starting")
    meta = _load_meta()
    pages = meta.get("pages", {})
    if not pages:
        log.info("Wiki maintenance: no pages to maintain")
        return

    pruned = 0
    now = datetime.now()

    for slug, info in list(pages.items()):
        # Prune very thin, stale pages
        updated = info.get("updated_at", "")
        word_count = info.get("word_count", 0)
        try:
            updated_date = datetime.strptime(updated, "%Y-%m-%d")
            age_days = (now - updated_date).days
        except (ValueError, TypeError):
            age_days = 999

        if age_days > 60 and word_count < 200:
            # Too thin and too old — remove
            page_path = WIKI_DIR / f"{slug}.md"
            if page_path.exists():
                page_path.unlink()
            del pages[slug]
            pruned += 1
            log.info("Wiki maintenance: pruned thin stale page '%s' (%d words, %d days old)",
                     slug, word_count, age_days)

    if pruned:
        meta["pages"] = pages
        _save_meta(meta)

    # Refresh cross-links for all remaining pages
    for slug, info in pages.items():
        content = load_wiki_page(slug)
        if content:
            related = find_related_pages(content, exclude_slug=slug)
            if related:
                try:
                    from knowledge_links import add_link
                    for r_slug in related:
                        add_link("wiki", slug, "wiki", r_slug,
                                 "related", confidence=0.5, created_by="wiki-maintenance",
                                 user_id=user_id)
                except Exception:
                    pass

    rebuild_wiki_index()
    meta["last_full_rebuild"] = now.strftime("%Y-%m-%d")
    _save_meta(meta)

    log.info("Wiki maintenance done: pruned %d pages, refreshed cross-links", pruned)
