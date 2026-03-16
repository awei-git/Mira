"""Semantic memory index — vector + keyword search across all soul files.

Uses SQLite for storage and OpenAI text-embedding-3-small for embeddings.
Indexes: identity, worldview, memory, interests, journal, reading_notes, skills.
Chunks text into ~400-token pieces with overlap for retrieval.

Inspired by OpenClaw's hybrid search: vector (70%) + keyword (30%).
"""
import hashlib
import json
import logging
import math
import re
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    SOUL_DIR, IDENTITY_FILE, MEMORY_FILE, INTERESTS_FILE, WORLDVIEW_FILE,
    READING_NOTES_DIR, SKILLS_DIR, SKILLS_INDEX, JOURNAL_DIR, SECRETS_FILE,
    CONVERSATIONS_DIR, EPISODES_DIR, CATALOG_FILE,
)

log = logging.getLogger("mira.memory_index")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
CHUNK_SIZE = 400          # ~tokens (chars / 4 as rough estimate)
CHUNK_OVERLAP = 80
DB_PATH = SOUL_DIR / ".memory_index.sqlite"
# Temporal decay: halve relevance every 30 days
DECAY_HALF_LIFE_DAYS = 30


# ---------------------------------------------------------------------------
# SQLite setup
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_path TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding BLOB,
            updated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Embedding via OpenAI API
# ---------------------------------------------------------------------------

