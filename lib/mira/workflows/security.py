"""Security audit for workflow packs before they become executable."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class WorkflowAuditFinding:
    check: str
    reason: str
    pattern: str
    file: str = ""


@dataclass(frozen=True)
class WorkflowAuditResult:
    path: str
    findings: list[WorkflowAuditFinding] = field(default_factory=list)
    files_checked: list[str] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)
    audit_hash: str = ""

    @property
    def passed(self) -> bool:
        return not self.findings

    def to_dict(self, *, include_generated_at: bool = False) -> dict:
        data = {
            "workflow_pack_audit": {
                "path": self.path,
                "result": "pass" if self.passed else "blocked",
                "files_checked": self.files_checked,
                "file_hashes": self.file_hashes,
                "findings": [
                    {
                        "check": finding.check,
                        "reason": finding.reason,
                        "pattern": finding.pattern,
                        "file": finding.file,
                    }
                    for finding in self.findings
                ],
                "audit_hash": self.audit_hash,
                "enabled_at": None,
            }
        }
        if self.passed:
            data["workflow_pack_audit"]["enabled_at"] = _utc_now()
        if include_generated_at:
            data["workflow_pack_audit"]["generated_at"] = _utc_now()
        return data


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
    return _audit_result(target, [target])


def audit_workflow_bundle(path: Path | str) -> WorkflowAuditResult:
    """Audit a command plus the surrounding workflow-pack files it can enable."""

    target = Path(path)
    return _audit_result(target, _bundle_files(target))


def write_workflow_audit_artifact(result: WorkflowAuditResult, directory: Path | str) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_stem(result.path)}-{result.audit_hash[:12]}.json"
    target.write_text(json.dumps(result.to_dict(include_generated_at=True), indent=2, sort_keys=True), encoding="utf-8")
    return target


def _audit_result(target: Path, files: list[Path]) -> WorkflowAuditResult:
    findings = [finding for file in files for finding in _audit_file(file)]
    file_hashes = {str(file): _file_sha256(file) for file in files}
    result = WorkflowAuditResult(
        path=str(target),
        findings=findings,
        files_checked=[str(file) for file in files],
        file_hashes=file_hashes,
    )
    payload = result.to_dict()
    payload["workflow_pack_audit"]["enabled_at"] = None
    audit_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return WorkflowAuditResult(
        path=result.path,
        findings=result.findings,
        files_checked=result.files_checked,
        file_hashes=result.file_hashes,
        audit_hash=audit_hash,
    )


def _audit_file(path: Path) -> list[WorkflowAuditFinding]:
    body = path.read_text(encoding="utf-8")
    return [
        WorkflowAuditFinding("suspicious_pattern", reason, pattern, str(path))
        for pattern, reason in SUSPICIOUS_PATTERNS
        if re.search(pattern, body, flags=re.IGNORECASE | re.DOTALL)
    ]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_stem(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80] or "workflow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bundle_files(target: Path) -> list[Path]:
    root = _pack_root(target)
    candidates = [
        target,
        root / "pack.yaml",
        root / "router.md",
        *sorted((root / "commands").glob("*.yaml")),
        *sorted((root / "skills").glob("*/skill.yaml")),
        *sorted((root / "skills").glob("*/SKILL.md")),
    ]
    seen: set[Path] = set()
    files: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if candidate.exists() and resolved not in seen:
            seen.add(resolved)
            files.append(candidate)
    return files


def _pack_root(target: Path) -> Path:
    parts = target.parts
    if "commands" in parts:
        return target.parents[1]
    if target.name in {"skill.yaml", "SKILL.md"} and len(target.parents) >= 3:
        return target.parents[2]
    return target.parent
