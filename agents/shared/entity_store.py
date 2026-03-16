"""Knowledge entity store — persistent records for papers, concepts, and key insights.

Entities are extracted from conversations and stored for recall across sessions.
When a user asks "which paper about X?" or "what did we discuss about Y?",
this store provides structured, fast keyword-search over known entities.

Entity types:
- paper:   Academic papers (arxiv, journals). Key fields: arxiv_id, title, authors, year
- concept: Key ideas, theories, frameworks discussed in depth
- person:  Researchers, thinkers, authors referenced repeatedly
- tool:    Software, methods, platforms referenced repeatedly

Extraction is regex-based (no LLM call needed) — fast enough to run after every task.
Semantic search over full conversation text is handled by memory_index.py.
This store adds structured lookup: "find all papers discussed", "what's Boppana 2026?".
"""
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from config import SOUL_DIR

log = logging.getLogger("mira.entity_store")

DB_PATH = SOUL_DIR / ".entity_store.sqlite"

ENTITY_TYPES = {"paper", "concept", "person", "tool"}


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            mention_count INTEGER DEFAULT 1,
            tags TEXT DEFAULT '[]',
            source_task_ids TEXT DEFAULT '[]'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE)")
    conn.commit()
    return conn


def _entity_id(entity_type: str, name: str) -> str:
    slug = re.sub(r'[^a-z0-9-]', '-', name.lower().strip())
    slug = re.sub(r'-+', '-', slug).strip('-')[:60]
    return f"{entity_type}:{slug}"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_entity(entity_type: str, name: str, summary: str,
                  tags: list = None, source_task_id: str = "") -> str:
    """Insert or update an entity. Returns entity ID.

    If entity already exists, updates summary, last_seen, and mention_count.
    """
    if entity_type not in ENTITY_TYPES:
        entity_type = "concept"

    entity_id = _entity_id(entity_type, name)
    now = datetime.now().isoformat()

    conn = _get_db()
    row = conn.execute(
        "SELECT id, mention_count, source_task_ids, tags FROM entities WHERE id = ?",
        (entity_id,)
    ).fetchone()

    if row:
        old_count = row[1]
        old_tasks = json.loads(row[2] or "[]")
        old_tags = json.loads(row[3] or "[]")

        if source_task_id and source_task_id not in old_tasks:
            old_tasks.append(source_task_id)

        merged_tags = list(set(old_tags + (tags or [])))

        conn.execute(
            """UPDATE entities SET
               summary = ?, last_seen = ?, mention_count = ?,
               source_task_ids = ?, tags = ?
               WHERE id = ?""",
            (summary, now, old_count + 1,
             json.dumps(old_tasks, ensure_ascii=False),
             json.dumps(merged_tags, ensure_ascii=False),
             entity_id)
        )
        log.info("Entity updated: %s (%s) × %d", name, entity_type, old_count + 1)
    else:
        task_ids = [source_task_id] if source_task_id else []
        conn.execute(
            """INSERT INTO entities
               (id, entity_type, name, summary, first_seen, last_seen,
                mention_count, tags, source_task_ids)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (entity_id, entity_type, name, summary, now, now,
             json.dumps(tags or [], ensure_ascii=False),
             json.dumps(task_ids, ensure_ascii=False))
        )
        log.info("Entity saved: %s (%s)", name, entity_type)

    conn.commit()
    conn.close()
    return entity_id


# ---------------------------------------------------------------------------
# Extraction (regex-based, no LLM needed)
# ---------------------------------------------------------------------------

# arxiv ID pattern: YYMM.NNNNN (v1, v2 optional)
_ARXIV_PATTERN = re.compile(
    r'\b(\d{4}\.\d{4,5})(?:v\d+)?\b'
)

# "Author et al. YYYY" or "Author YYYY" followed by common paper markers
_PAPER_AUTHOR_PATTERN = re.compile(
    r'([A-Z][a-zÀ-ÿ]+(?:\s+et\s+al\.?)?\s+(?:20|19)\d{2})\b'
)

# Quoted titles (single or double quotes, or Chinese quotes)
_TITLE_PATTERN = re.compile(
    r'["""「]([^"""「」\n]{10,100})["""」]'
)

# "Reasoning Theater", "CoT Skepticism" — bold or quoted short phrases treated as names
_BOLD_PATTERN = re.compile(r'\*\*([^*\n]{5,60})\*\*')


