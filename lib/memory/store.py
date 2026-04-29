"""Unified memory interface backed by PostgreSQL + pgvector.

Replaces the SQLite-based memory_index.py with:
- PostgreSQL + pgvector for vector storage and similarity search
- oMLX nomic-embed-text for local, free, fast embeddings (768 dims)
- Hybrid scoring: 70% vector + 30% keyword, with temporal decay
- Fallback to memory_index.py if Postgres is unavailable

Usage:
    from memory_store import get_store, search_formatted, rebuild_index

    store = get_store()
    store.remember("important fact", source_type="episode", source_id="task_123")
    results = store.recall("what did we discuss about X?")
    formatted = search_formatted("query", top_k=5)
"""

import hashlib
import logging
import math
import re
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("mira.memory_store")

# Chunking parameters (match memory_index.py)
_CHUNK_CHARS = 1600  # ~400 tokens
_CHUNK_OVERLAP = 320  # ~80 tokens
_EMBED_BATCH = 50
_DECAY_HALF_LIFE = 30  # days
_VECTOR_WEIGHT = 0.7
_KEYWORD_WEIGHT = 0.3


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of ~400 tokens."""
    if len(text) <= _CHUNK_CHARS:
        return [text]

    # Split by paragraphs first
    paragraphs = re.split(r"\n\n+", text)
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 > _CHUNK_CHARS and current:
            chunks.append(current.strip())
            # Overlap: keep tail of current chunk
            current = current[-_CHUNK_OVERLAP:] + "\n\n" + para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks if chunks else [text]


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts using oMLX nomic-embed-text. Returns list of 768-dim vectors."""
    from llm_providers.local import omlx_embed

    embeddings = []
    for text in texts:
        emb = omlx_embed(text)
        embeddings.append(emb)

    return embeddings


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _keyword_score(query: str, content: str) -> float:
    """Simple word-presence fraction."""
    terms = set(query.lower().split())
    if not terms:
        return 0.0
    content_lower = content.lower()
    matches = sum(1 for t in terms if t in content_lower)
    return matches / len(terms)


