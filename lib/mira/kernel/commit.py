"""Gateway-created kernel commits and quarantine decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Literal

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
    finding_type: str = ""
    severity: str = ""

    def __post_init__(self) -> None:
        if not self.finding_type:
            object.__setattr__(self, "finding_type", _finding_type_for_check(self.check))
        if not self.severity:
            object.__setattr__(self, "severity", _severity_for_decision(self.decision))

    def to_dict(self) -> dict:
        return to_jsonable(self)


def _finding_type_for_check(check: str) -> str:
    return {
        "schema_valid": "schema_error",
        "source_trust": "untrusted_source",
        "pii_secret_scan": "secret_detected",  # pragma: allowlist secret
        "privacy_tier": "pii_detected",
        "injection_scan": "prompt_injection",
        "policy_intent": "policy_bypass",
        "unsupported_causal_claims": "causal_claim_unverified",
        "duplicate_memory": "duplicate",
        "contradiction": "contradiction",
        "evidence_ref": "unsupported_claim",
        "risk_tier_assignment": "privacy_violation",
        "allow": "validated",
    }.get(check, check)


def _severity_for_decision(decision: GatewayDecision) -> str:
    return {
        "allow": "low",
        "redact": "medium",
        "quarantine": "high",
        "reject": "high",
        "require_human": "high",
    }[decision]


def _privacy_tier_from_findings(findings: list["ValidationFinding"]) -> str:
    if any(finding.check == "pii_secret_scan" for finding in findings):
        return "secret_quarantine"
    if any(finding.check == "privacy_tier" for finding in findings):
        return "redacted"
    return "normal"


def _evidence_refs_from_actions(actions: list[MemoryAction]) -> list[str]:
    refs: list[str] = []
    for action in actions:
        for key in ("evidence_ref", "evidence_refs"):
            value = action.metadata.get(key)
            if not value:
                continue
            refs.extend(part.strip() for part in str(value).split(",") if part.strip())
        detail = action.detail
        if "evidence_ref=" in detail:
            refs.append(detail.split("evidence_ref=", 1)[1].split()[0].strip(".,;"))
        if "evidence:" in detail.lower():
            refs.append(detail.split(":", 1)[1].strip().split()[0].strip(".,;"))
    return sorted(set(refs))


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
    source_trust: str = "observed"
    memory_class: str = ""
    risk_level: str = "low"
    privacy_tier: str = "normal"
    evidence_refs: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
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
            source_trust=data.get("source_trust", "observed"),
            memory_class=data.get("memory_class", ""),
            risk_level=data.get("risk_level", "low"),
            privacy_tier=data.get("privacy_tier", "normal"),
            evidence_refs=list(data.get("evidence_refs", [])),
            contradictions=list(data.get("contradictions", [])),
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


@dataclass(frozen=True)
class QuarantineRecord:
    proposal_id: str
    run_id: str
    pipeline: str
    action: MemoryAction
    finding: ValidationFinding
    record_id: str = field(default_factory=lambda: new_id("quarantine"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QuarantineRecord":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            proposal_id=data["proposal_id"],
            run_id=data["run_id"],
            pipeline=data["pipeline"],
            action=MemoryAction(**data["action"]),
            finding=ValidationFinding(**data["finding"]),
            record_id=data.get("record_id") or new_id("quarantine"),
            timestamp=timestamp,
        )


class MemoryQuarantineStore:
    """Append-only store for memory proposals that need human review."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: QuarantineRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def list(self, limit: int | None = None) -> list[QuarantineRecord]:
        if not self.path.exists():
            return []
        records: list[QuarantineRecord] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(QuarantineRecord.from_dict(json.loads(line)))
        records.sort(key=lambda r: r.timestamp)
        if limit is not None:
            return records[-limit:]
        return records


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
    _POLICY_WEAKENING_RE = re.compile(
        r"\b(?:bypass|skip|disable|turn\s+off|weaken|lower|reduce|relax|remove|ignore|auto[-\s]?approve)\b"
        r".{0,64}\b(?:approval|review|safety|policy|security\s+gateway|memory\s+gateway|gateway|risk\s+gate|"
        r"risk\s+approval)\b",
        re.IGNORECASE,
    )
    _POLICY_WEAKENING_NEGATION_RE = re.compile(
        r"(?:do\s+not|don't|never|cannot|can't|must\s+not|should\s+not)\s+(?:be\s+)?$",
        re.IGNORECASE,
    )
    _SEMANTIC_CLAIM_RE = re.compile(
        r"\b(?P<negative>does\s+not|do\s+not|don't|never|no\s+longer|should\s+not|must\s+not|cannot|can't)\s+"
        r"(?P<neg_object>[^.;,\n]{4,140})"
        r"|"
        r"\b(?P<positive>wants?|prefers?|likes?|needs?|requires?|should|must|always|keep|use)\s+"
        r"(?P<pos_object>[^.;,\n]{4,140})",
        re.IGNORECASE,
    )
    _CLAIM_OBJECT_PREFIX_RE = re.compile(
        r"^(?:to\s+|that\s+|the\s+|a\s+|an\s+|wants?\s+|prefers?\s+|likes?\s+|needs?\s+|requires?\s+|"
        r"should\s+|must\s+|keep\s+|use\s+)+",
        re.IGNORECASE,
    )
    _CLAIM_STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "before",
        "be",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "wa",
        "with",
    }
    _PUBLIC_ROUTING_RE = re.compile(
        r"\b(?:publish(?:ed|ing)?|post(?:ed|ing)?|share(?:d|ing)?|send(?:ing)?|expose(?:d|ing)?|"
        r"export(?:ed|ing)?|upload(?:ed|ing)?|public(?:ly)?|external(?:ly)?)\b",
        re.IGNORECASE,
    )
    _PRIVATE_ROUTING_RE = re.compile(
        r"\b(?:private|local[-\s]?only|local|internal|draft[-\s]?only|staged?|remain|keep|"
        r"do\s+not\s+publish|don't\s+publish|never\s+publish|not\s+public)\b",
        re.IGNORECASE,
    )
    _ROUTING_WORD_RE = re.compile(
        r"\b(?:should|must|may|can|cannot|can't|do|does|not|be|been|being|remain|keep|kept|stage|staged|"
        r"publish(?:ed|ing)?|post(?:ed|ing)?|share(?:d|ing)?|send(?:ing)?|expose(?:d|ing)?|"
        r"export(?:ed|ing)?|upload(?:ed|ing)?|public(?:ly)?|external(?:ly)?|private|local[-\s]?only|"
        r"local|internal|draft[-\s]?only|before|after|until|approval|review)\b",
        re.IGNORECASE,
    )
    _NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
    _NUMERIC_CONTEXT_STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "per",
        "set",
        "should",
        "the",
        "to",
        "use",
        "with",
    }
    _EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
    _HIGH_RISK_ACTIONS = {"create_scar", "form_hypothesis", "update_hypothesis"}
    _EVIDENCE_REQUIRED_ACTIONS = {"form_hypothesis", "update_hypothesis"}
    _CONTRADICTION_PAIRS = (
        ("prefers concise", "prefers detailed"),
        ("prefers short", "prefers long"),
        ("likes ", "dislikes "),
        ("use ", "avoid "),
    )

    def __init__(
        self,
        *,
        existing_memory: Iterable[str] | None = None,
        quarantine_store: MemoryQuarantineStore | None = None,
    ):
        self.existing_memory = [self._normalize(text) for text in existing_memory or [] if text]
        self.quarantine_store = quarantine_store

    def validate(self, proposal: MemoryDeltaProposal) -> MemoryCommit:
        if proposal.status == "no_kernel_change" or not proposal.actions:
            return MemoryCommit(
                proposal_id=proposal.proposal_id,
                run_id=proposal.run_id,
                pipeline=proposal.pipeline,
                committed_actions=[],
                findings=[ValidationFinding("schema_valid", "allow", "no kernel changes proposed")],
                status="noop",
                source_trust=proposal.trust_tier,
                memory_class=proposal.memory_class,
                risk_level=proposal.risk_level,
                privacy_tier="normal",
                evidence_refs=[],
                contradictions=[],
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

        commit = MemoryCommit(
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
            source_trust=proposal.trust_tier,
            memory_class=proposal.memory_class,
            risk_level=proposal.risk_level,
            privacy_tier=_privacy_tier_from_findings(findings),
            evidence_refs=_evidence_refs_from_actions(proposal.actions),
            contradictions=[
                finding.reason
                for finding in findings
                if finding.check == "contradiction" or finding.finding_type == "contradiction"
            ],
        )
        self._store_quarantine(proposal, commit)
        return commit

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
        if self._weakens_policy(body):
            return ValidationFinding(
                "policy_intent",
                "require_human",
                "memory proposal appears to weaken approval, safety, or gateway policy",
                action.target,
            )
        duplicate_or_contradiction = self._memory_consistency_check(action)
        if duplicate_or_contradiction is not None:
            return duplicate_or_contradiction
        if action.type in self._EVIDENCE_REQUIRED_ACTIONS and not self._has_evidence_ref(action):
            return ValidationFinding(
                "evidence_ref",
                "require_human",
                "memory action needs an evidence_ref before it can mutate the kernel",
                action.target,
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
        if risk_level in {"critical", "high"}:
            return True
        return False

    def _redact(self, value: str) -> str:
        return self._EMAIL_RE.sub("[redacted-email]", value)

    def _store_quarantine(self, proposal: MemoryDeltaProposal, commit: MemoryCommit) -> None:
        if self.quarantine_store is None:
            return
        findings_by_target = {finding.action_target: finding for finding in commit.findings}
        for action in [*commit.quarantined_actions, *commit.rejected_actions]:
            finding = findings_by_target.get(action.target)
            if finding is None:
                continue
            self.quarantine_store.append(
                QuarantineRecord(
                    proposal_id=proposal.proposal_id,
                    run_id=proposal.run_id,
                    pipeline=proposal.pipeline,
                    action=action,
                    finding=finding,
                )
            )

    def _memory_consistency_check(self, action: MemoryAction) -> ValidationFinding | None:
        detail = self._normalize(action.detail)
        if not detail:
            return None
        if detail in self.existing_memory:
            return ValidationFinding("duplicate_memory", "reject", "same memory already exists", action.target)
        for existing in self.existing_memory:
            if self._contradicts(detail, existing):
                return ValidationFinding(
                    "contradiction",
                    "require_human",
                    "new memory appears to contradict existing memory",
                    action.target,
                )
        return None

    def _has_evidence_ref(self, action: MemoryAction) -> bool:
        if action.metadata.get("evidence_ref") or action.metadata.get("evidence_refs"):
            return True
        return "evidence_ref=" in action.detail or "evidence:" in action.detail.lower()

    def _contradicts(self, new: str, existing: str) -> bool:
        for left, right in self._CONTRADICTION_PAIRS:
            if left in new and right in existing:
                return True
            if right in new and left in existing:
                return True
        if self._semantic_claims_contradict(new, existing):
            return True
        if self._routing_claims_contradict(new, existing):
            return True
        if self._numeric_claims_contradict(new, existing):
            return True
        return False

    def _semantic_claims_contradict(self, new: str, existing: str) -> bool:
        new_claims = self._semantic_claims(new)
        existing_claims = self._semantic_claims(existing)
        for new_polarity, new_object in new_claims:
            for existing_polarity, existing_object in existing_claims:
                if new_polarity == existing_polarity:
                    continue
                if self._claim_objects_overlap(new_object, existing_object):
                    return True
        return False

    def _semantic_claims(self, value: str) -> list[tuple[str, str]]:
        claims: list[tuple[str, str]] = []
        for match in self._SEMANTIC_CLAIM_RE.finditer(value):
            if match.group("negative"):
                claims.append(("negative", self._normalize_claim_object(match.group("neg_object"))))
            elif match.group("positive"):
                claims.append(("positive", self._normalize_claim_object(match.group("pos_object"))))
        return [(polarity, obj) for polarity, obj in claims if obj]

    def _normalize_claim_object(self, value: str) -> str:
        value = re.sub(r"[^a-z0-9\s-]", " ", value.lower())
        value = self._CLAIM_OBJECT_PREFIX_RE.sub("", " ".join(value.split()))
        return " ".join(value.split())

    def _claim_objects_overlap(self, left: str, right: str) -> bool:
        if left == right:
            return True
        if min(len(left), len(right)) >= 16 and (left in right or right in left):
            return True
        left_tokens = self._claim_tokens(left)
        right_tokens = self._claim_tokens(right)
        if len(left_tokens) < 2 or len(right_tokens) < 2:
            return False
        overlap = left_tokens & right_tokens
        return len(overlap) / min(len(left_tokens), len(right_tokens)) >= 0.65

    def _claim_tokens(self, value: str) -> set[str]:
        return {
            token for token in re.findall(r"[a-z0-9]+", value) if len(token) > 2 and token not in self._CLAIM_STOPWORDS
        }

    def _routing_claims_contradict(self, new: str, existing: str) -> bool:
        new_claims = self._routing_claims(new)
        existing_claims = self._routing_claims(existing)
        for new_direction, new_object in new_claims:
            for existing_direction, existing_object in existing_claims:
                if new_direction == existing_direction:
                    continue
                if self._claim_objects_overlap(new_object, existing_object):
                    return True
        return False

    def _routing_claims(self, value: str) -> list[tuple[str, str]]:
        claims: list[tuple[str, str]] = []
        for clause in re.split(r"[.;,\n]+", value):
            clause = clause.strip()
            if not clause:
                continue
            public = bool(self._PUBLIC_ROUTING_RE.search(clause))
            private = bool(self._PRIVATE_ROUTING_RE.search(clause))
            if not public and not private:
                continue
            direction = "public" if public and not private else "private"
            obj = self._normalize_routing_object(clause)
            if obj:
                claims.append((direction, obj))
        return claims

    def _normalize_routing_object(self, value: str) -> str:
        value = self._ROUTING_WORD_RE.sub(" ", value.lower())
        value = re.sub(r"[^a-z0-9\s-]", " ", value)
        return " ".join(value.split())

    def _numeric_claims_contradict(self, new: str, existing: str) -> bool:
        new_claims = self._numeric_claims(new)
        existing_claims = self._numeric_claims(existing)
        for new_value, new_context in new_claims:
            for existing_value, existing_context in existing_claims:
                if new_value == existing_value:
                    continue
                if self._numeric_contexts_overlap(new_context, existing_context):
                    return True
        return False

    def _numeric_claims(self, value: str) -> list[tuple[str, set[str]]]:
        tokens = re.findall(r"[a-z]+|\d+(?:\.\d+)?", value.lower())
        claims: list[tuple[str, set[str]]] = []
        for index, token in enumerate(tokens):
            if not self._NUMBER_RE.fullmatch(token):
                continue
            window = [*tokens[max(0, index - 6) : index], *tokens[index + 1 : index + 7]]
            context = {
                item
                for item in window
                if len(item) > 2 and not self._NUMBER_RE.fullmatch(item) and item not in self._NUMERIC_CONTEXT_STOPWORDS
            }
            if len(context) >= 2:
                claims.append((token, context))
        return claims

    def _numeric_contexts_overlap(self, left: set[str], right: set[str]) -> bool:
        if len(left) < 2 or len(right) < 2:
            return False
        overlap = left & right
        return len(overlap) / min(len(left), len(right)) >= 0.75

    def _weakens_policy(self, value: str) -> bool:
        for match in self._POLICY_WEAKENING_RE.finditer(value):
            prefix = value[max(0, match.start() - 32) : match.start()]
            if self._POLICY_WEAKENING_NEGATION_RE.search(prefix):
                continue
            return True
        return False

    def _normalize(self, value: str) -> str:
        return " ".join(value.strip().lower().split())
