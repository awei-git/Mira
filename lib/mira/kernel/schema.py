"""Core Memory Kernel schema.

The kernel is durable identity and experience. Pipeline runs receive read-only
snapshots from it; they never mutate the kernel directly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Literal

MemoryClass = Literal["creative", "social", "operational", "bodily", "epistemic", "self_modification"]
HypothesisStatus = Literal["testing", "confirmed", "rejected", "inconclusive"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    return value


def parse_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return utc_now()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Identity:
    statement: str = "Mira is persistent memory acting through disposable agents."
    integrity_hash: str | None = None
    protected: bool = True


@dataclass
class Worldview:
    beliefs: list[str] = field(default_factory=list)
    evolving_positions: dict[str, str] = field(default_factory=dict)


@dataclass
class Commitment:
    description: str
    created_at: datetime = field(default_factory=utc_now)
    due_at: datetime | None = None
    status: Literal["open", "kept", "broken", "superseded"] = "open"


@dataclass
class Preferences:
    aesthetic: dict[str, str] = field(default_factory=dict)
    operational: dict[str, str] = field(default_factory=dict)
    interpersonal: dict[str, str] = field(default_factory=dict)


@dataclass
class Scar:
    """A failure that changed behavior."""

    incident: str
    root_cause: str
    behavioral_change: str
    policy_created: str | None = None
    reinforcement_count: int = 0
    date: datetime = field(default_factory=utc_now)
    scar_id: str = ""

    def __post_init__(self) -> None:
        if not self.scar_id:
            self.scar_id = "scar:" + self.incident.lower().replace(" ", "_")[:80]


@dataclass
class Question:
    prompt: str
    source: str = ""
    opened_at: datetime = field(default_factory=utc_now)
    status: Literal["open", "answered", "archived"] = "open"


@dataclass
class SkillTrace:
    """A trace of actual skill use and outcomes."""

    skill_name: str
    times_used: int = 0
    success_rate: float = 0.0
    last_used: datetime | None = None
    last_outcome: str = ""
    decay_score: float = 1.0

    def record_use(self, succeeded: bool, outcome: str, when: datetime | None = None) -> None:
        prior_successes = self.success_rate * self.times_used
        self.times_used += 1
        self.success_rate = (prior_successes + (1.0 if succeeded else 0.0)) / self.times_used
        self.last_used = when or utc_now()
        self.last_outcome = outcome
        self.decay_score = min(1.0, self.decay_score + 0.15)


@dataclass
class FailureSignature:
    """A pattern that predicts failure before it happens."""

    pattern: str
    detection_rule: str
    occurrences: int = 0
    failure_rate: float = 0.0


@dataclass
class Hypothesis:
    """A change hypothesis being tested by self-evolution."""

    claim: str
    test_pipeline: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    start_date: datetime = field(default_factory=utc_now)
    status: HypothesisStatus = "testing"
    baseline_window: str = ""
    test_window: str = ""
    min_n: int = 1
    current_metric: str = ""
    rollback_plan: str = ""
    hypothesis_id: str = ""

    def __post_init__(self) -> None:
        try:
            self.min_n = max(1, int(self.min_n or 1))
        except (TypeError, ValueError):
            self.min_n = 1
        if not self.hypothesis_id:
            self.hypothesis_id = "hypothesis:" + self.claim.lower().replace(" ", "_")[:80]


@dataclass
class OutcomeStore:
    outcome_ids: list[str] = field(default_factory=list)


@dataclass
class EvalCalibration:
    scores: dict[str, float] = field(default_factory=dict)
    threshold_adjustments: dict[str, float] = field(default_factory=dict)


@dataclass
class RelationshipModel:
    notes: list[str] = field(default_factory=list)
    preferences: dict[str, str] = field(default_factory=dict)


@dataclass
class Interests:
    active: list[str] = field(default_factory=list)
    cooling: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


@dataclass
class Thread:
    thread_id: str
    title: str
    status: Literal["active", "paused", "closed"] = "active"


@dataclass
class ArchivedMemory:
    item_id: str
    source: str
    summary: str
    archived_at: datetime = field(default_factory=utc_now)
    run_id: str = ""
    effect_id: str = ""


@dataclass
class MemoryKernel:
    identity: Identity = field(default_factory=Identity)
    worldview: Worldview = field(default_factory=Worldview)
    commitments: list[Commitment] = field(default_factory=list)
    preferences: Preferences = field(default_factory=Preferences)
    scars: list[Scar] = field(default_factory=list)
    open_questions: list[Question] = field(default_factory=list)
    skill_traces: list[SkillTrace] = field(default_factory=list)
    failure_signatures: list[FailureSignature] = field(default_factory=list)
    outcome_history: OutcomeStore = field(default_factory=OutcomeStore)
    eval_calibration: EvalCalibration = field(default_factory=EvalCalibration)
    relationship_model: RelationshipModel = field(default_factory=RelationshipModel)
    interests: Interests = field(default_factory=Interests)
    active_threads: list[Thread] = field(default_factory=list)
    pending_hypotheses: list[Hypothesis] = field(default_factory=list)
    archived_memories: list[ArchivedMemory] = field(default_factory=list)

    def skill_trace(self, skill_name: str) -> SkillTrace:
        for trace in self.skill_traces:
            if trace.skill_name == skill_name:
                return trace
        trace = SkillTrace(skill_name=skill_name)
        self.skill_traces.append(trace)
        return trace

    def hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        return next((h for h in self.pending_hypotheses if h.hypothesis_id == hypothesis_id), None)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryKernel":
        kernel = cls()
        identity = data.get("identity") or {}
        kernel.identity = Identity(**identity)
        kernel.worldview = Worldview(**(data.get("worldview") or {}))
        kernel.preferences = Preferences(**(data.get("preferences") or {}))
        kernel.scars = [Scar(**{**s, "date": parse_dt(s.get("date"))}) for s in data.get("scars", [])]
        kernel.open_questions = [
            Question(**{**q, "opened_at": parse_dt(q.get("opened_at"))}) for q in data.get("open_questions", [])
        ]
        kernel.skill_traces = [
            SkillTrace(**{**s, "last_used": parse_dt(s.get("last_used")) if s.get("last_used") else None})
            for s in data.get("skill_traces", [])
        ]
        kernel.failure_signatures = [FailureSignature(**f) for f in data.get("failure_signatures", [])]
        kernel.outcome_history = OutcomeStore(**(data.get("outcome_history") or {}))
        kernel.eval_calibration = EvalCalibration(**(data.get("eval_calibration") or {}))
        kernel.relationship_model = RelationshipModel(**(data.get("relationship_model") or {}))
        kernel.interests = Interests(**(data.get("interests") or {}))
        kernel.pending_hypotheses = [
            Hypothesis(**{**h, "start_date": parse_dt(h.get("start_date"))}) for h in data.get("pending_hypotheses", [])
        ]
        kernel.active_threads = [Thread(**t) for t in data.get("active_threads", [])]
        kernel.archived_memories = [
            ArchivedMemory(**{**item, "archived_at": parse_dt(item.get("archived_at"))})
            for item in data.get("archived_memories", [])
        ]
        kernel.commitments = [
            Commitment(
                description=c["description"],
                created_at=parse_dt(c.get("created_at")),
                due_at=parse_dt(c.get("due_at")) if c.get("due_at") else None,
                status=c.get("status", "open"),
            )
            for c in data.get("commitments", [])
        ]
        return kernel
