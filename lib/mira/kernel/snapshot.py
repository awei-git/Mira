"""Read-only memory snapshots for pipeline runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import ExperienceLedger, ExperienceRecord
from .schema import FailureSignature, Hypothesis, MemoryClass, MemoryKernel, Scar, SkillTrace


@dataclass(frozen=True)
class MemorySnapshot:
    pipeline: str
    memory_class: MemoryClass
    scars: tuple[Scar, ...] = ()
    skill_traces: tuple[SkillTrace, ...] = ()
    failure_signatures: tuple[FailureSignature, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    recent_experiences: tuple[ExperienceRecord, ...] = ()
    relationship_notes: tuple[str, ...] = ()
    causal_context: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()

    def causal_links(self) -> list[str]:
        return list(self.causal_context)


@dataclass
class SnapshotBuilder:
    ledger: ExperienceLedger | None = None
    recent_limit: int = 5

    def build(
        self,
        kernel: MemoryKernel,
        pipeline: str,
        memory_class: MemoryClass,
        involved_skills: list[str] | None = None,
        intent: str = "",
    ) -> MemorySnapshot:
        involved_skills = involved_skills or []
        relevant_traces = tuple(t for t in kernel.skill_traces if t.skill_name in involved_skills)
        relevant_hypotheses = tuple(
            h for h in kernel.pending_hypotheses if h.test_pipeline == pipeline and h.status == "testing"
        )
        recent = tuple(self.ledger.recent_for_pipeline(pipeline, self.recent_limit)) if self.ledger else ()
        hints = self._behavioral_hints(kernel=kernel, pipeline=pipeline, intent=intent, recent=recent)
        return MemorySnapshot(
            pipeline=pipeline,
            memory_class=memory_class,
            scars=tuple(kernel.scars),
            skill_traces=relevant_traces,
            failure_signatures=tuple(kernel.failure_signatures),
            hypotheses=relevant_hypotheses,
            recent_experiences=recent,
            relationship_notes=tuple(kernel.relationship_model.notes),
            causal_context=tuple(r.id for r in recent),
            hints=tuple(hints),
        )

    def _behavioral_hints(
        self,
        kernel: MemoryKernel,
        pipeline: str,
        intent: str,
        recent: tuple[ExperienceRecord, ...],
    ) -> list[str]:
        hints: list[str] = []
        for scar in kernel.scars:
            hints.append(f"Scar: {scar.behavioral_change}")
        for note in kernel.relationship_model.notes[-5:]:
            hints.append(f"WA preference: {note}")
        for record in recent:
            changed = record.delta.what_changed
            if changed:
                hints.append(f"Prior {pipeline} run changed behavior: {changed}")
        if intent and "concise" in " ".join(kernel.relationship_model.notes).lower():
            hints.append("Prefer concise output for WA.")
        return hints
