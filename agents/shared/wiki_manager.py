"""Wiki Manager — topic-indexed personal knowledge base.

Maintains a structured wiki in WIKI_DIR (iCloud, iOS-browsable).
Pages are organized by topic, not date. Each page synthesizes
knowledge from multiple reading notes into connected prose.

Designed to be called from workflows (journal, reflect) — not directly
from the 30s LaunchAgent cycle.
"""
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    READING_NOTES_DIR, WIKI_DIR, WIKI_META, WIKI_LOG_MAX_LINES,
)
from soul_manager import _atomic_write, _locked_read_modify_write, _log_change

log = logging.getLogger("mira")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WIKI_INDEX = WIKI_DIR / "index.md"
WIKI_LOG = WIKI_DIR / "log.md"


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def _load_meta() -> dict:
    """Load wiki metadata. Returns empty structure if missing."""
    if WIKI_META.exists():
        try:
            return json.loads(WIKI_META.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"pages": {}, "last_full_rebuild": None}


def _save_meta(meta: dict):
    """Save wiki metadata atomically."""
    WIKI_META.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(WIKI_META, json.dumps(meta, ensure_ascii=False, indent=2))


def list_wiki_pages() -> list[dict]:
    """List all wiki pages with metadata."""
    meta = _load_meta()
    return [
        {"slug": slug, **info}
        for slug, info in meta.get("pages", {}).items()
    ]


def load_wiki_page(slug: str) -> str | None:
    """Read a wiki page by slug."""
    path = WIKI_DIR / f"{slug}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def save_wiki_page(slug: str, title: str, content: str,
                   description: str = "", category: str = "general",
                   source_count: int = 0, reason: str = ""):
    """Save a wiki page and update metadata + log."""
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    path = WIKI_DIR / f"{slug}.md"
    _atomic_write(path, content)

    # Update metadata
    meta = _load_meta()
    now = datetime.now().strftime("%Y-%m-%d")
    existing = meta["pages"].get(slug, {})
    meta["pages"][slug] = {
        "title": title,
        "description": description or existing.get("description", ""),
        "category": category or existing.get("category", "general"),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "update_count": existing.get("update_count", 0) + 1,
        "source_count": source_count or existing.get("source_count", 0),
        "word_count": len(content.split()),
    }
    _save_meta(meta)

    action = "UPDATE" if existing else "CREATE"
    append_wiki_log(slug, action, reason or title)
    _log_change(f"WIKI_{action}", slug, title[:60])
    log.info("Wiki %s: %s (%d words)", action.lower(), slug, len(content.split()))


def append_wiki_log(slug: str, action: str, detail: str = ""):
    """Append to wiki/log.md changelog."""
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {action} [{slug}]({slug}.md): {detail}\n"

    def _modify(text):
        if not text:
            text = "# Wiki Changelog\n\n"
        text += line
        lines = text.split("\n")
        if len(lines) > WIKI_LOG_MAX_LINES:
            header = lines[:2]
            trimmed = lines[-(WIKI_LOG_MAX_LINES - 2):]
            text = "\n".join(header + trimmed)
        return text

    _locked_read_modify_write(WIKI_LOG, _modify)


# ---------------------------------------------------------------------------
# Topic detection — find wiki-worthy topics from reading notes
# ---------------------------------------------------------------------------

def _load_recent_notes(days: int = 14) -> list[dict]:
    """Load reading notes from recent days with title and content."""
    if not READING_NOTES_DIR.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    notes = []
    for path in sorted(READING_NOTES_DIR.glob("*.md")):
        try:
            date_str = path.stem[:10]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                continue
            content = path.read_text(encoding="utf-8")
            # Extract title from first line, clean up prefixes
            title = content.split("\n")[0].strip("# ").strip()
            # Remove "Reading Note: " prefix and bold/number markers
            title = re.sub(r'^Reading Note:\s*', '', title)
            title = re.sub(r'^\*{1,2}\d+\.\s*', '', title)
            title = title.strip("*").strip()
            notes.append({
                "path": path.name,
                "date": date_str,
                "title": title,
                "content": content[:1500],
            })
        except (ValueError, OSError):
            continue
    return notes


