"""Knowledge Links — explicit relationships between knowledge fragments.

Enables cross-referencing between memories, worldview sections, reading notes,
learned skills, and episodes. Links are stored in PostgreSQL and can be
traversed to surface related context during recall.
"""

import logging
from datetime import datetime

log = logging.getLogger("mira")

VALID_TYPES = {"memory", "worldview", "reading_note", "skill", "episode", "writeback", "wiki"}
VALID_RELATIONS = {"supports", "contradicts", "extends", "supersedes", "related"}
_COLUMN_CACHE: dict[str, bool] = {}


def _get_conn():
    """Get PostgreSQL connection from memory store."""
    try:
        from memory.store import get_store

        store = get_store()
        conn = store.conn
        if not conn:
            log.warning("knowledge_links unavailable: memory store returned no DB connection")
        return conn
    except Exception as e:
        log.warning("knowledge_links unavailable: failed to get DB connection: %s", e)
        return None


def _ensure_table():
    """Create the knowledge_links table if it doesn't exist."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_links (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL DEFAULT 'ang',
                source_type VARCHAR(30) NOT NULL,
                source_id VARCHAR(200) NOT NULL,
                target_type VARCHAR(30) NOT NULL,
                target_id VARCHAR(200) NOT NULL,
                relation VARCHAR(50) NOT NULL,
                confidence FLOAT DEFAULT 0.5,
                created_at TIMESTAMPTZ DEFAULT now(),
                created_by VARCHAR(50) DEFAULT 'auto'
            )
        """
        )
        cur.execute("ALTER TABLE knowledge_links ADD COLUMN IF NOT EXISTS user_id VARCHAR(100) NOT NULL DEFAULT 'ang'")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kl_user_source ON knowledge_links(user_id, source_type, source_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_kl_user_target ON knowledge_links(user_id, target_type, target_id)")
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        log.warning("knowledge_links table setup failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _has_user_id_column() -> bool:
    cached = _COLUMN_CACHE.get("knowledge_links.user_id")
    if cached is not None:
        return cached
    conn = _get_conn()
    if not conn:
        _COLUMN_CACHE["knowledge_links.user_id"] = False
        return False
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT 1
               FROM information_schema.columns
               WHERE table_name = 'knowledge_links' AND column_name = 'user_id'
               LIMIT 1"""
        )
        present = bool(cur.fetchone())
        cur.close()
    except Exception:
        present = False
    _COLUMN_CACHE["knowledge_links.user_id"] = present
    return present


def add_link(
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relation: str,
    confidence: float = 0.5,
    created_by: str = "auto",
    user_id: str = "ang",
) -> bool:
    """Add a link between two knowledge fragments.

    Returns True if link was created, False otherwise.
    Skips duplicate links (same source+target+relation).
    """
    if source_type not in VALID_TYPES or target_type not in VALID_TYPES:
        log.warning("Invalid link type: %s -> %s", source_type, target_type)
        return False
    if relation not in VALID_RELATIONS:
        log.warning("Invalid relation: %s", relation)
        return False

    conn = _get_conn()
    if not conn:
        return False

    try:
        _ensure_table()
        cur = conn.cursor()
        # Skip if duplicate
        if _has_user_id_column():
            cur.execute(
                """
                SELECT 1 FROM knowledge_links
                WHERE user_id = %s
                  AND source_type = %s AND source_id = %s
                  AND target_type = %s AND target_id = %s
                  AND relation = %s
                LIMIT 1
            """,
                (user_id, source_type, source_id, target_type, target_id, relation),
            )
        else:
            cur.execute(
                """
                SELECT 1 FROM knowledge_links
                WHERE source_type = %s AND source_id = %s
                  AND target_type = %s AND target_id = %s
                  AND relation = %s
                LIMIT 1
            """,
                (source_type, source_id, target_type, target_id, relation),
            )
        if cur.fetchone():
            cur.close()
            return False  # Already exists

        if _has_user_id_column():
            cur.execute(
                """
                INSERT INTO knowledge_links
                    (user_id, source_type, source_id, target_type, target_id, relation, confidence, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
                (user_id, source_type, source_id, target_type, target_id, relation, confidence, created_by),
            )
        else:
            cur.execute(
                """
                INSERT INTO knowledge_links
                    (source_type, source_id, target_type, target_id, relation, confidence, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
                (source_type, source_id, target_type, target_id, relation, confidence, created_by),
            )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        log.warning("add_link failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def get_links(source_type: str, source_id: str, user_id: str = "ang") -> list[dict]:
    """Get all outgoing links from a knowledge fragment."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        _ensure_table()
        cur = conn.cursor()
        if _has_user_id_column():
            cur.execute(
                """
                SELECT target_type, target_id, relation, confidence, created_at
                FROM knowledge_links
                WHERE user_id = %s AND source_type = %s AND source_id = %s
                ORDER BY confidence DESC
            """,
                (user_id, source_type, source_id),
            )
        else:
            cur.execute(
                """
                SELECT target_type, target_id, relation, confidence, created_at
                FROM knowledge_links
                WHERE source_type = %s AND source_id = %s
                ORDER BY confidence DESC
            """,
                (source_type, source_id),
            )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "target_type": r[0],
                "target_id": r[1],
                "relation": r[2],
                "confidence": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("get_links failed: %s", e)
        return []


def get_backlinks(target_type: str, target_id: str, user_id: str = "ang") -> list[dict]:
    """Get all incoming links to a knowledge fragment."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        _ensure_table()
        cur = conn.cursor()
        if _has_user_id_column():
            cur.execute(
                """
                SELECT source_type, source_id, relation, confidence, created_at
                FROM knowledge_links
                WHERE user_id = %s AND target_type = %s AND target_id = %s
                ORDER BY confidence DESC
            """,
                (user_id, target_type, target_id),
            )
        else:
            cur.execute(
                """
                SELECT source_type, source_id, relation, confidence, created_at
                FROM knowledge_links
                WHERE target_type = %s AND target_id = %s
                ORDER BY confidence DESC
            """,
                (target_type, target_id),
            )
        rows = cur.fetchall()
        cur.close()
        return [
            {
                "source_type": r[0],
                "source_id": r[1],
                "relation": r[2],
                "confidence": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in rows
        ]
    except Exception as e:
        log.warning("get_backlinks failed: %s", e)
        return []


def auto_link(
    content: str, source_type: str, source_id: str, top_k: int = 3, min_score: float = 0.5, user_id: str = "ang"
) -> int:
    """Automatically discover and create links to related knowledge.

    Uses vector similarity from memory_store to find related entries,
    then creates 'related' links to the top matches.

    Returns the number of links created.
    """
    created = 0
    try:
        from memory.store import get_store

        store = get_store()
        results = store.recall(content[:500], top_k=top_k, user_id=user_id)

        for r in results:
            score = r.get("score", 0)
            if score < min_score:
                continue
            target_type_raw = r.get("source_type", "memory")
            # Map source_type to our link type vocabulary
            type_map = {
                "memory_entry": "memory",
                "memory_overflow": "memory",
                "episode": "episode",
                "writeback": "writeback",
                "reading_note": "reading_note",
                "explore_briefing": "reading_note",
            }
            target_type = type_map.get(target_type_raw, "memory")
            target_id = r.get("source_id", str(r.get("id", "")))

            # Don't self-link
            if target_type == source_type and target_id == source_id:
                continue

            if add_link(
                source_type, source_id, target_type, target_id, "related", confidence=round(score, 3), user_id=user_id
            ):
                created += 1

    except Exception as e:
        log.warning("auto_link failed: %s", e)

    if created:
        log.info("Auto-linked %s/%s → %d related entries", source_type, source_id[:30], created)
    return created