def _get_openai_key() -> str:
    from sub_agent import _parse_secrets_simple
    secrets = _parse_secrets_simple(SECRETS_FILE)
    keys = secrets.get("api_keys", {})
    openai_cfg = keys.get("openai", {})
    if isinstance(openai_cfg, dict):
        return openai_cfg.get("api_key", "")
    return ""


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via OpenAI API. Returns list of float vectors."""
    api_key = _get_openai_key()
    if not api_key:
        log.warning("No OpenAI API key — cannot embed")
        return [[] for _ in texts]

    # OpenAI allows up to 2048 inputs per batch, but keep it reasonable
    body = json.dumps({
        "input": texts,
        "model": EMBEDDING_MODEL,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/embeddings",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            # Sort by index to preserve order
            data = sorted(result["data"], key=lambda x: x["index"])
            embeddings = [d["embedding"] for d in data]
            # Validate embedding dimensions
            for emb in embeddings:
                if emb and len(emb) != EMBEDDING_DIMS:
                    log.error("Embedding dimension mismatch: got %d, expected %d",
                              len(emb), EMBEDDING_DIMS)
                    return [[] for _ in texts]
            return embeddings
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                log.warning("Embedding API rate limited, retrying in %ds...", wait)
                time.sleep(wait)
                continue
            log.error("Embedding API HTTP %d: %s", e.code,
                      e.read().decode("utf-8", errors="replace")[:200])
            return [[] for _ in texts]
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                log.warning("Embedding API failed (attempt %d/%d): %s, retrying in %ds...",
                            attempt + 1, max_retries, e, wait)
                time.sleep(wait)
                continue
            log.error("Embedding API failed after %d attempts: %s", max_retries, e)
            return [[] for _ in texts]
    return [[] for _ in texts]


def _embed_single(text: str) -> list[float]:
    results = _embed_texts([text])
    return results[0] if results else []


# ---------------------------------------------------------------------------
# Vector operations (pure Python — no numpy needed at this scale)
# ---------------------------------------------------------------------------

def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, source: str, source_path: str) -> list[dict]:
    """Split text into overlapping chunks with metadata."""
    # Rough char-based chunking (chars / 4 ≈ tokens)
    char_size = CHUNK_SIZE * 4
    char_overlap = CHUNK_OVERLAP * 4
    chunks = []

    # Split by paragraphs first, then merge into chunks
    paragraphs = re.split(r"\n\n+", text.strip())
    current = ""

    for para in paragraphs:
        if len(current) + len(para) > char_size and current:
            chunks.append({
                "content": current.strip(),
                "source": source,
                "source_path": source_path,
            })
            # Keep overlap
            current = current[-char_overlap:] + "\n\n" + para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append({
            "content": current.strip(),
            "source": source,
            "source_path": source_path,
        })

    return chunks


def _content_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def _gather_sources() -> list[dict]:
    """Gather all indexable content from soul files."""
    sources = []

    # Core soul files
    for name, path in [
        ("identity", IDENTITY_FILE),
        ("memory", MEMORY_FILE),
        ("interests", INTERESTS_FILE),
        ("worldview", WORLDVIEW_FILE),
    ]:
        if path.exists():
            sources.append({
                "source": name,
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            })

    # Journal entries
    if JOURNAL_DIR.exists():
        for path in sorted(JOURNAL_DIR.glob("*.md")):
            sources.append({
                "source": "journal",
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            })

    # Reading notes
    if READING_NOTES_DIR.exists():
        for path in sorted(READING_NOTES_DIR.glob("*.md")):
            sources.append({
                "source": "reading_note",
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            })

    # Learned skills
    if SKILLS_DIR.exists():
        for path in sorted(SKILLS_DIR.glob("*.md")):
            sources.append({
                "source": "skill",
                "path": str(path),
                "content": path.read_text(encoding="utf-8"),
            })

    # Conversation archives — full task conversations saved for recall across sessions
    if CONVERSATIONS_DIR.exists():
        for path in sorted(CONVERSATIONS_DIR.glob("*.md")):
            try:
                sources.append({
                    "source": "conversation",
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8"),
                })
            except OSError:
                continue

    # Episodes — complete task conversations archived for long-term recall
    if EPISODES_DIR.exists():
        for path in sorted(EPISODES_DIR.glob("*.md")):
            try:
                sources.append({
                    "source": "episode",
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8"),
                })
            except OSError:
                continue

    # Content catalog — metadata for all produced content
    if CATALOG_FILE.exists():
        try:
            content = CATALOG_FILE.read_text(encoding="utf-8")
            if content.strip():
                sources.append({
                    "source": "catalog",
                    "path": str(CATALOG_FILE),
                    "content": content,
                })
        except OSError:
            pass

    return sources


def rebuild_index(force: bool = False) -> int:
    """Rebuild the memory index. Only re-embeds changed content.

    Args:
        force: If True, re-embed everything regardless of hash.

    Returns:
        Number of chunks embedded.
    """
    conn = _get_db()
    try:
        sources = _gather_sources()

        # Chunk everything
        all_chunks = []
        for src in sources:
            chunks = _chunk_text(src["content"], src["source"], src["path"])
            all_chunks.extend(chunks)

        # Check which chunks need embedding
        to_embed = []
        for i, chunk in enumerate(all_chunks):
            chunk_id = f"{chunk['source']}:{_content_hash(chunk['content'])}"
            chunk["id"] = chunk_id
            chunk["hash"] = _content_hash(chunk["content"])

            if not force:
                row = conn.execute(
                    "SELECT content_hash FROM chunks WHERE id = ?", (chunk_id,)
                ).fetchone()
                if row and row[0] == chunk["hash"]:
                    continue  # unchanged

            to_embed.append((i, chunk))

        if not to_embed:
            log.info("Memory index: all %d chunks up to date", len(all_chunks))
            return 0

        # Batch embed
        texts = [chunk["content"] for _, chunk in to_embed]
        log.info("Embedding %d chunks (of %d total)...", len(texts), len(all_chunks))

        # Embed in batches of 50 with exponential backoff
        batch_size = 50
        embeddings = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            batch_embs = _embed_texts(batch)
            embeddings.extend(batch_embs)
            if start + batch_size < len(texts):
                time.sleep(0.5)  # Rate limit courtesy

        # Upsert
        now = datetime.now().isoformat()
        for (_, chunk), emb in zip(to_embed, embeddings):
            blob = _vec_to_blob(emb) if emb else None
            conn.execute(
                """INSERT OR REPLACE INTO chunks
                   (id, source, source_path, content, embedding, updated_at, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (chunk["id"], chunk["source"], chunk["source_path"],
                 chunk["content"], blob, now, chunk["hash"]),
            )

        # Clean up stale chunks (from deleted files)
        current_ids = {c["id"] for c in all_chunks}
        existing_ids = {row[0] for row in conn.execute("SELECT id FROM chunks").fetchall()}
        stale = existing_ids - current_ids
        if stale:
            conn.executemany("DELETE FROM chunks WHERE id = ?", [(cid,) for cid in stale])
            log.info("Removed %d stale chunks", len(stale))

        conn.commit()
        log.info("Memory index rebuilt: %d chunks embedded", len(to_embed))
        return len(to_embed)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _keyword_score(query: str, text: str) -> float:
    """Simple BM25-ish keyword matching score."""
    query_terms = set(re.findall(r"\w+", query.lower()))
    text_lower = text.lower()
    if not query_terms:
        return 0.0
    matches = sum(1 for t in query_terms if t in text_lower)
    return matches / len(query_terms)


