"""Auditable causal traces for memory use."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from .ledger_ids import new_id
from .schema import parse_dt, to_jsonable, utc_now

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
    run_id: str = ""
    pipeline: str = ""
    trace_ids: list[str] = field(default_factory=list)
    decision_ids: list[str] = field(default_factory=list)
    effect_ids: list[str] = field(default_factory=list)
    ablation_ref: str | None = None
    evidence_id: str = field(default_factory=lambda: new_id("causal"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CausalEvidence":
        return cls(
            memory_id=data["memory_id"],
            level=data["level"],
            reason=data["reason"],
            run_id=data.get("run_id", ""),
            pipeline=data.get("pipeline", ""),
            trace_ids=list(data.get("trace_ids", [])),
            decision_ids=list(data.get("decision_ids", [])),
            effect_ids=list(data.get("effect_ids", [])),
            ablation_ref=data.get("ablation_ref"),
            evidence_id=data.get("evidence_id") or new_id("causal"),
            timestamp=parse_dt(data.get("timestamp")),
        )


class CausalEvidenceLog:
    """Append-only log of causal evidence records referenced by experiences."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, evidence: CausalEvidence) -> CausalEvidence:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(evidence.to_dict(), sort_keys=True) + "\n")
        return evidence

    def list(self, run_id: str | None = None, limit: int | None = None) -> list[CausalEvidence]:
        if not self.path.exists():
            return []
        rows: list[CausalEvidence] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                evidence = CausalEvidence.from_dict(json.loads(line))
                if run_id is None or evidence.run_id == run_id:
                    rows.append(evidence)
        rows.sort(key=lambda row: row.timestamp)
        if limit is not None:
            return rows[-limit:]
        return rows

    def get(self, evidence_id: str) -> CausalEvidence | None:
        for evidence in self.list():
            if evidence.evidence_id == evidence_id:
                return evidence
        return None


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
        return CausalEvidence(memory_id=memory_id, level="L0", reason="memory was not retrieved")
    if not any(trace.included for trace in traces):
        return CausalEvidence(
            memory_id=memory_id,
            level="L1",
            reason="memory was retrieved but excluded",
            trace_ids=[t.trace_id for t in traces],
        )

    trace_ids = [trace.trace_id for trace in traces if trace.included]
    linked_decisions = [decision for decision in decisions if set(decision.memory_trace_ids) & set(trace_ids)]
    if not linked_decisions:
        return CausalEvidence(
            memory_id=memory_id,
            level="L2",
            reason="memory was included but no decision cites it",
            trace_ids=trace_ids,
        )

    decision_ids = [decision.decision_id for decision in linked_decisions]
    linked_effects = [
        effect
        for effect in effects
        if effect.memory_id == memory_id and effect.decision_id in decision_ids and effect.counterfactual
    ]
    if not linked_effects:
        return CausalEvidence(
            memory_id=memory_id,
            level="L2",
            reason="memory influenced a decision but produced no auditable behavioral effect",
            trace_ids=trace_ids,
            decision_ids=decision_ids,
        )
    if ablation_ref:
        return CausalEvidence(
            memory_id=memory_id,
            level="L4",
            reason="behavioral effect survived ablation check",
            trace_ids=trace_ids,
            decision_ids=decision_ids,
            effect_ids=[effect.effect_id for effect in linked_effects],
            ablation_ref=ablation_ref,
        )
    return CausalEvidence(
        memory_id=memory_id,
        level="L3",
        reason="memory has trace, decision, and behavioral effect",
        trace_ids=trace_ids,
        decision_ids=decision_ids,
        effect_ids=[effect.effect_id for effect in linked_effects],
    )
