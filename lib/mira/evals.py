"""V3 eval criteria and calibration history."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, Mapping

from mira.experiment_registry import ExperimentRecord, build_experiment_registry
from mira.kernel.causal import BehavioralEffect, DecisionRecord, build_causal_traces

EVAL_CRITERIA: dict[str, dict[str, object]] = {
    "article": {"criteria": "Personal voice, examples, not generic", "threshold": 0.7},
    "podcast": {"criteria": "Podcastability: depth, audio-friendly", "threshold": 0.6},
    "briefing": {"criteria": "Signal: actionable and concrete", "threshold": 3},
    "journal": {"criteria": "Threads not list, connections", "threshold": 0.6},
    "zhesi": {"criteria": "Convergence: new insight", "threshold": "present"},
    "reflection": {"criteria": "Depth: change vs last week", "threshold": "delta detected"},
    "evolution": {"criteria": "Hypothesis quality: testable, evidenced", "threshold": "measurable"},
    "research": {"criteria": "Novel insight, not summary", "threshold": 0.7},
    "book": {"criteria": "Novelty of viewpoint", "threshold": 0.7},
}

PIPELINE_EVAL_CRITERIA_ALIASES = {
    "article_creation": "article",
    "podcast_production": "podcast",
    "intelligence_briefing": "briefing",
    "daily_journal": "journal",
    "research_deep_dive": "research",
    "book_reading_notes": "book",
    "self_evolution": "evolution",
}

FIRST_STAGE_EVAL_PIPELINES = frozenset(
    {
        "podcast",
        "podcast_production",
        "system_health",
        "incident",
        "incident_response",
        "article",
        "article_creation",
        "briefing",
        "intelligence_briefing",
        "a2a_trust_experiment",
    }
)


@dataclass(frozen=True)
class EvalEvent:
    pipeline: str
    score: float
    passed: bool
    outcome_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvalHistory:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: EvalEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    def list(self, pipeline: str | None = None) -> list[EvalEvent]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                if pipeline is None or data["pipeline"] == pipeline:
                    events.append(EvalEvent(**data))
        return events


def bounded_threshold_adjustment(current: float, desired: float, max_weekly_delta: float = 0.05) -> float:
    delta = max(-max_weekly_delta, min(max_weekly_delta, desired - current))
    return round(current + delta, 4)


@dataclass(frozen=True)
class IncidentEvent:
    id: str
    run_id: str
    severity: Literal["low", "medium", "high", "critical"]
    action_id: str | None
    was_approved: bool | None
    root_cause: str
    preventable: bool


@dataclass(frozen=True)
class FailureEvent:
    id: str
    run_id: str
    pipeline: str
    failure_sig_id: str
    severity: Literal["low", "medium", "high", "critical"]
    root_cause: str
    detected_at_step: str
    prevented: bool = False
    caused_incident: bool = False


@dataclass(frozen=True)
class FailureSignatureEval:
    failure_sig_id: str
    first_seen_at: datetime
    scar_created_at: datetime | None
    opportunities_before_scar: int
    failures_before_scar: int
    opportunities_after_scar: int
    failures_after_scar: int
    preventions_after_scar: int


@dataclass(frozen=True)
class FailureReductionSummary:
    signature_count: int
    failure_event_count: int
    repeat_error_rate: float
    post_scar_recurrence_rate: float
    scar_prevention_rate: float
    high_severity_repeat_failures: int

    @property
    def passed(self) -> bool:
        return (
            self.high_severity_repeat_failures == 0
            and self.post_scar_recurrence_rate <= self.repeat_error_rate
            and self.scar_prevention_rate >= 0.50
        )


@dataclass(frozen=True)
class NorthStarScorecard:
    repeated_error: float
    causal_memory: float
    output_quality: float
    memory_health: float
    self_evolution: float
    approval_safety: float
    traceability: float
    critical_memory_pollution: int = 0
    unapproved_high_risk_action: int = 0
    unreplayable_action: int = 0
    invalid_replay_bundle: int = 0
    orphan_important_action: int = 0
    causal_link_validity: float = 1.0
    l4_required_causal_evidence: float = 1.0

    @property
    def score(self) -> float:
        return round(
            0.20 * self.repeated_error
            + 0.20 * self.causal_memory
            + 0.15 * self.output_quality
            + 0.15 * self.memory_health
            + 0.10 * self.self_evolution
            + 0.10 * self.approval_safety
            + 0.10 * self.traceability,
            4,
        )

    @property
    def hard_gate_failures(self) -> list[str]:
        failures: list[str] = []
        if self.critical_memory_pollution > 0:
            failures.append("critical_memory_pollution")
        if self.unapproved_high_risk_action > 0:
            failures.append("unapproved_high_risk_action")
        if self.unreplayable_action > 0:
            failures.append("unreplayable_action")
        if self.invalid_replay_bundle > 0:
            failures.append("invalid_replay_bundle")
        if self.orphan_important_action > 0:
            failures.append("orphan_important_action")
        if self.causal_link_validity < 0.70:
            failures.append("causal_link_validity")
        if self.l4_required_causal_evidence < 1.0:
            failures.append("l4_required_causal_evidence")
        return failures


@dataclass(frozen=True)
class EvalMetric:
    name: str
    score: float
    passed: bool
    detail: str


@dataclass(frozen=True)
class EvalRecord:
    id: str
    run_id: str
    pipeline: str
    criterion: str
    score: float
    threshold: float
    passed: bool
    judge_model: str
    rubric_version: str
    evidence_refs: list[str]


@dataclass(frozen=True)
class OutcomeRecord:
    id: str
    run_id: str
    metric_name: str
    metric_value: float | str
    observed_at: datetime
    attribution_window: str
    confounders: list[str]


@dataclass(frozen=True)
class RunEvidenceBundle:
    run_id: str
    pipeline: str
    workflow: str
    timestamp: datetime
    intent: str
    expected_outcome: str
    actual_outcome: str
    snapshot_id: str
    retrieved_memory_ids: list[str]
    included_memory_ids: list[str]
    decision_records: list[DecisionRecord]
    behavioral_effects: list[BehavioralEffect]
    failure_events: list[FailureEvent]
    eval_records: list[EvalRecord]
    outcome_records: list[OutcomeRecord]
    approval_events: list[object]
    incident_events: list[IncidentEvent]
    memory_delta_proposal_id: str | None
    memory_commit_id: str | None
    causal_links: list[str]


@dataclass(frozen=True)
class RunEvalBundle:
    metrics: list[EvalMetric]
    scorecard: NorthStarScorecard
    failure_events: list[FailureEvent] = field(default_factory=list)
    failure_signature_evals: list[FailureSignatureEval] = field(default_factory=list)
    incident_events: list[IncidentEvent] = field(default_factory=list)
    eval_records: list[EvalRecord] = field(default_factory=list)
    outcome_records: list[OutcomeRecord] = field(default_factory=list)
    decision_records: list[DecisionRecord] = field(default_factory=list)
    behavioral_effects: list[BehavioralEffect] = field(default_factory=list)
    approval_events: list[object] = field(default_factory=list)
    run_evidence_bundles: list[RunEvidenceBundle] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(metric.passed for metric in self.metrics) and not self.scorecard.hard_gate_failures


@dataclass(frozen=True)
class StrategicNorthStarScorecard:
    a2a_questions_advanced: int = 0
    a2a_experiments_completed: int = 0
    reproducible_artifacts: int = 0
    tool_prototypes: int = 0
    public_writeups: int = 0
    public_feedback_items: int = 0
    product_thesis_updates: int = 0
    commercial_options: int = 0
    public_writeup_refs: list[str] = field(default_factory=list)
    public_feedback_refs: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        question_progress = min(self.a2a_questions_advanced, 3) / 3
        experiment_progress = min(self.a2a_experiments_completed, 2) / 2
        a2a_research_progress = (question_progress + experiment_progress) / 2
        reproducible_experiment_score = min(self.reproducible_artifacts, 4) / 4
        tool_artifact_score = ((min(self.tool_prototypes, 2) / 2) + min(self.public_writeups, 1)) / 2
        public_feedback_score = min(self.public_feedback_items, 3) / 3
        product_thesis_score = min(self.product_thesis_updates, 1)
        revenue_option_score = min(self.commercial_options, 2) / 2
        score = (
            0.25 * a2a_research_progress
            + 0.20 * reproducible_experiment_score
            + 0.20 * tool_artifact_score
            + 0.15 * public_feedback_score
            + 0.10 * product_thesis_score
            + 0.10 * revenue_option_score
        )
        return round(score, 4)

    @property
    def hard_gate_failures(self) -> list[str]:
        failures: list[str] = []
        if self.a2a_experiments_completed < 1:
            failures.append("no_a2a_trust_experiment")
        if self.reproducible_artifacts < 1:
            failures.append("no_reproducible_artifact")
        if self.tool_prototypes < 1:
            failures.append("no_tool_or_validator_prototype")
        return failures


@dataclass(frozen=True)
class VoiceStabilitySummary:
    sample_count: int
    voice_score_mean: float
    voice_score_std: float
    generic_failure_rate: float

    @property
    def passed(self) -> bool:
        return (
            self.sample_count > 0
            and self.voice_score_mean >= 0.70
            and self.voice_score_std <= 0.20
            and self.generic_failure_rate <= 0.10
        )


@dataclass(frozen=True)
class InterestProfile:
    topic: str
    weight: float
    source: Literal["explicit", "inferred", "recent_behavior", "long_term"]
    evidence_refs: list[str]
    last_updated: datetime


BriefingUserAction = Literal[
    "read",
    "ignored",
    "dismissed",
    "saved",
    "asked_followup",
    "promoted_to_research",
    "promoted_to_article",
]

BRIEFING_FEEDBACK_BUTTONS: tuple[str, ...] = (
    "useful",
    "too_obvious",
    "surprising",
    "wrong",
    "follow_up",
    "pursue_research",
    "pursue_article",
    "not_useful",
)

BRIEFING_FEEDBACK_ACTIONS: dict[str, BriefingUserAction] = {
    "useful": "read",
    "read": "read",
    "save": "saved",
    "saved": "saved",
    "surprising": "saved",
    "not_useful": "dismissed",
    "too_obvious": "dismissed",
    "wrong": "dismissed",
    "dismiss": "dismissed",
    "dismissed": "dismissed",
    "ignored": "ignored",
    "pursue": "asked_followup",
    "follow_up": "asked_followup",
    "asked_followup": "asked_followup",
    "pursue_research": "promoted_to_research",
    "research": "promoted_to_research",
    "promoted_to_research": "promoted_to_research",
    "pursue_article": "promoted_to_article",
    "article": "promoted_to_article",
    "promoted_to_article": "promoted_to_article",
}


MemoryAuditVerdict = Literal["valid", "unsupported", "false", "stale", "unsafe", "duplicative"]
MemoryAuditAction = Literal["keep", "downgrade", "quarantine", "delete", "merge", "expire"]


@dataclass(frozen=True)
class BriefingItemEval:
    item_id: str
    text: str
    topics: list[str]
    matched_interest_ids: list[str]
    novelty_score: float
    actionability_score: float
    user_action: BriefingUserAction | None = None


@dataclass(frozen=True)
class BriefingInterestSummary:
    sample_count: int
    item_count: int
    precision_at_5: float
    action_rate: float
    dismiss_rate: float = 0.0
    interest_coverage: float = 0.0
    novel_but_relevant_rate: float = 0.0
    feedback_item_count: int = 0
    promoted_item_count: int = 0
    blind_sample_count: int = 0
    feedback_coverage_rate: float = 0.0

    @property
    def passed(self) -> bool:
        return (
            self.sample_count > 0
            and self.item_count > 0
            and self.feedback_item_count > 0
            and self.precision_at_5 >= 0.60
            and self.action_rate >= 0.20
            and self.promoted_item_count >= 2
        )


@dataclass(frozen=True)
class MemoryAuditRecord:
    memory_id: str
    source_type: str
    trust_tier: str
    privacy_tier: str
    evidence_refs: list[str]
    audited_at: datetime
    verdict: MemoryAuditVerdict
    severity: Literal["low", "medium", "high", "critical"]
    action_taken: MemoryAuditAction


@dataclass(frozen=True)
class MemoryHealthSummary:
    audited_memories: int
    memory_precision: float
    unsupported_claim_rate: float
    critical_pollution_count: int
    quarantine_recall: float
    snapshot_contamination_rate: float
    contaminated_snapshot_count: int
    snapshots_audited: int

    @property
    def passed(self) -> bool:
        return (
            self.critical_pollution_count == 0
            and self.snapshot_contamination_rate == 0.0
            and self.quarantine_recall >= 0.95
            and self.memory_precision >= 0.90
            and self.unsupported_claim_rate <= 0.05
        )


def build_operational_eval_bundle(
    records: list,
    commits: list,
    effects: list,
    causal_evidence: list | None = None,
    approval_events: list | None = None,
) -> RunEvalBundle:
    total = max(len(records), 1)
    failed = [record for record in records if _is_failure_record(record)]
    repeated_error = 1.0 - min(len(failed) / total, 1.0)
    causal_memory, causal_opportunities, causal_changes = _scar_opportunity_causal_memory(records)
    causal_link_validity = _causal_link_validity(records, causal_evidence or [])
    l4_required = _l4_required_causal_evidence(records, causal_evidence or [])
    output_quality = sum(1 for record in records if record.outcome not in {"failed", "blocked_preflight"}) / total
    pollution = sum(1 for commit in commits if commit.status in {"quarantined", "rejected"})
    memory_health = 1.0 - min(pollution / max(len(commits), 1), 1.0)
    self_evolution = (
        1.0 if any(record.pipeline in {"self_evolution", "a2a_trust_experiment"} for record in records) else 0.0
    )
    current_effects = _latest_effects(effects)
    unsafe_effects = sum(1 for effect in current_effects if effect.status == "unknown")
    unapproved_high_risk_effects = sum(1 for effect in current_effects if _unapproved_high_risk_effect(effect))
    unreplayable_effects = sum(1 for effect in current_effects if _unreplayable_important_effect(effect))
    invalid_replay_bundles = sum(1 for effect in current_effects if _invalid_replay_bundle_effect(effect))
    important_replay_effects = sum(1 for effect in current_effects if _effect_requires_replay_bundle(effect))
    replay_bundle_refs = sum(
        1
        for effect in current_effects
        if _effect_requires_replay_bundle(effect) and getattr(effect, "replay_bundle_ref", "")
    )
    unreplayable_action_rate = min(unreplayable_effects / max(important_replay_effects, 1), 1.0)
    replay_bundle_validity = 1.0 - min(invalid_replay_bundles / max(replay_bundle_refs, 1), 1.0)
    approval_safety = 1.0 - min(unsafe_effects / max(len(current_effects), 1), 1.0)
    traceability = (
        sum(
            1
            for record in records
            if record.memory_commit_id
            or record.side_effect_refs
            or record.artifacts
            or record.delta.status == "no_kernel_change"
        )
        / total
    )
    causal_traces = build_causal_traces(records, effects)
    failure_events = build_failure_events(records)
    failure_signature_evals = build_failure_signature_evals(records)
    incident_events = build_incident_events(records, effects)
    eval_records = build_eval_records(records)
    outcome_records = build_outcome_records(records)
    decision_records = build_decision_records(records)
    behavioral_effects = build_behavioral_effects(records, causal_evidence or [])
    approval_event_rows = list(approval_events or [])
    run_evidence_bundles = build_run_evidence_bundles(
        records,
        decision_records,
        behavioral_effects,
        failure_events,
        eval_records,
        outcome_records,
        approval_event_rows,
        incident_events,
    )
    important_trace_completeness = (
        sum(trace.completeness_score for trace in causal_traces) / len(causal_traces) if causal_traces else 1.0
    )
    scorecard = NorthStarScorecard(
        repeated_error=repeated_error,
        causal_memory=causal_memory,
        output_quality=output_quality,
        memory_health=memory_health,
        self_evolution=self_evolution,
        approval_safety=approval_safety,
        traceability=traceability,
        critical_memory_pollution=pollution,
        unapproved_high_risk_action=unapproved_high_risk_effects,
        unreplayable_action=unreplayable_effects,
        invalid_replay_bundle=invalid_replay_bundles,
        orphan_important_action=unsafe_effects,
        causal_link_validity=causal_link_validity,
        l4_required_causal_evidence=l4_required,
    )
    metrics = [
        EvalMetric("repeated_errors_decrease", repeated_error, repeated_error >= 0.8, f"{len(failed)} failed runs"),
        EvalMetric(
            "causal_memory",
            causal_memory,
            causal_memory >= 0.5,
            f"{causal_changes} causal changes from {causal_opportunities} scar opportunities",
        ),
        EvalMetric("output_quality", output_quality, output_quality >= 0.8, "non-failed outputs"),
        EvalMetric("memory_health", memory_health, memory_health >= 0.95, f"{pollution} polluted commits"),
        EvalMetric(
            "self_evolution_records", self_evolution, self_evolution >= 1.0, "experiment/self-evolution present"
        ),
        EvalMetric("approval_safety", approval_safety, approval_safety >= 0.95, f"{unsafe_effects} unknown effects"),
        EvalMetric(
            "unapproved_high_risk_action",
            1.0 if unapproved_high_risk_effects == 0 else 0.0,
            unapproved_high_risk_effects == 0,
            f"{unapproved_high_risk_effects} executed high-risk effects without approval token and preview hash",
        ),
        EvalMetric(
            "unreplayable_action_rate",
            unreplayable_action_rate,
            unreplayable_effects == 0,
            f"{unreplayable_effects} public/memory/code effects missing replay bundles from {important_replay_effects} important effects",
        ),
        EvalMetric(
            "replay_bundle_validity",
            replay_bundle_validity,
            invalid_replay_bundles == 0,
            f"{invalid_replay_bundles} invalid local replay bundles from {replay_bundle_refs} replay bundle refs",
        ),
        EvalMetric("traceability", traceability, traceability >= 0.9, "records with trace anchors"),
        EvalMetric(
            "important_behavior_causal_trace",
            important_trace_completeness,
            important_trace_completeness >= 0.95,
            f"{sum(1 for trace in causal_traces if trace.completeness_score < 0.95)} important traces below 95% completeness",
        ),
        EvalMetric(
            "l4_required_causal_evidence",
            l4_required,
            l4_required >= 1.0,
            "North Star and self-evolution causal claims with ablation evidence",
        ),
    ]
    return RunEvalBundle(
        metrics=metrics,
        scorecard=scorecard,
        failure_events=failure_events,
        failure_signature_evals=failure_signature_evals,
        incident_events=incident_events,
        eval_records=eval_records,
        outcome_records=outcome_records,
        decision_records=decision_records,
        behavioral_effects=behavioral_effects,
        approval_events=approval_event_rows,
        run_evidence_bundles=run_evidence_bundles,
    )


def build_eval_records(records: list) -> list[EvalRecord]:
    eval_records: list[EvalRecord] = []
    for record in records:
        failed = _is_failure_record(record)
        criterion_key = PIPELINE_EVAL_CRITERIA_ALIASES.get(record.pipeline, record.pipeline)
        criterion_spec = EVAL_CRITERIA.get(criterion_key) or {}
        criterion = str(criterion_spec.get("criteria") or "Operational outcome quality")
        threshold = _numeric_threshold(criterion_spec.get("threshold"), default=0.8)
        score = 0.0 if failed else max(0.0, min(float(getattr(record, "confidence", 0.0) or 0.0), 1.0))
        eval_records.append(
            EvalRecord(
                id=f"eval:{record.id}:outcome_quality",
                run_id=record.id,
                pipeline=record.pipeline,
                criterion=criterion,
                score=score,
                threshold=threshold,
                passed=score >= threshold,
                judge_model="deterministic-v3.1",
                rubric_version="v3.1-outcome-quality",
                evidence_refs=_record_evidence_refs(record),
            )
        )
    return eval_records


def build_outcome_records(records: list) -> list[OutcomeRecord]:
    return [
        OutcomeRecord(
            id=f"outcome:{record.id}:actual_outcome",
            run_id=record.id,
            metric_name="actual_outcome",
            metric_value=record.outcome,
            observed_at=record.timestamp,
            attribution_window="single_run",
            confounders=_record_confounders(record),
        )
        for record in records
    ]


def build_decision_records(records: list) -> list[DecisionRecord]:
    """Derive V3.1 decision rows from ledger records with trace anchors."""

    decision_type = DecisionRecord
    decisions: list[DecisionRecord] = []
    for record in records:
        if not _record_has_trace_anchor(record):
            continue
        decisions.append(
            decision_type(
                run_id=str(record.id),
                pipeline=str(record.pipeline),
                step=_decision_step(record),
                decision=_decision_text(record),
                memory_trace_ids=[_memory_trace_id(record, link) for link in getattr(record, "causal_links", [])],
                decision_id=_decision_id(record),
                timestamp=getattr(record, "timestamp", datetime.now(timezone.utc)),
            )
        )
    return decisions


def build_behavioral_effects(records: list, causal_evidence: list | None = None) -> list[BehavioralEffect]:
    """Derive V3.1 behavioral effects for asserted causal-memory changes."""

    evidence_by_id = {str(getattr(evidence, "evidence_id", "")): evidence for evidence in causal_evidence or []}
    effect_type = BehavioralEffect
    effects: list[BehavioralEffect] = []
    for record in records:
        for link in getattr(record, "causal_links", []):
            link_id = str(link)
            evidence = evidence_by_id.get(link_id)
            memory_id = str(getattr(evidence, "memory_id", "") or link_id)
            effects.append(
                effect_type(
                    memory_id=memory_id,
                    decision_id=_decision_id(record),
                    effect_type=_behavioral_effect_type(record),
                    counterfactual=_counterfactual_text(record, evidence),
                    effect_id=_behavioral_effect_id(record, link_id),
                    timestamp=getattr(record, "timestamp", datetime.now(timezone.utc)),
                )
            )
    return effects


def build_run_evidence_bundles(
    records: list,
    decision_records: list[DecisionRecord],
    behavioral_effects: list[BehavioralEffect],
    failure_events: list[FailureEvent],
    eval_records: list[EvalRecord],
    outcome_records: list[OutcomeRecord],
    approval_events: list,
    incident_events: list[IncidentEvent],
) -> list[RunEvidenceBundle]:
    """Build the V3.1 per-run evidence bundle shape under the aggregate scorecard."""

    decisions_by_run: dict[str, list[DecisionRecord]] = {}
    for decision in decision_records:
        decisions_by_run.setdefault(decision.run_id, []).append(decision)
    decision_run_by_id = {decision.decision_id: decision.run_id for decision in decision_records}
    effects_by_run: dict[str, list[BehavioralEffect]] = {}
    for effect in behavioral_effects:
        run_id = decision_run_by_id.get(effect.decision_id)
        if run_id:
            effects_by_run.setdefault(run_id, []).append(effect)
    failure_by_run = _group_by_run_id(failure_events)
    evals_by_run = _group_by_run_id(eval_records)
    outcomes_by_run = _group_by_run_id(outcome_records)
    approvals_by_run = _group_by_run_id(approval_events)
    incidents_by_run = _group_by_run_id(incident_events)

    return [
        RunEvidenceBundle(
            run_id=str(record.id),
            pipeline=str(record.pipeline),
            workflow=_workflow_ref(record),
            timestamp=record.timestamp,
            intent=str(record.intent),
            expected_outcome=_expected_outcome(record),
            actual_outcome=str(record.outcome),
            snapshot_id=f"snapshot:{record.id}",
            retrieved_memory_ids=[str(link) for link in getattr(record, "causal_links", [])],
            included_memory_ids=[str(link) for link in getattr(record, "causal_links", [])],
            decision_records=decisions_by_run.get(str(record.id), []),
            behavioral_effects=effects_by_run.get(str(record.id), []),
            failure_events=failure_by_run.get(str(record.id), []),
            eval_records=evals_by_run.get(str(record.id), []),
            outcome_records=outcomes_by_run.get(str(record.id), []),
            approval_events=approvals_by_run.get(str(record.id), []),
            incident_events=incidents_by_run.get(str(record.id), []),
            memory_delta_proposal_id=str(record.memory_delta_proposal_id),
            memory_commit_id=getattr(record, "memory_commit_id", None),
            causal_links=[str(link) for link in getattr(record, "causal_links", [])],
        )
        for record in records
    ]


def build_failure_events(records: list) -> list[FailureEvent]:
    events: list[FailureEvent] = []
    known_signatures_by_pipeline: dict[str, list[str]] = {}
    for record in sorted(records, key=_item_timestamp):
        pipeline = str(getattr(record, "pipeline", ""))
        if _is_failure_record(record):
            signature = _failure_signature_id(record)
            mode = _failure_mode(record)
            severity = _record_incident_severity(record, mode)
            events.append(
                FailureEvent(
                    id=f"failure_event:{getattr(record, 'id', '')}:{signature}",
                    run_id=str(getattr(record, "id", "")),
                    pipeline=pipeline,
                    failure_sig_id=signature,
                    severity=severity,
                    root_cause=mode,
                    detected_at_step=_failure_detected_step(record),
                    prevented=False,
                    caused_incident=severity in {"high", "critical"},
                )
            )
        elif _record_prevents_known_failure(record) and known_signatures_by_pipeline.get(pipeline):
            signature = known_signatures_by_pipeline[pipeline][0]
            events.append(
                FailureEvent(
                    id=f"failure_prevention:{getattr(record, 'id', '')}:{signature}",
                    run_id=str(getattr(record, "id", "")),
                    pipeline=pipeline,
                    failure_sig_id=signature,
                    severity="low",
                    root_cause="prevented_by_memory",
                    detected_at_step=_failure_detected_step(record),
                    prevented=True,
                    caused_incident=False,
                )
            )
        for signature in _record_failure_signature_targets(record):
            sig_pipeline = _failure_signature_pipeline(signature) or pipeline
            signatures = known_signatures_by_pipeline.setdefault(sig_pipeline, [])
            if signature not in signatures:
                signatures.append(signature)
    return events


def build_failure_signature_evals(records: list) -> list[FailureSignatureEval]:
    events = build_failure_events(records)
    if not events:
        return []
    scar_created_at = _failure_signature_scar_times(records)
    events_by_signature: dict[str, list[FailureEvent]] = {}
    for event in events:
        events_by_signature.setdefault(event.failure_sig_id, []).append(event)
    eval_type = FailureSignatureEval
    summaries: list[FailureSignatureEval] = []
    for signature, signature_events in sorted(events_by_signature.items()):
        ordered = sorted(signature_events, key=lambda event: _event_time(event, records))
        first_seen = _event_time(ordered[0], records)
        scar_at = scar_created_at.get(signature)
        before = [event for event in ordered if scar_at is None or _event_time(event, records) <= scar_at]
        after = [event for event in ordered if scar_at is not None and _event_time(event, records) > scar_at]
        summaries.append(
            eval_type(
                failure_sig_id=signature,
                first_seen_at=first_seen,
                scar_created_at=scar_at,
                opportunities_before_scar=len(before),
                failures_before_scar=sum(1 for event in before if not event.prevented),
                opportunities_after_scar=len(after),
                failures_after_scar=sum(1 for event in after if not event.prevented),
                preventions_after_scar=sum(1 for event in after if event.prevented),
            )
        )
    return summaries


def evaluate_failure_reduction(records: list) -> FailureReductionSummary:
    signature_evals = build_failure_signature_evals(records)
    failure_events = build_failure_events(records)
    before_opportunities = sum(item.opportunities_before_scar for item in signature_evals)
    before_failures = sum(item.failures_before_scar for item in signature_evals)
    after_opportunities = sum(item.opportunities_after_scar for item in signature_evals)
    after_failures = sum(item.failures_after_scar for item in signature_evals)
    after_preventions = sum(item.preventions_after_scar for item in signature_evals)
    high_repeat_failures = sum(
        1
        for item in signature_evals
        for event in failure_events
        if event.failure_sig_id == item.failure_sig_id
        and item.scar_created_at is not None
        and _event_time(event, records) >= item.scar_created_at
        and not event.prevented
        and event.severity in {"high", "critical"}
    )
    return FailureReductionSummary(
        signature_count=len(signature_evals),
        failure_event_count=len(failure_events),
        repeat_error_rate=round(before_failures / max(before_opportunities, 1), 4),
        post_scar_recurrence_rate=round(after_failures / max(after_opportunities, 1), 4),
        scar_prevention_rate=round(after_preventions / max(after_opportunities, 1), 4),
        high_severity_repeat_failures=high_repeat_failures,
    )


def _numeric_threshold(value: object, *, default: float) -> float:
    return value if isinstance(value, (int, float)) else default


def _record_evidence_refs(record) -> list[str]:
    refs = [
        *getattr(record, "artifacts", []),
        *getattr(record, "causal_links", []),
        *getattr(record, "eval_refs", []),
        *getattr(record, "side_effect_refs", []),
    ]
    if getattr(record, "memory_commit_id", None):
        refs.append(record.memory_commit_id)
    return [str(ref) for ref in refs if ref]


def _record_has_trace_anchor(record) -> bool:
    return bool(
        getattr(record, "causal_links", [])
        or getattr(record, "artifacts", [])
        or getattr(record, "eval_refs", [])
        or getattr(record, "side_effect_refs", [])
        or getattr(record, "memory_commit_id", None)
        or getattr(getattr(record, "delta", None), "status", "") == "no_kernel_change"
    )


def _group_by_run_id(items: list) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for item in items:
        run_id = str(getattr(item, "run_id", "") or "")
        if run_id:
            grouped.setdefault(run_id, []).append(item)
    return grouped


def _workflow_ref(record) -> str:
    for ref in getattr(record, "eval_refs", []):
        text = str(ref)
        if text.startswith("workflow:"):
            return text
    return f"workflow:{getattr(record, 'pipeline', '')}"


def _expected_outcome(record) -> str:
    if _is_approval_gate_record(record):
        return "approval_required"
    if _is_preflight_gate_record(record):
        return "blocked_preflight"
    if getattr(getattr(record, "delta", None), "status", "") == "no_kernel_change":
        return "no_kernel_change"
    return "completed"


def _decision_id(record) -> str:
    return f"decision:{getattr(record, 'id', '')}:ledger_outcome"


def _decision_step(record) -> str:
    actions = getattr(getattr(record, "delta", None), "actions", [])
    if actions:
        return str(getattr(actions[-1], "type", "") or "record_outcome")
    if getattr(getattr(record, "delta", None), "status", "") == "no_kernel_change":
        return "no_kernel_change"
    return "record_outcome"


def _decision_text(record) -> str:
    changed = str(getattr(getattr(record, "delta", None), "what_changed", "") or "").strip()
    if changed:
        return changed
    return f"{getattr(record, 'pipeline', '')} -> {getattr(record, 'outcome', '')}"


def _memory_trace_id(record, link: object) -> str:
    digest = hashlib.sha256(str(link).encode("utf-8")).hexdigest()[:12]
    return f"memtrace:{getattr(record, 'id', '')}:{digest}"


def _behavioral_effect_id(record, link: str) -> str:
    digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:12]
    return f"effect:{getattr(record, 'id', '')}:{digest}"


def _behavioral_effect_type(record) -> str:
    text = " ".join(
        [
            str(getattr(record, "pipeline", "")),
            str(getattr(getattr(record, "delta", None), "what_changed", "")),
            str(getattr(getattr(record, "delta", None), "what_mattered", "")),
            *[str(ref) for ref in getattr(record, "eval_refs", [])],
            *[str(ref) for ref in getattr(record, "side_effect_refs", [])],
            *[
                " ".join([str(action.type), str(action.target), str(action.detail)])
                for action in getattr(getattr(record, "delta", None), "actions", [])
            ],
        ]
    )
    if re.search(r"\b(schedule|cadence|cron|heartbeat)\b", text, re.IGNORECASE):
        return "changed_schedule"
    if re.search(r"\b(escalat|approval|human|review)\b", text, re.IGNORECASE):
        return "escalated"
    if re.search(r"\b(skip|skipped|blocked|no[_ -]?op|no kernel change)\b", text, re.IGNORECASE):
        return "skipped_action"
    if re.search(r"\b(check|preflight|gate|validation|guardrail|audit|eval|threshold)\b", text, re.IGNORECASE):
        return "added_check"
    if re.search(r"\b(tool|tts|provider|adapter|connector|fallback)\b", text, re.IGNORECASE):
        return "changed_tool"
    return "changed_route"


def _counterfactual_text(record, evidence: object | None) -> str:
    reason = str(getattr(evidence, "reason", "") or "").strip()
    if reason:
        return reason
    return f"without linked memory, {getattr(record, 'pipeline', '')} may have kept its prior route"


def _record_confounders(record) -> list[str]:
    confounders: list[str] = []
    if _is_approval_gate_record(record):
        confounders.append("approval_gate")
    if _is_preflight_gate_record(record):
        confounders.append("preflight_gate")
    if getattr(record.delta, "what_failed", None):
        confounders.append(str(record.delta.what_failed))
    if not getattr(record, "causal_links", []):
        confounders.append("no_causal_link")
    return confounders


def _is_failure_record(record) -> bool:
    if _is_approval_gate_record(record):
        return False
    if _is_preflight_gate_record(record):
        return False
    return getattr(record, "outcome", "") == "failed" or bool(getattr(record.delta, "what_failed", None))


def _is_approval_gate_record(record) -> bool:
    outcome = getattr(record, "outcome", "")
    if outcome == "approval_required":
        return True
    if outcome not in {"needs-input", "needs_input"}:
        return False
    prompt = str(getattr(getattr(record, "delta", None), "what_failed", "") or "").lower()
    return bool(re.search(r"\bconfirm\b.*\b(publish|post|send|upload)\b", prompt))


def _is_near_miss_record(record) -> bool:
    if _is_approval_gate_record(record) or _is_preflight_gate_record(record):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "outcome", ""),
            getattr(getattr(record, "delta", None), "what_failed", ""),
            getattr(getattr(record, "delta", None), "what_changed", ""),
            getattr(getattr(record, "delta", None), "what_happened", ""),
        )
    ).lower()
    return bool(re.search(r"blocked_preflight|preflight blocked|missing capabilities", text))


def _is_preflight_gate_record(record) -> bool:
    failure_text = str(getattr(getattr(record, "delta", None), "what_failed", "") or "").lower()
    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "outcome", ""),
            getattr(getattr(record, "delta", None), "what_failed", ""),
            getattr(getattr(record, "delta", None), "what_changed", ""),
            getattr(getattr(record, "delta", None), "what_happened", ""),
        )
    ).lower()
    if "preflight failed" in failure_text or "failed preflight" in failure_text:
        return False
    if "preflight blocked" in failure_text:
        return True
    return bool(re.search(r"blocked_preflight|preflight blocked|missing capabilities", text))


_SYNTHETIC_TASK_FIXTURE_IDS = {
    "task123",
    "task124",
    "task125",
    "task126",
    "task127",
    "task127b",
    "task128",
    "task128b",
    "task128c",
    "task129",
    "task129a",
    "task129b",
    "task129c",
    "task129d",
    "task129z",
    "task129za",
    "task129zb",
    "task141",
    "task142",
    "autowrite_2026-04-05",
}


def _is_synthetic_task_fixture_record(record) -> bool:
    if getattr(record, "trigger", "") != "task_result":
        return False
    intent = str(getattr(record, "intent", ""))
    task_id = intent.removeprefix("complete task ").strip()
    return task_id in _SYNTHETIC_TASK_FIXTURE_IDS


def build_incident_events(records: list, effects: list) -> list[IncidentEvent]:
    """Derive the V3.1 IncidentEvent view from durable ledger and effect evidence."""

    events: list[IncidentEvent] = []
    seen: set[str] = set()
    for effect in _latest_effects(effects):
        if not _is_effect_incident(effect):
            continue
        event = _incident_event_from_effect(effect)
        if event.id not in seen:
            seen.add(event.id)
            events.append(event)
    for record in records:
        if not _is_failure_record(record):
            continue
        event = _incident_event_from_record(record)
        if event.id not in seen:
            seen.add(event.id)
            events.append(event)
    return events


def _incident_event_from_effect(effect) -> IncidentEvent:
    action_text = " ".join(
        str(value or "")
        for value in (
            getattr(effect, "action", ""),
            getattr(effect, "action_type", ""),
            getattr(effect, "target", ""),
            getattr(effect, "detail", ""),
            getattr(effect, "status", ""),
        )
    )
    unapproved_high_risk = _unapproved_high_risk_effect(effect)
    status = str(getattr(effect, "status", ""))
    return IncidentEvent(
        id=f"effect:{getattr(effect, 'effect_id', '') or getattr(effect, 'idempotency_key', '')}",
        run_id=str(getattr(effect, "run_id", "")),
        severity=_effect_incident_severity(effect, action_text, unapproved_high_risk),
        action_id=str(getattr(effect, "idempotency_key", "") or getattr(effect, "effect_id", "")) or None,
        was_approved=bool(getattr(effect, "approval_token_id", None)),
        root_cause=_root_cause(
            getattr(effect, "detail", ""),
            status,
            getattr(effect, "action", ""),
            getattr(effect, "target", ""),
        ),
        preventable=unapproved_high_risk or status == "unknown",
    )


def _incident_event_from_record(record) -> IncidentEvent:
    mode = _failure_mode(record)
    return IncidentEvent(
        id=f"record:{getattr(record, 'id', '')}",
        run_id=str(getattr(record, "id", "")),
        severity=_record_incident_severity(record, mode),
        action_id=None,
        was_approved=None,
        root_cause=mode,
        preventable=mode
        in {
            "approval_prompt",
            "preflight_blocked",
            "preflight_failed",
            "missing_reasoning_field",
            "no_verifiable_output",
            "missing_source_material",
            "handler_load_failed",
            "effect_reconciliation_required",
        },
    )


def evaluate_voice_stability(records: list) -> VoiceStabilitySummary:
    texts = _record_artifact_texts(records, {"article_creation", "social_reactive", "social_proactive"})
    if not texts:
        return VoiceStabilitySummary(0, 0.0, 0.0, 0.0)
    scores = [_voice_score(text) for text in texts]
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores) / len(scores)
    generic_failures = sum(1 for text, score in zip(texts, scores) if _has_generic_ai_markers(text) or score < 0.70)
    return VoiceStabilitySummary(
        sample_count=len(texts),
        voice_score_mean=round(mean, 4),
        voice_score_std=round(variance**0.5, 4),
        generic_failure_rate=round(generic_failures / len(texts), 4),
    )


def evaluate_briefing_interest_fit(records: list) -> BriefingInterestSummary:
    texts = _record_artifact_texts(records, {"intelligence_briefing"})
    if not texts:
        return BriefingInterestSummary(0, 0, 0.0, 0.0)
    item_rows = build_briefing_item_reviews(records)
    feedback_items = [item for item in item_rows if item.user_action]
    positive_user_actions = {"read", "saved", "asked_followup", "promoted_to_research", "promoted_to_article"}
    promoted_user_actions = {"asked_followup", "promoted_to_research", "promoted_to_article"}
    precision_scores: list[float] = []
    item_count = 0
    for text in texts:
        items = _briefing_items(text)[:5]
        if not items:
            continue
        item_count += len(items)
        precision_scores.append(sum(1 for item in items if _matches_interest_profile(item)) / len(items))
    if not precision_scores or item_count == 0:
        return BriefingInterestSummary(len(texts), 0, 0.0, 0.0)
    total_items = max(item_count, 1)
    if feedback_items:
        action_rate = sum(1 for item in feedback_items if item.user_action in positive_user_actions) / total_items
        dismiss_rate = sum(1 for item in feedback_items if item.user_action == "dismissed") / total_items
    else:
        action_rate = 0.0
        dismiss_rate = 0.0
    matched_interest_ids = {interest for item in item_rows for interest in item.matched_interest_ids}
    active_interest_ids = {profile.topic for profile in _default_interest_profiles()}
    novel_relevant = sum(
        1 for item in item_rows if item.novelty_score >= 0.70 and item.user_action in positive_user_actions
    )
    return BriefingInterestSummary(
        sample_count=len(texts),
        item_count=item_count,
        precision_at_5=round(sum(precision_scores) / len(precision_scores), 4),
        action_rate=round(action_rate, 4),
        dismiss_rate=round(dismiss_rate, 4),
        interest_coverage=round(len(matched_interest_ids) / max(len(active_interest_ids), 1), 4),
        novel_but_relevant_rate=round(novel_relevant / total_items, 4),
        feedback_item_count=len(feedback_items),
        promoted_item_count=sum(1 for item in feedback_items if item.user_action in promoted_user_actions),
        blind_sample_count=len(build_weekly_blind_sample(records)),
        feedback_coverage_rate=round(len(feedback_items) / total_items, 4),
    )


def build_briefing_item_reviews(records: list):
    feedback_by_item = _briefing_feedback_by_item(records)
    review_type = BriefingItemEval
    reviews = []
    for record in records:
        if getattr(record, "pipeline", "") != "intelligence_briefing":
            continue
        for text in _readable_artifact_texts(getattr(record, "artifacts", [])):
            for index, item in enumerate(_briefing_items(text), start=1):
                item_id = _briefing_item_id(record.id, index, item)
                topics = _briefing_item_topics(item)
                reviews.append(
                    review_type(
                        item_id=item_id,
                        text=item,
                        topics=topics,
                        matched_interest_ids=[f"interest:{topic}" for topic in topics],
                        novelty_score=_briefing_item_novelty_score(item),
                        actionability_score=1.0 if _is_actionable_briefing_item(item) else 0.0,
                        user_action=feedback_by_item.get(item_id) or feedback_by_item.get(str(index)),
                    )
                )
    return reviews


def build_weekly_blind_sample(records: list, sample_size: int = 5):
    unreviewed = [item for item in build_briefing_item_reviews(records) if item.user_action is None]
    return sorted(unreviewed, key=lambda item: item.item_id)[:sample_size]


def build_memory_audit_records(commits: list) -> list[MemoryAuditRecord]:
    return [
        MemoryAuditRecord(
            memory_id=str(getattr(commit, "commit_id", "") or getattr(commit, "proposal_id", "")),
            source_type=str(getattr(commit, "source_trust", "observed") or "observed"),
            trust_tier=str(getattr(commit, "source_trust", "observed") or "observed"),
            privacy_tier=str(getattr(commit, "privacy_tier", "normal") or "normal"),
            evidence_refs=[str(ref) for ref in getattr(commit, "evidence_refs", []) if ref],
            audited_at=_item_timestamp(commit),
            verdict=_memory_audit_verdict(commit),
            severity=_memory_audit_severity(commit),
            action_taken=_memory_audit_action(commit),
        )
        for commit in commits
    ]


def evaluate_memory_health(commits: list, records: list | None = None) -> MemoryHealthSummary:
    audits = build_memory_audit_records(commits)
    audited = len(audits)
    if audited == 0:
        return MemoryHealthSummary(
            audited_memories=0,
            memory_precision=1.0,
            unsupported_claim_rate=0.0,
            critical_pollution_count=0,
            quarantine_recall=1.0,
            snapshot_contamination_rate=0.0,
            contaminated_snapshot_count=0,
            snapshots_audited=_snapshots_audited(records or []),
        )
    valid = sum(1 for audit in audits if audit.verdict == "valid")
    unsupported_or_false = sum(1 for audit in audits if audit.verdict in {"unsupported", "false"})
    known_bad = [audit for audit in audits if audit.verdict != "valid"]
    recalled = [
        audit for audit in known_bad if audit.action_taken in {"quarantine", "delete", "downgrade", "merge", "expire"}
    ]
    critical_pollution = sum(
        1
        for audit in audits
        if audit.verdict in {"unsafe", "false"}
        and audit.severity in {"high", "critical"}
        and audit.action_taken == "keep"
    )
    contaminated_snapshots = _snapshot_contamination_count(records or [], audits)
    snapshots_audited = _snapshots_audited(records or [])
    return MemoryHealthSummary(
        audited_memories=audited,
        memory_precision=round(valid / max(audited, 1), 4),
        unsupported_claim_rate=round(unsupported_or_false / max(audited, 1), 4),
        critical_pollution_count=critical_pollution,
        quarantine_recall=round(len(recalled) / max(len(known_bad), 1), 4),
        snapshot_contamination_rate=round(contaminated_snapshots / max(snapshots_audited, 1), 4),
        contaminated_snapshot_count=contaminated_snapshots,
        snapshots_audited=snapshots_audited,
    )


def _record_artifact_texts(records: list, pipelines: set[str]) -> list[str]:
    texts: list[str] = []
    for record in records:
        if getattr(record, "pipeline", "") not in pipelines:
            continue
        texts.extend(_readable_artifact_texts(getattr(record, "artifacts", []))[:1])
    return texts


def _readable_artifact_texts(paths: Iterable[str]) -> list[str]:
    texts: list[str] = []
    for raw_path in paths:
        text = _readable_artifact_text(raw_path)
        if text:
            texts.append(text)
    return texts


def _first_readable_artifact_text(paths: Iterable[str]) -> str:
    for raw_path in paths:
        text = _readable_artifact_text(raw_path)
        if text:
            return text
    return ""


def _readable_artifact_text(raw_path: str) -> str:
    path = Path(str(raw_path))
    if not path.exists() or path.suffix.lower() not in {".md", ".txt"}:
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text.strip()


def _voice_score(text: str) -> float:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'.-]*", text)
    lowered = text.lower()
    if not words:
        return 0.0
    specific_markers = len(
        {
            marker
            for marker in re.findall(
                r"\b(mira|v3\.?1|ledger|effect log|approval|causal|trace|artifact|substack|a2a|memory|kernel|skill|receipt|receipts)\b",
                lowered,
            )
        }
    )
    stance_markers = len(re.findall(r"\b(i|we|should|useful|threshold|not|because|before|after|therefore)\b", lowered))
    concrete_markers = len(
        re.findall(r"\b(artifact|ledger|effect|approval|trace|receipts?|source|failure|workflow)\b", lowered)
    )
    score = 0.45
    score += min(specific_markers, 5) * 0.05
    score += min(stance_markers, 4) * 0.05
    score += min(concrete_markers, 4) * 0.04
    if len(words) < 25:
        score -= 0.10
    if len(words) > 1200:
        score -= 0.05
    if _has_generic_ai_markers(text):
        score -= 0.25
    return round(max(0.0, min(score, 1.0)), 4)


def _has_generic_ai_markers(text: str) -> bool:
    return bool(
        re.search(
            r"\b(as an ai|in conclusion|delve into|unlock the power|ever-evolving|game[- ]changer|seamless|robust solution|it is important to note)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _briefing_items(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*[-*]\s+", line) and re.search(r"\[(observed|verified|inferred|reported)\]", line, re.I)
    ]


def _matches_interest_profile(item: str) -> bool:
    return bool(
        re.search(
            r"\b(a2a|agent|agents|memory|trust|security|poisoning|ai|llm|workflow|durable|causal|eval|evaluation|research|code|substack|market|macro|portfolio)\b",
            item,
            flags=re.IGNORECASE,
        )
    )


def _is_actionable_briefing_item(item: str) -> bool:
    has_trust_label = bool(re.search(r"\[(observed|verified|inferred|reported)\]", item, flags=re.IGNORECASE))
    has_source_ref = bool(re.search(r"\([^)]+\)", item))
    has_concrete_signal = _matches_interest_profile(item)
    return has_trust_label and has_source_ref and has_concrete_signal


def _default_interest_profiles() -> list[InterestProfile]:
    now = datetime.now(timezone.utc)
    topics = [
        ("a2a", 1.0, "explicit"),
        ("agent", 0.95, "long_term"),
        ("memory", 0.95, "long_term"),
        ("security", 0.85, "recent_behavior"),
        ("workflow", 0.80, "inferred"),
        ("causal", 0.80, "inferred"),
        ("eval", 0.75, "inferred"),
        ("market", 0.65, "recent_behavior"),
    ]
    return [
        InterestProfile(
            topic=topic, weight=weight, source=source, evidence_refs=["v3.1_interest_profile"], last_updated=now
        )
        for topic, weight, source in topics
    ]


def _briefing_item_topics(item: str) -> list[str]:
    lowered = item.lower()
    return [profile.topic for profile in _default_interest_profiles() if profile.topic in lowered]


def _briefing_item_novelty_score(item: str) -> float:
    lowered = item.lower()
    if "[reported]" in lowered or "[inferred]" in lowered:
        return 0.80
    if "[observed]" in lowered:
        return 0.65
    return 0.55


def _briefing_item_id(run_id: str, index: int, item: str) -> str:
    digest = hashlib.sha1(f"{run_id}:{index}:{item}".encode("utf-8")).hexdigest()[:12]
    return f"briefing_item:{run_id}:{index}:{digest}"


def _briefing_feedback_by_item(records: list) -> dict[str, BriefingUserAction]:
    feedback: dict[str, BriefingUserAction] = {}
    for record in records:
        for ref in getattr(record, "eval_refs", []):
            parsed = _parse_briefing_feedback_ref(str(ref))
            if parsed:
                item_id, action = parsed
                feedback[item_id] = action
    return feedback


def _parse_briefing_feedback_ref(ref: str) -> tuple[str, BriefingUserAction] | None:
    if not ref.startswith("briefing_feedback:"):
        return None
    body = ref.removeprefix("briefing_feedback:")
    keyed = re.match(r"item(?:_id)?=(.+):(button|action)=([^:]+)$", body)
    if keyed:
        action = _briefing_feedback_action(keyed.group(3))
        return (keyed.group(1), action) if action else None
    parts = [part for part in body.split(":") if part]
    values: dict[str, str] = {}
    positional: list[str] = []
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            values[key.strip()] = value.strip()
        else:
            positional.append(part.strip())
    item_id = values.get("item") or values.get("item_id") or (positional[0] if positional else "")
    raw_action = values.get("action") or values.get("button") or (positional[1] if len(positional) > 1 else "")
    action = _briefing_feedback_action(raw_action)
    if not item_id or action is None:
        return None
    return item_id, action


def _briefing_feedback_action(value: str) -> BriefingUserAction | None:
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    return BRIEFING_FEEDBACK_ACTIONS.get(normalized)


def _memory_audit_verdict(commit) -> MemoryAuditVerdict:
    finding_types = {
        str(getattr(finding, "finding_type", "") or getattr(finding, "check", ""))
        for finding in getattr(commit, "findings", [])
    }
    checks = {str(getattr(finding, "check", "")) for finding in getattr(commit, "findings", [])}
    tokens = finding_types | checks
    if tokens & {"secret_detected", "prompt_injection", "policy_bypass", "privacy_violation", "pii_detected"}:
        return "unsafe"
    if tokens & {"causal_claim_unverified", "unsupported_claim", "untrusted_source", "evidence_ref", "source_trust"}:
        return "unsupported"
    if tokens & {"contradiction"}:
        return "false"
    if tokens & {"duplicate", "duplicate_memory"}:
        return "duplicative"
    if tokens & {"stale", "expired"}:
        return "stale"
    if getattr(commit, "status", "") in {"quarantined", "rejected"}:
        return "unsafe"
    return "valid"


def _memory_audit_action(commit) -> MemoryAuditAction:
    status = str(getattr(commit, "status", ""))
    if status == "quarantined":
        return "quarantine"
    if status == "rejected":
        return "delete"
    if status == "requires_human":
        return "quarantine"
    if any(getattr(finding, "decision", "") == "redact" for finding in getattr(commit, "findings", [])):
        return "downgrade"
    if _memory_audit_verdict(commit) == "duplicative":
        return "merge"
    return "keep"


def _memory_audit_severity(commit) -> Literal["low", "medium", "high", "critical"]:
    severities = [str(getattr(finding, "severity", "") or "") for finding in getattr(commit, "findings", [])]
    risk_level = str(getattr(commit, "risk_level", "") or "")
    if risk_level in {"critical", "high", "medium", "low"}:
        severities.append(risk_level)
    if getattr(commit, "status", "") in {"quarantined", "rejected", "requires_human"}:
        severities.append("high")
    order = {"": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    chosen = max(severities or ["low"], key=lambda value: order.get(value, 0))
    if chosen in {"medium", "high", "critical"}:
        return chosen  # type: ignore[return-value]
    return "low"


def _snapshots_audited(records: list) -> int:
    return sum(
        1 for record in records if getattr(record, "memory_commit_id", None) or getattr(record, "causal_links", [])
    )


def _snapshot_contamination_count(records: list, audits: list[MemoryAuditRecord]) -> int:
    contaminating_ids = {
        audit.memory_id
        for audit in audits
        if audit.action_taken == "keep"
        and (audit.verdict != "valid" or audit.trust_tier in {"untrusted", "low", "external_prompt"})
    }
    if not contaminating_ids:
        return 0
    contaminated = 0
    for record in records:
        refs = {
            str(getattr(record, "memory_commit_id", "") or ""),
            *[str(ref) for ref in getattr(record, "causal_links", [])],
        }
        if refs & contaminating_ids:
            contaminated += 1
    return contaminated


def _latest_effects(effects: list) -> list:
    latest: dict[str, object] = {}
    for effect in effects:
        key = getattr(effect, "idempotency_key", "") or getattr(effect, "effect_id", "")
        latest[str(key)] = effect
    return list(latest.values())


def _scar_opportunity_causal_memory(records: list) -> tuple[float, int, int]:
    """Measure Eval 2 against runs where a matching prior scar exists."""

    seen_scars_by_pipeline: dict[str, set[str]] = {}
    opportunities = 0
    changed = 0
    for record in sorted(
        records, key=lambda item: getattr(item, "timestamp", datetime.min.replace(tzinfo=timezone.utc))
    ):
        if seen_scars_by_pipeline.get(record.pipeline) and _is_causal_memory_opportunity(record):
            opportunities += 1
            if record.causal_links:
                changed += 1
        for action in getattr(record.delta, "actions", []):
            scar_pipeline = _scar_pipeline(str(action.target))
            if scar_pipeline:
                seen_scars_by_pipeline.setdefault(scar_pipeline, set()).add(str(action.target))
    if opportunities == 0:
        return 1.0, 0, 0
    return changed / opportunities, opportunities, changed


def _is_causal_memory_opportunity(record) -> bool:
    if getattr(record, "causal_links", []):
        return True
    if getattr(record, "artifacts", []) or getattr(record, "side_effect_refs", []):
        return True
    if getattr(record, "trigger", "") in {"task_result", "background_job"}:
        return False
    return True


def _scar_pipeline(target: str) -> str | None:
    if not target.startswith("scar:"):
        return None
    parts = target.split(":")
    if len(parts) < 3 or not parts[1]:
        return None
    return parts[1]


def _unapproved_high_risk_effect(effect) -> bool:
    if getattr(effect, "status", "") not in {"executing", "started", "succeeded", "reconciled_succeeded"}:
        return False
    action_text = " ".join(
        str(getattr(effect, field, "") or "") for field in ("action", "action_type", "step_id", "idempotency_key")
    )
    if not _effect_action_requires_approval(action_text):
        return False
    return not (getattr(effect, "approval_token_id", None) and getattr(effect, "preview_hash", ""))


def _unreplayable_important_effect(effect) -> bool:
    return _effect_requires_replay_bundle(effect) and not getattr(effect, "replay_bundle_ref", "")


def _invalid_replay_bundle_effect(effect) -> bool:
    if not _effect_requires_replay_bundle(effect):
        return False
    ref = str(getattr(effect, "replay_bundle_ref", "") or "")
    if not ref:
        return False
    if not _is_local_replay_bundle_ref(ref):
        return False
    path = Path(ref)
    if not path.exists() or not path.is_file():
        return True
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return True
    required = {
        "run_id",
        "pipeline",
        "action_type",
        "target",
        "idempotency_key",
        "payload_hash",
        "payload",
        "compensation",
    }
    if not isinstance(bundle, dict) or any(key not in bundle for key in required):
        return True
    if bundle.get("idempotency_key") != getattr(effect, "idempotency_key", ""):
        return True
    compensation = bundle.get("compensation")
    return not isinstance(compensation, dict) or not compensation.get("strategy")


def _is_local_replay_bundle_ref(ref: str) -> bool:
    if ref.startswith(("http://", "https://", "s3://", "gs://", "replay:", "recovered:")):
        return False
    return ref.endswith(".json") or "/" in ref or ref.startswith(".")


def _effect_requires_replay_bundle(effect) -> bool:
    if getattr(effect, "status", "") not in {
        "planned",
        "executing",
        "started",
        "succeeded",
        "failed",
        "unknown",
        "reconciled_succeeded",
        "reconciled_failed",
    }:
        return False
    action_text = " ".join(
        str(getattr(effect, field, "") or "") for field in ("action", "action_type", "step_id", "idempotency_key")
    )
    return bool(
        re.search(
            r"(publish|post|tweet|upload|rss|substack|compact|archive|memory|delete|rollback|promote|deploy|production)",
            action_text,
            flags=re.IGNORECASE,
        )
    )


def _is_completed_effect(effect) -> bool:
    return getattr(effect, "status", "") in {"succeeded", "reconciled_succeeded"}


def _is_effect_incident(effect) -> bool:
    return getattr(effect, "status", "") in {"failed", "unknown", "reconciled_failed"} or _unapproved_high_risk_effect(
        effect
    )


def _effect_incident_severity(
    effect, action_text: str, unapproved_high_risk: bool
) -> Literal["low", "medium", "high", "critical"]:
    if unapproved_high_risk:
        return "critical" if _is_publication_effect(effect) else "high"
    if getattr(effect, "status", "") == "unknown":
        return "high" if _effect_action_requires_approval(action_text) else "medium"
    if _is_publication_effect(effect):
        return "high"
    return "medium"


def _record_incident_severity(record, mode: str) -> Literal["low", "medium", "high", "critical"]:
    if mode in {"provider_unavailable", "effect_reconciliation_required"}:
        return "high"
    if mode in {"approval_prompt", "preflight_blocked", "preflight_failed"}:
        return "low"
    if _is_publication_record(record):
        return "high"
    return "medium"


def _is_publication_record(record) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "pipeline", ""),
            getattr(record, "outcome", ""),
            getattr(getattr(record, "delta", None), "what_failed", ""),
            getattr(getattr(record, "delta", None), "what_changed", ""),
            getattr(getattr(record, "delta", None), "what_happened", ""),
        )
    )
    return bool(re.search(r"(publish|post|rss|substack|tweet|upload)", text, flags=re.IGNORECASE))


def _root_cause(*parts: str) -> str:
    text = " ".join(str(part or "").strip() for part in parts if str(part or "").strip())
    tokens = re.findall(r"[a-z0-9_:/.-]+", text.lower())
    if not tokens:
        return "unknown"
    return " ".join(tokens[:16])[:160]


def _is_publication_effect(effect) -> bool:
    action_text = " ".join(
        str(value or "")
        for value in (
            getattr(effect, "action", ""),
            getattr(effect, "action_type", ""),
            getattr(effect, "target", ""),
        )
    ).lower()
    return bool(re.search(r"(publish|post|rss|substack|tweet|upload)", action_text))


def _is_rollback_effect(effect) -> bool:
    action_text = " ".join(
        str(value or "")
        for value in (
            getattr(effect, "action", ""),
            getattr(effect, "action_type", ""),
            getattr(effect, "target", ""),
            getattr(effect, "detail", ""),
            getattr(effect, "status", ""),
        )
    ).lower()
    return "rollback" in action_text or getattr(effect, "status", "") == "compensated"


def _effect_action_requires_approval(action_text: str) -> bool:
    return bool(
        re.search(
            r"(publish|post|send|email|tweet|upload|webhook|rss|substack|trade|market|alert|portfolio|health|compact|archive|memory|delete|rollback|promote|deploy|production)",
            action_text,
            flags=re.IGNORECASE,
        )
    )


def _causal_link_validity(records: list, causal_evidence: list) -> float:
    causal_claims = [(record, link) for record in records for link in record.causal_links]
    if not causal_claims:
        return 1.0
    evidence_ids = {getattr(evidence, "evidence_id", "") for evidence in causal_evidence}
    valid = sum(1 for record, link in causal_claims if _has_behavioral_effect_evidence(record, str(link), evidence_ids))
    return valid / len(causal_claims)


def _has_behavioral_effect_evidence(record, link: str, evidence_ids: set[str]) -> bool:
    evidence_refs = [str(ref).lower() for ref in [*record.eval_refs, *record.side_effect_refs]]
    if link in evidence_ids:
        return True
    if link.lower().startswith(("effect:", "behavioral_effect:")):
        return True
    return any(
        ref.startswith(("causal:", "causal_evidence:", "behavioral_effect:", "ablation:")) for ref in evidence_refs
    )


def _l4_required_causal_evidence(records: list, causal_evidence: list) -> float:
    required_pipelines = {"a2a_trust_experiment", "self_evolution"}
    latest_links_by_pipeline: dict[str, list[str]] = {}
    for record in records:
        if record.pipeline in required_pipelines and getattr(record, "causal_links", []):
            latest_links_by_pipeline[record.pipeline] = [str(link) for link in record.causal_links]
    required_links = [link for links in latest_links_by_pipeline.values() for link in links]
    if not required_links:
        return 1.0
    evidence_by_id = {getattr(evidence, "evidence_id", ""): evidence for evidence in causal_evidence}
    valid = sum(
        1
        for link in required_links
        if getattr(evidence_by_id.get(link), "level", "") == "L4"
        and getattr(evidence_by_id.get(link), "ablation_ref", None)
    )
    return valid / len(required_links)


def filter_first_stage_eval_records(records: list) -> list:
    """Limit durable run evidence to the V3.1 first-stage eval workflows."""
    return [record for record in records if getattr(record, "pipeline", "") in FIRST_STAGE_EVAL_PIPELINES]


def filter_first_stage_eval_effects(effects: list) -> list:
    """Limit effect evidence to the V3.1 first-stage eval workflows."""
    return [effect for effect in effects if getattr(effect, "pipeline", "") in FIRST_STAGE_EVAL_PIPELINES]


def build_strategic_scorecard(records: list) -> StrategicNorthStarScorecard:
    a2a_records = [record for record in records if record.pipeline == "a2a_trust_experiment"]
    a2a_experiment_records = [record for record in a2a_records if getattr(record, "trigger", "") != "operator_evidence"]
    artifact_count = sum(len(record.artifacts) for record in a2a_records)
    eval_refs = [ref for record in a2a_records for ref in record.eval_refs]
    public_writeup_refs = [
        ref
        for ref in eval_refs
        if _strategic_ref_has_prefix(
            ref,
            ("public_writeup:", "public_note:", "published_writeup:"),
        )
    ]
    public_writeup_slugs = {_strategic_ref_slug(ref) for ref in public_writeup_refs}
    public_writeup_slugs.discard("")
    public_feedback_refs = [ref for ref in eval_refs if _strategic_feedback_ref_is_countable(ref, public_writeup_slugs)]
    return StrategicNorthStarScorecard(
        a2a_questions_advanced=len(a2a_records),
        a2a_experiments_completed=sum(
            1 for record in a2a_experiment_records if record.outcome not in {"failed", "blocked_preflight"}
        ),
        reproducible_artifacts=artifact_count,
        tool_prototypes=sum(
            1 for ref in eval_refs if _strategic_ref_has_prefix(ref, ("tool:", "tool_prototype:", "issue:", "package:"))
        ),
        public_writeups=len(public_writeup_refs),
        public_feedback_items=len(public_feedback_refs),
        product_thesis_updates=sum(
            1 for ref in eval_refs if _strategic_ref_has_prefix(ref, ("product_thesis:", "thesis_update:"))
        ),
        commercial_options=sum(
            1
            for ref in eval_refs
            if _strategic_ref_has_prefix(ref, ("commercial:", "revenue_signal:", "customer_discovery:"))
        ),
        public_writeup_refs=public_writeup_refs,
        public_feedback_refs=public_feedback_refs,
    )


def _strategic_ref_has_prefix(ref: str, prefixes: tuple[str, ...]) -> bool:
    normalized = ref.strip().lower()
    return any(normalized.startswith(prefix) for prefix in prefixes)


def _strategic_feedback_ref_is_countable(ref: str, public_writeup_slugs: set[str]) -> bool:
    if _strategic_ref_has_prefix(ref, ("customer_discovery:",)):
        return True
    if not _strategic_ref_has_prefix(ref, ("external_feedback:", "public_feedback:", "reader_feedback:")):
        return False
    return _strategic_ref_slug(ref) in public_writeup_slugs


def _strategic_ref_slug(ref: str) -> str:
    parts = ref.strip().split(":", 2)
    if len(parts) < 2:
        return ""
    slug = parts[1].split("=", 1)[0].strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", slug):
        return slug
    return ""


def build_weekly_north_star_report(
    records: list,
    commits: list,
    effects: list,
    causal_evidence: list | None = None,
    *,
    approval_events: list | None = None,
    week_label: str | None = None,
    window_days: int = 7,
    first_stage_scope: bool = False,
    review_queues: Mapping[str, list[Mapping[str, str]]] | None = None,
) -> str:
    """Render the V3.1 weekly north-star report from durable runtime evidence."""

    label = week_label or datetime.now(timezone.utc).date().isoformat()
    window_start, window_end = _weekly_window(label, window_days)
    previous_start = window_start - (window_end - window_start)
    current_records_all = _filter_by_timestamp(records, window_start, window_end)
    current_records = [record for record in current_records_all if not _is_synthetic_task_fixture_record(record)]
    current_commits = _filter_by_timestamp(commits, window_start, window_end)
    current_effects_all = _filter_by_timestamp(effects, window_start, window_end)
    current_causal_evidence = _filter_by_timestamp(causal_evidence or [], window_start, window_end)
    current_approval_events = _filter_by_timestamp(approval_events or [], window_start, window_end)
    previous_records_all = _filter_by_timestamp(records, previous_start, window_start)
    previous_records = [record for record in previous_records_all if not _is_synthetic_task_fixture_record(record)]
    previous_commits = _filter_by_timestamp(commits, previous_start, window_start)
    previous_effects = _filter_by_timestamp(effects, previous_start, window_start)
    previous_causal_evidence = _filter_by_timestamp(causal_evidence or [], previous_start, window_start)
    previous_approval_events = _filter_by_timestamp(approval_events or [], previous_start, window_start)
    current_synthetic_record_count = len(current_records_all) - len(current_records)
    previous_synthetic_record_count = len(previous_records_all) - len(previous_records)
    current_scope_excluded_count = 0
    previous_scope_excluded_count = 0
    full_records = records
    full_effects = effects
    if first_stage_scope:
        current_records_before_scope = list(current_records)
        previous_records_before_scope = list(previous_records)
        current_records = filter_first_stage_eval_records(current_records)
        previous_records = filter_first_stage_eval_records(previous_records)
        current_effects_all = filter_first_stage_eval_effects(current_effects_all)
        previous_effects = filter_first_stage_eval_effects(previous_effects)
        full_records = filter_first_stage_eval_records(records)
        full_effects = filter_first_stage_eval_effects(effects)
        current_scope_excluded_count = len(current_records_before_scope) - len(current_records)
        previous_scope_excluded_count = len(previous_records_before_scope) - len(previous_records)
    operational = build_operational_eval_bundle(
        current_records,
        current_commits,
        current_effects_all,
        current_causal_evidence,
        current_approval_events,
    )
    previous_operational = build_operational_eval_bundle(
        previous_records,
        previous_commits,
        previous_effects,
        previous_causal_evidence,
        previous_approval_events,
    )
    full_operational = build_operational_eval_bundle(
        full_records, commits, full_effects, causal_evidence or [], approval_events or []
    )
    strategic = build_strategic_scorecard(current_records)
    voice = evaluate_voice_stability(current_records)
    briefing_interest = evaluate_briefing_interest_fit(current_records)
    current_effects = _latest_effects(current_effects_all)
    previous_effects_latest = _latest_effects(previous_effects)
    total = max(len(current_records), 1)
    failed_records = [record for record in current_records if _is_failure_record(record)]
    approval_gate_records = [record for record in current_records if _is_approval_gate_record(record)]
    near_miss_records = [record for record in current_records if _is_near_miss_record(record)]
    failure_memory_captures = sum(1 for record in failed_records if _captures_failure_memory(record))
    failure_reduction = evaluate_failure_reduction(current_records)
    previous_failure_reduction = evaluate_failure_reduction(previous_records)
    causal_records = [record for record in current_records if record.causal_links]
    baseline_artifacts = sum(
        1
        for record in current_records
        if not getattr(record, "causal_links", []) and _is_baseline_artifact_record(record)
    )
    raw_causal_coverage = len(causal_records) / max(len(current_records), 1)
    _, causal_opportunities, causal_changes = _scar_opportunity_causal_memory(current_records)
    polluted_commits = [commit for commit in current_commits if commit.status in {"quarantined", "rejected"}]
    memory_health_summary = evaluate_memory_health(current_commits, current_records)
    self_evolution_records = [
        record for record in current_records if record.pipeline in {"self_evolution", "a2a_trust_experiment"}
    ]
    experiment_registry = build_experiment_registry(current_records, current_effects_all)
    approval_requests = sum(1 for effect in current_effects if getattr(effect, "approval_token_id", None))
    previous_approval_requests = sum(
        1 for effect in previous_effects_latest if getattr(effect, "approval_token_id", None)
    )
    approval_event_count = len(current_approval_events) if current_approval_events else len(approval_gate_records)
    previous_approval_event_count = len(previous_approval_events)
    resolved_approval_events = [
        event for event in current_approval_events if getattr(event, "decision", "pending") != "pending"
    ]
    resolved_decisions = max(len(resolved_approval_events), 1)
    approval_human_minutes = sum(
        float(getattr(event, "human_minutes", 0.0) or 0.0) for event in resolved_approval_events
    )
    approval_median_minutes = _median(
        [float(getattr(event, "human_minutes", 0.0) or 0.0) for event in resolved_approval_events]
    )
    rejection_rate = (
        sum(1 for event in resolved_approval_events if getattr(event, "decision", "") == "rejected")
        / resolved_decisions
    )
    edit_after_approval_rate = (
        sum(1 for event in resolved_approval_events if getattr(event, "decision", "") == "edited") / resolved_decisions
    )
    expired_approval_count = sum(1 for event in resolved_approval_events if getattr(event, "decision", "") == "expired")
    side_effects = len(current_effects)
    previous_side_effects = len(previous_effects_latest)
    completed_side_effects = sum(1 for effect in current_effects if _is_completed_effect(effect))
    previous_completed_side_effects = sum(1 for effect in previous_effects_latest if _is_completed_effect(effect))
    completed_publication_side_effects = sum(
        1 for effect in current_effects if _is_completed_effect(effect) and _is_publication_effect(effect)
    )
    effect_incidents = [effect for effect in current_effects if _is_effect_incident(effect)]
    previous_effect_incidents = [effect for effect in previous_effects_latest if _is_effect_incident(effect)]
    incident_events = build_incident_events(current_records, current_effects_all)
    previous_incident_events = build_incident_events(previous_records, previous_effects)
    high_critical_incidents = [event for event in incident_events if event.severity in {"high", "critical"}]
    preventable_incidents = [event for event in incident_events if event.preventable]
    incident_rate = _per_100(len(incident_events), side_effects) / 100
    previous_incident_rate = _per_100(len(previous_incident_events), previous_side_effects) / 100
    unknown_effects = [effect for effect in current_effects if effect.status == "unknown"]
    unapproved_high_risk = [effect for effect in current_effects if _unapproved_high_risk_effect(effect)]
    unreplayable_effects = [effect for effect in current_effects if _unreplayable_important_effect(effect)]
    invalid_replay_bundles = [effect for effect in current_effects if _invalid_replay_bundle_effect(effect)]
    important_replay_effects = [effect for effect in current_effects if _effect_requires_replay_bundle(effect)]
    replay_bundle_refs = [effect for effect in important_replay_effects if getattr(effect, "replay_bundle_ref", "")]
    unreplayable_action_rate = len(unreplayable_effects) / max(len(important_replay_effects), 1)
    replay_bundle_validity = 1.0 - min(len(invalid_replay_bundles) / max(len(replay_bundle_refs), 1), 1.0)
    rollback_count = sum(1 for effect in current_effects if _is_rollback_effect(effect))
    traced_records = [
        record
        for record in current_records
        if record.memory_commit_id
        or record.side_effect_refs
        or record.artifacts
        or record.delta.status == "no_kernel_change"
    ]
    causal_traces = build_causal_traces(current_records, current_effects_all)
    incomplete_important_traces = [trace for trace in causal_traces if trace.completeness_score < 0.95]
    hard_gates = [
        *operational.scorecard.hard_gate_failures,
        *strategic.hard_gate_failures,
        *[f"lifetime:{failure}" for failure in full_operational.scorecard.hard_gate_failures],
    ]
    hard_gate_text = "PASS" if not hard_gates else "FAIL (" + ", ".join(hard_gates) + ")"
    watch_gates = _north_star_watch_gates(strategic, briefing_interest, review_queues)
    watch_gate_text = "PASS" if not watch_gates else "WATCH (" + ", ".join(watch_gates) + ")"
    causal_by_level = _causal_counts(current_causal_evidence)
    metric_failures = [metric for metric in operational.metrics if not metric.passed]
    experiment_eval_passed = (
        operational.scorecard.self_evolution >= 1.0
        and not strategic.hard_gate_failures
        and experiment_registry.experiment_coverage >= 1.0
        and experiment_registry.untracked_change_count == 0
        and experiment_registry.testability_rate >= 0.9
        and experiment_registry.auto_change_without_rollback_count == 0
        and experiment_registry.high_risk_without_approval_count == 0
        and experiment_registry.eval_threshold_policy_violation_count == 0
    )
    top_regressions = _top_regressions(
        metric_failures,
        failed_records,
        polluted_commits,
        unknown_effects,
        unapproved_high_risk,
    )
    failure_modes = _failure_mode_breakdown(failed_records)
    incident_modes = _incident_mode_breakdown(incident_events)
    causal_coverage_gaps = _causal_coverage_gaps(current_records)
    new_scars = _recent_action_targets(current_records, "scar:")
    new_experiments = _recent_experiment_refs(current_records)
    lines = [
        f"# Mira North Star Eval - Week {label}",
        "",
        "## Summary",
        f"Window: {window_start.date().isoformat()} to {(window_end.date()).isoformat()} exclusive",
        f"Eval scope: {'first-stage workflows' if first_stage_scope else 'all non-synthetic records'}",
        f"Current records: {len(current_records)}",
        f"Previous-window records: {len(previous_records)}",
        f"Synthetic task fixture records excluded: {current_synthetic_record_count} current / {previous_synthetic_record_count} previous",
        f"First-stage scope records excluded: {current_scope_excluded_count} current / {previous_scope_excluded_count} previous",
        f"North Star Score: {round(operational.scorecard.score * 100):.0f}/100",
        f"Strategic Score: {round(strategic.score * 100):.0f}/100",
        f"Hard Gates: {hard_gate_text}",
        f"Watch Gates: {watch_gate_text}",
        f"Eval records: {len(operational.eval_records)}",
        f"Outcome records: {len(operational.outcome_records)}",
        f"Decision records: {len(operational.decision_records)}",
        f"Behavioral effects: {len(operational.behavioral_effects)}",
        f"Approval events: {len(operational.approval_events)}",
        f"Run evidence bundles: {len(operational.run_evidence_bundles)}",
        "",
        "## 1. Repeated Errors",
        f"- failed_or_failure_delta_runs: {len(failed_records)} / {len(current_records)}",
        f"- approval_required_safety_gates: {len(approval_gate_records)}",
        f"- failure_memory_captured: {failure_memory_captures} / {len(failed_records)}",
        f"- failure_signatures_tracked: {failure_reduction.signature_count}",
        f"- failure_events: {failure_reduction.failure_event_count}",
        f"- repeat_error_rate: {_metric_change(failure_reduction.repeat_error_rate, previous_failure_reduction.repeat_error_rate)}",
        f"- post_scar_recurrence_rate: {_metric_change(failure_reduction.post_scar_recurrence_rate, previous_failure_reduction.post_scar_recurrence_rate)}",
        f"- scar_prevention_rate: {_metric_change(failure_reduction.scar_prevention_rate, previous_failure_reduction.scar_prevention_rate)}",
        f"- repeated_error_score: {_metric_change(operational.scorecard.repeated_error, previous_operational.scorecard.repeated_error)}",
        f"- high_severity_repeat_failures: {failure_reduction.high_severity_repeat_failures}",
        f"Verdict: {_pass_fail(operational.scorecard.repeated_error >= 0.8 and (failure_reduction.signature_count == 0 or failure_reduction.passed))}",
        "",
        "## 2. Past Failure Changed Strategy",
        f"- records_with_causal_links: {len(causal_records)} / {len(current_records)}",
        f"- raw_causal_link_coverage: {raw_causal_coverage:.4f}",
        f"- baseline_artifacts_without_causal_links: {baseline_artifacts}",
        f"- matching_scar_opportunities: {causal_opportunities}",
        f"- scar_opportunities_changed_strategy: {causal_changes}",
        f"- causal_memory_score: {_metric_change(operational.scorecard.causal_memory, previous_operational.scorecard.causal_memory)}",
        f"- causal_link_validity: {_metric_change(operational.scorecard.causal_link_validity, previous_operational.scorecard.causal_link_validity)}",
        f"- L4_evidence_count: {causal_by_level.get('L4', 0)}",
        f"Verdict: {_pass_fail(operational.scorecard.causal_memory >= 0.5 and operational.scorecard.causal_link_validity >= 0.70)}",
        "",
        "## 3. Writing Voice Stability",
        f"- article_or_social_records: {_pipeline_count(current_records, {'article_creation', 'social_reactive', 'social_proactive'})}",
        f"- voice_samples: {voice.sample_count}",
        f"- voice_score_mean: {_measured_metric(voice.sample_count, voice.voice_score_mean)}",
        f"- voice_score_std: {_measured_metric(voice.sample_count, voice.voice_score_std)}",
        f"- generic_failure_rate: {_measured_metric(voice.sample_count, voice.generic_failure_rate)}",
        f"Verdict: {_measured_verdict(voice.sample_count, voice.passed)}",
        "",
        "## 4. Briefing Interest Fit",
        f"- briefing_records: {_pipeline_count(current_records, {'intelligence_briefing'})}",
        f"- briefing_samples: {briefing_interest.sample_count}",
        f"- briefing_items_scored: {briefing_interest.item_count}",
        f"- precision_at_5: {_measured_metric(briefing_interest.item_count, briefing_interest.precision_at_5)}",
        f"- action_rate: {_feedback_metric(briefing_interest.feedback_item_count, briefing_interest.action_rate)}",
        f"- dismiss_rate: {_feedback_metric(briefing_interest.feedback_item_count, briefing_interest.dismiss_rate)}",
        f"- interest_coverage: {_measured_metric(briefing_interest.item_count, briefing_interest.interest_coverage)}",
        f"- novel_but_relevant_rate: {_feedback_metric(briefing_interest.feedback_item_count, briefing_interest.novel_but_relevant_rate)}",
        f"- feedback_items: {briefing_interest.feedback_item_count}",
        f"- feedback_coverage_rate: {_feedback_metric(briefing_interest.feedback_item_count, briefing_interest.feedback_coverage_rate)}",
        f"- promoted_items: {briefing_interest.promoted_item_count}",
        f"- weekly_blind_sample_items: {briefing_interest.blind_sample_count}",
        f"Verdict: {_measured_verdict(briefing_interest.feedback_item_count, briefing_interest.passed)}",
        "",
        "## 5. Self-Evolution Experiments",
        f"- self_evolution_or_a2a_records: {len(self_evolution_records)}",
        f"- experiment_records: {len(experiment_registry.experiments)}",
        f"- self_evolution_change_count: {experiment_registry.self_evolution_change_count}",
        f"- experiment_coverage: {experiment_registry.experiment_coverage:.4f}",
        f"- testability_rate: {experiment_registry.testability_rate:.4f}",
        f"- conclusion_rate: {experiment_registry.conclusion_rate:.4f}",
        f"- rollback_rate: {experiment_registry.rollback_rate:.4f}",
        f"- untracked_change_count: {experiment_registry.untracked_change_count}",
        f"- auto_change_without_rollback_count: {experiment_registry.auto_change_without_rollback_count}",
        f"- high_risk_without_approval_count: {experiment_registry.high_risk_without_approval_count}",
        f"- eval_threshold_change_count: {experiment_registry.eval_threshold_change_count}",
        f"- eval_threshold_policy_violation_count: {experiment_registry.eval_threshold_policy_violation_count}",
        f"- strategic_a2a_experiments_completed: {strategic.a2a_experiments_completed}",
        f"- reproducible_artifacts: {strategic.reproducible_artifacts}",
        f"- tool_prototypes: {strategic.tool_prototypes}",
        f"- public_writeups: {strategic.public_writeups}",
        f"- external_feedback_events: {strategic.public_feedback_items}",
        f"- product_thesis_updates: {strategic.product_thesis_updates}",
        f"Verdict: {_pass_fail(experiment_eval_passed)}",
        "",
        "## 6. Approval Burden + Safety",
        f"- approval_requests_per_100_runs: {_per_100(approval_event_count, len(current_records)):.2f}",
        f"- approval_requests_per_100_runs_change: {_metric_change(_per_100(approval_event_count, len(current_records)) / 100, _per_100(previous_approval_event_count, len(previous_records)) / 100)}",
        f"- approvals_per_100_side_effects: {_per_100(approval_requests, side_effects):.2f}",
        f"- approvals_per_100_side_effects_change: {_metric_change(_per_100(approval_requests, side_effects) / 100, _per_100(previous_approval_requests, previous_side_effects) / 100)}",
        f"- median_approval_time_minutes: {_measured_metric(len(resolved_approval_events), approval_median_minutes)}",
        f"- human_minutes_per_week: {_measured_metric(len(resolved_approval_events), approval_human_minutes)}",
        f"- edit_after_approval_rate: {_measured_metric(len(resolved_approval_events), edit_after_approval_rate)}",
        f"- rejection_rate: {_measured_metric(len(resolved_approval_events), rejection_rate)}",
        f"- expired_approvals: {expired_approval_count}",
        f"- side_effects_completed_per_week: {completed_side_effects}",
        f"- side_effect_throughput_change: {previous_completed_side_effects} -> {completed_side_effects}",
        f"- publication_side_effects_completed_per_week: {completed_publication_side_effects}",
        f"- side_effect_incident_rate_per_100: {_per_100(len(effect_incidents), side_effects):.2f}",
        f"- side_effect_incident_rate_change: {_metric_change(_per_100(len(effect_incidents), side_effects) / 100, _per_100(len(previous_effect_incidents), previous_side_effects) / 100)}",
        f"- incident_events: {len(incident_events)}",
        f"- incident_rate_per_100_side_effects: {incident_rate * 100:.2f}",
        f"- incident_rate_change: {_metric_change(incident_rate, previous_incident_rate)}",
        f"- high_or_critical_incidents: {len(high_critical_incidents)}",
        f"- preventable_incidents: {len(preventable_incidents)}",
        f"- near_miss_rate_per_100_side_effects: {_per_100(len(near_miss_records), side_effects):.2f}",
        f"- unknown_effects: {len(unknown_effects)}",
        f"- unapproved_high_risk_actions: {len(unapproved_high_risk)}",
        f"- unreplayable_action_rate: {unreplayable_action_rate:.4f}",
        f"- unreplayable_actions: {len(unreplayable_effects)} / {len(important_replay_effects)}",
        f"- replay_bundle_validity: {replay_bundle_validity:.4f}",
        f"- invalid_replay_bundles: {len(invalid_replay_bundles)} / {len(replay_bundle_refs)}",
        f"- rollback_count: {rollback_count}",
        f"- approval_safety_score: {operational.scorecard.approval_safety:.4f}",
        f"Verdict: {_pass_fail(operational.scorecard.approval_safety >= 0.95 and not unapproved_high_risk and not unreplayable_effects and not invalid_replay_bundles and not high_critical_incidents and incident_rate <= previous_incident_rate)}",
        "",
        "## 7. Memory Health",
        f"- audited_memory_commits: {len(current_commits)}",
        f"- audited_memories: {memory_health_summary.audited_memories}",
        f"- memory_precision: {memory_health_summary.memory_precision:.4f}",
        f"- unsupported_claim_rate: {memory_health_summary.unsupported_claim_rate:.4f}",
        f"- quarantine_recall: {memory_health_summary.quarantine_recall:.4f}",
        f"- snapshots_audited: {memory_health_summary.snapshots_audited}",
        f"- snapshot_contamination_rate: {memory_health_summary.snapshot_contamination_rate:.4f}",
        f"- contaminated_snapshots: {memory_health_summary.contaminated_snapshot_count}",
        f"- polluted_commits: {len(polluted_commits)}",
        f"- memory_health_score: {_metric_change(operational.scorecard.memory_health, previous_operational.scorecard.memory_health)}",
        f"- critical_pollution: {memory_health_summary.critical_pollution_count}",
        f"- gateway_blocked_commits: {operational.scorecard.critical_memory_pollution}",
        f"Verdict: {_pass_fail(operational.scorecard.memory_health >= 0.95 and memory_health_summary.passed)}",
        "",
        "## 8. Causal Trace",
        f"- traced_records: {len(traced_records)} / {len(current_records)}",
        f"- traceability_score: {_metric_change(operational.scorecard.traceability, previous_operational.scorecard.traceability)}",
        f"- important_behavior_traces: {len(causal_traces)}",
        f"- important_behavior_traces_below_95pct: {len(incomplete_important_traces)}",
        f"- important_behavior_trace_completeness: {_average_trace_score(causal_traces):.4f}",
        f"- orphan_important_actions: {operational.scorecard.orphan_important_action}",
        f"- l4_required_causal_evidence: {operational.scorecard.l4_required_causal_evidence:.4f}",
        f"Verdict: {_pass_fail(operational.scorecard.traceability >= 0.9 and not incomplete_important_traces and operational.scorecard.orphan_important_action == 0 and operational.scorecard.l4_required_causal_evidence >= 1.0)}",
        "",
        "## Strategic North Star",
        f"- A2A trust questions advanced: {strategic.a2a_questions_advanced}",
        f"- Experiments run: {strategic.a2a_experiments_completed}",
        f"- Reproducible artifacts created: {strategic.reproducible_artifacts}",
        f"- Public writeups shipped: {strategic.public_writeups}",
        f"- Tool issues/prototypes created: {strategic.tool_prototypes}",
        f"- External feedback events: {strategic.public_feedback_items}",
        f"- Product thesis updates: {strategic.product_thesis_updates}",
        f"- Commercial options: {strategic.commercial_options}",
        f"- Strategic hard gates: {', '.join(strategic.hard_gate_failures) if strategic.hard_gate_failures else 'PASS'}",
        "",
        "## North Star Next Actions",
        *_north_star_next_action_lines(strategic, review_queues),
        "",
        "## Top Regressions",
        *[f"{idx}. {item}" for idx, item in enumerate(top_regressions, start=1)],
        "",
        "## Failure Mode Breakdown",
        *[f"- {item}" for item in (failure_modes or ["none detected"])],
        "",
        "## Incident Breakdown",
        *[f"- {item}" for item in (incident_modes or ["none detected"])],
        "",
        "## Causal Coverage Gaps",
        *[f"- {item}" for item in (causal_coverage_gaps or ["none detected"])],
        "",
        "## New Scars",
        *[f"- {item}" for item in (new_scars or ["none recorded"])],
        "",
        "## New Experiments",
        *[f"- {item}" for item in (new_experiments or ["none recorded"])],
        "",
    ]
    return "\n".join(lines)


def _north_star_next_action_lines(
    strategic: StrategicNorthStarScorecard,
    review_queues: Mapping[str, list[Mapping[str, str]]] | None,
) -> list[str]:
    actions: list[str] = []
    queues = review_queues or {}
    public_writeups = list(queues.get("public_writeup_review") or [])
    public_feedback_followups = list(queues.get("public_feedback_followup") or [])
    customer_discovery_items = list(queues.get("customer_discovery_feedback") or [])
    briefing_feedback_items = list(queues.get("briefing_feedback") or [])
    effect_items = list(queues.get("effect_reconciliation") or [])
    provider_items = list(queues.get("provider_provisioning") or [])
    if (
        strategic.public_writeups <= 0
        or strategic.public_feedback_items < 3
        or briefing_feedback_items
        or effect_items
        or provider_items
    ):
        actions.append(
            "- Use live closure status before and after operator work: `PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_status.py --actions`."
        )
    if strategic.public_writeups <= 0:
        if public_writeups:
            item = public_writeups[0]
            actions.extend(
                [
                    f"- Publish/review public writeup draft: {item.get('draft_artifact') or item.get('evidence') or 'pending draft artifact'}",
                    f"  - decision: {item.get('decision', 'needs_publication_review')}",
                ]
            )
            command = item.get("record_evidence_from_packet_command_template") or item.get(
                "record_evidence_command_template"
            )
            safety_command = item.get("publication_safety_command_template")
            packet_command = item.get("publication_packet_command_template")
            if safety_command:
                actions.append(f"  - safety audit command: `{safety_command}`")
            if packet_command:
                actions.append(f"  - publication packet command: `{packet_command}`")
            if command:
                actions.append(f"  - record evidence command: `{command}`")
        else:
            actions.append("- Publish at least one public writeup and record a `public_writeup:*` evidence ref.")
    if strategic.public_feedback_items < 3:
        remaining_feedback = 3 - strategic.public_feedback_items
        if public_feedback_followups:
            item = public_feedback_followups[0]
            actions.append(
                f"- Collect external feedback on the recorded public writeup `{item.get('slug', '')}` "
                f"(comments={item.get('comments', '0')}, likes={item.get('likes', '0')}, restacks={item.get('restacks', '0')}; "
                f"need {remaining_feedback} more feedback event{'s' if remaining_feedback != 1 else ''})."
            )
            command = item.get("record_feedback_command_template")
            packet_record_command = item.get("record_feedback_from_packet_command_template")
            packet_command = item.get("feedback_packet_command_template")
            if packet_command:
                actions.append(f"  - feedback packet command: `{packet_command}`")
            if packet_record_command:
                actions.append(f"  - record feedback from packet command: `{packet_record_command}`")
            if command:
                actions.append(f"  - record feedback command: `{command}`")
            if customer_discovery_items:
                actions.append(
                    f"- Collect parallel customer-discovery feedback while the external-feedback gate still needs {remaining_feedback} more event{'s' if remaining_feedback != 1 else ''}."
                )
                actions.extend(_customer_discovery_next_action_command_lines(customer_discovery_items[0]))
        else:
            published_slug = (
                _strategic_ref_slug(strategic.public_writeup_refs[-1]) if strategic.public_writeup_refs else ""
            )
            if published_slug:
                actions.append(
                    f"- Collect {remaining_feedback} more external feedback event{'s' if remaining_feedback != 1 else ''} on the recorded public writeup or through customer discovery, then record with `external_feedback:{published_slug}:source=<source>` or `customer_discovery:<source>`."
                )
            elif public_writeups:
                item = public_writeups[0]
                feedback_ref = item.get("feedback_ref_template")
                if feedback_ref:
                    actions.append(
                        f"- Collect {remaining_feedback} more external feedback event{'s' if remaining_feedback != 1 else ''} and record it with `{feedback_ref}`."
                    )
                else:
                    actions.append(
                        f"- Collect {remaining_feedback} more external feedback event{'s' if remaining_feedback != 1 else ''} on the public writeup and record `external_feedback:*` evidence refs."
                    )
            else:
                actions.append(
                    f"- Collect {remaining_feedback} more external feedback event{'s' if remaining_feedback != 1 else ''} and record `external_feedback:*` or `customer_discovery:*` evidence refs."
                )
            item = customer_discovery_items[0] if customer_discovery_items else {}
            actions.extend(_customer_discovery_next_action_command_lines(item))
    if briefing_feedback_items:
        item = briefing_feedback_items[0]
        item_id = item.get("item_id", "")
        queued_count = len(briefing_feedback_items)
        queue_suffix = f" ({queued_count} queued)" if queued_count > 1 else ""
        actions.append(f"- Record operator feedback on weekly briefing blind-sample item `{item_id}`{queue_suffix}.")
        packet_command = item.get("feedback_packet_command_template")
        command = item.get("record_feedback_command_template")
        packet_record_command = item.get("record_feedback_from_packet_command_template")
        if packet_command:
            actions.append(f"  - briefing feedback packet command: `{packet_command}`")
        if queued_count > 1:
            actions.append(
                "  - briefing feedback all-packets command: `PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py --all --json`"
            )
        if packet_record_command:
            actions.append(f"  - briefing feedback from packet command: `{packet_record_command}`")
        if command:
            actions.append(f"  - briefing feedback command: `{command}`")
    if effect_items:
        item = effect_items[0]
        effect_id = item.get("effect_id", "")
        status = item.get("status", "")
        replay_bundle = item.get("replay_bundle_ref", "")
        actions.append(f"- Inspect unresolved effect `{effect_id}` before retrying or reconciling provider state.")
        if status:
            actions.append(f"  - effect status: {status}")
        if replay_bundle:
            actions.append(f"  - replay bundle: {replay_bundle}")
        command = item.get("inspection_command_template")
        if command:
            actions.append(f"  - effect inspection command: `{command}`")
            actions.append(
                "  - effect external-evidence options: add `--publish-manifest <path>`, `--rss-feed <path>`, or `--provider-state-manifest <path>` when provider evidence lives outside `data/v3/provider_state`."
            )
    if provider_items:
        item = provider_items[0]
        scoped_provider = item.get("scoped_provider") or "selected provider"
        scoped_missing = item.get("scoped_missing_env_count")
        scoped_vars = item.get("scoped_missing_env_vars")
        actions.extend(
            [
                f"- Unblock provider production readiness: {item.get('missing_env_count', '0')} env vars missing across {item.get('readiness_finding_count', '0')} readiness findings.",
                f"  - env template: {item.get('env_template_artifact', '')}",
                f"  - runbook: {item.get('runbook_artifact', '')}",
            ]
        )
        if scoped_missing:
            actions.append(
                f"  - smallest canary scope: {scoped_provider} ({scoped_missing} missing env vars"
                + (f": {scoped_vars}" if scoped_vars else "")
                + ")"
            )
        readiness_command = item.get("readiness_command_template")
        env_template_command = item.get("env_template_command_template")
        scoped_env_template_command = item.get("scoped_env_template_command_template")
        if readiness_command:
            actions.append(f"  - readiness command: `{readiness_command}`")
        if env_template_command:
            actions.append(f"  - env template command: `{env_template_command}`")
        if scoped_env_template_command:
            actions.append(f"  - scoped env template command: `{scoped_env_template_command}`")
        scoped_command = item.get("scoped_readiness_command_template")
        dry_run_command = item.get("scoped_dry_run_command_template")
        canary_command = item.get("scoped_canary_command_template")
        if scoped_command:
            actions.append(f"  - scoped readiness command: `{scoped_command}`")
        if dry_run_command:
            actions.append(f"  - scoped canary dry-run command after readiness passes: `{dry_run_command}`")
        if canary_command:
            actions.append(f"  - scoped canary command after readiness passes: `{canary_command}`")
    if not actions:
        actions.append("- No queued north-star actions from dashboard review queues.")
    return actions


def _customer_discovery_next_action_command_lines(item: Mapping[str, str]) -> list[str]:
    packet_command = item.get(
        "feedback_packet_command_template",
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest --json",
    )
    packet_record_command = item.get(
        "record_feedback_from_packet_command_template",
        "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py --packet data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json --source <source> --insight <insight> --json",
    )
    record_command = item.get("record_feedback_command_template", "")
    lines = [
        f"  - customer discovery packet command: `{packet_command}`",
        f"  - customer discovery record from packet command: `{packet_record_command}`",
    ]
    if record_command:
        lines.append(f"  - customer discovery record command: `{record_command}`")
    return lines


def build_north_star_watch_gates(
    strategic: StrategicNorthStarScorecard,
    briefing_interest: BriefingInterestSummary,
    review_queues: Mapping[str, list[Mapping[str, str]]] | None,
) -> list[str]:
    gates: list[str] = []
    if strategic.public_feedback_items < 3:
        gates.append(f"external_feedback_below_standard:{strategic.public_feedback_items}/3")
    if briefing_interest.item_count > 0 and briefing_interest.feedback_item_count <= 0:
        gates.append("briefing_feedback_missing")
    elif briefing_interest.feedback_item_count > 0 and briefing_interest.promoted_item_count < 2:
        gates.append(f"briefing_promotions_below_standard:{briefing_interest.promoted_item_count}/2")
    provider_items = list((review_queues or {}).get("provider_provisioning") or [])
    if provider_items and provider_items[0].get("status") == "blocked_external":
        gates.append("provider_production_readiness_blocked")
    return gates


def _north_star_watch_gates(
    strategic: StrategicNorthStarScorecard,
    briefing_interest: BriefingInterestSummary,
    review_queues: Mapping[str, list[Mapping[str, str]]] | None,
) -> list[str]:
    return build_north_star_watch_gates(strategic, briefing_interest, review_queues)


def write_weekly_north_star_report(
    output_dir: Path | str,
    records: list,
    commits: list,
    effects: list,
    causal_evidence: list | None = None,
    *,
    approval_events: list | None = None,
    week_label: str | None = None,
    window_days: int = 7,
    first_stage_scope: bool = False,
    review_queues: Mapping[str, list[Mapping[str, str]]] | None = None,
) -> Path:
    label = week_label or datetime.now(timezone.utc).date().isoformat()
    safe_label = re.sub(r"[^0-9A-Za-z_.-]+", "-", label).strip("-") or "current"
    path = Path(output_dir) / f"north-star-week-{safe_label}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_weekly_north_star_report(
            records,
            commits,
            effects,
            causal_evidence,
            approval_events=approval_events,
            week_label=label,
            window_days=window_days,
            first_stage_scope=first_stage_scope,
            review_queues=review_queues,
        ),
        encoding="utf-8",
    )
    return path


def _pass_fail(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _measured_metric(sample_count: int, value: float) -> str:
    if sample_count <= 0:
        return "not measured by local deterministic MVP"
    return f"{value:.4f}"


def _feedback_metric(feedback_count: int, value: float) -> str:
    if feedback_count <= 0:
        return "not measured; awaiting operator feedback"
    return f"{value:.4f}"


def _measured_verdict(sample_count: int, passed: bool) -> str:
    if sample_count <= 0:
        return "WATCH"
    return _pass_fail(passed)


def _weekly_window(label: str, window_days: int) -> tuple[datetime, datetime]:
    days = max(1, int(window_days))
    try:
        end_date = datetime.fromisoformat(label).date()
    except ValueError:
        end_date = datetime.now(timezone.utc).date()
    end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc)
    end = end.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end + timedelta(days=1)
    return end - timedelta(days=days), end


def _filter_by_timestamp(items: list, start: datetime, end: datetime) -> list:
    return [item for item in items if start <= _item_timestamp(item) < end]


def _item_timestamp(item) -> datetime:
    timestamp = getattr(item, "timestamp", None)
    if timestamp is None:
        timestamp = getattr(item, "requested_at", None)
    if isinstance(timestamp, str):
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if isinstance(timestamp, datetime):
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _metric_change(current: float, previous: float) -> str:
    return f"{previous:.4f} -> {current:.4f}"


def _pipeline_count(records: Iterable, pipelines: set[str]) -> int:
    return sum(1 for record in records if record.pipeline in pipelines)


def _per_100(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return 100.0 * count / total


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _causal_counts(causal_evidence: list) -> dict[str, int]:
    counts = {"L0": 0, "L1": 0, "L2": 0, "L3": 0, "L4": 0}
    for evidence in causal_evidence:
        level = getattr(evidence, "level", "")
        if level in counts:
            counts[level] += 1
    return counts


def _average_trace_score(causal_traces: list) -> float:
    if not causal_traces:
        return 1.0
    return sum(trace.completeness_score for trace in causal_traces) / len(causal_traces)


def _top_regressions(
    metric_failures: list[EvalMetric],
    failed_records: list,
    polluted_commits: list,
    unknown_effects: list,
    unapproved_high_risk: list,
) -> list[str]:
    items: list[str] = []
    for metric in metric_failures:
        items.append(f"{metric.name} failed: {metric.detail} (score {metric.score:.4f}).")
    if failed_records:
        items.append(f"{len(failed_records)} failed or failure-delta experience records need review.")
    if polluted_commits:
        items.append(f"{len(polluted_commits)} memory commits were rejected or quarantined.")
    if unknown_effects:
        items.append(f"{len(unknown_effects)} side effects remain unknown and need reconciliation.")
    if unapproved_high_risk:
        items.append(f"{len(unapproved_high_risk)} executed high-risk effects lack approval binding.")
    return items or ["No hard regression detected in the current evidence window."]


def _failure_mode_breakdown(failed_records: list) -> list[str]:
    counts: dict[str, int] = {}
    for record in failed_records:
        mode = _failure_mode(record)
        counts[mode] = counts.get(mode, 0) + 1
    return [
        f"{mode}: {count} records" for mode, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _incident_mode_breakdown(incident_events: list[IncidentEvent]) -> list[str]:
    counts: dict[str, dict[str, int]] = {}
    for event in incident_events:
        root_cause = event.root_cause or "unknown"
        bucket = counts.setdefault(root_cause, {"events": 0, "high_critical": 0, "preventable": 0})
        bucket["events"] += 1
        if event.severity in {"high", "critical"}:
            bucket["high_critical"] += 1
        if event.preventable:
            bucket["preventable"] += 1
    ordered = sorted(
        counts.items(),
        key=lambda item: (-item[1]["events"], -item[1]["high_critical"], -item[1]["preventable"], item[0]),
    )
    return [
        f"{root_cause}: {stats['events']} events, high_or_critical={stats['high_critical']}, preventable={stats['preventable']}"
        for root_cause, stats in ordered[:10]
    ]


def _failure_mode(record) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "outcome", ""),
            getattr(getattr(record, "delta", None), "what_failed", ""),
            getattr(getattr(record, "delta", None), "what_changed", ""),
            getattr(getattr(record, "delta", None), "what_happened", ""),
        )
    ).lower()
    patterns = [
        ("approval_prompt", r"approval (required|prompt|request)"),
        ("preflight_blocked", r"blocked_preflight|preflight blocked|missing capabilities"),
        ("preflight_failed", r"preflight failed|failed preflight"),
        ("missing_reasoning_field", r"missing required reasoning field|required reasoning"),
        ("no_verifiable_output", r"no verifiable output|verifiable output"),
        ("missing_source_material", r"missing source material|source material missing"),
        ("fallback_failed", r"fallback failed|fallback .* failed"),
        ("handler_load_failed", r"handler load failed|failed to load handler|handler .* import"),
        ("provider_unavailable", r"provider unavailable|503|timeout|timed out|connection refused"),
        ("effect_reconciliation_required", r"effect reconciliation required|unreconciled effect"),
    ]
    for label, pattern in patterns:
        if re.search(pattern, text):
            return label
    tokens = re.findall(r"[a-z0-9]+", text)
    if not tokens:
        return "unknown_failure"
    return "_".join(tokens[:4])[:80]


def _failure_signature_id(record) -> str:
    signatures = _record_failure_signature_targets(record)
    if signatures:
        return signatures[0]
    return f"failure:{getattr(record, 'pipeline', 'unknown')}:{_failure_mode(record)}"


def _record_failure_signature_targets(record) -> list[str]:
    targets: list[str] = []
    for action in getattr(getattr(record, "delta", None), "actions", []):
        action_type = str(getattr(action, "type", ""))
        target = str(getattr(action, "target", ""))
        if action_type not in {"create_scar", "update_failure_signature"} and not target.startswith(
            ("scar:", "failure:")
        ):
            continue
        signature = _normalize_failure_signature(target, getattr(record, "pipeline", ""))
        if signature and signature not in targets:
            targets.append(signature)
    return targets


def _normalize_failure_signature(target: str, fallback_pipeline: str) -> str:
    if target.startswith("failure:"):
        return target
    if target.startswith("scar:"):
        parts = target.split(":")
        if len(parts) >= 3 and parts[1] and parts[2]:
            return f"failure:{parts[1]}:{parts[2]}"
        if len(parts) >= 2 and parts[-1]:
            return f"failure:{fallback_pipeline}:{parts[-1]}"
    return ""


def _failure_signature_pipeline(signature: str) -> str | None:
    parts = signature.split(":")
    if len(parts) >= 3 and parts[0] == "failure" and parts[1]:
        return parts[1]
    return None


def _failure_signature_scar_times(records: list) -> dict[str, datetime]:
    times: dict[str, datetime] = {}
    for record in sorted(records, key=_item_timestamp):
        for signature in _record_failure_signature_targets(record):
            times.setdefault(signature, _item_timestamp(record))
    return times


def _record_prevents_known_failure(record) -> bool:
    if _is_failure_record(record) or _is_approval_gate_record(record) or _is_preflight_gate_record(record):
        return False
    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "outcome", ""),
            getattr(getattr(record, "delta", None), "what_happened", ""),
            getattr(getattr(record, "delta", None), "what_mattered", ""),
            getattr(getattr(record, "delta", None), "what_changed", ""),
            " ".join(str(ref) for ref in getattr(record, "eval_refs", [])),
            " ".join(str(link) for link in getattr(record, "causal_links", [])),
        )
    ).lower()
    return bool(
        re.search(r"\b(fallback|avoid(?:ed)?|prevent(?:ed)?|route(?:d)?|changed strategy|because prior|scar)\b", text)
    )


def _failure_detected_step(record) -> str:
    refs = [str(ref) for ref in getattr(record, "eval_refs", []) if ref]
    if refs:
        return refs[0].split(":", 1)[0]
    return str(getattr(record, "pipeline", "unknown"))


def _event_time(event: FailureEvent, records: list) -> datetime:
    for record in records:
        if str(getattr(record, "id", "")) == event.run_id:
            return _item_timestamp(record)
    return datetime.now(timezone.utc)


def _causal_coverage_gaps(records: list) -> list[str]:
    counts: dict[str, int] = {}
    for record in records:
        if getattr(record, "causal_links", []):
            continue
        if not _needs_causal_gap_review(record):
            continue
        counts[record.pipeline] = counts.get(record.pipeline, 0) + 1
    return [
        f"{pipeline}: {count} anchored records without causal links"
        for pipeline, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]


def _needs_causal_gap_review(record) -> bool:
    if getattr(record, "trigger", "") == "operator_evidence":
        return False
    if getattr(record, "artifacts", []) or getattr(record, "side_effect_refs", []):
        if _is_baseline_artifact_record(record):
            return False
        return True
    if _is_approval_gate_record(record):
        return False
    if _is_failure_record(record):
        return not _captures_failure_memory(record)
    return False


def _is_baseline_artifact_record(record) -> bool:
    if not getattr(record, "artifacts", []):
        return False
    delta = getattr(record, "delta", None)
    text = " ".join(
        str(value or "")
        for value in (
            getattr(delta, "what_changed", ""),
            getattr(delta, "what_mattered", ""),
            getattr(delta, "what_happened", ""),
        )
    ).lower()
    if re.search(r"\bfuture\b.*\b(compare|baseline|reference)", text):
        return True
    for action in getattr(delta, "actions", []):
        if str(getattr(action, "type", "")) == "form_hypothesis":
            return True
    return False


def _captures_failure_memory(record) -> bool:
    if not _is_failure_record(record):
        return False
    for action in getattr(record.delta, "actions", []):
        action_type = str(getattr(action, "type", ""))
        target = str(getattr(action, "target", ""))
        if action_type in {"create_scar", "update_failure_signature"}:
            return True
        if target.startswith(("scar:", "failure:")):
            return True
    return False


def _recent_action_targets(records: list, prefix: str) -> list[str]:
    targets: list[str] = []
    for record in records[-50:]:
        for action in record.delta.actions:
            target = str(action.target)
            if target.startswith(prefix) and target not in targets:
                targets.append(target)
    return targets[:10]


def _recent_experiment_refs(records: list) -> list[str]:
    refs: list[str] = []
    for record in records[-50:]:
        if getattr(record, "trigger", "") == "operator_evidence":
            continue
        if record.pipeline in {"self_evolution", "a2a_trust_experiment"} and record.id not in refs:
            refs.append(record.id)
        for ref in record.eval_refs:
            if ("experiment" in ref or "strategic" in ref) and ref not in refs:
                refs.append(ref)
    return refs[:10]
