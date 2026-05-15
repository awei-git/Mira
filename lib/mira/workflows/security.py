"""Security audit for workflow packs before they become executable."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class WorkflowAuditFinding:
    check: str
    reason: str
    pattern: str


@dataclass(frozen=True)
class WorkflowAuditResult:
    path: str
    findings: list[WorkflowAuditFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings


SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-rf\b", "destructive filesystem removal"),
    (r"\bchmod\s+777\b", "broad permission escalation"),
    (r"\b(eval|exec)\s*\(", "dynamic code execution"),
    (r"\bos\.system\s*\(", "shell execution from workflow"),
    (r"\bsubprocess\.", "subprocess execution from workflow"),
    (r"\bcurl\b.+\|\s*(sh|bash)", "remote shell execution"),
    (r"\b(secret|password|api[_-]?key|private[_-]?key)\b", "credential material referenced in workflow"),
    (r"\bbase64\s+-d\b", "obfuscated payload decode"),
)


def audit_workflow_pack(path: Path | str) -> WorkflowAuditResult:
    target = Path(path)
    body = target.read_text(encoding="utf-8")
    findings = [
        WorkflowAuditFinding("suspicious_pattern", reason, pattern)
        for pattern, reason in SUSPICIOUS_PATTERNS
        if re.search(pattern, body, flags=re.IGNORECASE | re.DOTALL)
    ]
    return WorkflowAuditResult(path=str(target), findings=findings)
