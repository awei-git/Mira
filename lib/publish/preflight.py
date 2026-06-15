"""Unified preflight checks and artifact verification for Mira.

All side-effect actions (publish, file write, external API, delete) must
pass preflight before execution. Post-action verification confirms the
side effect actually happened.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger("mira")


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    proves: str = ""
    assumes: str = ""


@dataclass
class PreflightResult:
    passed: bool
    action_type: str
    checks: list[CheckResult] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    verification_trace: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        status = "PASS" if self.passed else "BLOCKED"
        reasons = "; ".join(self.blocking_reasons) if self.blocking_reasons else "all checks passed"
        return f"PREFLIGHT {status} [{self.action_type}]: {reasons}"


# ---------------------------------------------------------------------------
# Minimum content lengths by action type
# ---------------------------------------------------------------------------
_MIN_CONTENT_LENGTH = {
    "publish": 200,  # articles must be > 200 chars
    "broadcast": 10,  # notes/messages
    "file_write": 1,  # non-empty
}

# Protected paths that should never be overwritten without explicit intent
_PROTECTED_PATHS = {
    "CLAUDE.md",
    ".env",
    "credentials.json",
    "config.yaml",
    "content_guard_hashes.json",
    "identity.md",
    "worldview.md",
}

_BLOCKED_PUBLISH_SENSITIVITY = {"confidential", "regulated"}
_CONTENT_GUARD_HASH_FILE = Path(config.MIRA_ROOT) / "data" / "content_guard_hashes.json"
_CONTENT_GUARD_FILES = (
    "agents/writer/checklists/anti-ai.md",
    "config/unethical_phrases.txt",
    "lib/sensitivity_patterns.json",
)
_SENSITIVE_TOPIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bTetra\b", re.I), "mentions Tetra"),
    (re.compile(r"\b(portfolio|position size|cost basis|stop loss|take profit)\b", re.I), "portfolio/trading term"),
    (re.compile(r"\b(buy|sell|long|short)\b.{0,80}\b(shares?|contracts?|position)\b", re.I), "trading action"),
    (re.compile(r"[$¥]\s?\d[\d,]{3,}(?:\.\d+)?", re.I), "large financial amount"),
)
HALLUCINATION_PRONE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "legal": (
        re.compile(r"\b\d+\s+(?:U\.S\.|S\. Ct\.|F\.\d+d|F\. Supp\. ?\d*d?|Cal\.|N\.Y\. ?\d*d?)\s+\d+\b", re.I),
        re.compile(r"\b\d+\s+U\.S\.C\.?\s+§+\s*\d+[A-Za-z0-9_.-]*\b", re.I),
        re.compile(r"\b(?:Section|§)\s+\d+[A-Za-z0-9_.-]*\b.{0,50}\b(?:Act|Code|law|statute)\b", re.I),
        re.compile(r"根据[^。！？\n]{1,30}法第[一二三四五六七八九十百千万\d]+条"),
    ),
    "historical": (
        re.compile(
            r"\b(?:on\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+\d{1,2},\s+\d{4}\b.{0,120}\b"
            r"(?:happened|occurred|began|ended|signed|declared|assassinated|invaded|founded|fell|collapsed|war|revolution)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:in|during)\s+(?:1[0-9]{3}|20[0-9]{2})\b.{0,100}\b"
            r"(?:happened|occurred|began|ended|signed|declared|invaded|founded|collapsed|war|revolution)\b",
            re.I,
        ),
        re.compile(
            r"在(?:公元)?[一二三四五六七八九十百千万零〇\d]{2,4}年，?[^。！？\n]{1,80}(?:发生|爆发|成立|签署|灭亡|开始|结束)"
        ),
    ),
    "code_api": (
        re.compile(r"\b[A-Za-z_]\w*\s*\([^)\n]{0,120}\)\s*(?:->|=>|:)?"),
        re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\s*\([^)\n]{0,120}\)"),
        re.compile(
            r"\b(?:React|Vue|Angular|Django|Flask|FastAPI|Pandas|NumPy|TensorFlow|PyTorch|"
            r"OpenAI|LangChain|Next\.js|Node\.js|Python)\s+(?:v(?:ersion)?\s*)?\d+(?:\.\d+){0,3}\b",
            re.I,
        ),
        re.compile(
            r"\b(?:introduced|removed|deprecated|available)\s+in\s+(?:v(?:ersion)?\s*)?\d+(?:\.\d+){0,3}\b", re.I
        ),
    ),
}


def check_hallucination_risk(content: str) -> list[str]:
    triggered: list[str] = []
    for domain, patterns in HALLUCINATION_PRONE_PATTERNS.items():
        if any(pattern.search(content) for pattern in patterns):
            triggered.append(domain)
    return triggered


def _content_smells_like_hallucination(text: str) -> tuple[bool, list[str]]:
    """Check for patterns that indicate plausible-but-unverified content.

    Returns (is_suspicious, reasons).
    These are heuristics, not certainties—they flag content for deeper review.
    """
    reasons = []

    # 1. Unverifiable statistics without source attribution
    stat_pattern = re.findall(r"\d{1,3}(\.\d+)?%\s+of", text)
    has_citation = re.search(r"\([^)]*\d{4}[^)]*\)|according to|reported by|published in", text, re.IGNORECASE)
    if len(stat_pattern) >= 2 and not has_citation:
        reasons.append(f"Multiple statistics ({len(stat_pattern)}) without any source citation")

    # 2. Vague authority appeals
    vague_auth = re.findall(
        r"(studies show|experts say|research indicates|many believe|it is widely|growing evidence)",
        text,
        re.IGNORECASE,
    )
    if len(vague_auth) >= 2:
        reasons.append(f"Vague authority appeals without specific attribution: {vague_auth[:3]}")

    # 3. Dense specific-but-unverifiable claims (named entities without context)
    # Heuristic: ratio of proper nouns to citation-like patterns
    proper_nouns = len(re.findall(r"\b[A-Z][a-z]+ (?:et al\.|and|[A-Z])", text))
    if proper_nouns >= 5 and not has_citation:
        reasons.append(f"Many named references ({proper_nouns}) without any verifiable source")

    # 4. Overly neat structural parallelism (de-AI smell that also signals fabricated coherence)
    parallelism = re.findall(r"(not\s+\w+\s+but\s+\w+|不是\w+而是\w+)", text, re.IGNORECASE)
    if len(parallelism) >= 3:
        reasons.append(
            f"Excessive structural parallelism ({len(parallelism)} instances) — possible fabricated coherence"
        )

    return (len(reasons) > 0, reasons)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_content_guard_integrity(checks: list, blockers: list) -> None:
    root = Path(config.MIRA_ROOT)
    try:
        expected_data = json.loads(_CONTENT_GUARD_HASH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        message = (
            "CRITICAL content guard integrity check failed: "
            f"cannot read {_CONTENT_GUARD_HASH_FILE}; run `mira update-content-guard` after human review"
        )
        log.critical("%s: %s", message, exc)
        checks.append(
            CheckResult(
                "content_guard_integrity",
                False,
                message,
                proves="content guard rule hashes match the human-authorized ledger",
                assumes="the hash ledger was updated only by explicit human authorization",
            )
        )
        blockers.append("content guard hash ledger unavailable")
        return

    expected_hashes = expected_data.get("files", {})
    mismatches = []
    for rel_path in _CONTENT_GUARD_FILES:
        expected = expected_hashes.get(rel_path, {}).get("sha256")
        path = root / rel_path
        try:
            actual = _sha256_file(path)
        except OSError as exc:
            mismatches.append(f"{rel_path}: unreadable ({exc})")
            continue
        if actual != expected:
            mismatches.append(f"{rel_path}: expected {expected or 'missing'}, got {actual}")

    if mismatches:
        message = (
            "CRITICAL content guard integrity mismatch: "
            + "; ".join(mismatches)
            + "; run `mira update-content-guard` after human review"
        )
        log.critical(message)
        checks.append(
            CheckResult(
                "content_guard_integrity",
                False,
                message,
                proves="content guard rule hashes match the human-authorized ledger",
                assumes="the hash ledger was updated only by explicit human authorization",
            )
        )
        blockers.append("content guard integrity mismatch")
        return

    checks.append(
        CheckResult(
            "content_guard_integrity",
            True,
            "ok",
            proves="content guard rule hashes match the human-authorized ledger",
            assumes="the hash ledger was updated only by explicit human authorization",
        )
    )


def preflight_check(action_type: str, context: dict) -> PreflightResult:
    """Run preflight checks before a side-effect action.

    Args:
        action_type: one of "publish", "file_write", "external_api", "broadcast", "delete"
        context: action-specific fields:
            publish:  {content, title, platform}
            file_write: {path, content}
            external_api: {endpoint, method}
            broadcast: {content, channel}
            delete: {path, recoverable}

    Returns:
        PreflightResult with pass/fail and structured check details.
    """
    checks = []
    blockers = []

    if action_type in ("publish", "broadcast"):
        _check_content_guard_integrity(checks, blockers)

    # Universal: instruction must be present
    instruction = context.get("instruction", "")
    if not instruction and action_type not in ("delete",):
        checks.append(
            CheckResult(
                "instruction_present",
                False,
                "No instruction provided",
                proves="an instruction exists for this action",
                assumes="presence of instruction correlates with intentional rather than accidental invocation",
            )
        )
        blockers.append("missing instruction")
    else:
        checks.append(
            CheckResult(
                "instruction_present",
                True,
                "ok",
                proves="an instruction exists for this action",
                assumes="presence of instruction correlates with intentional rather than accidental invocation",
            )
        )

    # Action-specific checks
    if action_type == "publish":
        _check_publish(context, checks, blockers)
        _check_hallucination_smell(context, checks, blockers)
        _check_hallucination_domain_risk(context, checks, blockers)
    elif action_type == "file_write":
        _check_file_write(context, checks, blockers)
    elif action_type == "delete":
        _check_delete(context, checks, blockers)
    elif action_type == "broadcast":
        _check_broadcast(context, checks, blockers)
        _check_hallucination_smell(context, checks, blockers)
    elif action_type == "external_api":
        _check_external_api(context, checks, blockers)

    passed = len(blockers) == 0
    verification_trace = [
        {
            "check_name": c.name,
            "passed": c.passed,
            "proves": c.proves,
            "assumes": c.assumes,
        }
        for c in checks
    ]
    result = PreflightResult(
        passed=passed,
        action_type=action_type,
        checks=checks,
        blocking_reasons=blockers,
        verification_trace=verification_trace,
    )
    log.info("PREFLIGHT %s: %s", "PASS" if passed else "BLOCKED", result.summary())
    log.info("PREFLIGHT_TRACE [%s]: %s", action_type, json.dumps(verification_trace))
    try:
        _logs_dir = Path(config.LOGS_DIR) if not isinstance(config.LOGS_DIR, Path) else config.LOGS_DIR
        _logs_dir.mkdir(parents=True, exist_ok=True)
        _preflight_record = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "action_type": action_type,
            "verdict": "pass" if passed else "fail",
            "fields_validated": {
                k: (str(v)[:300] if isinstance(v, str) else v) for k, v in context.items() if k != "instruction"
            },
            "checks": [{"name": c.name, "passed": c.passed, "message": c.message} for c in checks],
            "blocking_reasons": blockers,
            "verification_trace": verification_trace,
        }
        _pf_log = _logs_dir / "publish_preflight_log.jsonl"
        with open(_pf_log, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_preflight_record) + "\n")
    except Exception as _pe:
        log.warning("Failed to write preflight log entry: %s", _pe)
    if not passed:
        try:
            _rej_dir = Path(config.MIRA_ROOT) / "logs" / "scaffold_rejections"
            _rej_dir.mkdir(parents=True, exist_ok=True)
            _rej_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "agent_id": context.get("agent_id", "unknown"),
                "pipeline_stage": context.get("pipeline_stage", action_type),
                "rejection_reason": "; ".join(blockers),
                "content_preview": str(context.get("content", ""))[:200],
            }
            _rej_file = _rej_dir / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
            with open(_rej_file, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_rej_entry, ensure_ascii=False) + "\n")
        except Exception as _re:
            log.warning("Failed to write scaffold rejection: %s", _re)
        try:
            _content_bytes = str(context.get("content", "")).encode("utf-8", errors="replace")
            _audit_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "guard_name": "preflight_check",
                "trigger_reason": "; ".join(blockers),
                "content_length": len(str(context.get("content", ""))),
                "severity": "blocked",
                "task_id": context.get("task_id", ""),
                "content_hash": hashlib.sha1(_content_bytes).hexdigest()[:8],
            }
            _audit_log = Path(config.MIRA_ROOT) / "logs" / "scaffolding_audit.jsonl"
            _audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(_audit_log, "a", encoding="utf-8") as _f:
                _f.write(json.dumps(_audit_entry, ensure_ascii=False) + "\n")
        except Exception as _ae:
            log.warning("Failed to write scaffolding audit entry: %s", _ae)
    return result


def _check_publish(ctx: dict, checks: list, blockers: list):
    content = ctx.get("content", "")
    title = ctx.get("title", "")
    min_len = _MIN_CONTENT_LENGTH["publish"]

    if not title:
        checks.append(
            CheckResult(
                "title_present",
                False,
                "No title",
                proves="title field was provided",
                assumes="non-empty title correlates with a properly prepared publish payload",
            )
        )
        blockers.append("missing title")
    else:
        checks.append(
            CheckResult(
                "title_present",
                True,
                f"title='{title[:50]}'",
                proves="title field was provided",
                assumes="non-empty title correlates with a properly prepared publish payload",
            )
        )

    if not content:
        checks.append(
            CheckResult(
                "content_present",
                False,
                "No content",
                proves="content field is non-empty",
                assumes="non-empty content is the intended article payload",
            )
        )
        blockers.append("empty content")
    elif len(content) < min_len:
        checks.append(
            CheckResult(
                "content_length",
                False,
                f"Content too short: {len(content)} < {min_len}",
                proves="content is non-trivially long",
                assumes="length correlates with completeness",
            )
        )
        blockers.append(f"content too short ({len(content)} chars)")
    else:
        checks.append(
            CheckResult(
                "content_length",
                True,
                f"{len(content)} chars",
                proves="content is non-trivially long",
                assumes="length correlates with completeness",
            )
        )

    _check_sensitivity(ctx, "publish", checks, blockers)


def _check_hallucination_smell(ctx: dict, checks: list, blockers: list) -> None:
    content = ctx.get("content", "")
    is_suspicious, smell_reasons = _content_smells_like_hallucination(content)
    if not is_suspicious:
        checks.append(
            CheckResult(
                "hallucination_smell",
                True,
                "ok",
                proves="content did not match heuristic plausible-but-unverified patterns",
                assumes="regex heuristics catch only obvious review triggers",
            )
        )
        return

    message = f"Hallucination smell detected: {smell_reasons}"
    log.warning("Content smells like hallucination: %s", smell_reasons)
    checks.append(
        CheckResult(
            "hallucination_smell",
            False,
            message,
            proves="content matched heuristic plausible-but-unverified patterns",
            assumes="heuristic matches require deeper review, not automatic factual certainty",
        )
    )
    if getattr(config, "STRICT_HALLUCINATION_GUARD", False):
        blockers.append(message)


def _check_hallucination_domain_risk(ctx: dict, checks: list, blockers: list) -> None:
    content = ctx.get("content", "")
    triggered_domains = check_hallucination_risk(content)
    if not triggered_domains:
        checks.append(
            CheckResult(
                "hallucination_domain_risk",
                True,
                "ok",
                proves="content did not match legal, historical, or code/API hallucination-prone domain patterns",
                assumes="regex heuristics catch only obvious high-risk factual domains",
            )
        )
        return

    domains = ", ".join(triggered_domains)
    message = f"mandatory source verification required for hallucination-prone domains: {domains}"
    log.warning("HALLUCINATION_DOMAIN_RISK domains=%s", domains)
    checks.append(
        CheckResult(
            "hallucination_domain_risk",
            False,
            message,
            proves="content matched legal, historical, or code/API hallucination-prone domain patterns",
            assumes="pattern matches indicate claims that need source verification before publication",
        )
    )
    blockers.append(message)


def _iter_memory_sensitivities(ctx: dict) -> list[str]:
    values: list[str] = []
    for key in ("memories", "retrieved_memories", "memory_context"):
        raw = ctx.get(key)
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict) and item.get("sensitivity"):
                values.append(str(item["sensitivity"]).strip().lower())
    if ctx.get("sensitivity"):
        values.append(str(ctx["sensitivity"]).strip().lower())
    return values


def _record_sensitivity_block(ctx: dict, reason: str, channel: str) -> None:
    try:
        audit_dir = Path(config.DATA_DIR) / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        content = str(ctx.get("content", ""))
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "reason": reason,
            "task_id": ctx.get("task_id", ""),
            "title": str(ctx.get("title", ""))[:200],
            "content_hash": hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:12],
            "content_preview": content[:160],
        }
        with (audit_dir / "sensitivity_blocks.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("Failed to write sensitivity block audit: %s", exc)


def _check_sensitivity(ctx: dict, channel: str, checks: list, blockers: list) -> None:
    sensitivities = _iter_memory_sensitivities(ctx)
    blocked = sorted({s for s in sensitivities if s in _BLOCKED_PUBLISH_SENSITIVITY})
    if blocked:
        reason = f"blocked sensitivity in publish payload: {', '.join(blocked)}"
        checks.append(
            CheckResult(
                "sensitivity_allowed",
                False,
                reason,
                proves="publish payload does not include confidential or regulated memory",
                assumes="memory sensitivity labels were assigned correctly upstream",
            )
        )
        blockers.append(reason)
        _record_sensitivity_block(ctx, reason, channel)
        return

    text = "\n".join(str(ctx.get(k, "")) for k in ("title", "content", "instruction"))
    for pattern, label in _SENSITIVE_TOPIC_PATTERNS:
        if pattern.search(text):
            reason = f"sensitive topic blocked: {label}"
            checks.append(
                CheckResult(
                    "sensitivity_topic",
                    False,
                    reason,
                    proves="publish payload does not include obvious private trading/portfolio content",
                    assumes="regex guard catches only high-confidence sensitive cases before LLM topic check exists",
                )
            )
            blockers.append(reason)
            _record_sensitivity_block(ctx, reason, channel)
            return

    checks.append(
        CheckResult(
            "sensitivity_allowed",
            True,
            "ok",
            proves="no confidential/regulated memory labels or high-confidence sensitive topic patterns were found",
            assumes="rule-level scan is a first-pass guard, not full semantic privacy review",
        )
    )


def _check_file_write(ctx: dict, checks: list, blockers: list):
    path_str = ctx.get("path", "")
    content = ctx.get("content", "")

    if not path_str:
        checks.append(
            CheckResult(
                "path_present",
                False,
                "No path",
                proves="a destination path was specified",
                assumes="path presence means write target is intentional",
            )
        )
        blockers.append("missing file path")
        return

    path = Path(path_str)

    # Check parent exists
    if not path.parent.exists():
        checks.append(
            CheckResult(
                "parent_exists",
                False,
                f"Parent dir missing: {path.parent}",
                proves="destination directory exists on disk",
                assumes="directory existence means the write will succeed",
            )
        )
        blockers.append(f"parent directory does not exist: {path.parent}")
    else:
        checks.append(
            CheckResult(
                "parent_exists",
                True,
                "ok",
                proves="destination directory exists on disk",
                assumes="directory existence means the write will succeed",
            )
        )

    # Check protected paths
    if path.name in _PROTECTED_PATHS:
        checks.append(
            CheckResult(
                "not_protected",
                False,
                f"Protected file: {path.name}",
                proves="filename is not on the protected list",
                assumes="protection list covers all critical config and identity files",
            )
        )
        blockers.append(f"refusing to overwrite protected file: {path.name}")
    else:
        checks.append(
            CheckResult(
                "not_protected",
                True,
                "ok",
                proves="filename is not on the protected list",
                assumes="protection list covers all critical config and identity files",
            )
        )

    # Check content non-empty
    if not content:
        checks.append(
            CheckResult(
                "content_present",
                False,
                "Empty content",
                proves="content is non-empty",
                assumes="non-empty content is the intended file payload",
            )
        )
        blockers.append("empty content for file write")
    else:
        checks.append(
            CheckResult(
                "content_present",
                True,
                f"{len(content)} chars",
                proves="content is non-empty",
                assumes="non-empty content is the intended file payload",
            )
        )


def _check_delete(ctx: dict, checks: list, blockers: list):
    path_str = ctx.get("path", "")
    recoverable = ctx.get("recoverable", False)

    if not path_str:
        checks.append(
            CheckResult(
                "path_present",
                False,
                "No path",
                proves="a target path was specified",
                assumes="path presence means delete target is intentional",
            )
        )
        blockers.append("missing path for delete")
        return

    path = Path(path_str)
    if not path.exists():
        checks.append(
            CheckResult(
                "target_exists",
                False,
                f"Does not exist: {path}",
                proves="target path exists on disk",
                assumes="existence means safe to attempt delete",
            )
        )
        blockers.append("target does not exist")
        return

    checks.append(
        CheckResult(
            "target_exists",
            True,
            str(path),
            proves="target path exists on disk",
            assumes="existence means safe to attempt delete",
        )
    )

    if not recoverable:
        checks.append(
            CheckResult(
                "recoverable",
                False,
                "Not recoverable — needs backup",
                proves="caller flagged operation as recoverable",
                assumes="recoverable flag means a backup exists or the operation is reversible",
            )
        )
        blockers.append("delete is not recoverable — create backup first")
    else:
        checks.append(
            CheckResult(
                "recoverable",
                True,
                "ok",
                proves="caller flagged operation as recoverable",
                assumes="recoverable flag means a backup exists or the operation is reversible",
            )
        )


def _check_broadcast(ctx: dict, checks: list, blockers: list):
    content = ctx.get("content", "")
    if not content or len(content) < _MIN_CONTENT_LENGTH["broadcast"]:
        checks.append(
            CheckResult(
                "content_present",
                False,
                "Content too short",
                proves="content meets minimum broadcast length",
                assumes="length threshold distinguishes real content from stubs or error messages",
            )
        )
        blockers.append("broadcast content too short")
    else:
        checks.append(
            CheckResult(
                "content_present",
                True,
                f"{len(content)} chars",
                proves="content meets minimum broadcast length",
                assumes="length threshold distinguishes real content from stubs or error messages",
            )
        )


def _check_external_api(ctx: dict, checks: list, blockers: list):
    endpoint = ctx.get("endpoint", "")
    if not endpoint:
        checks.append(
            CheckResult(
                "endpoint_present",
                False,
                "No endpoint",
                proves="an endpoint URL was provided",
                assumes="endpoint presence means the API call is intentional",
            )
        )
        blockers.append("missing API endpoint")
    else:
        checks.append(
            CheckResult(
                "endpoint_present",
                True,
                endpoint[:100],
                proves="an endpoint URL was provided",
                assumes="endpoint presence means the API call is intentional",
            )
        )


# ---------------------------------------------------------------------------
# Post-action artifact verification
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    verified: bool
    artifact_type: str
    checks: list[CheckResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "VERIFIED" if self.verified else "FAILED"
        detail = "; ".join(self.reasons) if self.reasons else "all checks passed"
        return f"VERIFY {status} [{self.artifact_type}]: {detail}"


def verify_artifact(artifact_type: str, path_or_url: str, expected: dict | None = None) -> VerifyResult:
    """Verify that a side-effect action produced the expected artifact.

    Args:
        artifact_type: "file", "url", "publish"
        path_or_url: file path or URL to check
        expected: optional dict with {min_size, contains, format}

    Returns:
        VerifyResult with verified/failed and details.
    """
    checks = []
    reasons = []
    expected = expected or {}

    if artifact_type == "file":
        _verify_file(path_or_url, expected, checks, reasons)
    elif artifact_type == "publish":
        _verify_publish(path_or_url, expected, checks, reasons)
    else:
        checks.append(CheckResult("type_known", False, f"Unknown type: {artifact_type}"))
        reasons.append(f"unknown artifact type: {artifact_type}")

    verified = len(reasons) == 0
    result = VerifyResult(
        verified=verified,
        artifact_type=artifact_type,
        checks=checks,
        reasons=reasons,
    )
    log.info("ARTIFACT_VERIFY %s: %s", "OK" if verified else "FAIL", result.summary())
    return result


def _verify_file(path_str: str, expected: dict, checks: list, reasons: list):
    path = Path(path_str)

    if not path.exists():
        checks.append(CheckResult("exists", False, f"File not found: {path}"))
        reasons.append(f"file does not exist: {path}")
        return

    checks.append(CheckResult("exists", True, str(path)))

    size = path.stat().st_size
    min_size = expected.get("min_size", 1)
    if size < min_size:
        checks.append(CheckResult("min_size", False, f"{size} < {min_size} bytes"))
        reasons.append(f"file too small: {size} bytes")
    else:
        checks.append(CheckResult("min_size", True, f"{size} bytes"))

    # Check content contains expected string
    contains = expected.get("contains")
    if contains:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if contains in text:
                checks.append(CheckResult("contains", True, f"found '{contains[:30]}'"))
            else:
                checks.append(CheckResult("contains", False, f"missing '{contains[:30]}'"))
                reasons.append(f"expected content not found: '{contains[:30]}'")
        except OSError as e:
            checks.append(CheckResult("readable", False, str(e)))
            reasons.append(f"cannot read file: {e}")


def _verify_publish(identifier: str, expected: dict, checks: list, reasons: list):
    """Verify a publish action. identifier could be a slug or URL."""
    # For publish, we check that the output file exists in the published dir
    published_dir = config.WRITINGS_OUTPUT_DIR / "_published"

    if not published_dir.exists():
        checks.append(CheckResult("published_dir", False, "Published dir missing"))
        reasons.append("published directory does not exist")
        return

    # Look for the slug in published files
    matches = list(published_dir.glob(f"*{identifier}*"))
    if not matches:
        checks.append(CheckResult("published_file", False, f"No file matching '{identifier}'"))
        reasons.append(f"no published file found for '{identifier}'")
    else:
        checks.append(CheckResult("published_file", True, str(matches[0])))

        # Check content length
        content = matches[0].read_text(encoding="utf-8", errors="replace")
        min_len = expected.get("min_length", 200)
        if len(content) < min_len:
            checks.append(CheckResult("content_length", False, f"{len(content)} < {min_len}"))
            reasons.append(f"published content too short: {len(content)} chars")
        else:
            checks.append(CheckResult("content_length", True, f"{len(content)} chars"))
