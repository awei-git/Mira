"""V3.1 experiment registry derived from durable run evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from mira.eval_thresholds import govern_eval_threshold_change_from_metadata


ExperimentStatus = Literal[
    "proposed",
    "approved",
    "running",
    "confirmed",
    "rejected",
    "inconclusive",
    "rolled_back",
]


@dataclass(frozen=True)
class ExperimentRecord:
    id: str
    run_id: str
    hypothesis_id: str
    mismatch_cluster_id: str
    claim: str
    intervention: str
    target_pipeline: str
    target_metric: str
    baseline_window: str
    test_window: str
    min_n: int
    expected_effect: str
    risk_level: Literal["low", "medium", "high", "critical"]
    approval_token_id: str | None
    status: ExperimentStatus
    rollback_plan: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExperimentRegistrySummary:
    experiments: list[ExperimentRecord]
    self_evolution_change_count: int
    self_evolution_changes_with_experiment_record: int
    untracked_change_count: int
    auto_change_without_rollback_count: int
    high_risk_without_approval_count: int
    eval_threshold_change_count: int = 0
    eval_threshold_policy_violation_count: int = 0

    @property
    def experiment_coverage(self) -> float:
        if self.self_evolution_change_count == 0:
            return 1.0
        return self.self_evolution_changes_with_experiment_record / self.self_evolution_change_count

    @property
    def testability_rate(self) -> float:
        if not self.experiments:
            return 1.0
        testable = sum(1 for experiment in self.experiments if _is_testable(experiment))
        return testable / len(self.experiments)

    @property
    def conclusion_rate(self) -> float:
        if not self.experiments:
            return 1.0
        concluded = sum(1 for experiment in self.experiments if experiment.status in {"confirmed", "rejected"})
        return concluded / len(self.experiments)

    @property
    def rollback_rate(self) -> float:
        if not self.experiments:
            return 0.0
        rolled_back = sum(1 for experiment in self.experiments if experiment.status == "rolled_back")
        return rolled_back / len(self.experiments)


def build_experiment_registry(records: list, effects: list | None = None) -> ExperimentRegistrySummary:
    approval_by_run = _approval_tokens_by_run(effects or [])
    experiments: list[ExperimentRecord] = []
    records_with_experiment = set()
    for record in records:
        created = _experiment_records_from_run(record, approval_by_run.get(record.id))
        if created:
            records_with_experiment.add(record.id)
            experiments.extend(created)

    self_evolution_changes = [record for record in records if _is_self_evolution_change(record)]
    tracked_change_count = sum(1 for record in self_evolution_changes if record.id in records_with_experiment)
    untracked_change_count = len(self_evolution_changes) - tracked_change_count
    auto_change_without_rollback_count = sum(
        1 for record in self_evolution_changes if not _record_has_rollback(record, experiments)
    )
    high_risk_without_approval_count = sum(
        1
        for experiment in experiments
        if experiment.risk_level in {"high", "critical"} and not experiment.approval_token_id
    )
    threshold_change_count = 0
    threshold_policy_violation_count = 0
    for record in self_evolution_changes:
        for action in _eval_threshold_change_actions(record):
            threshold_change_count += 1
            if action is None:
                threshold_policy_violation_count += 1
                continue
            metadata = dict(getattr(action, "metadata", {}) or {})
            effective_approval = metadata.get("approval_token_id") or approval_by_run.get(record.id)
            try:
                decision = govern_eval_threshold_change_from_metadata(
                    metadata,
                    approval_token_id=effective_approval,
                )
            except (TypeError, ValueError):
                threshold_policy_violation_count += 1
                continue
            if not decision.allowed:
                threshold_policy_violation_count += 1
    return ExperimentRegistrySummary(
        experiments=experiments,
        self_evolution_change_count=len(self_evolution_changes),
        self_evolution_changes_with_experiment_record=tracked_change_count,
        untracked_change_count=untracked_change_count,
        auto_change_without_rollback_count=auto_change_without_rollback_count,
        high_risk_without_approval_count=high_risk_without_approval_count,
        eval_threshold_change_count=threshold_change_count,
        eval_threshold_policy_violation_count=threshold_policy_violation_count,
    )


def _experiment_records_from_run(record, approval_token_id: str | None) -> list[ExperimentRecord]:
    records: list[ExperimentRecord] = []
    for action in getattr(record.delta, "actions", []):
        if action.type not in {"form_hypothesis", "update_hypothesis"}:
            continue
        if not str(action.target).startswith("hypothesis:"):
            continue
        records.append(_experiment_record_from_action(record, action, approval_token_id))
    if records:
        return records
    if _has_experiment_marker(record):
        records.append(_fallback_experiment_record(record, approval_token_id))
    return records


def _experiment_record_from_action(record, action, approval_token_id: str | None) -> ExperimentRecord:
    metadata = dict(getattr(action, "metadata", {}) or {})
    risk_level = metadata.get("risk_level", "low")
    if risk_level not in {"low", "medium", "high", "critical"}:
        risk_level = "low"
    evidence_for: list[str] = []
    evidence_against: list[str] = []
    if action.type == "update_hypothesis":
        if getattr(record.delta, "what_failed", None):
            evidence_against.append(action.detail)
        else:
            evidence_for.append(action.detail)
    return ExperimentRecord(
        id=metadata.get("experiment_id") or f"experiment:{record.id}:{action.target}",
        run_id=record.id,
        hypothesis_id=action.target,
        mismatch_cluster_id=metadata.get("mismatch_cluster_id") or f"{record.pipeline}:mismatch",
        claim=action.detail,
        intervention=metadata.get("intervention") or _default_intervention(record),
        target_pipeline=metadata.get("target_pipeline") or record.pipeline,
        target_metric=metadata.get("target_metric") or metadata.get("current_metric") or _default_target_metric(record),
        baseline_window=metadata.get("baseline_window") or f"prior {record.pipeline} evidence window",
        test_window=metadata.get("test_window") or "current active experiment window",
        min_n=_safe_min_n(metadata.get("min_n")),
        expected_effect=metadata.get("expected_effect") or action.detail,
        risk_level=risk_level,
        approval_token_id=metadata.get("approval_token_id") or approval_token_id,
        status=_experiment_status(record),
        rollback_plan=metadata.get("rollback_plan") or _default_rollback_plan(record),
        evidence_for=evidence_for,
        evidence_against=evidence_against,
    )


def _fallback_experiment_record(record, approval_token_id: str | None) -> ExperimentRecord:
    return ExperimentRecord(
        id=f"experiment:{record.id}:fallback",
        run_id=record.id,
        hypothesis_id=f"hypothesis:{record.pipeline}:{record.id}",
        mismatch_cluster_id=f"{record.pipeline}:mismatch",
        claim=getattr(record.delta, "what_mattered", "") or record.intent,
        intervention=_default_intervention(record),
        target_pipeline=record.pipeline,
        target_metric=_default_target_metric(record),
        baseline_window=f"prior {record.pipeline} evidence window",
        test_window="current active experiment window",
        min_n=1,
        expected_effect=getattr(record.delta, "what_changed", "") or record.outcome,
        risk_level="low",
        approval_token_id=approval_token_id,
        status=_experiment_status(record),
        rollback_plan=_default_rollback_plan(record),
    )


def _approval_tokens_by_run(effects: list) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for effect in effects:
        token = getattr(effect, "approval_token_id", None)
        run_id = getattr(effect, "run_id", "")
        if token and run_id and run_id not in tokens:
            tokens[run_id] = token
    return tokens


def _is_self_evolution_change(record) -> bool:
    if record.pipeline != "self_evolution":
        return False
    refs = " ".join(getattr(record, "eval_refs", []) + getattr(record, "side_effect_refs", [])).lower()
    return any(
        marker in refs
        for marker in (
            "branch_canary:deployed",
            "branch_canary:rolled_back",
            "production_promotion",
            "promote_production",
            "code_change",
            "policy_change",
            "pipeline_change",
            "eval_threshold_change",
            "workflow_route_change",
        )
    )


def _eval_threshold_change_actions(record) -> list:
    marker_text = " ".join(getattr(record, "eval_refs", []) + getattr(record, "side_effect_refs", [])).lower()
    has_record_marker = "eval_threshold_change" in marker_text
    actions = [action for action in getattr(record.delta, "actions", []) if _is_eval_threshold_change_action(action)]
    if actions:
        return actions
    if has_record_marker:
        return [None]
    return []


def _is_eval_threshold_change_action(action) -> bool:
    metadata = dict(getattr(action, "metadata", {}) or {})
    typed_markers = {
        str(metadata.get("change_type", "")).lower(),
        str(metadata.get("action_type", "")).lower(),
        str(metadata.get("change", "")).lower(),
    }
    if typed_markers.intersection({"eval_threshold", "eval_threshold_change", "threshold_change"}):
        return True
    text = " ".join(
        [
            str(getattr(action, "target", "")),
            str(getattr(action, "detail", "")),
            str(metadata.get("target_metric", "")),
        ]
    ).lower()
    return "eval_threshold_change" in text or "threshold:" in text


def _has_experiment_marker(record) -> bool:
    refs = " ".join(getattr(record, "eval_refs", [])).lower()
    artifacts = " ".join(getattr(record, "artifacts", [])).lower()
    return "experiment" in refs or "self_evolution_experiment.md" in artifacts


def _record_has_rollback(record, experiments: list[ExperimentRecord]) -> bool:
    run_experiments = [experiment for experiment in experiments if experiment.run_id == record.id]
    if any(experiment.rollback_plan for experiment in run_experiments):
        return True
    refs = " ".join(getattr(record, "eval_refs", [])).lower()
    artifacts = " ".join(getattr(record, "artifacts", [])).lower()
    return "rollback" in refs or "rollback" in artifacts


def _experiment_status(record) -> ExperimentStatus:
    refs = " ".join(getattr(record, "eval_refs", [])).lower()
    outcome = str(getattr(record, "outcome", "")).lower()
    if "rolled_back" in refs or "rollback_executed" in refs:
        return "rolled_back"
    if "confirmed" in refs:
        return "confirmed"
    if "rejected" in refs or outcome == "failed":
        return "rejected"
    if "inconclusive" in refs:
        return "inconclusive"
    if "approval_required" in outcome or "preview" in refs:
        return "proposed"
    if "staged" in refs or "canary" in refs:
        return "running"
    return "running"


def _default_intervention(record) -> str:
    return getattr(record.delta, "what_changed", "") or f"run {record.pipeline} experiment"


def _default_target_metric(record) -> str:
    if record.pipeline == "self_evolution":
        return "self_evolution_experiment_coverage"
    if record.pipeline == "a2a_trust_experiment":
        return "a2a_trust_reproducible_artifacts"
    return f"{record.pipeline}_outcome_quality"


def _default_rollback_plan(record) -> str:
    if record.pipeline == "self_evolution":
        return "revert the self-evolution change and record evidence_against before retry"
    return "record evidence_against and return to the previous workflow behavior"


def _safe_min_n(value: str | int | None) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _is_testable(experiment: ExperimentRecord) -> bool:
    return bool(
        experiment.target_metric and experiment.baseline_window and experiment.test_window and experiment.min_n >= 1
    )
