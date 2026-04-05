"""Unified preflight checks and artifact verification for Mira.

All side-effect actions (publish, file write, external API, delete) must
pass preflight before execution. Post-action verification confirms the
side effect actually happened.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import config

log = logging.getLogger("mira")


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str


@dataclass
class PreflightResult:
    passed: bool
    action_type: str
    checks: list[CheckResult] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "PASS" if self.passed else "BLOCKED"
        reasons = "; ".join(self.blocking_reasons) if self.blocking_reasons else "all checks passed"
        return f"PREFLIGHT {status} [{self.action_type}]: {reasons}"


# ---------------------------------------------------------------------------
# Minimum content lengths by action type
# ---------------------------------------------------------------------------
_MIN_CONTENT_LENGTH = {
    "publish": 200,       # articles must be > 200 chars
    "broadcast": 10,      # notes/messages
    "file_write": 1,      # non-empty
}

# Protected paths that should never be overwritten without explicit intent
_PROTECTED_PATHS = {
    "CLAUDE.md", ".env", "credentials.json", "config.yaml",
    "identity.md", "worldview.md",
}


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

    # Universal: instruction must be present
    instruction = context.get("instruction", "")
    if not instruction and action_type not in ("delete",):
        checks.append(CheckResult("instruction_present", False, "No instruction provided"))
        blockers.append("missing instruction")
    else:
        checks.append(CheckResult("instruction_present", True, "ok"))

    # Action-specific checks
    if action_type == "publish":
        _check_publish(context, checks, blockers)
    elif action_type == "file_write":
        _check_file_write(context, checks, blockers)
    elif action_type == "delete":
        _check_delete(context, checks, blockers)
    elif action_type == "broadcast":
        _check_broadcast(context, checks, blockers)
    elif action_type == "external_api":
        _check_external_api(context, checks, blockers)

    passed = len(blockers) == 0
    result = PreflightResult(
        passed=passed,
        action_type=action_type,
        checks=checks,
        blocking_reasons=blockers,
    )
    log.info("PREFLIGHT %s: %s", "PASS" if passed else "BLOCKED", result.summary())
    return result


def _check_publish(ctx: dict, checks: list, blockers: list):
    content = ctx.get("content", "")
    title = ctx.get("title", "")
    min_len = _MIN_CONTENT_LENGTH["publish"]

    if not title:
        checks.append(CheckResult("title_present", False, "No title"))
        blockers.append("missing title")
    else:
        checks.append(CheckResult("title_present", True, f"title='{title[:50]}'"))

    if not content:
        checks.append(CheckResult("content_present", False, "No content"))
        blockers.append("empty content")
    elif len(content) < min_len:
        checks.append(CheckResult("content_length", False,
                                  f"Content too short: {len(content)} < {min_len}"))
        blockers.append(f"content too short ({len(content)} chars)")
    else:
        checks.append(CheckResult("content_length", True, f"{len(content)} chars"))


def _check_file_write(ctx: dict, checks: list, blockers: list):
    path_str = ctx.get("path", "")
    content = ctx.get("content", "")

    if not path_str:
        checks.append(CheckResult("path_present", False, "No path"))
        blockers.append("missing file path")
        return

    path = Path(path_str)

    # Check parent exists
    if not path.parent.exists():
        checks.append(CheckResult("parent_exists", False, f"Parent dir missing: {path.parent}"))
        blockers.append(f"parent directory does not exist: {path.parent}")
    else:
        checks.append(CheckResult("parent_exists", True, "ok"))

    # Check protected paths
    if path.name in _PROTECTED_PATHS:
        checks.append(CheckResult("not_protected", False, f"Protected file: {path.name}"))
        blockers.append(f"refusing to overwrite protected file: {path.name}")
    else:
        checks.append(CheckResult("not_protected", True, "ok"))

    # Check content non-empty
    if not content:
        checks.append(CheckResult("content_present", False, "Empty content"))
        blockers.append("empty content for file write")
    else:
        checks.append(CheckResult("content_present", True, f"{len(content)} chars"))


def _check_delete(ctx: dict, checks: list, blockers: list):
    path_str = ctx.get("path", "")
    recoverable = ctx.get("recoverable", False)

    if not path_str:
        checks.append(CheckResult("path_present", False, "No path"))
        blockers.append("missing path for delete")
        return

    path = Path(path_str)
    if not path.exists():
        checks.append(CheckResult("target_exists", False, f"Does not exist: {path}"))
        blockers.append("target does not exist")
        return

    checks.append(CheckResult("target_exists", True, str(path)))

    if not recoverable:
        checks.append(CheckResult("recoverable", False, "Not recoverable — needs backup"))
        blockers.append("delete is not recoverable — create backup first")
    else:
        checks.append(CheckResult("recoverable", True, "ok"))


def _check_broadcast(ctx: dict, checks: list, blockers: list):
    content = ctx.get("content", "")
    if not content or len(content) < _MIN_CONTENT_LENGTH["broadcast"]:
        checks.append(CheckResult("content_present", False, "Content too short"))
        blockers.append("broadcast content too short")
    else:
        checks.append(CheckResult("content_present", True, f"{len(content)} chars"))


def _check_external_api(ctx: dict, checks: list, blockers: list):
    endpoint = ctx.get("endpoint", "")
    if not endpoint:
        checks.append(CheckResult("endpoint_present", False, "No endpoint"))
        blockers.append("missing API endpoint")
    else:
        checks.append(CheckResult("endpoint_present", True, endpoint[:100]))


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


def verify_artifact(artifact_type: str, path_or_url: str,
                    expected: dict | None = None) -> VerifyResult:
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
            checks.append(CheckResult("content_length", False,
                                      f"{len(content)} < {min_len}"))
            reasons.append(f"published content too short: {len(content)} chars")
        else:
            checks.append(CheckResult("content_length", True, f"{len(content)} chars"))
