"""Memory consolidation and immediate delta application."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .delta import MemoryAction, MemoryDelta
from .schema import FailureSignature, Hypothesis, MemoryKernel, Scar


@dataclass
class ConsolidationResult:
    applied: list[str]
    escalations: list[str]


class MemoryConsolidator:
    """Applies per-run deltas to the durable kernel."""

    def apply_delta(self, kernel: MemoryKernel, delta: MemoryDelta) -> ConsolidationResult:
        applied: list[str] = []
        escalations: list[str] = []
        for action in delta.actions:
            label = self._apply_action(kernel, delta, action, escalations)
            applied.append(label)
        kernel.outcome_history.outcome_ids.append(delta.run_id)
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
        if action.type == "update_skill_trace":
            trace = kernel.skill_trace(action.target.removeprefix("skill:"))
            trace.record_use(succeeded=delta.what_failed is None, outcome=action.detail, when=delta.timestamp)
            return f"updated {action.target}"
        if action.type == "form_hypothesis":
            if kernel.hypothesis(action.target) is None:
                kernel.pending_hypotheses.append(
                    Hypothesis(hypothesis_id=action.target, claim=action.detail, test_pipeline=delta.pipeline)
                )
            return f"formed {action.target}"
        if action.type == "update_hypothesis":
            hypothesis = kernel.hypothesis(action.target)
            if hypothesis is None:
                hypothesis = Hypothesis(hypothesis_id=action.target, claim=action.detail, test_pipeline=delta.pipeline)
                kernel.pending_hypotheses.append(hypothesis)
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