def _extract_topic_phrases(notes: list[dict]) -> Counter:
    """Extract meaningful topic phrases from note titles and content.

    Returns Counter of topic phrases appearing in multiple notes.
    Focuses on multi-word concepts, not individual words.
    """
    topics = Counter()

    # Stop words — common words that don't form good wiki topics
    stop = {
        # Chinese
        "的", "是", "在", "和", "了", "不", "与", "从", "到", "而", "这", "那",
        "也", "就", "都", "要", "会", "对", "说", "问题", "可以", "因为", "所以",
        "但是", "如果", "没有", "什么", "为什么", "怎么", "一个", "这个", "那个",
        "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
        # English
        "the", "a", "an", "is", "are", "in", "on", "of", "and", "to",
        "for", "it", "that", "this", "but", "or", "reading", "note",
        "not", "how", "what", "why", "when", "will", "can", "has", "have",
        "about", "from", "with", "more", "new", "just", "than",
        # Meta terms from notes format
        "source", "insight", "question",
    }

    for note in notes:
        title = note["title"]
        content = note["content"]

        # Strategy 1: Extract bold phrases (**multi-word term**)
        # Skip bold phrases in the title/header lines
        body = "\n".join(content.split("\n")[3:])  # Skip first 3 header lines
        bold_terms = re.findall(r'\*\*([^*]{4,60})\*\*', body)
        for term in bold_terms:
            cleaned = re.sub(r'^[\d.\s]+', '', term).strip()
            # Must be 4+ chars, multi-word, and not just stop words
            words = cleaned.lower().split()
            meaningful = [w for w in words if w not in stop and len(w) > 2]
            if len(meaningful) >= 2 and len(cleaned) >= 6:
                topics[cleaned] += 1

        # Strategy 2: Extract compound concepts from titles
        # Split title on punctuation, take meaningful segments
        segments = re.split(r'[—\-:：,，。!！?？]', title)
        for seg in segments:
            seg = seg.strip()
            if 6 <= len(seg) <= 50:
                words = seg.lower().split()
                meaningful = [w for w in words if w not in stop and len(w) > 2]
                if len(meaningful) >= 2:  # Require multi-word concepts
                    topics[seg] += 1

    return topics


def detect_wiki_candidates(days: int = 14, min_count: int = 3) -> list[dict]:
    """Find topics appearing in 3+ reading notes without a wiki page.

    Uses both keyword frequency and vector similarity clustering.
    """
    notes = _load_recent_notes(days)
    if not notes:
        return []

    existing_slugs = set(_load_meta().get("pages", {}).keys())
    candidates = []

    # Strategy 1: Use LLM-free keyword clustering
    topic_counts = _extract_topic_phrases(notes)

    # Strategy 2: Use vector similarity to cluster notes by topic
    try:
        from memory_store import get_store
        store = get_store()

        # Group notes by similarity — find clusters
        clusters = {}  # topic_label -> [note paths]
        for note in notes[:50]:  # Cap to avoid slow queries
            results = store.recall(
                note["title"][:200],
                top_k=5,
                source_filter="reading_note",
            )
            # If 3+ notes are very similar, they form a cluster
            similar = [r for r in results if r.get("score", 0) > 0.6]
            if len(similar) >= 2:
                # Use this note's title as cluster label
                label = note["title"][:60]
                if label not in clusters:
                    clusters[label] = set()
                clusters[label].add(note["path"])
                for r in similar:
                    sid = r.get("source_id", "")
                    if sid:
                        clusters[label].add(sid)

        # Convert clusters to candidates
        for label, paths in clusters.items():
            if len(paths) >= min_count and len(label) >= 6:
                slug = _slugify(label)
                if slug not in existing_slugs:
                    candidates.append({
                        "topic": label,
                        "slug": slug,
                        "count": len(paths),
                        "source_notes": list(paths)[:10],
                    })
    except Exception as e:
        log.debug("Vector-based candidate detection failed: %s", e)

    # Fallback: use keyword frequency for candidates
    for topic, count in topic_counts.most_common(20):
        if count >= min_count and len(topic) >= 6:
            slug = _slugify(topic)
            if not slug or len(slug) < 3:
                continue
            if slug not in existing_slugs and not any(c["slug"] == slug for c in candidates):
                # Find which notes mention this topic
                source_notes = [
                    n["path"] for n in notes
                    if topic.lower() in n["content"].lower()
                ]
                if len(source_notes) >= min_count:
                    candidates.append({
                        "topic": topic,
                        "slug": slug,
                        "count": len(source_notes),
                        "source_notes": source_notes[:10],
                    })

    # Sort by count descending
    candidates.sort(key=lambda c: c["count"], reverse=True)
    return candidates[:10]  # Top 10


