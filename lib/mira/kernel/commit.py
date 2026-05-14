"""Gateway-created kernel commits and quarantine decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from .delta import MemoryAction, MemoryDeltaProposal, RiskLevel
from .ledger_ids import new_id
from .schema import to_jsonable, utc_now

GatewayDecision = Literal["allow", "redact", "quarantine", "reject", "require_human"]
CommitStatus = Literal["applied", "noop", "quarantined", "rejected", "requires_human"]


@dataclass(frozen=True)
class ValidationFinding:
    check: str
    decision: GatewayDecision
    reason: str
    action_target: str | None = None

    def to_dict(self) -> dict:
        return to_jsonable(self)


@dataclass(frozen=True)
class MemoryCommit:
    proposal_id: str
    run_id: str
    pipeline: str
    committed_actions: list[MemoryAction]
    rejected_actions: list[MemoryAction] = field(default_factory=list)
    quarantined_actions: list[MemoryAction] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    approved_by: str | None = "security_gateway"
    rollback_pointer: str | None = None
    status: CommitStatus = "applied"
    commit_id: str = field(default_factory=lambda: new_id("commit"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryCommit":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            proposal_id=data["proposal_id"],
            run_id=data["run_id"],
            pipeline=data["pipeline"],
            committed_actions=[MemoryAction(**a) for a in data.get("committed_actions", [])],
            rejected_actions=[MemoryAction(**a) for a in data.get("rejected_actions", [])],
            quarantined_actions=[MemoryAction(**a) for a in data.get("quarantined_actions", [])],
            findings=[ValidationFinding(**f) for f in data.get("findings", [])],
            approved_by=data.get("approved_by"),
            rollback_pointer=data.get("rollback_pointer"),
            status=data.get("status", "applied"),
            commit_id=data.get("commit_id") or data.get("id") or new_id("commit"),
            timestamp=timestamp,
        )


class MemoryCommitLog:
    """Append-only JSONL log of gateway decisions."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, commit: MemoryCommit) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(commit.to_dict(), sort_keys=True) + "\n")

    def list(self, status: CommitStatus | None = None, limit: int | None = None) -> list[MemoryCommit]:
        if not self.path.exists():
            return []
        commits: list[MemoryCommit] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                commit = MemoryCommit.from_dict(json.loads(line))
                if status is None or commit.status == status:
                    commits.append(commit)
        commits.sort(key=lambda c: c.timestamp)
        if limit is not None:
            return commits[-limit:]
        return commits


class SecurityGateway:
    """Validates memory proposals before the durable kernel can change."""

    _INJECTION_RE = re.compile(
        r"(ignore\s+all\s+polic|store\s+as\s+preference|without\s+approval|auto[-\s]?publish)",
        re.IGNORECASE,
    )
    _SECRET_RE = re.compile(
        r"(api[_-]?key|secret|token|password|private[_-]?key)\s*[:=]\s*\S+",
        re.IGNORECASE,
    )
    _EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _HIGH_RISK_ACTIONS = {"create_scar", "form_hypothesis", "update_hypothesis"}

    def validate(self, proposal: MemoryDeltaProposal) -> MemoryCommit:
        if proposal.status == "no_kernel_change" or not proposal.actions:
            return MemoryCommit(
                proposal_id=proposal.proposal_id,
                run_id=proposal.run_id,
                pipeline=proposal.pipeline,
                committed_actions=[],
                findings=[ValidationFinding("schema_valid", "allow", "no kernel changes proposed")],
                status="noop",
            )

        committed: list[MemoryAction] = []
        rejected: list[MemoryAction] = []
        quarantined: list[MemoryAction] = []
        findings: list[ValidationFinding] = []

        for action in proposal.actions:
            decision = self._validate_action(proposal, action)
            findings.append(decision)
            if decision.decision == "allow":
                committed.append(action)
            elif decision.decision == "redact":
                committed.append(MemoryAction(action.type, action.target, self._redact(action.detail)))
            elif decision.decision == "quarantine":
                quarantined.append(action)
            else:
                rejected.append(action)

        if any(f.decision == "require_human" for f in findings):
            status: CommitStatus = "requires_human"
            approved_by = None
        elif quarantined:
            status = "quarantined"
            approved_by = None
        elif rejected and not committed:
            status = "rejected"
            approved_by = None
        else:
            status = "applied" if committed else "noop"
            approved_by = "security_gateway"

        return MemoryCommit(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            pipeline=proposal.pipeline,
            committed_actions=committed if status == "applied" else [],
            rejected_actions=rejected,
            quarantined_actions=quarantined,
            findings=findings,
            approved_by=approved_by,
            rollback_pointer=proposal.run_id,
            status=status,
        )

    def _validate_action(self, proposal: MemoryDeltaProposal, action: MemoryAction) -> ValidationFinding:
        if not action.type or not action.target:
            return ValidationFinding("schema_valid", "reject", "action type and target are required", action.target)
        body = f"{action.target}\n{action.detail}"
        if self._INJECTION_RE.search(body):
            return ValidationFinding(
                "injection_scan", "quarantine", "possible prompt-injection memory write", action.target
            )
        if self._SECRET_RE.search(body):
            return ValidationFinding(
                "pii_secret_scan", "quarantine", "possible secret in memory proposal", action.target
            )
        if self._EMAIL_RE.search(body) and proposal.memory_class != "bodily":
            return ValidationFinding("privacy_tier", "redact", "redacted email-like personal data", action.target)
        if proposal.trust_tier == "untrusted":
            return ValidationFinding(
                "source_trust", "quarantine", "untrusted source cannot mutate kernel", action.target
            )
        if self._risk_requires_human(proposal.risk_level, action.type):
            return ValidationFinding(
                "risk_tier_assignment", "require_human", "high-risk kernel change needs approval", action.target
            )
        if "caused by memory" in action.detail.lower():
            return ValidationFinding(
                "unsupported_causal_claims",
                "reject",
                "causal claims must be derived from behavioral effects",
                action.target,
            )
        return ValidationFinding("allow", "allow", "validated by gateway", action.target)

    def _risk_requires_human(self, risk_level: RiskLevel, action_type: str) -> bool:
        if risk_level == "critical":
            return True
        return risk_level == "high" and action_type in self._HIGH_RISK_ACTIONS

    def _redact(self, value: str) -> str:
        return self._EMAIL_RE.sub("[redacted-email]", value)
