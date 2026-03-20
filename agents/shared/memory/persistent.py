"""Persistent memory backed by PostgreSQL + pgvector.

Three-tier memory architecture:
  Tier 1: Working Memory  → in-process dict (ephemeral, instant)
  Tier 2: Episodic Memory → PostgreSQL + pgvector (persistent, semantic search)
  Tier 3: Core Identity   → Filesystem soul files (hand-curated)

Embeddings come from local Ollama (nomic-embed-text) — nothing leaves localhost.
"""
import json
import logging
import time
from typing import Optional

log = logging.getLogger("mira.memory")


class PersistentMemory:
    """Two-tier memory + in-process working context. All localhost."""

    def __init__(self):
        self._working: dict = {}

    # --- Tier 1: Working Memory (in-process dict, ephemeral) ---

    def set_working(self, user: str, agent: str, context: dict):
        """Store active conversation context. Lost on process restart."""
        self._working[f"{user}:{agent}"] = {
            "context": context,
            "ts": time.time(),
        }

    def get_working(self, user: str, agent: str, max_age: int = 1800) -> Optional[dict]:
        """Get current context. Returns None if stale or missing."""
        entry = self._working.get(f"{user}:{agent}")
        if entry and (time.time() - entry["ts"]) < max_age:
            return entry["context"]
        return None

    # --- Tier 2: Episodic Memory (PostgreSQL + pgvector) ---

    def remember(self, user_id: int, agent_type: str, content: str,
                 memory_type: str = "episodic", importance: float = 0.5):
        """Store a memory with vector embedding for later semantic recall."""
        from sub_agent import ollama_embed
        from database import get_conn

        embedding = ollama_embed(content)
        if not embedding:
            log.warning("Failed to embed memory, storing without vector")

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_memory "
                    "(user_id, agent_type, memory_type, content, embedding, importance) "
                    "VALUES (%s, %s, %s, %s, %s::vector, %s)",
                    (user_id, agent_type, memory_type, content,
                     json.dumps(embedding) if embedding else None,
                     importance),
                )
        log.info("Stored memory: user=%s agent=%s type=%s importance=%.1f",
                 user_id, agent_type, memory_type, importance)

    def recall(self, user_id: int, agent_type: str, query: str,
               limit: int = 5) -> list[dict]:
        """Recall relevant memories using semantic similarity."""
        from sub_agent import ollama_embed
        from database import get_conn

        query_emb = ollama_embed(query)
        if not query_emb:
            log.warning("Failed to embed query, falling back to recency")
            return self._recall_recent(user_id, agent_type, limit)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, importance, memory_type, created_at "
                    "FROM agent_memory "
                    "WHERE user_id = %s AND agent_type = %s AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> %s::vector "
                    "LIMIT %s",
                    (user_id, agent_type, json.dumps(query_emb), limit),
                )
                rows = cur.fetchall()

        return [
            {"content": r[0], "importance": r[1], "type": r[2], "created_at": str(r[3])}
            for r in rows
        ]

    def _recall_recent(self, user_id: int, agent_type: str, limit: int) -> list[dict]:
        """Fallback: recall most recent memories without semantic search."""
        from database import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, importance, memory_type, created_at "
                    "FROM agent_memory "
                    "WHERE user_id = %s AND agent_type = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (user_id, agent_type, limit),
                )
                rows = cur.fetchall()

        return [
            {"content": r[0], "importance": r[1], "type": r[2], "created_at": str(r[3])}
            for r in rows
        ]

    # --- Combined: Build prompt context from all tiers ---

    def build_context(self, user: str, user_id: int,
                      agent: str, current_query: str) -> dict:
        """Build combined context from working memory + episodic recall."""
        context = {}

        working = self.get_working(user, agent)
        if working:
            context["working"] = working

        relevant = self.recall(user_id, agent, current_query, limit=5)
        if relevant:
            context["memories"] = [r["content"] for r in relevant]

        return context

    def format_context(self, context: dict) -> str:
        """Format memory context for injection into LLM prompt."""
        parts = []
        if context.get("working"):
            parts.append(f"## Active Context\n{json.dumps(context['working'], ensure_ascii=False)}")
        if context.get("memories"):
            parts.append("## Relevant Memories")
            for m in context["memories"]:
                parts.append(f"- {m}")
        return "\n\n".join(parts)


# Singleton instance
_instance: Optional[PersistentMemory] = None


def get_memory() -> PersistentMemory:
    """Get the singleton PersistentMemory instance."""
    global _instance
    if _instance is None:
        _instance = PersistentMemory()
    return _instance