def _temporal_decay(updated_at: str) -> float:
    """Exponential decay based on age. Returns multiplier 0..1."""
    try:
        dt = datetime.fromisoformat(updated_at)
        age_days = (datetime.now() - dt).total_seconds() / 86400
        return math.exp(-0.693 * age_days / DECAY_HALF_LIFE_DAYS)
    except (ValueError, TypeError):
        return 0.5


def search(query: str, top_k: int = 5,
           source_filter: str | None = None,
           include_decay: bool = True) -> list[dict]:
    """Hybrid search: vector similarity (70%) + keyword (30%).

    Args:
        query: Search query text.
        top_k: Number of results to return.
        source_filter: Optional filter by source type (e.g. "journal", "skill").
        include_decay: Apply temporal decay to scores.

    Returns:
        List of dicts with keys: content, source, source_path, score.
    """
    conn = _get_db()
    try:
        # Get query embedding
        query_emb = _embed_single(query)

        # Fetch all chunks (with optional source filter)
        if source_filter:
            rows = conn.execute(
                "SELECT id, source, source_path, content, embedding, updated_at "
                "FROM chunks WHERE source = ?",
                (source_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, source, source_path, content, embedding, updated_at "
                "FROM chunks"
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    results = []
    for row in rows:
        chunk_id, source, source_path, content, emb_blob, updated_at = row

        # Vector score
        if query_emb and emb_blob:
            vec = _blob_to_vec(emb_blob)
            vec_score = _cosine_sim(query_emb, vec)
        else:
            vec_score = 0.0

        # Keyword score
        kw_score = _keyword_score(query, content)

        # Combined: 70% vector + 30% keyword
        combined = 0.7 * vec_score + 0.3 * kw_score

        # Temporal decay
        if include_decay:
            combined *= _temporal_decay(updated_at)

        results.append({
            "content": content,
            "source": source,
            "source_path": source_path,
            "score": combined,
        })

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def search_formatted(query: str, top_k: int = 5,
                     max_chars: int = 3000) -> str:
    """Search and return formatted results for prompt injection.

    Returns a string like:
    ---
    [journal] 2026-03-08: ...content...
    ---
    [worldview] ...content...
    """
    results = search(query, top_k=top_k)
    if not results:
        return ""

    parts = []
    total = 0
    for r in results:
        if total > max_chars:
            break
        source_label = r["source"]
        # Extract date from path if available
        path = Path(r["source_path"])
        if path.stem[:4].isdigit():
            source_label += f" ({path.stem[:10]})"
        snippet = r["content"][:800]
        parts.append(f"[{source_label}] {snippet}")
        total += len(snippet)

    return "\n---\n".join(parts)


# ---------------------------------------------------------------------------
# Index stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """Get index statistics."""
    conn = _get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        by_source = dict(conn.execute(
            "SELECT source, COUNT(*) FROM chunks GROUP BY source"
        ).fetchall())
        has_embedding = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "total_chunks": total,
        "embedded_chunks": has_embedding,
        "by_source": by_source,
        "db_path": str(DB_PATH),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "rebuild":
        force = "--force" in sys.argv
        n = rebuild_index(force=force)
        print(f"Rebuilt: {n} chunks embedded")
        stats = get_stats()
        print(f"Total: {stats['total_chunks']} chunks, {stats['embedded_chunks']} with embeddings")
        print(f"By source: {stats['by_source']}")
    elif len(sys.argv) > 1 and sys.argv[1] == "search":
        query = " ".join(sys.argv[2:])
        results = search(query, top_k=5)
        for i, r in enumerate(results):
            print(f"\n--- Result {i+1} (score: {r['score']:.3f}, source: {r['source']}) ---")
            print(r["content"][:300])
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats = get_stats()
        print(json.dumps(stats, indent=2))
    else:
        print("Usage: python memory_index.py [rebuild [--force] | search <query> | stats]")
