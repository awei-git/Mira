"""Unified memory record schema for Mira.

All memory subsystems (memory_store, thread_manager, soul_manager) should
converge on this schema for structured, governable memory records.

Memory types:
    fact         External facts with source attribution
    belief       Mira's opinions/judgments (see also belief_store.py)
    episode      Record of a task, conversation, or event
    task_state   Workflow checkpoints and pending items
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


VALID_MEMORY_TYPES = {"fact", "belief", "episode", "task_state"}
VALID_CONFIDENCE = (0.0, 1.0)


@dataclass
class MemoryRecord:
    """Unified memory record with provenance and governance fields."""

    content: str
    memory_type: str  # fact | belief | episode | task_state
    source: str  # e.g., "explore_briefing", "user_conversation", "reflection"
    source_id: str = ""  # unique ID of source (task_id, thread_id, etc.)
    confidence: float = 0.8
    created_at: str = field(default_factory=_utc_now)
    last_verified_at: str | None = None
    ttl_days: int | None = None  # None = permanent
    tags: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    owner_scope: str = "global"  # "global", "thread:{id}", "agent:{name}"
    record_id: str = field(default_factory=_new_id)

    def __post_init__(self):
        if self.memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(f"Invalid memory_type: {self.memory_type}. " f"Must be one of {VALID_MEMORY_TYPES}")
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MemoryRecord:
        # Filter to only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    def is_fresh(self, max_age_days: int = 30) -> bool:
        """Check if this memory is still within acceptable age."""
        try:
            created = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - created).days
            return age <= max_age_days
        except (ValueError, TypeError):
            return True  # if we can't parse, assume fresh

    def should_decay(self) -> bool:
        """Check if this memory has exceeded its TTL."""
        if self.ttl_days is None:
            return False
        return not self.is_fresh(self.ttl_days)

    def mark_verified(self):
        """Update last_verified_at to now."""
        self.last_verified_at = _utc_now()


def filter_by_freshness(records: list[MemoryRecord], max_age_days: int = 90) -> list[MemoryRecord]:
    """Filter out stale records beyond max_age_days."""
    return [r for r in records if r.is_fresh(max_age_days)]


def filter_by_confidence(records: list[MemoryRecord], min_confidence: float = 0.3) -> list[MemoryRecord]:
    """Filter out low-confidence records."""
    return [r for r in records if r.confidence >= min_confidence]


def detect_conflicts(records: list[MemoryRecord]) -> list[tuple[MemoryRecord, MemoryRecord]]:
    """Find pairs of records that have declared conflicts."""
    id_map = {r.record_id: r for r in records}
    conflicts = []
    for r in records:
        for cid in r.conflicts_with:
            if cid in id_map and id_map[cid].record_id != r.record_id:
                pair = tuple(sorted([r, id_map[cid]], key=lambda x: x.record_id))
                if pair not in conflicts:
                    conflicts.append(pair)
    return conflicts


def deduplicate(records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Simple dedup: remove records with identical or near-identical content.

    Uses normalized content comparison. For semantic dedup,
    use the vector store's embedding similarity instead.
    """
    seen_contents: set[str] = set()
    unique = []
    for r in records:
        normalized = r.content.strip().lower()[:200]
        if normalized not in seen_contents:
            unique.append(r)
            seen_contents.add(normalized)
    return unique