class MemoryStore:
    """Unified memory interface backed by PostgreSQL + pgvector.

    All tables are assumed to have the user_id column (added by migration
    002_memory_user_scope.sql). Migrations are applied automatically on
    first connection via _ensure_migrated().
    """

    def __init__(self, db_url: str, min_conn: int = 1, max_conn: int = 4):
        self._db_url = db_url
        self._pool = None
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._migrated = False

    def _get_pool(self):
        """Lazy pool initialization with auto-migration."""
        if self._pool is None:
            from psycopg2 import pool as _pool

            self._pool = _pool.ThreadedConnectionPool(self._min_conn, self._max_conn, self._db_url)
            self._ensure_migrated()
        return self._pool

    def _ensure_migrated(self):
        """Run pending DB migrations on first connect."""
        if self._migrated:
            return
        try:
            import sys

            migrations_dir = Path(__file__).resolve().parent.parent.parent / "agents" / "shared" / "migrations"
            if str(migrations_dir.parent) not in sys.path:
                sys.path.insert(0, str(migrations_dir.parent))
            from migrations.run import run_migrations

            run_migrations()
            self._migrated = True
        except Exception as e:
            log.warning("Auto-migration failed (non-fatal): %s", e)
            self._migrated = True  # Don't retry every call

    def _get_conn(self):
        """Get a connection from the pool with auto-reconnect."""
        try:
            p = self._get_pool()
            conn = p.getconn()
            conn.autocommit = False
            return conn
        except Exception:
            # Pool may be exhausted or broken — reset and retry once
            self._pool = None
            p = self._get_pool()
            conn = p.getconn()
            conn.autocommit = False
            return conn

    def _put_conn(self, conn):
        """Return a connection to the pool."""
        try:
            if self._pool and conn and not conn.closed:
                self._pool.putconn(conn)
        except Exception as e:
            log.debug("Failed to return connection to pool: %s", e)

    @property
    def conn(self):
        """Best-effort compatibility property for callers needing a raw connection.

        WARNING: Callers using this property must call _put_conn() when done,
        otherwise the connection leaks from the pool.
        """
        try:
            return self._get_conn()
        except Exception as e:
            log.warning("Failed to get DB connection: %s", e)
            return None

    def _execute(self, sql: str, params: tuple = (), fetch: bool = False):
        """Execute SQL with connection pooling."""
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            if fetch:
                result = cur.fetchall()
            else:
                result = None
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            self._put_conn(conn)

    # ------------------------------------------------------------------
    # REMEMBER
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        source_type: str,
        source_id: str = "",
        title: str = "",
        importance: float = 0.5,
        tags: list[str] | None = None,
        summary: str = "",
        table: str = "episodic",
        user_id: str = "ang",
    ) -> int | None:
        """Store a memory with vector embedding.

        Args:
            content: The text to remember.
            source_type: Category (episode, conversation, memory_entry, memory_overflow, etc.)
            source_id: Original task_id or filename for dedup.
            title: Optional title.
            importance: 0.0-1.0 relevance weight.
            tags: Optional tags for filtering.
            summary: Optional LLM-generated summary.
            table: "episodic" or "thought" (for thought_stream).

        Returns: The row ID, or None on failure.
        """
        try:
            emb = _embed_texts([content[:2000]])[0]  # cap embedding input
            if not emb:
                log.warning("Empty embedding for remember(); storing without vector")

            emb_literal = f"[{','.join(str(x) for x in emb)}]" if emb else None

            if table == "episodic":
                row = self._execute(
                    """INSERT INTO episodic_memory
                       (user_id, source_type, source_id, title, content, summary,
                        embedding, tags, importance)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                       RETURNING id""",
                    (user_id, source_type, source_id, title, content, summary, emb_literal, tags or [], importance),
                    fetch=True,
                )
            elif table == "thought":
                row = self._execute(
                    """INSERT INTO thought_stream
                       (user_id, thought_type, content, embedding, source_context, tags)
                       VALUES (%s, %s, %s, %s::vector, %s, %s)
                       RETURNING id""",
                    (user_id, source_type, content, emb_literal, source_id, tags or []),
                    fetch=True,
                )
            else:
                log.error("Unknown table: %s", table)
                return None

            return row[0][0] if row else None
        except Exception as e:
            log.error("remember() failed: %s", e)
            return None

    def verify_memory(self, memory_id: int, table: str = "episodic"):
        """Mark a memory as verified — resets decay by updating created_at to now.

        Call during reflect when a memory is confirmed still relevant.
        This gives verified memories a fresh decay window.
        """
        tbl = "episodic_memory" if table == "episodic" else "thought_stream"
        try:
            self._execute(
                f"UPDATE {tbl} SET created_at = NOW(), importance = LEAST(importance + 0.1, 1.0) WHERE id = %s",
                (memory_id,),
            )
            log.debug("Memory %d verified (decay reset)", memory_id)
        except Exception as e:
            log.warning("verify_memory failed: %s", e)

    # ------------------------------------------------------------------
    # RECALL (hybrid vector + keyword search)
    # ------------------------------------------------------------------

    def recall(
        self,
        query: str,
        top_k: int = 5,
        source_filter: str | None = None,
        table: str | None = None,
        include_decay: bool = True,
        user_id: str = "ang",
    ) -> list[dict]:
        """Hybrid semantic + keyword search across memory tables.

        Searches episodic_memory and semantic_memory (unless table is specified).
        Returns list of dicts: {content, source_type, score, created_at, ...}
        """
        try:
            query_emb = _embed_texts([query])[0]
        except Exception:
            query_emb = []

        results = []

        tables_to_search = []
        if table:
            tables_to_search = [table]
        else:
            tables_to_search = ["episodic_memory", "semantic_memory"]

        for tbl in tables_to_search:
            try:
                rows = self._search_table(
                    tbl, query, query_emb, source_filter, include_decay, user_id, top_k * 2
                )  # fetch extra, merge later
                results.extend(rows)
            except Exception as e:
                log.warning("Search in %s failed: %s", tbl, e)

        # Sort by score, deduplicate by content hash
        results.sort(key=lambda r: r["score"], reverse=True)
        seen = set()
        deduped = []
        for r in results:
            h = _content_hash(r["content"][:500])
            if h not in seen:
                seen.add(h)
                deduped.append(r)
            if len(deduped) >= top_k:
                break

        return deduped

    def _search_table(
        self,
        table: str,
        query: str,
        query_emb: list[float],
        source_filter: str | None,
        include_decay: bool,
        user_id: str,
        limit: int,
    ) -> list[dict]:
        """Search a single table with hybrid scoring."""
        # Build WHERE clause
        where_parts = ["user_id = %s"]
        params: list = [user_id]
        if source_filter:
            where_parts.append("source_type = %s")
            params.append(source_filter)

        where_sql = "WHERE " + " AND ".join(where_parts)

        # Fetch rows (we score in Python for hybrid + decay flexibility)
        if table == "episodic_memory":
            sql = f"""SELECT id, source_type, source_id, title, content,
                             embedding, importance, created_at
                      FROM episodic_memory {where_sql}
                      ORDER BY created_at DESC LIMIT %s"""
        elif table == "semantic_memory":
            sql = f"""SELECT id, source_type, source_path, '' as title, content,
                             embedding, importance, created_at
                      FROM semantic_memory {where_sql}
                      ORDER BY updated_at DESC LIMIT %s"""
        else:
            return []

        params.append(min(limit * 10, 2000))  # fetch pool
        rows = self._execute(sql, tuple(params), fetch=True)
        if not rows:
            return []

        now = datetime.now()
        scored = []

        for row in rows:
            (row_id, src_type, src_id, title, content, emb_raw, importance, created_at) = row

            # Vector score
            vec_score = 0.0
            if query_emb and emb_raw:
                # pgvector returns a string like "[0.1,0.2,...]" or binary
                if isinstance(emb_raw, str):
                    row_emb = [float(x) for x in emb_raw.strip("[]").split(",")]
                elif isinstance(emb_raw, (list, tuple)):
                    row_emb = list(emb_raw)
                else:
                    row_emb = []
                vec_score = _cosine_sim(query_emb, row_emb)

            # Keyword score
            kw_score = _keyword_score(query, content)

            # Combined
            score = _VECTOR_WEIGHT * vec_score + _KEYWORD_WEIGHT * kw_score

            # Temporal decay
            if include_decay and created_at:
                age_days = (now - created_at.replace(tzinfo=None)).total_seconds() / 86400
                decay = math.exp(-0.693 * age_days / _DECAY_HALF_LIFE)
                score *= decay

            # Importance boost
            score *= 0.5 + importance

            scored.append(
                {
                    "id": row_id,
                    "content": content,
                    "source_type": src_type,
                    "source_id": src_id,
                    "title": title,
                    "score": score,
                    "created_at": created_at,
                }
            )

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:limit]

    # ------------------------------------------------------------------
    # SEARCH FORMATTED (drop-in for memory_index.search_formatted)
    # ------------------------------------------------------------------

    def search_formatted(self, query: str, top_k: int = 5, max_chars: int = 3000, user_id: str = "ang") -> str:
        """Search and return formatted results for prompt injection."""
        results = self.recall(query, top_k=top_k, user_id=user_id)
        if not results:
            return ""

        parts = []
        total = 0
        for r in results:
            label = r["source_type"]
            date_str = ""
            if r["created_at"]:
                date_str = r["created_at"].strftime(" %Y-%m-%d")

            snippet = r["content"][:600]
            entry = f"[{label}{date_str}] {snippet}"

            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry) + 4  # separator

        return "\n---\n".join(parts)

    # ------------------------------------------------------------------
    # REBUILD FROM SOUL FILES
    # ------------------------------------------------------------------

    def rebuild_from_soul(self, force: bool = False, user_id: str = "ang") -> int:
        """Re-index all soul files into semantic_memory.

        Replaces memory_index.rebuild_index(). Only re-embeds changed content
        (by content_hash comparison) unless force=True.
        """
        from config import (
            SOUL_DIR,
            IDENTITY_FILE,
            MEMORY_FILE,
            INTERESTS_FILE,
            WORLDVIEW_FILE,
            JOURNAL_DIR,
            READING_NOTES_DIR,
            SKILLS_DIR,
            CONVERSATIONS_DIR,
            EPISODES_DIR,
            CATALOG_FILE,
            BRIEFINGS_DIR,
            WRITINGS_OUTPUT_DIR,
            RESEARCH_DIR,
        )

        sources = []
        # Core soul files
        for fpath, stype in [
            (IDENTITY_FILE, "identity"),
            (MEMORY_FILE, "memory"),
            (INTERESTS_FILE, "interests"),
            (WORLDVIEW_FILE, "worldview"),
        ]:
            if fpath.exists():
                sources.append((fpath, stype))

        # Directory-based sources (flat — *.md in top level only)
        for dirpath, stype in [
            (JOURNAL_DIR, "journal"),
            (READING_NOTES_DIR, "reading_note"),
            (SKILLS_DIR, "skill"),
            (CONVERSATIONS_DIR, "conversation"),
            (EPISODES_DIR, "episode"),
            (BRIEFINGS_DIR, "briefing"),
        ]:
            if dirpath.exists():
                for f in sorted(dirpath.glob("*.md")):
                    sources.append((f, stype))

        # Directory-based sources (recursive — *.md in subdirs too)
        for dirpath, stype in [
            (WRITINGS_OUTPUT_DIR, "writing"),
            (RESEARCH_DIR, "research"),
        ]:
            if dirpath.exists():
                for f in sorted(dirpath.rglob("*.md")):
                    sources.append((f, stype))

        # Catalog
        if CATALOG_FILE.exists():
            sources.append((CATALOG_FILE, "catalog"))

        # Get existing hashes
        existing_hashes = {}
        if not force:
            rows = self._execute(
                "SELECT source_path, content_hash FROM semantic_memory WHERE user_id = %s",
                (user_id,),
                fetch=True,
            )
            if rows:
                for sp, ch in rows:
                    existing_hashes[sp] = ch

        embedded_count = 0
        batch_texts = []
        batch_meta = []

        for fpath, stype in sources:
            try:
                text = fpath.read_text("utf-8")
            except Exception:
                continue

            chunks = _chunk_text(text)
            fpath_str = str(fpath)

            for i, chunk in enumerate(chunks):
                ch = _content_hash(chunk)
                chunk_path = f"{fpath_str}:{i}"

                if not force and chunk_path in existing_hashes:
                    if existing_hashes[chunk_path] == ch:
                        continue  # unchanged

                batch_texts.append(chunk)
                batch_meta.append((stype, fpath_str, i, chunk, ch))

                if len(batch_texts) >= _EMBED_BATCH:
                    embedded_count += self._flush_semantic_batch(batch_texts, batch_meta, user_id=user_id)
                    batch_texts = []
                    batch_meta = []

        # Flush remaining
        if batch_texts:
            embedded_count += self._flush_semantic_batch(batch_texts, batch_meta, user_id=user_id)

        # Clean up stale entries (files that no longer exist)
        all_paths = {str(fp) for fp, _ in sources}
        stale = self._execute(
            "SELECT DISTINCT source_path FROM semantic_memory WHERE user_id = %s",
            (user_id,),
            fetch=True,
        )
        if stale:
            for (sp,) in stale:
                # Extract base path (before :chunk_index)
                base = sp.split(":")[0] if ":" in sp else sp
                if base not in all_paths:
                    self._execute(
                        "DELETE FROM semantic_memory WHERE user_id = %s AND source_path LIKE %s",
                        (user_id, f"{base}%"),
                    )

        log.info("Rebuilt semantic memory: %d chunks embedded", embedded_count)
        return embedded_count

    def _flush_semantic_batch(self, texts: list[str], meta: list[tuple], *, user_id: str = "ang") -> int:
        """Embed and insert a batch of semantic memory chunks."""
        embeddings = _embed_texts(texts)
        count = 0

        for emb, (stype, fpath, idx, content, ch) in zip(embeddings, meta):
            emb_literal = f"[{','.join(str(x) for x in emb)}]" if emb else None
            chunk_path = f"{fpath}:{idx}"

            try:
                # Upsert: delete old, insert new
                self._execute(
                    "DELETE FROM semantic_memory WHERE user_id = %s AND source_path = %s AND chunk_index = %s",
                    (user_id, fpath, idx),
                )
                self._execute(
                    """INSERT INTO semantic_memory
                       (user_id, source_type, source_path, chunk_index, content,
                        content_hash, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s::vector)""",
                    (user_id, stype, fpath, idx, content, ch, emb_literal),
                )
                count += 1
            except Exception as e:
                log.warning("Failed to insert chunk %s: %s", chunk_path, e)

        return count

    # ------------------------------------------------------------------
    # THOUGHT STREAM
    # ------------------------------------------------------------------

    def store_thought(
        self,
        content: str,
        thought_type: str,
        parent_id: int | None = None,
        source_context: str = "",
        tags: list[str] | None = None,
        user_id: str = "ang",
    ) -> int | None:
        """Store a thought in the thought_stream table."""
        try:
            emb = _embed_texts([content[:2000]])[0]
            emb_literal = f"[{','.join(str(x) for x in emb)}]" if emb else None

            row = self._execute(
                """INSERT INTO thought_stream
                   (user_id, thought_type, content, embedding, parent_id,
                    source_context, tags)
                   VALUES (%s, %s, %s, %s::vector, %s, %s, %s)
                   RETURNING id""",
                (user_id, thought_type, content, emb_literal, parent_id, source_context, tags or []),
                fetch=True,
            )
            return row[0][0] if row else None
        except Exception as e:
            log.error("store_thought() failed: %s", e)
            return None

    def recall_thoughts(
        self,
        query: str,
        top_k: int = 5,
        min_maturity: float = 0.0,
        thought_type: str | None = None,
        user_id: str = "ang",
    ) -> list[dict]:
        """Recall relevant thoughts from thought_stream."""
        try:
            query_emb = _embed_texts([query])[0]
        except Exception:
            query_emb = []

        where_parts = ["maturity >= %s", "user_id = %s"]
        params: list = [min_maturity, user_id]
        if thought_type:
            where_parts.append("thought_type = %s")
            params.append(thought_type)

        where_sql = "WHERE " + " AND ".join(where_parts)
        params.append(min(top_k * 10, 500))

        rows = self._execute(
            f"""SELECT id, thought_type, content, embedding, parent_id,
                       source_context, maturity, access_count, tags, created_at
                FROM thought_stream {where_sql}
                ORDER BY created_at DESC LIMIT %s""",
            tuple(params),
            fetch=True,
        )
        if not rows:
            return []

        scored = []
        for row in rows:
            (tid, ttype, content, emb_raw, parent_id, src_ctx, maturity, access_count, tags, created_at) = row

            vec_score = 0.0
            if query_emb and emb_raw:
                if isinstance(emb_raw, str):
                    row_emb = [float(x) for x in emb_raw.strip("[]").split(",")]
                elif isinstance(emb_raw, (list, tuple)):
                    row_emb = list(emb_raw)
                else:
                    row_emb = []
                vec_score = _cosine_sim(query_emb, row_emb)

            kw_score = _keyword_score(query, content)
            score = _VECTOR_WEIGHT * vec_score + _KEYWORD_WEIGHT * kw_score

            scored.append(
                {
                    "id": tid,
                    "thought_type": ttype,
                    "content": content,
                    "parent_id": parent_id,
                    "maturity": maturity,
                    "score": score,
                    "created_at": created_at,
                    "tags": tags or [],
                }
            )

        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:top_k]

    def mature_thought(self, thought_id: int, increment: float = 0.2) -> float:
        """Increase maturity of a thought. Returns new maturity."""
        row = self._execute(
            """UPDATE thought_stream
               SET maturity = LEAST(maturity + %s, 1.0),
                   access_count = access_count + 1
               WHERE id = %s
               RETURNING maturity""",
            (increment, thought_id),
            fetch=True,
        )
        return row[0][0] if row else 0.0

    def get_thought_chain(self, thought_id: int) -> list[dict]:
        """Get a thought and all its ancestors (parent chain)."""
        chain = []
        current_id = thought_id
        seen = set()

        while current_id and current_id not in seen:
            seen.add(current_id)
            row = self._execute(
                """SELECT id, thought_type, content, parent_id, maturity, created_at
                   FROM thought_stream WHERE id = %s""",
                (current_id,),
                fetch=True,
            )
            if not row:
                break
            tid, ttype, content, parent_id, maturity, created_at = row[0]
            chain.append(
                {
                    "id": tid,
                    "thought_type": ttype,
                    "content": content,
                    "parent_id": parent_id,
                    "maturity": maturity,
                    "created_at": created_at,
                }
            )
            current_id = parent_id

        chain.reverse()  # oldest first
        return chain

    # ------------------------------------------------------------------
    # STATS
    # ------------------------------------------------------------------

    def get_stats(self, user_id: str | None = None) -> dict:
        """Stats across all memory tables."""
        stats = {}
        for tbl in ("episodic_memory", "semantic_memory", "thought_stream"):
            try:
                if user_id:
                    row = self._execute(f"SELECT count(*) FROM {tbl} WHERE user_id = %s", (user_id,), fetch=True)
                else:
                    row = self._execute(f"SELECT count(*) FROM {tbl}", fetch=True)
                stats[tbl] = row[0][0] if row else 0
            except Exception:
                stats[tbl] = -1
        return stats


# ======================================================================
# MODULE-LEVEL SINGLETON + BACKWARD COMPAT
# ======================================================================

_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    """Get or create the singleton MemoryStore."""
    global _store
    if _store is None:
        from config import DATABASE_URL

        _store = MemoryStore(DATABASE_URL)
    return _store


def search_formatted(query: str, top_k: int = 5, max_chars: int = 3000, user_id: str = "ang") -> str:
    """Drop-in replacement for memory_index.search_formatted()."""
    try:
        return get_store().search_formatted(query, top_k, max_chars, user_id=user_id)
    except Exception as e:
        log.warning("Postgres search failed, falling back to SQLite: %s", e)
        try:
            from memory_index import search_formatted as _sqlite_search

            return _sqlite_search(query, top_k, max_chars)
        except Exception:
            return ""


def rebuild_index(force: bool = False, user_id: str = "ang") -> int:
    """Drop-in replacement for memory_index.rebuild_index()."""
    try:
        return get_store().rebuild_from_soul(force, user_id=user_id)
    except Exception as e:
        log.warning("Postgres rebuild failed, falling back to SQLite: %s", e)
        try:
            from memory_index import rebuild_index as _sqlite_rebuild

            return _sqlite_rebuild(force)
        except Exception:
            return 0
