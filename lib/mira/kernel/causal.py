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
CausalEvidenceLevel = Literal["L0", "L1", "L2", "L3", "L4"]


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


@dataclass(frozen=True)
class CausalEvidence:
    memory_id: str
    level: CausalEvidenceLevel
    reason: str
    trace_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    effect_ids: list[str] = field(default_factory=list)
    ablation_ref: str | None = None

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


def classify_causal_evidence(
    memory_id: str,
    memory_traces: list[MemoryUseTrace],
    decisions: list[DecisionRecord],
    effects: list[BehavioralEffect],
    *,
    ablation_ref: str | None = None,
) -> CausalEvidence:
    """Classify causal evidence from L0 to L4.

    L0 means no observed retrieval. L4 requires the normal trace/decision/effect
    chain plus an explicit ablation reference.
    """

    traces = [trace for trace in memory_traces if trace.memory_id == memory_id]
    if not any(trace.retrieved for trace in traces):
        return CausalEvidence(memory_id, "L0", "memory was not retrieved")
    if not any(trace.included for trace in traces):
        return CausalEvidence(memory_id, "L1", "memory was retrieved but excluded", [t.trace_id for t in traces])

    trace_ids = [trace.trace_id for trace in traces if trace.included]
    linked_decisions = [decision for decision in decisions if set(decision.memory_trace_ids) & set(trace_ids)]
    if not linked_decisions:
        return CausalEvidence(memory_id, "L2", "memory was included but no decision cites it", trace_ids)

    decision_ids = [decision.decision_id for decision in linked_decisions]
    linked_effects = [
        effect
        for effect in effects
        if effect.memory_id == memory_id and effect.decision_id in decision_ids and effect.counterfactual
    ]
    if not linked_effects:
        return CausalEvidence(
            memory_id,
            "L2",
            "memory influenced a decision but produced no auditable behavioral effect",
            trace_ids,
            decision_ids,
        )
    if ablation_ref:
        return CausalEvidence(
            memory_id,
            "L4",
            "behavioral effect survived ablation check",
            trace_ids,
            decision_ids,
            [effect.effect_id for effect in linked_effects],
            ablation_ref,
        )
    return CausalEvidence(
        memory_id,
        "L3",
        "memory has trace, decision, and behavioral effect",
        trace_ids,
        decision_ids,
        [effect.effect_id for effect in linked_effects],
    )
