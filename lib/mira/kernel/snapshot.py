"""Read-only memory snapshots for pipeline runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .ledger import ExperienceLedger, ExperienceRecord
from .schema import FailureSignature, Hypothesis, MemoryClass, MemoryKernel, Scar, SkillTrace, to_jsonable


@dataclass(frozen=True)
class SnapshotItem:
    item_id: str
    text: str
    score: float
    score_breakdown: dict[str, float] = field(default_factory=dict)
    trust_tier: str = "observed"
    privacy_tier: str = "normal"
    included: bool = True
    exclusion_reason: str | None = None

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class SnapshotManifest:
    item_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    exclusion_reasons: dict[str, str] = field(default_factory=dict)
    item_scores: dict[str, float] = field(default_factory=dict)
    total_tokens: int = 0
    hash: str = ""

    @classmethod
    def build(
        cls,
        *,
        item_ids: list[str],
        excluded_ids: list[str] | None = None,
        exclusion_reasons: dict[str, str] | None = None,
        item_scores: dict[str, float] | None = None,
        total_tokens: int = 0,
    ) -> "SnapshotManifest":
        excluded_ids = excluded_ids or []
        exclusion_reasons = exclusion_reasons or {}
        item_scores = item_scores or {}
        body = {
            "item_ids": sorted(item_ids),
            "excluded_ids": sorted(excluded_ids),
            "exclusion_reasons": exclusion_reasons,
            "item_scores": item_scores,
            "total_tokens": total_tokens,
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        return cls(
            item_ids=tuple(body["item_ids"]),
            excluded_ids=tuple(body["excluded_ids"]),
            exclusion_reasons=exclusion_reasons,
            item_scores=item_scores,
            total_tokens=total_tokens,
            hash=digest,
        )

    def to_dict(self) -> dict:
        return to_jsonable(self)


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
    items: tuple[SnapshotItem, ...] = ()
    manifest: SnapshotManifest = field(default_factory=SnapshotManifest)

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
        items = self._items(kernel, relevant_traces, relevant_hypotheses, recent, memory_class)
        included_items = tuple(item for item in items if item.included)
        excluded_items = tuple(item for item in items if not item.included)
        hints = self._behavioral_hints(
            kernel=kernel,
            pipeline=pipeline,
            intent=intent,
            recent=recent,
            items=included_items,
        )
        item_ids = [item.item_id for item in included_items]
        manifest = SnapshotManifest.build(
            item_ids=item_ids,
            excluded_ids=[item.item_id for item in excluded_items],
            exclusion_reasons={item.item_id: item.exclusion_reason or "excluded" for item in excluded_items},
            item_scores={item.item_id: item.score for item in included_items},
            total_tokens=sum(len(hint.split()) for hint in hints),
        )
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
            items=included_items,
            manifest=manifest,
        )

    def _items(
        self,
        kernel: MemoryKernel,
        relevant_traces: tuple[SkillTrace, ...],
        relevant_hypotheses: tuple[Hypothesis, ...],
        recent: tuple[ExperienceRecord, ...],
        memory_class: MemoryClass,
    ) -> tuple[SnapshotItem, ...]:
        items: list[SnapshotItem] = []
        for scar in kernel.scars:
            items.append(self._item(scar.scar_id, f"Scar: {scar.behavioral_change}", 0.95, memory_class))
        for trace in relevant_traces:
            items.append(
                self._item(
                    f"skill:{trace.skill_name}",
                    f"Skill {trace.skill_name} success rate {trace.success_rate:.2f}: {trace.last_outcome}",
                    0.65 + min(trace.times_used, 10) * 0.02,
                    memory_class,
                )
            )
        for sig in kernel.failure_signatures:
            items.append(self._item(f"failure:{sig.pattern}", f"Failure pattern: {sig.pattern}", 0.80, memory_class))
        for hyp in relevant_hypotheses:
            items.append(self._item(hyp.hypothesis_id, f"Hypothesis: {hyp.claim}", 0.70, memory_class))
        for record in recent:
            items.append(
                self._item(record.id, f"Prior run changed behavior: {record.delta.what_changed}", 0.60, memory_class)
            )
        for index, note in enumerate(kernel.relationship_model.notes[-5:]):
            items.append(self._item(f"relationship:{index}", f"WA preference: {note}", 0.75, memory_class))
        return tuple(items)

    def _item(self, item_id: str, text: str, base_score: float, memory_class: MemoryClass) -> SnapshotItem:
        privacy_tier = "local_only" if self._is_private(text) else "normal"
        included = privacy_tier != "local_only" or memory_class == "bodily"
        score = round(min(1.0, base_score), 4)
        return SnapshotItem(
            item_id=item_id,
            text=text,
            score=score,
            score_breakdown={
                "relevance": round(base_score, 4),
                "trust": 0.8,
                "privacy": 0.0 if not included else 1.0,
                "token_budget": 1.0,
            },
            privacy_tier=privacy_tier,
            included=included,
            exclusion_reason=None if included else "local-only memory excluded from non-bodily snapshot",
        )

    def _behavioral_hints(
        self,
        kernel: MemoryKernel,
        pipeline: str,
        intent: str,
        recent: tuple[ExperienceRecord, ...],
        items: tuple[SnapshotItem, ...],
    ) -> list[str]:
        hints: list[str] = []
        for item in sorted(items, key=lambda i: i.score, reverse=True):
            hints.append(item.text)
        if intent and "concise" in " ".join(kernel.relationship_model.notes).lower():
            hints.append("Prefer concise output for WA.")
        return hints

    def _is_private(self, text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in ("private:", "local-only", "api key", "secret", "password"))
