"""Security audit for workflow packs before they become executable."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml


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


@dataclass(frozen=True)
class WorkflowTreeAuditResult:
    root: str
    results: list[WorkflowAuditResult]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def files_checked(self) -> list[str]:
        seen: set[str] = set()
        files: list[str] = []
        for result in self.results:
            for file in result.files_checked:
                if file not in seen:
                    seen.add(file)
                    files.append(file)
        return files

    @property
    def findings(self) -> list[WorkflowAuditFinding]:
        return [finding for result in self.results for finding in result.findings]

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow_tree_audit": {
                "root": self.root,
                "result": "pass" if self.passed else "blocked",
                "target_count": len(self.results),
                "files_checked_count": len(self.files_checked),
                "finding_count": len(self.findings),
                "files_checked": self.files_checked,
                "findings": [
                    {
                        "check": finding.check,
                        "reason": finding.reason,
                        "pattern": finding.pattern,
                        "file": finding.file,
                    }
                    for finding in self.findings
                ],
                "targets": [
                    {
                        "path": result.path,
                        "result": "pass" if result.passed else "blocked",
                        "finding_count": len(result.findings),
                        "audit_hash": result.audit_hash,
                    }
                    for result in self.results
                ],
            }
        }


SUSPICIOUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-rf\b", "destructive filesystem removal"),
    (r"\bsudo\b", "privilege escalation command"),
    (r"\bchmod\s+777\b", "broad permission escalation"),
    (r"\b(eval|exec)\s*\(", "dynamic code execution"),
    (r"\bos\.system\s*\(", "shell execution from workflow"),
    (r"\bsubprocess\.", "subprocess execution from workflow"),
    (r"\b(?:requests|httpx|urllib)\.(?:get|post|put|patch|delete)\s*\(", "direct network request"),
    (r"\bcurl\b.+\|\s*(sh|bash)", "remote shell execution"),
    (r"\b(?:/etc/(?:shadow|passwd)|~?/\.ssh|~?/\.aws|keychain)\b", "credential or system secret path"),
    (r"\bsecurity\s+find-(?:generic|internet)-password\b", "credential store access"),
    (r"\b(secret|password|api[_-]?key|private[_-]?key)\b", "credential material referenced in workflow"),
    (r"\bbase64\s+-d\b", "obfuscated payload decode"),
)


def audit_workflow_pack(path: Path | str) -> WorkflowAuditResult:
    target = Path(path)
    return _audit_result(target, [target], _audit_context([target]))


def audit_workflow_bundle(path: Path | str) -> WorkflowAuditResult:
    """Audit a command plus the surrounding workflow-pack files it can enable."""

    target = Path(path)
    files = _bundle_files(target)
    return _audit_result(target, files, _audit_context(files))


def audit_workflow_tree(root: Path | str) -> WorkflowTreeAuditResult:
    """Audit every workflow command and skill file under a workflow pack tree."""

    target = Path(root)
    targets = _workflow_tree_targets(target)
    return WorkflowTreeAuditResult(
        root=str(target),
        results=[
            audit_workflow_bundle(item) if _is_command_path(item) else audit_workflow_pack(item) for item in targets
        ],
    )


def audit_workflow_skill_candidate(
    name: str,
    *,
    skill_yaml: str = "",
    skill_markdown: str = "",
) -> WorkflowAuditResult:
    """Audit a generated/imported skill candidate before it is saved or enabled."""

    safe_name = _clean_trust_label(name) or "candidate"
    files = {
        f"candidate://{safe_name}/skill.yaml": skill_yaml,
        f"candidate://{safe_name}/SKILL.md": skill_markdown,
    }
    context = {"declares_live_side_effect": False}
    findings = [finding for label, body in files.items() if body for finding in _audit_body(label, body, context)]
    file_hashes = {label: hashlib.sha256(body.encode("utf-8")).hexdigest() for label, body in files.items() if body}
    result = WorkflowAuditResult(
        path=f"candidate://{safe_name}",
        findings=findings,
        files_checked=list(file_hashes),
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


def write_workflow_audit_artifact(result: WorkflowAuditResult, directory: Path | str) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_stem(result.path)}-{result.audit_hash[:12]}.json"
    artifact = result.to_dict(include_generated_at=True)
    _sign_audit_artifact(artifact, _audit_signing_key_path(target_dir))
    target.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    return target


def verify_workflow_audit_artifact(
    path: Path | str,
    key_path: Path | str | None = None,
    trust_dir: Path | str | None = None,
) -> bool:
    target = Path(path)
    artifact = json.loads(target.read_text(encoding="utf-8"))
    audit = artifact.get("workflow_pack_audit") or {}
    signature = audit.get("signature") or {}
    value = signature.get("value")
    if not value:
        return False
    keys = _verification_keys_for_artifact(
        artifact,
        key_path=Path(key_path) if key_path is not None else None,
        trust_dir=Path(trust_dir) if trust_dir is not None else target.parent,
    )
    return any(hmac.compare_digest(str(value), _signature_for_artifact(artifact, key)) for key in keys)


def rotate_workflow_audit_signing_key(directory: Path | str) -> dict:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    key_path = _audit_signing_key_path(target_dir)
    previous_key = key_path.read_text(encoding="utf-8").strip() if key_path.exists() else None
    previous_key_id = _key_id(previous_key) if previous_key else None
    if previous_key:
        _remember_verification_key(target_dir, previous_key, status="verify_only")
    active_key = secrets.token_hex(32)
    key_path.write_text(active_key, encoding="utf-8")
    key_path.chmod(0o600)
    active_key_id = _remember_verification_key(target_dir, active_key, status="active")
    return {
        "previous_key_id": previous_key_id,
        "active_key_id": active_key_id,
        "keyring_path": str(_audit_keyring_path(target_dir)),
    }


def export_workflow_audit_trust_bundle(
    directory: Path | str,
    destination: Path | str,
    *,
    operator: str = "local",
    scope: str = "workflow_audit",
) -> Path:
    source_dir = Path(directory)
    key = _load_or_create_signing_key(_audit_signing_key_path(source_dir))
    active_key_id = _key_id(key)
    keyring = _load_keyring(source_dir)
    keyring[active_key_id] = {"key_id": active_key_id, "key": key, "status": "active"}
    bundle = {
        "workflow_audit_trust_bundle": {
            "version": 1,
            "exported_at": _utc_now(),
            "active_key_id": active_key_id,
            "producer": {
                "operator": _clean_trust_label(operator) or "local",
                "scope": _clean_trust_label(scope) or "workflow_audit",
                "symmetric_key_material": True,
            },
            "keys": sorted(keyring.values(), key=lambda item: str(item["key_id"])),
        }
    }
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    target.chmod(0o600)
    return target


def import_workflow_audit_trust_bundle(
    directory: Path | str,
    source: Path | str,
    *,
    activate: bool = False,
    trusted_operators: Iterable[str] | None = None,
    allow_symmetric_activation: bool = False,
) -> dict:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    source_path = Path(source)
    bundle = json.loads(source_path.read_text(encoding="utf-8"))
    payload = bundle.get("workflow_audit_trust_bundle") or {}
    producer = payload.get("producer") if isinstance(payload.get("producer"), dict) else {}
    operator = _clean_trust_label(str(producer.get("operator") or "local"))
    scope = _clean_trust_label(str(producer.get("scope") or "workflow_audit"))
    trusted = _operator_is_trusted(operator, trusted_operators)
    bundle_fingerprint = _file_sha256(source_path)
    imported: list[str] = []
    active_key_id = str(payload.get("active_key_id") or "")
    active_key: str | None = None
    if not trusted:
        return {
            "imported_key_ids": imported,
            "active_key_id": None,
            "keyring_path": str(_audit_keyring_path(target_dir)),
            "trusted": False,
            "operator": operator,
            "scope": scope,
            "rejected_reason": "operator_not_trusted",
        }
    for entry in payload.get("keys", []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        key_id = str(entry.get("key_id") or "")
        if not key or _key_id(key) != key_id:
            continue
        status = "active" if activate and allow_symmetric_activation and key_id == active_key_id else "verify_only"
        _remember_verification_key(
            target_dir,
            key,
            status=status,
            origin_operator=operator,
            origin_scope=scope,
            origin_bundle=bundle_fingerprint,
            trust_status="trusted",
        )
        imported.append(key_id)
        if status == "active":
            active_key = key
    if activate and active_key:
        key_path = _audit_signing_key_path(target_dir)
        key_path.write_text(active_key, encoding="utf-8")
        key_path.chmod(0o600)
    return {
        "imported_key_ids": imported,
        "active_key_id": active_key_id if activate and active_key else None,
        "keyring_path": str(_audit_keyring_path(target_dir)),
        "trusted": True,
        "operator": operator,
        "scope": scope,
        "activation_blocked_reason": (
            "symmetric_activation_requires_explicit_allow" if activate and imported and not active_key else None
        ),
    }


def _audit_result(target: Path, files: list[Path], context: dict[str, bool]) -> WorkflowAuditResult:
    findings = [finding for file in files for finding in _audit_file(file, context)]
    findings.extend(_yaml_consistency_findings(files))
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


def _audit_file(path: Path, context: dict[str, bool]) -> list[WorkflowAuditFinding]:
    body = path.read_text(encoding="utf-8")
    return _audit_body(str(path), body, context)


def _audit_body(file_label: str, body: str, context: dict[str, bool]) -> list[WorkflowAuditFinding]:
    findings = [
        WorkflowAuditFinding("suspicious_pattern", reason, pattern, file_label)
        for pattern, reason in SUSPICIOUS_PATTERNS
        if re.search(pattern, body, flags=re.IGNORECASE | re.DOTALL)
    ]
    findings.extend(_semantic_findings(file_label, body, context))
    return findings


def _yaml_consistency_findings(files: list[Path]) -> list[WorkflowAuditFinding]:
    findings: list[WorkflowAuditFinding] = []
    for file in files:
        if file.suffix not in {".yaml", ".yml"}:
            continue
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict) or "steps" not in data:
            continue
        steps = {str(step.get("name")) for step in data.get("steps", []) if isinstance(step, dict) and step.get("name")}
        risk_actions = {str(key): str(value) for key, value in (data.get("risk_actions") or {}).items()}
        effect_steps = {str(key): str(value) for key, value in (data.get("effect_steps") or {}).items()}
        for step_name in sorted(set(risk_actions) - steps):
            findings.append(
                WorkflowAuditFinding(
                    "orphaned_risk_action",
                    "risk action references a workflow step that does not exist",
                    step_name,
                    str(file),
                )
            )
        for step_name, action_name in sorted(effect_steps.items()):
            if step_name not in steps:
                findings.append(
                    WorkflowAuditFinding(
                        "orphaned_effect_step",
                        "effect step references a workflow step that does not exist",
                        step_name,
                        str(file),
                    )
                )
            if _public_effect_name(step_name, action_name) and step_name not in risk_actions:
                findings.append(
                    WorkflowAuditFinding(
                        "public_effect_without_risk",
                        "public or external side effect is declared without a matching risk approval action",
                        f"{step_name}: {action_name}",
                        str(file),
                    )
                )
    return findings


def _semantic_findings(file_label: str, body: str, context: dict[str, bool]) -> list[WorkflowAuditFinding]:
    findings: list[WorkflowAuditFinding] = []
    for line in body.splitlines():
        normalized = _normalize_line(line)
        if not normalized or _is_negated_safety_line(normalized):
            continue
        if _privacy_downgrade(normalized):
            findings.append(
                WorkflowAuditFinding(
                    "privacy_downgrade",
                    "private or local-only memory appears to be routed into public context",
                    line.strip(),
                    file_label,
                )
            )
        if _undeclared_live_tool_use(normalized) and not context["declares_live_side_effect"]:
            findings.append(
                WorkflowAuditFinding(
                    "undeclared_tool_use",
                    "live connector or public side effect is mentioned without risk/effect declaration",
                    line.strip(),
                    file_label,
                )
            )
        if _memory_write_without_evidence(normalized):
            findings.append(
                WorkflowAuditFinding(
                    "memory_write_without_evidence",
                    "memory write proposal appears without an evidence or gateway requirement",
                    line.strip(),
                    file_label,
                )
            )
    return findings


def _audit_context(files: list[Path]) -> dict[str, bool]:
    declared = False
    for file in files:
        if file.suffix not in {".yaml", ".yml"}:
            continue
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        declared = declared or bool(data.get("risk_actions") or data.get("effect_steps"))
        declared = declared or bool(data.get("required_capabilities"))
    return {"declares_live_side_effect": declared}


def _normalize_line(line: str) -> str:
    return " ".join(line.strip().lower().split())


def _is_negated_safety_line(line: str) -> bool:
    return bool(
        re.search(
            r"\b(do not|don't|never|no\s+public|no\s+external|without approval|until .*approval|keep .*local|"
            r"outside the mvp path|behind .*approval|only through .*approval|separate approved step)\b",
            line,
        )
    )


def _privacy_downgrade(line: str) -> bool:
    return bool(
        re.search(r"\b(private|local[- ]only|secret|personal)\b", line)
        and re.search(r"\b(public|publish|post|rss|tweet|substack|external)\b", line)
        and re.search(r"\b(include|copy|move|route|send|publish|post|expose|export)\b", line)
    )


def _undeclared_live_tool_use(line: str) -> bool:
    return bool(
        re.search(r"\b(publish|post|send|email|tweet|upload|call api|webhook|publish rss|post to)\b", line)
        and re.search(r"\b(call|publish|post|send|email|tweet|upload|create|write|open)\b", line)
    )


def _public_effect_name(step_name: str, action_name: str) -> bool:
    return bool(
        re.search(
            r"(publish|post|send|email|tweet|upload|webhook|rss|substack|trade|alert)",
            f"{step_name} {action_name}",
            flags=re.IGNORECASE,
        )
    )


def _memory_write_without_evidence(line: str) -> bool:
    if not re.search(r"\b(write|store|save|commit|add)\b.*\b(memory|long[- ]term memory|kernel)\b", line):
        return False
    return not re.search(r"\b(evidence|evidence_ref|gateway|review|approval|proposal)\b", line)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_stem(path: str) -> str:
    stem = Path(path).stem
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)[:80] or "workflow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit_signing_key_path(directory: Path) -> Path:
    return directory / ".workflow_audit_signing_key"


def _audit_keyring_path(directory: Path) -> Path:
    return directory / ".workflow_audit_trusted_keys.json"


def _sign_audit_artifact(artifact: dict, key_path: Path) -> None:
    key = _load_or_create_signing_key(key_path)
    audit = artifact["workflow_pack_audit"]
    audit["signature"] = {
        "algorithm": "HMAC-SHA256",
        "key_id": _key_id(key),
        "value": _signature_for_artifact(artifact, key),
    }


def _load_or_create_signing_key(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        _remember_verification_key(path.parent, key, status="active")
        return key
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    path.chmod(0o600)
    _remember_verification_key(path.parent, key, status="active")
    return key


def _key_id(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _verification_keys_for_artifact(
    artifact: dict,
    *,
    key_path: Path | None,
    trust_dir: Path,
) -> list[str]:
    if key_path is not None:
        if not key_path.exists():
            return []
        return [key_path.read_text(encoding="utf-8").strip()]
    audit = artifact.get("workflow_pack_audit") or {}
    signature = audit.get("signature") or {}
    key_id = str(signature.get("key_id") or "")
    keys: dict[str, str] = {}
    active_key_path = _audit_signing_key_path(trust_dir)
    if active_key_path.exists():
        active_key = active_key_path.read_text(encoding="utf-8").strip()
        keys[_key_id(active_key)] = active_key
    for entry in _load_keyring(trust_dir).values():
        key = str(entry.get("key") or "")
        if key:
            keys[_key_id(key)] = key
    if key_id:
        return [keys[key_id]] if key_id in keys else []
    return list(keys.values())


def _remember_verification_key(
    directory: Path,
    key: str,
    *,
    status: str,
    origin_operator: str = "local",
    origin_scope: str = "workflow_audit",
    origin_bundle: str = "",
    trust_status: str = "local",
) -> str:
    key_id = _key_id(key)
    keyring = _load_keyring(directory)
    created_at = str(keyring.get(key_id, {}).get("created_at") or _utc_now())
    if status == "active":
        for entry in keyring.values():
            if entry.get("status") == "active":
                entry["status"] = "verify_only"
    keyring[key_id] = {
        "key_id": key_id,
        "key": key,
        "status": status,
        "created_at": created_at,
        "origin_operator": origin_operator,
        "origin_scope": origin_scope,
        "origin_bundle": origin_bundle,
        "trust_status": trust_status,
        "trusted_at": _utc_now(),
    }
    _write_keyring(directory, keyring, active_key_id=key_id if status == "active" else None)
    return key_id


def _load_keyring(directory: Path) -> dict[str, dict[str, Any]]:
    path = _audit_keyring_path(directory)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    data = payload.get("workflow_audit_keyring") or {}
    entries: dict[str, dict[str, Any]] = {}
    for entry in data.get("keys", []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        key_id = str(entry.get("key_id") or "")
        if key and _key_id(key) == key_id:
            entries[key_id] = {
                "key_id": key_id,
                "key": key,
                "status": str(entry.get("status") or "verify_only"),
                "created_at": str(entry.get("created_at") or _utc_now()),
                "origin_operator": str(entry.get("origin_operator") or "local"),
                "origin_scope": str(entry.get("origin_scope") or "workflow_audit"),
                "origin_bundle": str(entry.get("origin_bundle") or ""),
                "trust_status": str(entry.get("trust_status") or "local"),
                "trusted_at": str(entry.get("trusted_at") or entry.get("created_at") or _utc_now()),
            }
    return entries


def _write_keyring(directory: Path, keyring: dict[str, dict[str, Any]], *, active_key_id: str | None) -> None:
    path = _audit_keyring_path(directory)
    existing_active = ""
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")).get("workflow_audit_keyring") or {}
            existing_active = str(existing.get("active_key_id") or "")
        except json.JSONDecodeError:
            existing_active = ""
    payload = {
        "workflow_audit_keyring": {
            "version": 1,
            "active_key_id": active_key_id or existing_active,
            "updated_at": _utc_now(),
            "keys": sorted(keyring.values(), key=lambda item: str(item["key_id"])),
        }
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def _signature_for_artifact(artifact: dict, key: str) -> str:
    unsigned = json.loads(json.dumps(artifact, sort_keys=True))
    unsigned.get("workflow_pack_audit", {}).pop("signature", None)
    payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _clean_trust_label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:@/-]+", "_", value.strip())[:120]


def _operator_is_trusted(operator: str, trusted_operators: Iterable[str] | None) -> bool:
    if trusted_operators is None:
        return True
    trusted = {_clean_trust_label(str(item)) for item in trusted_operators}
    return operator in trusted


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


def _workflow_tree_targets(root: Path) -> list[Path]:
    candidates = [
        *sorted(root.glob("*/commands/*.yaml")),
        *sorted(root.glob("*/commands/*.yml")),
        *sorted(root.glob("*/skills/*/skill.yaml")),
        *sorted(root.glob("*/skills/*/SKILL.md")),
        *sorted(root.glob("*/pack.yaml")),
        *sorted(root.glob("*/router.md")),
    ]
    seen: set[Path] = set()
    targets: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if candidate.exists() and resolved not in seen:
            seen.add(resolved)
            targets.append(candidate)
    return targets


def _is_command_path(path: Path) -> bool:
    return "commands" in path.parts and path.suffix in {".yaml", ".yml"}


def _pack_root(target: Path) -> Path:
    parts = target.parts
    if "commands" in parts:
        return target.parents[1]
    if target.name in {"skill.yaml", "SKILL.md"} and len(target.parents) >= 3:
        return target.parents[2]
    return target.parent
