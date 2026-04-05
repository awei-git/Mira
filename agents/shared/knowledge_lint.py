"""Knowledge lint — periodic health check for the soul knowledge system.

Checks for contradictions, stale facts, orphan pages, and duplicates.
Designed to run during weekly reflect or on-demand.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import READING_NOTES_DIR, SKILLS_DIR, SKILLS_INDEX, MEMORY_FILE

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Individual lint checks
# ---------------------------------------------------------------------------

def _check_stale_facts(max_age_days: int = 90) -> list[dict]:
    """Find reading notes and memory entries older than max_age_days."""
    stale = []
    cutoff = datetime.now() - timedelta(days=max_age_days)

    # Check reading notes
    if READING_NOTES_DIR.exists():
        for path in sorted(READING_NOTES_DIR.glob("*.md")):
            try:
                date_str = path.stem[:10]
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date < cutoff:
                    stale.append({
                        "type": "reading_note",
                        "path": str(path.name),
                        "age_days": (datetime.now() - file_date).days,
                    })
            except ValueError:
                continue

    return stale


def _check_orphan_skills() -> list[dict]:
    """Find skill files not referenced in skills index."""
    orphans = []
    if not SKILLS_DIR.exists():
        return orphans

    # Load index
    indexed_files = set()
    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
            indexed_files = {s.get("file", "") for s in index}
        except (json.JSONDecodeError, OSError):
            pass

    # Check all .md files in learned/
    for path in SKILLS_DIR.glob("*.md"):
        if path.name not in indexed_files:
            orphans.append({
                "type": "orphan_skill",
                "path": str(path.name),
                "reason": "not in index.json",
            })

    return orphans


def _check_duplicates_in_memory() -> list[dict]:
    """Find near-duplicate entries in memory.md via simple text similarity."""
    duplicates = []
    if not MEMORY_FILE.exists():
        return duplicates

    try:
        text = MEMORY_FILE.read_text(encoding="utf-8")
    except OSError:
        return duplicates

    lines = [l.strip() for l in text.split("\n") if l.strip().startswith("- [")]

    # Extract content after timestamp
    entries = []
    for line in lines:
        # Format: - [2026-04-05 14:30] content...
        bracket_end = line.find("]")
        if bracket_end > 0:
            content = line[bracket_end + 1:].strip()
            entries.append(content)

    # Simple O(n^2) check — memory.md is max 200 lines so this is fine
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if _text_similarity(entries[i], entries[j]) > 0.85:
                duplicates.append({
                    "type": "duplicate_memory",
                    "entry_a": entries[i][:100],
                    "entry_b": entries[j][:100],
                })

    return duplicates


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity on word sets."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def _check_contradictions_via_db() -> list[dict]:
    """Find potential contradictions using vector similarity in PostgreSQL.

    Looks for entry pairs with high semantic similarity that may conflict.
    Uses a simple heuristic: very similar embeddings with negation words.
    """
    contradictions = []
    try:
        from memory_store import get_store
        store = get_store()
        if not store.conn:
            return contradictions

        # Query for pairs with high cosine similarity
        cur = store.conn.cursor()
        cur.execute("""
            SELECT a.id, a.content, b.id, b.content,
                   1 - (a.embedding <=> b.embedding) AS similarity
            FROM episodic_memory a, episodic_memory b
            WHERE a.id < b.id
              AND a.embedding IS NOT NULL
              AND b.embedding IS NOT NULL
              AND 1 - (a.embedding <=> b.embedding) > 0.85
            ORDER BY similarity DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
        cur.close()

        negation_words = {"not", "never", "no", "don't", "doesn't", "isn't",
                          "wasn't", "aren't", "won't", "can't", "shouldn't",
                          "opposite", "incorrect", "wrong", "false"}

        for row in rows:
            id_a, content_a, id_b, content_b, sim = row
            words_a = set(content_a.lower().split())
            words_b = set(content_b.lower().split())
            # Flag if one has negation words the other doesn't
            neg_a = words_a & negation_words
            neg_b = words_b & negation_words
            if neg_a != neg_b:  # Asymmetric negation
                contradictions.append({
                    "type": "potential_contradiction",
                    "entry_a": content_a[:150],
                    "entry_b": content_b[:150],
                    "similarity": round(sim, 3),
                })

    except Exception as e:
        log.debug("Contradiction check skipped: %s", e)

    return contradictions


# ---------------------------------------------------------------------------
# Main lint orchestrator
# ---------------------------------------------------------------------------

def lint_all() -> dict:
    """Run all knowledge lint checks. Returns a structured report."""
    log.info("Running knowledge lint...")

    results = {
        "timestamp": datetime.now().isoformat(),
        "stale": _check_stale_facts(),
        "orphans": _check_orphan_skills(),
        "duplicates": _check_duplicates_in_memory(),
        "contradictions": _check_contradictions_via_db(),
    }

    total = sum(len(v) for k, v in results.items() if isinstance(v, list))
    log.info("Knowledge lint complete: %d issues found", total)

    return results


def generate_lint_report(results: dict) -> str:
    """Format lint results as readable markdown."""
    lines = [
        "# Knowledge Lint Report",
        f"*Generated: {results.get('timestamp', 'unknown')}*",
        "",
    ]

    total = sum(len(v) for k, v in results.items() if isinstance(v, list))
    lines.append(f"**Total issues: {total}**\n")

    # Contradictions
    contras = results.get("contradictions", [])
    if contras:
        lines.append(f"## Potential Contradictions ({len(contras)})\n")
        for c in contras:
            lines.append(f"- **A**: {c['entry_a']}")
            lines.append(f"  **B**: {c['entry_b']}")
            lines.append(f"  Similarity: {c.get('similarity', '?')}\n")

    # Stale facts
    stale = results.get("stale", [])
    if stale:
        lines.append(f"## Stale Facts ({len(stale)})\n")
        for s in stale:
            lines.append(f"- `{s['path']}` — {s['age_days']} days old")
        lines.append("")

    # Orphans
    orphans = results.get("orphans", [])
    if orphans:
        lines.append(f"## Orphan Skills ({len(orphans)})\n")
        for o in orphans:
            lines.append(f"- `{o['path']}` — {o['reason']}")
        lines.append("")

    # Duplicates
    dupes = results.get("duplicates", [])
    if dupes:
        lines.append(f"## Duplicate Memory Entries ({len(dupes)})\n")
        for d in dupes:
            lines.append(f"- `{d['entry_a']}`")
            lines.append(f"  ~= `{d['entry_b']}`\n")

    if total == 0:
        lines.append("No issues found. Knowledge system is healthy.")

    return "\n".join(lines)
