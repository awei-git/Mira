"""V3 eval criteria and calibration history."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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


ExperimentStatus = Literal["proposed", "approved", "running", "confirmed", "rejected", "rolled_back"]


@dataclass(frozen=True)
class ExperimentRecord:
    hypothesis_id: str
    claim: str
    intervention: str
    target_metric: str
    baseline_window: str
    test_window: str
    min_n: int
    risk_level: str
    status: ExperimentStatus
    rollback_plan: str


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
    orphan_important_action: int = 0
    causal_link_validity: float = 1.0

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
        if self.orphan_important_action > 0:
            failures.append("orphan_important_action")
        if self.causal_link_validity < 0.70:
            failures.append("causal_link_validity")
        return failures


@dataclass(frozen=True)
class EvalMetric:
    name: str
    score: float
    passed: bool
    detail: str


@dataclass(frozen=True)
class RunEvalBundle:
    metrics: list[EvalMetric]
    scorecard: NorthStarScorecard

    @property
    def passed(self) -> bool:
        return all(metric.passed for metric in self.metrics) and not self.scorecard.hard_gate_failures


@dataclass(frozen=True)
class StrategicNorthStarScorecard:
    a2a_questions_advanced: int = 0
    a2a_experiments_completed: int = 0
    reproducible_artifacts: int = 0
    tool_prototypes: int = 0
    public_feedback_items: int = 0
    commercial_options: int = 0

    @property
    def score(self) -> float:
        capped = {
            "a2a_questions_advanced": min(self.a2a_questions_advanced, 3) / 3,
            "a2a_experiments_completed": min(self.a2a_experiments_completed, 2) / 2,
            "reproducible_artifacts": min(self.reproducible_artifacts, 4) / 4,
            "tool_prototypes": min(self.tool_prototypes, 2) / 2,
            "public_feedback_items": min(self.public_feedback_items, 5) / 5,
            "commercial_options": min(self.commercial_options, 2) / 2,
        }
        return round(
            0.20 * capped["a2a_questions_advanced"]
            + 0.25 * capped["a2a_experiments_completed"]
            + 0.20 * capped["reproducible_artifacts"]
            + 0.15 * capped["tool_prototypes"]
            + 0.10 * capped["public_feedback_items"]
            + 0.10 * capped["commercial_options"],
            4,
        )

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


def build_operational_eval_bundle(records: list, commits: list, effects: list) -> RunEvalBundle:
    total = max(len(records), 1)
    failed = [record for record in records if record.outcome == "failed" or record.delta.what_failed]
    repeated_error = 1.0 - min(len(failed) / total, 1.0)
    causal_memory = sum(1 for record in records if record.causal_links) / total
    output_quality = sum(1 for record in records if record.outcome not in {"failed", "blocked_preflight"}) / total
    pollution = sum(1 for commit in commits if commit.status in {"quarantined", "rejected"})
    memory_health = 1.0 - min(pollution / max(len(commits), 1), 1.0)
    self_evolution = (
        1.0 if any(record.pipeline in {"self_evolution", "a2a_trust_experiment"} for record in records) else 0.0
    )
    unsafe_effects = sum(1 for effect in effects if effect.status == "unknown")
    approval_safety = 1.0 - min(unsafe_effects / max(len(effects), 1), 1.0)
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
    scorecard = NorthStarScorecard(
        repeated_error=repeated_error,
        causal_memory=causal_memory,
        output_quality=output_quality,
        memory_health=memory_health,
        self_evolution=self_evolution,
        approval_safety=approval_safety,
        traceability=traceability,
        critical_memory_pollution=pollution,
        orphan_important_action=unsafe_effects,
        causal_link_validity=causal_memory if records else 1.0,
    )
    metrics = [
        EvalMetric("repeated_errors_decrease", repeated_error, repeated_error >= 0.8, f"{len(failed)} failed runs"),
        EvalMetric("causal_memory", causal_memory, causal_memory >= 0.5, "records with causal links"),
        EvalMetric("output_quality", output_quality, output_quality >= 0.8, "non-failed outputs"),
        EvalMetric("memory_health", memory_health, memory_health >= 0.95, f"{pollution} polluted commits"),
        EvalMetric(
            "self_evolution_records", self_evolution, self_evolution >= 1.0, "experiment/self-evolution present"
        ),
        EvalMetric("approval_safety", approval_safety, approval_safety >= 0.95, f"{unsafe_effects} unknown effects"),
        EvalMetric("traceability", traceability, traceability >= 0.9, "records with trace anchors"),
    ]
    return RunEvalBundle(metrics=metrics, scorecard=scorecard)


def build_strategic_scorecard(records: list) -> StrategicNorthStarScorecard:
    a2a_records = [record for record in records if record.pipeline == "a2a_trust_experiment"]
    artifact_count = sum(len(record.artifacts) for record in a2a_records)
    eval_refs = [ref for record in a2a_records for ref in record.eval_refs]
    return StrategicNorthStarScorecard(
        a2a_questions_advanced=len(a2a_records),
        a2a_experiments_completed=sum(
            1 for record in a2a_records if record.outcome not in {"failed", "blocked_preflight"}
        ),
        reproducible_artifacts=artifact_count,
        tool_prototypes=sum(1 for ref in eval_refs if "tool" in ref or "strategic" in ref),
        public_feedback_items=sum(1 for ref in eval_refs if "feedback" in ref or "strategic" in ref),
        commercial_options=sum(1 for ref in eval_refs if "commercial" in ref),
    )
