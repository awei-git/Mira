"""Memory delta proposal contract.

V3.1 separates the append-only experience ledger from durable kernel mutation:
every run may propose memory changes, but only gateway-created commits can
mutate the kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .schema import MemoryClass, to_jsonable, utc_now
from .ledger_ids import new_id

MemoryActionType = Literal[
    "reinforce",
    "weaken",
    "archive",
    "escalate",
    "create_scar",
    "update_failure_signature",
    "update_skill_trace",
    "form_hypothesis",
    "update_hypothesis",
    "update_relationship",
]
TrustTier = Literal["untrusted", "observed", "verified", "human_confirmed"]
RiskLevel = Literal["low", "medium", "high", "critical"]
ProposalStatus = Literal["proposed", "no_kernel_change"]


@dataclass(frozen=True)
class MemoryAction:
    """A specific action on memory resulting from a pipeline run."""

    type: MemoryActionType
    target: str
    detail: str
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class MemoryDeltaProposal:
    """Optional proposed kernel changes from a pipeline run."""

    pipeline: str
    run_id: str
    memory_class: MemoryClass
    what_happened: str
    what_mattered: str
    what_changed: str
    actions: list[MemoryAction]
    what_failed: str | None = None
    trust_tier: TrustTier = "observed"
    risk_level: RiskLevel = "low"
    status: ProposalStatus = "proposed"
    proposal_id: str = field(default_factory=lambda: new_id("proposal"))
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        missing = [
            name
            for name in ("pipeline", "run_id", "what_happened", "what_mattered", "what_changed")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"MemoryDelta missing required fields: {', '.join(missing)}")
        if self.actions is None:
            raise ValueError("MemoryDeltaProposal.actions must be a list")
        if not self.actions and self.status != "no_kernel_change":
            object.__setattr__(self, "status", "no_kernel_change")
        if self.status == "no_kernel_change" and self.risk_level != "low":
            object.__setattr__(self, "risk_level", "low")

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def no_kernel_change(
        cls,
        *,
        pipeline: str,
        run_id: str,
        memory_class: MemoryClass,
        what_happened: str,
        what_mattered: str,
        what_changed: str,
        trust_tier: TrustTier = "observed",
    ) -> "MemoryDeltaProposal":
        return cls(
            pipeline=pipeline,
            run_id=run_id,
            memory_class=memory_class,
            what_happened=what_happened,
            what_mattered=what_mattered,
            what_changed=what_changed,
            actions=[],
            trust_tier=trust_tier,
            risk_level="low",
            status="no_kernel_change",
        )

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryDeltaProposal":
        actions = [MemoryAction(**a) for a in data.get("actions", [])]
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            pipeline=data["pipeline"],
            run_id=data["run_id"],
            timestamp=timestamp,
            memory_class=data["memory_class"],
            what_happened=data["what_happened"],
            what_mattered=data["what_mattered"],
            what_changed=data["what_changed"],
            what_failed=data.get("what_failed"),
            actions=actions,
            trust_tier=data.get("trust_tier", "observed"),
            risk_level=data.get("risk_level", "low"),
            status=data.get("status", "proposed"),
            proposal_id=data.get("proposal_id") or data.get("id") or new_id("proposal"),
        )


MemoryDelta = MemoryDeltaProposal
