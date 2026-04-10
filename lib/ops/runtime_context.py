"""Unified runtime context bundle for persona + thread + memory injection.

Primary paths should consume this instead of each handler inventing its own
long-context assembly. The goal is not maximal context volume, but a stable
contract with provenance and freshness signals.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from persona.persona_context import PersonaContext, get_persona_context

log = logging.getLogger("mira.runtime_context")

from config import MIRA_ROOT; _AGENTS_DIR = MIRA_ROOT / "agents"
_SUPER_DIR = _AGENTS_DIR / "super"
if str(_SUPER_DIR) not in sys.path:
    sys.path.insert(0, str(_SUPER_DIR))

from execution.context import (  # noqa: E402
    _load_recent_briefings,
    _load_recent_journals,
    load_thread_history,
    load_thread_memory,
)


@dataclass
class RecallEntry:
    """A memory recall record with runtime governance metadata."""

    content: str
    source_type: str
    source_id: str
    title: str
    score: float
    created_at: object | None
    freshness: str
    confidence: str
    provenance: str


@dataclass
class RuntimeContextBundle:
    """Shared context bundle injected into primary Mira paths."""

    persona: PersonaContext
    thread_history: str = ""
    thread_memory: str = ""
    memory_recall: list[RecallEntry] = field(default_factory=list)
    recent_journals: str = ""
    recent_briefings: str = ""

    def recall_block(self, max_chars: int = 1200) -> str:
        """Format memory recall as a prompt-injectable block."""
        if not self.memory_recall:
            return ""

        parts = []
        total = 0
        for entry in self.memory_recall:
            snippet = entry.content.strip().replace("\n", " ")
            prefix = f"- [{entry.confidence} confidence | {entry.freshness}] {entry.provenance}: "
            available = max_chars - total - len(prefix)
            if available <= 0 and parts:
                break
            truncated = snippet[: max(0, available)] if available > 0 else ""
            if available <= 0 and not parts:
                truncated = snippet[:120]
            line = (
                f"- [{entry.confidence} confidence | {entry.freshness}] "
                f"{entry.provenance}: {truncated}"
            )
            if total + len(line) > max_chars and parts:
                break
            parts.append(line)
            total += len(line) + 1

        if not parts:
            return ""
        return "## Relevant Memory Recall\n" + "\n".join(parts)


def build_runtime_context(
    query: str,
    *,
    user_id: str = "ang",
    thread_id: str = "",
    persona_domains: list[str] | None = None,
    include_journals: int = 0,
    include_briefings: int = 0,
    recall_top_k: int = 4,
) -> RuntimeContextBundle:
    """Build the canonical runtime context for a request."""
    bundle = RuntimeContextBundle(
        persona=get_persona_context(domains=persona_domains),
    )
    if thread_id:
        bundle.thread_history = load_thread_history(thread_id, user_id=user_id)
        bundle.thread_memory = load_thread_memory(thread_id, user_id=user_id)
    if include_journals:
        bundle.recent_journals = _load_recent_journals(include_journals)
    if include_briefings:
        bundle.recent_briefings = _load_recent_briefings(include_briefings)

    if query.strip():
        bundle.memory_recall = _recall_entries(query, user_id=user_id, top_k=recall_top_k)
    return bundle


def _recall_entries(query: str, *, user_id: str, top_k: int) -> list[RecallEntry]:
    """Best-effort governed memory recall for prompt injection."""
    try:
        from memory_store import get_store

        store = get_store()
        if not store:
            return []
        rows = store.recall(query, top_k=top_k, user_id=user_id)
    except Exception as exc:
        log.debug("Memory recall unavailable: %s", exc)
        return []

    results = []
    for row in rows:
        created_at = row.get("created_at")
        results.append(
            RecallEntry(
                content=str(row.get("content", "")),
                source_type=str(row.get("source_type", "")),
                source_id=str(row.get("source_id", "")),
                title=str(row.get("title", "")),
                score=float(row.get("score", 0.0) or 0.0),
                created_at=created_at,
                freshness=_freshness_label(created_at),
                confidence=_confidence_label(float(row.get("score", 0.0) or 0.0)),
                provenance=_provenance_label(row),
            )
        )
    return results


def _freshness_label(created_at) -> str:
    if not created_at:
        return "unknown"
    try:
        if hasattr(created_at, "tzinfo"):
            ts = created_at
        else:
            ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds() / 86400
    except Exception:
        return "unknown"

    if age_days <= 7:
        return "fresh"
    if age_days <= 30:
        return "warm"
    return "stale"


def _confidence_label(score: float) -> str:
    if score >= 0.6:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _provenance_label(row: dict) -> str:
    title = str(row.get("title", "")).strip()
    source_type = str(row.get("source_type", "")).strip() or "memory"
    source_id = str(row.get("source_id", "")).strip()
    if title:
        return f"{source_type}:{title[:80]}"
    if source_id:
        return f"{source_type}:{source_id[:80]}"
    return source_type
