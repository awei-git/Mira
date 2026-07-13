"""Experience Ledger storage.

The ledger is durable memory, not debugging logs. JSONL is the default backend
because it works locally and in tests; records are schema-validated at write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .delta import MemoryDelta, MemoryDeltaProposal
from .ledger_ids import new_id
from .schema import MemoryClass, to_jsonable, utc_now


def new_run_id(prefix: str = "run") -> str:
    return new_id(prefix)


@dataclass(frozen=True)
class ExperienceRecord:
    """A durable record of experience."""

    pipeline: str
    trigger: str
    intent: str
    outcome: str
    delta: MemoryDelta
    causal_links: list[str]
    confidence: float
    memory_class: MemoryClass
    artifacts: list[str] = field(default_factory=list)
    eval_refs: list[str] = field(default_factory=list)
    side_effect_refs: list[str] = field(default_factory=list)
    memory_commit_id: str | None = None
    id: str = field(default_factory=lambda: new_run_id("exp"))
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.delta.pipeline != self.pipeline:
            raise ValueError("ExperienceRecord.delta.pipeline must match pipeline")
        if self.delta.run_id != self.id:
            raise ValueError("MemoryDelta.run_id must match ExperienceRecord.id")
        if self.delta.memory_class != self.memory_class:
            raise ValueError("MemoryDelta.memory_class must match ExperienceRecord.memory_class")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> dict:
        data = to_jsonable(self)
        data["run_id"] = self.id
        data["actual_outcome"] = self.outcome
        data["memory_delta_proposal"] = self.delta.to_dict()
        data["memory_delta_proposal_id"] = self.delta.proposal_id
        return data

    @property
    def run_id(self) -> str:
        return self.id

    @property
    def actual_outcome(self) -> str:
        return self.outcome

    @property
    def memory_delta_proposal(self) -> MemoryDeltaProposal:
        return self.delta

    @property
    def memory_delta_proposal_id(self) -> str:
        return self.delta.proposal_id

    @classmethod
    def from_dict(cls, data: dict) -> "ExperienceRecord":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        delta_body = data.get("memory_delta_proposal") or data.get("delta")
        return cls(
            id=data.get("id") or data.get("run_id"),
            pipeline=data["pipeline"],
            trigger=data["trigger"],
            intent=data["intent"],
            outcome=data.get("actual_outcome") or data["outcome"],
            delta=MemoryDelta.from_dict(delta_body),
            causal_links=list(data.get("causal_links", [])),
            confidence=float(data["confidence"]),
            timestamp=timestamp,
            memory_class=data["memory_class"],
            artifacts=list(data.get("artifacts", [])),
            eval_refs=list(data.get("eval_refs", [])),
            side_effect_refs=list(data.get("side_effect_refs", [])),
            memory_commit_id=data.get("memory_commit_id"),
        )


class ExperienceLedger:
    """Append-only JSONL experience ledger."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: ExperienceRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def list(self, pipeline: str | None = None, limit: int | None = None) -> list[ExperienceRecord]:
        if not self.path.exists():
            return []
        rows: list[ExperienceRecord] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = ExperienceRecord.from_dict(json.loads(line))
                if pipeline is None or record.pipeline == pipeline:
                    rows.append(record)
        rows.sort(key=lambda r: r.timestamp)
        if limit is not None:
            return rows[-limit:]
        return rows

    def recent_for_pipeline(self, pipeline: str, limit: int = 5) -> list[ExperienceRecord]:
        return self.list(pipeline=pipeline, limit=limit)

    def get(self, record_id: str) -> ExperienceRecord | None:
        for record in self.list():
            if record.id == record_id:
                return record
        return None