def get_notes_for_topic(topic: str, days: int = 30) -> list[dict]:
    """Find reading notes related to a topic via content search + vector."""
    notes = _load_recent_notes(days)
    matches = []

    # Direct content match
    topic_lower = topic.lower()
    for note in notes:
        if topic_lower in note["content"].lower() or topic_lower in note["title"].lower():
            matches.append(note)

    # Vector search supplement
    try:
        from memory_store import get_store
        store = get_store()
        results = store.recall(topic, top_k=10, source_filter="reading_note")
        for r in results:
            if r.get("score", 0) > 0.5:
                sid = r.get("source_id", "")
                if sid and not any(m["path"] == sid for m in matches):
                    matches.append({
                        "path": sid,
                        "date": "",
                        "title": r.get("content", "")[:80],
                        "content": r.get("content", "")[:1500],
                    })
    except Exception as e:
        log.debug("Vector search for topic notes failed: %s", e)

    return matches[:15]


def find_related_pages(content: str, exclude_slug: str = "") -> list[str]:
    """Find wiki pages related to given content."""
    meta = _load_meta()
    pages = meta.get("pages", {})
    if not pages:
        return []

    related = []
    content_lower = content.lower()

    for slug, info in pages.items():
        if slug == exclude_slug:
            continue
        title = info.get("title", "").lower()
        desc = info.get("description", "").lower()
        # Simple keyword match — title or description appears in content
        if title and (title in content_lower or any(
            w in content_lower for w in title.split() if len(w) > 3
        )):
            related.append(slug)
        elif desc and any(w in content_lower for w in desc.split() if len(w) > 4):
            related.append(slug)

    return related[:5]


# ---------------------------------------------------------------------------
# Index generation
# ---------------------------------------------------------------------------

def rebuild_wiki_index():
    """Regenerate wiki/index.md from metadata."""
    meta = _load_meta()
    pages = meta.get("pages", {})
    if not pages:
        return

    WIKI_DIR.mkdir(parents=True, exist_ok=True)

    # Group by category
    by_category: dict[str, list] = {}
    for slug, info in sorted(pages.items(), key=lambda x: x[1].get("updated_at", ""), reverse=True):
        cat = info.get("category", "general")
        by_category.setdefault(cat, []).append((slug, info))

    lines = [
        "# Mira's Wiki",
        "",
        f"*{len(pages)} topics | Last updated: {datetime.now().strftime('%Y-%m-%d')}*",
        "",
    ]

    for category in sorted(by_category.keys()):
        lines.append(f"## {category.title()}")
        lines.append("")
        for slug, info in by_category[category]:
            title = info.get("title", slug)
            desc = info.get("description", "")
            words = info.get("word_count", 0)
            updated = info.get("updated_at", "")
            desc_str = f" — {desc}" if desc else ""
            lines.append(f"- [{title}]({slug}.md){desc_str} ({words} words, updated {updated})")
        lines.append("")

    _atomic_write(WIKI_INDEX, "\n".join(lines))
    log.info("Wiki index rebuilt: %d pages in %d categories",
             len(pages), len(by_category))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert topic text to a URL-safe slug."""
    # Handle Chinese + English mix
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = slug.strip('-')[:60]
    return slug or "untitled"
