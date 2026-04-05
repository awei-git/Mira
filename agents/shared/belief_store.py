"""Structured belief store for Mira's opinions and positions.

Complements soul/worldview.md (human-readable) with machine-readable
structured records that agents can query and update programmatically.

Usage:
    store = BeliefStore()
    beliefs = store.get_beliefs(domain="ai_systems")
    context = store.get_belief_context(["ai_systems", "security"])
"""
from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mira")

_SOUL_DIR = Path(__file__).resolve().parent / "soul"
_BELIEFS_FILE = _SOUL_DIR / "beliefs.json"

VALID_STANCES = {"strong", "moderate", "tentative", "exploring"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BeliefRecord:
    """A single structured belief/opinion held by Mira."""

    statement: str
    domain: str  # e.g., "ai_systems", "security", "cognition", "self"
    stance: str = "moderate"  # strong | moderate | tentative | exploring
    confidence: float = 0.7
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    source: str = ""  # where this belief originated
    created_at: str = field(default_factory=_utc_now)
    last_reconsidered_at: str | None = None
    updated_from: str | None = None  # previous version of this belief

    def __post_init__(self):
        if self.stance not in VALID_STANCES:
            self.stance = "moderate"
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> BeliefRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class BeliefStore:
    """Manages Mira's structured beliefs."""

    def __init__(self, path: Path | None = None):
        self._path = path or _BELIEFS_FILE
        self._beliefs: list[BeliefRecord] = []
        self.load()

    def load(self):
        """Load beliefs from JSON file."""
        if not self._path.exists():
            self._beliefs = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._beliefs = [BeliefRecord.from_dict(b) for b in data]
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load beliefs: %s", e)
            self._beliefs = []

    def save(self):
        """Save beliefs to JSON file with fcntl locking."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        lock = self._path.with_suffix(".lock")
        data = json.dumps([b.to_dict() for b in self._beliefs],
                          indent=2, ensure_ascii=False)
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            tmp.write_text(data, encoding="utf-8")
            tmp.rename(self._path)
            fcntl.flock(lf, fcntl.LOCK_UN)

    def get_beliefs(self, domain: str | None = None) -> list[BeliefRecord]:
        """Get beliefs, optionally filtered by domain."""
        if domain is None:
            return list(self._beliefs)
        return [b for b in self._beliefs if b.domain == domain]

    def get_belief_context(self, domains: list[str] | None = None,
                           max_beliefs: int = 10) -> str:
        """Format beliefs as a prompt-injectable context string."""
        if domains:
            beliefs = [b for b in self._beliefs if b.domain in domains]
        else:
            beliefs = list(self._beliefs)

        # Sort by confidence desc, take top N
        beliefs.sort(key=lambda b: b.confidence, reverse=True)
        beliefs = beliefs[:max_beliefs]

        if not beliefs:
            return ""

        lines = ["## Mira's Current Positions\n"]
        for b in beliefs:
            stance_marker = {"strong": "firmly", "moderate": "", "tentative": "tentatively", "exploring": "exploring"}.get(b.stance, "")
            prefix = f"({stance_marker}) " if stance_marker else ""
            lines.append(f"- {prefix}{b.statement}")
            if b.evidence_against:
                lines.append(f"  Counter: {b.evidence_against[0]}")
        return "\n".join(lines)

    def add_belief(self, record: BeliefRecord) -> bool:
        """Add a belief, checking for duplicates by statement similarity."""
        normalized = record.statement.strip().lower()[:100]
        for existing in self._beliefs:
            if existing.statement.strip().lower()[:100] == normalized:
                log.info("Duplicate belief, skipping: %s", record.statement[:50])
                return False
        self._beliefs.append(record)
        self.save()
        return True

    def update_belief(self, statement_prefix: str, *,
                      new_evidence: str | None = None,
                      new_stance: str | None = None,
                      new_confidence: float | None = None) -> bool:
        """Update an existing belief by matching statement prefix."""
        prefix = statement_prefix.strip().lower()[:50]
        for b in self._beliefs:
            if b.statement.strip().lower()[:50].startswith(prefix):
                b.updated_from = b.statement
                b.last_reconsidered_at = _utc_now()
                if new_evidence:
                    b.evidence_for.append(new_evidence)
                if new_stance and new_stance in VALID_STANCES:
                    b.stance = new_stance
                if new_confidence is not None:
                    b.confidence = max(0.0, min(1.0, new_confidence))
                self.save()
                log.info("Updated belief: %s", b.statement[:50])
                return True
        return False

    def domains(self) -> list[str]:
        """Return sorted list of unique domains."""
        return sorted(set(b.domain for b in self._beliefs))

    def __len__(self) -> int:
        return len(self._beliefs)
