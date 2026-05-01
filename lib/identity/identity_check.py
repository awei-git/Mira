from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IDENTITY_CORE_PATH = ROOT / "data" / "soul" / "identity_core.md"
DEFAULT_HASH_PATH = ROOT / "data" / "soul" / ".identity_hash"


class IdentityViolation(RuntimeError):
    """Raised when an identity-core invariant is broken."""


@dataclass(frozen=True)
class IdentityCheckResult:
    severity: str
    reason: str
    matched_pattern: str | None = None

    @property
    def ok(self) -> bool:
        return self.severity == "compatible"


_FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bMira\s+is\s+(just\s+)?(a\s+)?(chatbot|assistant|productivity\s+tool)\b", re.I),
        "Mira must not be reduced to a generic chatbot or productivity tool.",
    ),
    (
        re.compile(
            r"\b(done|complete|completed)\b.*\b(without|before)\b.*\b(verif(?:y|ied|ication)|observable)\b", re.I
        ),
        "Completion claims must be tied to observable verification.",
    ),
    (
        re.compile(r"\bbypass\b.*\b(writer|preflight|publish(?:ing)?)\b", re.I),
        "Public publishing must not bypass writer or preflight gates.",
    ),
    (
        re.compile(r"\b(iCloud|CloudKit|icloud)\b.*\b(primary|main|canonical)\b.*\b(dispatch|command|task)\b", re.I),
        "iCloud must not become the primary command dispatch path again.",
    ),
    (
        re.compile(r"\b(rewrite|delete|discard|reset)\b.*\b(soul|memory|skills|artifacts?)\b", re.I),
        "Existing soul, memory, skills, and artifacts must be preserved.",
    ),
)


def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_expected_hash(hash_path: Path) -> str:
    try:
        return hash_path.read_text(encoding="utf-8").strip().split()[0]
    except (IndexError, OSError):
        return ""


def verify_identity_core(
    identity_path: Path = DEFAULT_IDENTITY_CORE_PATH,
    hash_path: Path = DEFAULT_HASH_PATH,
) -> IdentityCheckResult:
    if not identity_path.exists():
        return IdentityCheckResult("violation", f"identity core missing: {identity_path}")
    if not hash_path.exists():
        return IdentityCheckResult("violation", f"identity hash missing: {hash_path}")

    expected = _read_expected_hash(hash_path)
    actual = compute_sha256(identity_path)
    if not expected:
        return IdentityCheckResult("violation", f"identity hash file is empty: {hash_path}")
    if actual != expected:
        return IdentityCheckResult(
            "violation",
            f"identity core hash mismatch: expected {expected[:12]}, got {actual[:12]}",
        )
    return IdentityCheckResult("compatible", "identity core hash verified")


def check_text_against_identity(
    content: str,
    *,
    identity_path: Path = DEFAULT_IDENTITY_CORE_PATH,
    hash_path: Path = DEFAULT_HASH_PATH,
) -> IdentityCheckResult:
    core = verify_identity_core(identity_path, hash_path)
    if not core.ok:
        return core

    for pattern, reason in _FORBIDDEN_PATTERNS:
        if pattern.search(content or ""):
            return IdentityCheckResult("violation", reason, matched_pattern=pattern.pattern)
    return IdentityCheckResult("compatible", "no rule-level identity conflict")
