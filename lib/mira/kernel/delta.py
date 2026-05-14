"""Mandatory Memory Delta contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .schema import MemoryClass, to_jsonable, utc_now

MemoryActionType = Literal[
    "reinforce",
    "weaken",
    "archive",
    "escalate",
    "create_scar",
    "update_skill_trace",
    "form_hypothesis",
    "update_hypothesis",
    "update_relationship",
]


@dataclass(frozen=True)
class MemoryAction:
    """A specific action on memory resulting from a pipeline run."""

    type: MemoryActionType
    target: str
    detail: str

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class MemoryDelta:
    """Structured experience produced by every pipeline run."""

    pipeline: str
    run_id: str
    memory_class: MemoryClass
    what_happened: str
    what_mattered: str
    what_changed: str
    actions: list[MemoryAction]
    what_failed: str | None = None
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        missing = [
            name
            for name in ("pipeline", "run_id", "what_happened", "what_mattered", "what_changed")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"MemoryDelta missing required fields: {', '.join(missing)}")
        if self.actions is None:
            raise ValueError("MemoryDelta.actions must be a list")

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryDelta":
        actions = [MemoryAction(**a) for a in data.get("actions", [])]
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            pipeline=data["pipeline"],
            run_id=data["run_id"],
            timestamp=timestamp,
            memory_class=data["memory_class"],
            what_happened=data["what_happened"],
            what_mattered=data["what_mattered"],
            what_changed=data["what_changed"],
            what_failed=data.get("what_failed"),
            actions=actions,
        )
