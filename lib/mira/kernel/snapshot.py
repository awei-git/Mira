"""Read-only memory snapshots for pipeline runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .ledger import ExperienceLedger, ExperienceRecord
from .schema import FailureSignature, Hypothesis, MemoryClass, MemoryKernel, Scar, SkillTrace, to_jsonable


@dataclass(frozen=True)
class SnapshotItem:
    item_id: str
    text: str
    score: float
    memory_id: str = ""
    score_breakdown: dict[str, float] = field(default_factory=dict)
    why_included: str = ""
    trust_tier: str = "observed"
    privacy_tier: str = "normal"
    included: bool = True
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.memory_id:
            object.__setattr__(self, "memory_id", self.item_id)
        if not self.why_included:
            object.__setattr__(self, "why_included", "selected by snapshot scoring")

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class SnapshotManifest:
    run_id: str = ""
    profile: str = ""
    item_ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    exclusion_reasons: dict[str, str] = field(default_factory=dict)
    item_scores: dict[str, float] = field(default_factory=dict)
    total_tokens: int = 0
    hash: str = ""

    @property
    def snapshot_hash(self) -> str:
        return self.hash

    @classmethod
    def build(
        cls,
        *,
        item_ids: list[str],
        run_id: str = "",
        profile: str = "",
        excluded_ids: list[str] | None = None,
        exclusion_reasons: dict[str, str] | None = None,
        item_scores: dict[str, float] | None = None,
        total_tokens: int = 0,
    ) -> "SnapshotManifest":
        excluded_ids = excluded_ids or []
        exclusion_reasons = exclusion_reasons or {}
        item_scores = item_scores or {}
        body = {
            "run_id": run_id,
            "profile": profile,
            "item_ids": sorted(item_ids),
            "excluded_ids": sorted(excluded_ids),
            "exclusion_reasons": exclusion_reasons,
            "item_scores": item_scores,
            "total_tokens": total_tokens,
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        return cls(
            run_id=run_id,
            profile=profile,
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
        run_id: str = "",
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
            run_id=run_id,
            profile=pipeline,
            item_ids=item_ids,
            excluded_ids=[item.item_id for item in excluded_items],
            exclusion_reasons={item.item_id: item.exclusion_reason or "excluded" for item in excluded_items},
            item_scores={item.item_id: item.score for item in included_items},
            total_tokens=sum(_approx_tokens(item.text) for item in included_items),
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
            causal_success = min(1.0, 0.5 + scar.reinforcement_count * 0.1)
            items.append(
                self._item(
                    scar.scar_id,
                    f"Scar: {scar.behavioral_change}",
                    0.95,
                    memory_class,
                    importance=1.0,
                    recency=_recency_score(scar.date),
                    causal_success=causal_success,
                    why="scar with behavior change",
                )
            )
        for trace in relevant_traces:
            items.append(
                self._item(
                    f"skill:{trace.skill_name}",
                    f"Skill {trace.skill_name} success rate {trace.success_rate:.2f}: {trace.last_outcome}",
                    0.65 + min(trace.times_used, 10) * 0.02,
                    memory_class,
                    importance=0.70,
                    recency=_recency_score(trace.last_used),
                    causal_success=trace.success_rate,
                    why="skill trace for involved workflow skill",
                )
            )
        for sig in kernel.failure_signatures:
            items.append(
                self._item(
                    f"failure:{sig.pattern}",
                    f"Failure pattern: {sig.pattern}",
                    0.80,
                    memory_class,
                    importance=0.90,
                    causal_success=1.0 - min(1.0, sig.failure_rate),
                    why="failure signature relevant to prevention",
                )
            )
        for hyp in relevant_hypotheses:
            evidence_count = len(hyp.evidence_for) + len(hyp.evidence_against)
            items.append(
                self._item(
                    hyp.hypothesis_id,
                    f"Hypothesis: {hyp.claim}",
                    0.70,
                    memory_class,
                    importance=0.75,
                    recency=_recency_score(hyp.start_date),
                    causal_success=min(1.0, evidence_count / max(hyp.min_n, 1)),
                    why="active hypothesis for this pipeline",
                )
            )
        for record in recent:
            items.append(
                self._item(
                    record.id,
                    f"Prior run changed behavior: {record.delta.what_changed}",
                    0.60,
                    memory_class,
                    importance=0.65,
                    recency=_recency_score(record.timestamp),
                    causal_success=1.0 if record.causal_links else 0.3,
                    why="recent same-pipeline experience",
                )
            )
        for index, note in enumerate(kernel.relationship_model.notes[-5:]):
            items.append(
                self._item(
                    f"relationship:{index}",
                    f"WA preference: {note}",
                    0.75,
                    memory_class,
                    importance=0.80,
                    causal_success=0.5,
                    why="relationship preference",
                )
            )
        return tuple(items)

    def _item(
        self,
        item_id: str,
        text: str,
        base_score: float,
        memory_class: MemoryClass,
        *,
        importance: float = 0.5,
        recency: float = 0.5,
        causal_success: float = 0.5,
        diversity: float = 1.0,
        trust: float = 0.8,
        why: str = "",
    ) -> SnapshotItem:
        privacy_tier = "local_only" if self._is_private(text) else "normal"
        included = privacy_tier != "local_only" or memory_class == "bodily"
        token_budget = max(0.0, 1.0 - min(_approx_tokens(text), 500) / 500)
        privacy = 0.0 if not included else 1.0
        score_breakdown = {
            "relevance": round(min(1.0, base_score), 4),
            "recency": round(min(1.0, recency), 4),
            "importance": round(min(1.0, importance), 4),
            "causal_success": round(min(1.0, causal_success), 4),
            "trust": round(min(1.0, trust), 4),
            "privacy": round(privacy, 4),
            "diversity": round(min(1.0, diversity), 4),
            "token_budget": round(token_budget, 4),
        }
        score = round(
            min(
                1.0,
                0.35 * score_breakdown["relevance"]
                + 0.15 * score_breakdown["recency"]
                + 0.20 * score_breakdown["importance"]
                + 0.20 * score_breakdown["causal_success"]
                + 0.10 * score_breakdown["trust"],
            ),
            4,
        )
        return SnapshotItem(
            item_id=item_id,
            text=text,
            score=score,
            score_breakdown=score_breakdown,
            why_included=why,
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


def _recency_score(timestamp: datetime | None) -> float:
    if timestamp is None:
        return 0.5
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400)
    return round(max(0.1, 1.0 - min(age_days, 90) / 90), 4)


def _approx_tokens(text: str) -> int:
    return max(1, len(text.split()))