def extract_entities_from_text(text: str, task_id: str = "") -> list[dict]:
    """Extract entity candidates from conversation/task text using regex heuristics.

    Returns list of entity dicts ready to upsert.
    Strategy:
    1. Find arxiv IDs → paper entities
    2. Find "Author et al. YYYY" patterns → paper entities
    3. Find quoted titles near arxiv IDs → enrich paper names
    4. Find bold terms used repeatedly → concept entities
    """
    if not text:
        return []

    entities = []
    seen_ids = set()

    # --- Pass 1: arxiv IDs ---
    for m in _ARXIV_PATTERN.finditer(text):
        arxiv_id = m.group(1)
        entity_id = f"paper:arxiv-{arxiv_id}"
        if entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)

        # Grab context around the arxiv ID (±200 chars) to build summary
        start = max(0, m.start() - 200)
        end = min(len(text), m.end() + 200)
        context = text[start:end].strip()

        # Try to find a title/author near this ID
        name = f"arxiv:{arxiv_id}"
        # Look for "Title" (quoted) near this match
        for tm in _TITLE_PATTERN.finditer(context):
            candidate = tm.group(1).strip()
            if len(candidate) > 10:
                name = f"{candidate} (arxiv:{arxiv_id})"
                break
        # Or look for "Author et al. YYYY"
        if name == f"arxiv:{arxiv_id}":
            for am in _PAPER_AUTHOR_PATTERN.finditer(context):
                name = f"{am.group(1)} (arxiv:{arxiv_id})"
                break

        entities.append({
            "entity_type": "paper",
            "name": name,
            "summary": context[:400],
            "tags": ["paper", "arxiv"],
            "source_task_id": task_id,
        })

    # --- Pass 2: "Author et al. YYYY" not near an arxiv ID ---
    for m in _PAPER_AUTHOR_PATTERN.finditer(text):
        candidate = m.group(1).strip()
        entity_id = _entity_id("paper", candidate)
        if entity_id in seen_ids:
            continue

        # Skip if it's very close to an already-found arxiv chunk
        context_start = max(0, m.start() - 100)
        context_end = min(len(text), m.end() + 100)
        context = text[context_start:context_end]
        if _ARXIV_PATTERN.search(context):
            # Already captured via arxiv pass
            continue

        seen_ids.add(entity_id)
        summary_context = text[context_start:context_end].strip()
        entities.append({
            "entity_type": "paper",
            "name": candidate,
            "summary": summary_context[:400],
            "tags": ["paper"],
            "source_task_id": task_id,
        })

    # --- Pass 3: Recurring bold/quoted terms → concept candidates ---
    bold_terms = _BOLD_PATTERN.findall(text)
    from collections import Counter
    counts = Counter(bold_terms)
    for term, count in counts.items():
        if count < 2:
            continue
        if len(term) < 5 or len(term) > 80:
            continue
        entity_id = _entity_id("concept", term)
        if entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)

        # Find first occurrence context
        m = re.search(re.escape(f"**{term}**"), text)
        if m:
            ctx_start = max(0, m.start() - 150)
            ctx_end = min(len(text), m.end() + 150)
            summary_context = text[ctx_start:ctx_end].strip()
        else:
            summary_context = term

        entities.append({
            "entity_type": "concept",
            "name": term,
            "summary": summary_context[:400],
            "tags": ["concept"],
            "source_task_id": task_id,
        })

    return entities


def extract_and_save_all(text: str, task_id: str = "") -> int:
    """Extract entities from text and save them to the store. Returns count saved."""
    candidates = extract_entities_from_text(text, task_id)
    saved = 0
    for e in candidates:
        try:
            upsert_entity(
                entity_type=e["entity_type"],
                name=e["name"],
                summary=e["summary"],
                tags=e.get("tags", []),
                source_task_id=e.get("source_task_id", task_id),
            )
            saved += 1
        except Exception as ex:
            log.warning("Failed to save entity '%s': %s", e.get("name"), ex)
    return saved


# ---------------------------------------------------------------------------
# Read / Search
# ---------------------------------------------------------------------------

def search_entities(query: str, entity_type: str = None, top_k: int = 5) -> list[dict]:
    """Search entities by name/summary keyword matching.

    Returns list of entity dicts sorted by score then mention_count desc.
    """
    conn = _get_db()

    if entity_type:
        rows = conn.execute(
            "SELECT id, entity_type, name, summary, last_seen, mention_count, tags "
            "FROM entities WHERE entity_type = ?",
            (entity_type,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, entity_type, name, summary, last_seen, mention_count, tags "
            "FROM entities"
        ).fetchall()

    conn.close()

    if not rows:
        return []

    query_lower = query.lower()
    query_terms = set(re.findall(r'\w+', query_lower))

    results = []
    for row in rows:
        entity_id, etype, name, summary, last_seen, count, tags_json = row
        name_lower = name.lower()
        summary_lower = summary.lower()

        # Name match score (high weight)
        if query_lower in name_lower or name_lower in query_lower:
            name_score = 1.0
        else:
            name_terms = set(re.findall(r'\w+', name_lower))
            overlap = query_terms & name_terms
            name_score = len(overlap) / max(len(query_terms), len(name_terms), 1) if overlap else 0.0

        # Summary match score
        summary_score = sum(1 for t in query_terms if t in summary_lower) / max(len(query_terms), 1)

        combined = 0.7 * name_score + 0.3 * summary_score
        if combined > 0.05:
            results.append({
                "id": entity_id,
                "entity_type": etype,
                "name": name,
                "summary": summary,
                "last_seen": last_seen,
                "mention_count": count,
                "tags": json.loads(tags_json or "[]"),
                "score": combined,
            })

    results.sort(key=lambda x: (x["score"], x["mention_count"]), reverse=True)
    return results[:top_k]


def format_entity_recall(query: str, top_k: int = 3) -> str:
    """Search entities and return formatted block for prompt injection.

    Returns empty string if nothing relevant found.
    """
    results = search_entities(query, top_k=top_k)
    if not results:
        return ""

    parts = []
    for r in results:
        etype = r["entity_type"]
        name = r["name"]
        count = r["mention_count"]
        summary = r["summary"][:300]
        parts.append(f"- **{name}** [{etype}, {count}x]: {summary}")

    return "## From entity memory\n" + "\n\n".join(parts)


def get_stats() -> dict:
    """Get entity store statistics."""
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    by_type = dict(conn.execute(
        "SELECT entity_type, COUNT(*) FROM entities GROUP BY entity_type"
    ).fetchall())
    conn.close()
    return {"total_entities": total, "by_type": by_type}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "search":
        query = " ".join(sys.argv[2:])
        results = search_entities(query, top_k=5)
        for r in results:
            print(f"\n[{r['entity_type']}] {r['name']} (×{r['mention_count']}, score={r['score']:.2f})")
            print(f"  {r['summary'][:200]}")
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(get_stats(), indent=2))
    else:
        print("Usage: python entity_store.py [search <query> | stats]")
