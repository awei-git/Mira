"""Memory consolidation and immediate delta application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .commit import MemoryCommit, SecurityGateway
from .delta import MemoryAction, MemoryDelta, MemoryDeltaProposal
from .schema import FailureSignature, Hypothesis, MemoryKernel, Scar


@dataclass
class ConsolidationResult:
    applied: list[str]
    escalations: list[str]


class MemoryConsolidator:
    """Applies gateway-approved commits to the durable kernel."""

    def apply_delta(self, kernel: MemoryKernel, delta: MemoryDelta) -> ConsolidationResult:
        commit = SecurityGateway().validate(delta)
        return self.apply_commit(kernel, delta, commit)

    def apply_commit(
        self,
        kernel: MemoryKernel,
        proposal: MemoryDeltaProposal,
        commit: MemoryCommit,
    ) -> ConsolidationResult:
        applied: list[str] = []
        escalations: list[str] = [
            f.reason for f in commit.findings if f.decision in {"quarantine", "require_human", "reject"}
        ]
        for action in commit.committed_actions:
            label = self._apply_action(kernel, proposal, action, escalations)
            applied.append(label)
        if commit.status in {"applied", "noop"}:
            kernel.outcome_history.outcome_ids.append(proposal.run_id)
        return ConsolidationResult(applied=applied, escalations=escalations)

    def apply_decay(self, kernel: MemoryKernel, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for trace in kernel.skill_traces:
            if trace.last_used is None:
                trace.decay_score *= 0.98
                continue
            age_days = max((now - trace.last_used).total_seconds() / 86400, 0.0)
            trace.decay_score = max(0.0, trace.decay_score * math.exp(-0.693 * age_days / 30))

    def _apply_action(
        self,
        kernel: MemoryKernel,
        delta: MemoryDelta,
        action: MemoryAction,
        escalations: list[str],
    ) -> str:
        if action.type == "create_scar":
            existing = next((s for s in kernel.scars if s.scar_id == action.target), None)
            if existing:
                existing.reinforcement_count += 1
                return f"reinforced {action.target}"
            kernel.scars.append(
                Scar(
                    scar_id=action.target,
                    incident=delta.what_failed or delta.what_happened,
                    root_cause=action.detail,
                    behavioral_change=action.detail,
                )
            )
            return f"created {action.target}"
        if action.type == "update_failure_signature":
            pattern = action.target.removeprefix("failure:")
            existing_signature = next((s for s in kernel.failure_signatures if s.pattern == pattern), None)
            if existing_signature:
                existing_signature.occurrences += 1
                existing_signature.failure_rate = _bounded_failure_rate(action, existing_signature.failure_rate)
                return f"updated {action.target}"
            kernel.failure_signatures.append(
                FailureSignature(
                    pattern=pattern,
                    detection_rule=action.detail,
                    occurrences=1,
                    failure_rate=_bounded_failure_rate(action, 1.0),
                )
            )
            return f"created {action.target}"
        if action.type == "update_skill_trace":
            trace = kernel.skill_trace(action.target.removeprefix("skill:"))
            trace.record_use(succeeded=delta.what_failed is None, outcome=action.detail, when=delta.timestamp)
            return f"updated {action.target}"
        if action.type == "form_hypothesis":
            hypothesis = kernel.hypothesis(action.target)
            if hypothesis is None:
                kernel.pending_hypotheses.append(_hypothesis_from_action(action, delta))
            else:
                _apply_hypothesis_metadata(hypothesis, action)
            return f"formed {action.target}"
        if action.type == "update_hypothesis":
            hypothesis = kernel.hypothesis(action.target)
            if hypothesis is None:
                hypothesis = _hypothesis_from_action(action, delta)
                kernel.pending_hypotheses.append(hypothesis)
            else:
                _apply_hypothesis_metadata(hypothesis, action)
            if delta.what_failed:
                hypothesis.evidence_against.append(action.detail)
            else:
                hypothesis.evidence_for.append(action.detail)
            return f"updated {action.target}"
        if action.type == "update_relationship":
            kernel.relationship_model.notes.append(action.detail)
            return f"updated {action.target}"
        if action.type == "escalate":
            escalations.append(f"{action.target}: {action.detail}")
            return f"escalated {action.target}"
        if action.type == "reinforce":
            for scar in kernel.scars:
                if scar.scar_id == action.target:
                    scar.reinforcement_count += 1
                    return f"reinforced {action.target}"
            return f"reinforced {action.target}"
        if action.type == "weaken":
            return f"weakened {action.target}"
        if action.type == "archive":
            return f"archived {action.target}"
        raise ValueError(f"Unsupported memory action: {action.type}")


def _bounded_failure_rate(action: MemoryAction, default: float) -> float:
    try:
        rate = float(action.metadata.get("failure_rate", default))
    except (TypeError, ValueError):
        rate = default
    return max(0.0, min(rate, 1.0))


def _hypothesis_from_action(action: MemoryAction, delta: MemoryDeltaProposal) -> Hypothesis:
    hypothesis = Hypothesis(hypothesis_id=action.target, claim=action.detail, test_pipeline=delta.pipeline)
    _apply_hypothesis_metadata(hypothesis, action)
    return hypothesis


def _apply_hypothesis_metadata(hypothesis: Hypothesis, action: MemoryAction) -> None:
    for field_name in ("baseline_window", "test_window", "current_metric", "rollback_plan"):
        if action.metadata.get(field_name):
            setattr(hypothesis, field_name, action.metadata[field_name])
    if action.metadata.get("min_n"):
        try:
            hypothesis.min_n = max(1, int(action.metadata["min_n"]))
        except (TypeError, ValueError):
            hypothesis.min_n = 1
