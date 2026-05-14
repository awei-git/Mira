"""Auditable causal traces for memory use."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .ledger_ids import new_id
from .schema import to_jsonable, utc_now

BehavioralEffectType = Literal[
    "changed_route",
    "changed_tool",
    "skipped_action",
    "escalated",
    "added_check",
    "changed_schedule",
]


@dataclass(frozen=True)
class MemoryUseTrace:
    memory_id: str
    run_id: str
    pipeline: str
    step: str
    retrieved: bool
    included: bool
    cited: bool
    trace_id: str = field(default_factory=lambda: new_id("memtrace"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class DecisionRecord:
    run_id: str
    pipeline: str
    step: str
    decision: str
    memory_trace_ids: list[str]
    decision_id: str = field(default_factory=lambda: new_id("decision"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class BehavioralEffect:
    memory_id: str
    decision_id: str
    effect_type: BehavioralEffectType
    counterfactual: str
    effect_id: str = field(default_factory=lambda: new_id("effect"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)


def derive_causal_links(
    memory_traces: list[MemoryUseTrace],
    decisions: list[DecisionRecord],
    effects: list[BehavioralEffect],
) -> list[str]:
    """Derive causal links only when retrieval, decision, and effect line up."""

    included_memory_ids = {t.memory_id for t in memory_traces if t.retrieved and t.included and t.cited}
    decision_ids = {d.decision_id for d in decisions}
    links = {
        effect.memory_id
        for effect in effects
        if effect.memory_id in included_memory_ids and effect.decision_id in decision_ids and effect.counterfactual
    }
    return sorted(links)
