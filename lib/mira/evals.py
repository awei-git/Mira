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
