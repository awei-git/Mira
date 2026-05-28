"""Auditable causal traces for memory use."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
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
ImportantBehaviorType = Literal[
    "publish_public",
    "send_message",
    "delete_or_overwrite",
    "code_change",
    "policy_change",
    "eval_threshold_change",
    "memory_commit",
    "scar_creation",
    "workflow_route_change",
    "approval_bypass",
    "high_cost_action",
    "user_facing_recommendation",
]


@dataclass(frozen=True)
class AblationCheck:
    memory_id: str
    run_id: str
    pipeline: str
    normal_decision: str
    counterfactual_decision: str
    ablation_id: str = field(default_factory=lambda: new_id("ablation"))
    timestamp: datetime = field(default_factory=utc_now)

    @property
    def changed_outcome(self) -> bool:
        return self.normal_decision.strip() != self.counterfactual_decision.strip()

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AblationCheck":
        return cls(
            memory_id=data["memory_id"],
            run_id=data["run_id"],
            pipeline=data["pipeline"],
            normal_decision=data["normal_decision"],
            counterfactual_decision=data["counterfactual_decision"],
            ablation_id=data.get("ablation_id") or new_id("ablation"),
            timestamp=parse_dt(data.get("timestamp")),
        )


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
class CausalTrace:
    action_id: str
    run_id: str
    behavior_type: ImportantBehaviorType
    trigger_ref: str
    intent_ref: str
    snapshot_ref: str
    memory_refs: list[str]
    decision_ref: str
    policy_refs: list[str]
    eval_refs: list[str]
    approval_ref: str | None
    effect_ref: str | None
    outcome_ref: str | None
    memory_delta_ref: str | None
    memory_commit_ref: str | None
    replay_bundle_ref: str
    completeness_score: float
    trace_id: str = field(default_factory=lambda: new_id("causaltrace"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CausalTrace":
        return cls(
            action_id=data["action_id"],
            run_id=data["run_id"],
            behavior_type=data["behavior_type"],
            trigger_ref=data["trigger_ref"],
            intent_ref=data["intent_ref"],
            snapshot_ref=data["snapshot_ref"],
            memory_refs=list(data.get("memory_refs", [])),
            decision_ref=data["decision_ref"],
            policy_refs=list(data.get("policy_refs", [])),
            eval_refs=list(data.get("eval_refs", [])),
            approval_ref=data.get("approval_ref"),
            effect_ref=data.get("effect_ref"),
            outcome_ref=data.get("outcome_ref"),
            memory_delta_ref=data.get("memory_delta_ref"),
            memory_commit_ref=data.get("memory_commit_ref"),
            replay_bundle_ref=data["replay_bundle_ref"],
            completeness_score=float(data["completeness_score"]),
            trace_id=data.get("trace_id") or new_id("causaltrace"),
            timestamp=parse_dt(data.get("timestamp")),
        )


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


IMPORTANT_BEHAVIOR_RE = re.compile(
    r"(publish|post|send|message|delete|overwrite|code|policy|eval|threshold|memory|commit|scar|"
    r"workflow|route|approval|recommendation|promote|deploy|production|health|market|alert|compact|archive)",
    re.IGNORECASE,
)


def build_causal_traces(records: list, effects: list | None = None) -> list[CausalTrace]:
    """Build V3.1 CausalTrace records for important behaviors from durable logs."""

    latest_effects: dict[str, object] = {}
    for effect in effects or []:
        key = str(getattr(effect, "idempotency_key", "") or getattr(effect, "effect_id", ""))
        latest_effects[key] = effect
    effects_by_run: dict[str, list] = {}
    for effect in latest_effects.values():
        effects_by_run.setdefault(str(getattr(effect, "run_id", "")), []).append(effect)

    traces: list[CausalTrace] = []
    for record in records:
        run_effects = effects_by_run.get(str(record.id), [])
        effect_behaviors = [(effect, _effect_behavior_type(effect)) for effect in run_effects]
        effect_behaviors = [(effect, behavior) for effect, behavior in effect_behaviors if behavior]
        if effect_behaviors:
            traces.extend(_trace_for_effect(record, effect, behavior) for effect, behavior in effect_behaviors)
            continue
        for behavior in _record_behavior_types(record):
            traces.append(_trace_for_record(record, behavior))
    return traces


def _record_behavior_types(record) -> list[ImportantBehaviorType]:
    types: list[ImportantBehaviorType] = []
    action_text = " ".join(
        [
            str(record.pipeline),
            str(record.outcome),
            str(record.intent),
            *[str(ref) for ref in record.eval_refs],
            *[str(ref) for ref in record.side_effect_refs],
            *[
                " ".join([str(action.type), str(action.target), str(action.detail)])
                for action in getattr(record.delta, "actions", [])
            ],
        ]
    )
    if getattr(record, "memory_commit_id", None):
        types.append("memory_commit")
    if "scar:" in action_text:
        types.append("scar_creation")
    if re.search(r"\b(route|workflow_route|router)\b", action_text, re.IGNORECASE):
        types.append("workflow_route_change")
    if re.search(r"\b(policy)\b", action_text, re.IGNORECASE):
        types.append("policy_change")
    if re.search(r"\b(eval|threshold)\b", action_text, re.IGNORECASE):
        types.append("eval_threshold_change")
    if re.search(r"\b(recommendation|recommend)\b", action_text, re.IGNORECASE):
        types.append("user_facing_recommendation")
    if not types and IMPORTANT_BEHAVIOR_RE.search(action_text):
        types.append("memory_commit")
    return list(dict.fromkeys(types))


def _effect_behavior_type(effect) -> ImportantBehaviorType | None:
    text = " ".join(
        str(getattr(effect, field_name, "") or "")
        for field_name in ("action", "action_type", "step_id", "idempotency_key", "target")
    )
    if re.search(r"(publish|post|rss|substack|upload)", text, re.IGNORECASE):
        return "publish_public"
    if re.search(r"(send|message|email|alert|market|health)", text, re.IGNORECASE):
        return "send_message"
    if re.search(r"(delete|overwrite|compact|archive)", text, re.IGNORECASE):
        return "delete_or_overwrite"
    if re.search(r"(code|promote|deploy|production|rollback)", text, re.IGNORECASE):
        return "code_change"
    if re.search(r"(approval.*bypass|bypass.*approval)", text, re.IGNORECASE):
        return "approval_bypass"
    return None


def _trace_for_effect(record, effect, behavior: ImportantBehaviorType) -> CausalTrace:
    return _build_trace(
        record,
        behavior,
        action_id=str(getattr(effect, "effect_id", "") or getattr(effect, "idempotency_key", "")),
        effect_ref=str(getattr(effect, "effect_id", "") or ""),
        approval_ref=getattr(effect, "approval_token_id", None),
    )


def _trace_for_record(record, behavior: ImportantBehaviorType) -> CausalTrace:
    return _build_trace(record, behavior, action_id=f"{record.id}:{behavior}", effect_ref=None, approval_ref=None)


def _build_trace(
    record,
    behavior: ImportantBehaviorType,
    *,
    action_id: str,
    effect_ref: str | None,
    approval_ref: str | None,
) -> CausalTrace:
    trace = CausalTrace(
        action_id=action_id,
        run_id=str(record.id),
        behavior_type=behavior,
        trigger_ref=str(record.trigger),
        intent_ref=str(record.intent),
        snapshot_ref=f"snapshot:{record.id}",
        memory_refs=[str(ref) for ref in record.causal_links],
        decision_ref=f"record:{record.id}:decision",
        policy_refs=_policy_refs_for_behavior(behavior),
        eval_refs=[str(ref) for ref in record.eval_refs],
        approval_ref=approval_ref,
        effect_ref=effect_ref,
        outcome_ref=f"record:{record.id}:outcome:{record.outcome}",
        memory_delta_ref=str(record.memory_delta_proposal_id),
        memory_commit_ref=record.memory_commit_id,
        replay_bundle_ref=_replay_bundle_ref(record),
        completeness_score=0.0,
    )
    score = _trace_completeness_score(trace)
    return replace(trace, completeness_score=score)


def _policy_refs_for_behavior(behavior: ImportantBehaviorType) -> list[str]:
    refs = ["policy:v3_causal_trace"]
    if behavior in {"publish_public", "send_message", "delete_or_overwrite", "code_change", "approval_bypass"}:
        refs.append("policy:action_risk_approval")
    return refs


def _replay_bundle_ref(record) -> str:
    if getattr(record, "artifacts", []):
        return str(record.artifacts[0])
    if getattr(record, "memory_commit_id", None):
        return f"commit:{record.memory_commit_id}"
    return f"ledger:{record.id}"


def _trace_completeness_score(trace: CausalTrace) -> float:
    required: list[object] = [
        trace.action_id,
        trace.run_id,
        trace.behavior_type,
        trace.trigger_ref,
        trace.intent_ref,
        trace.snapshot_ref,
        trace.memory_refs,
        trace.decision_ref,
        trace.policy_refs,
        trace.outcome_ref,
        trace.memory_delta_ref,
        trace.replay_bundle_ref,
    ]
    if trace.behavior_type in {"publish_public", "send_message", "delete_or_overwrite", "code_change"}:
        required.append(trace.effect_ref)
    if trace.behavior_type in {"memory_commit", "scar_creation", "policy_change", "eval_threshold_change"}:
        required.append(trace.memory_commit_ref)
    present = sum(1 for item in required if _trace_field_present(item))
    return round(present / len(required), 4)


def _trace_field_present(item: object) -> bool:
    if isinstance(item, list):
        return True
    return bool(item)


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


def confirm_ablation_evidence(
    *,
    memory_id: str,
    run_id: str,
    pipeline: str,
    normal_decision: str,
    counterfactual_decision: str,
    effect_ids: list[str] | None = None,
) -> CausalEvidence:
    """Promote a causal claim to L4 only when removing memory changes the decision."""

    check = AblationCheck(
        memory_id=memory_id,
        run_id=run_id,
        pipeline=pipeline,
        normal_decision=normal_decision,
        counterfactual_decision=counterfactual_decision,
    )
    if not check.changed_outcome:
        return CausalEvidence(
            memory_id=memory_id,
            level="L3",
            reason="memory effect did not survive ablation; counterfactual decision matched normal decision",
            run_id=run_id,
            pipeline=pipeline,
            effect_ids=effect_ids or [],
        )
    return CausalEvidence(
        memory_id=memory_id,
        level="L4",
        reason=(
            "memory effect survived ablation check: "
            f"normal='{normal_decision}' counterfactual_without_memory='{counterfactual_decision}'"
        ),
        run_id=run_id,
        pipeline=pipeline,
        effect_ids=effect_ids or [],
        ablation_ref=check.ablation_id,
    )
