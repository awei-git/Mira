#!/usr/bin/env python3
"""Mira Self-Audit — autonomous code + log scanning, fix proposals, self-testing.

Runs daily as a scheduled background job. Scans own logs for recurring errors,
checks codebase for anti-patterns, generates fix proposals, self-tests them,
and notifies the user with a report.

Usage:
    python self_audit.py              # Full audit
    python self_audit.py --logs-only  # Only scan logs
    python self_audit.py --tests-only # Only run tests
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

# Paths
_HERE = Path(__file__).resolve().parent
_AGENTS_DIR = _HERE.parent
_MIRA_ROOT = _AGENTS_DIR.parent
from config import LOGS_DIR as _LOGS_DIR

_SHARED_DIR = _AGENTS_DIR.parent / "lib"

sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_SHARED_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [self-audit] %(message)s")
log = logging.getLogger("self-audit")


# ---------------------------------------------------------------------------
# Step 1: Scan logs for recurring errors
# ---------------------------------------------------------------------------


def scan_logs(days: int = 1) -> list[dict]:
    """Scan recent log files for error patterns. Returns deduplicated findings."""
    findings = []
    error_counts: dict[str, int] = {}
    today = datetime.now()

    for i in range(days):
        date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        log_file = _LOGS_DIR / f"{date}.log"
        if not log_file.exists():
            continue

        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for line in text.splitlines():
            if "[ERROR]" in line or "[WARNING]" in line and "failed" in line.lower():
                # Extract error template (remove timestamps, PIDs, specific IDs)
                template = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+", "", line)
                template = re.sub(r"PID \d+", "PID X", template)
                template = re.sub(r"[0-9a-f]{6,}", "ID", template)
                template = template.strip()[:120]

                error_counts[template] = error_counts.get(template, 0) + 1

    # Sort by frequency, report top errors
    for template, count in sorted(error_counts.items(), key=lambda x: -x[1])[:15]:
        severity = "critical" if count > 10 else "warning" if count > 3 else "info"
        findings.append(
            {
                "type": "recurring_error",
                "severity": severity,
                "count": count,
                "pattern": template,
            }
        )

    return findings


def scan_runtime_health() -> list[dict]:
    """Convert current operator/runtime failures into self-audit findings."""
    findings: list[dict] = []
    try:
        from operator_dashboard import (
            _ALERT_REPEAT_HOURS,
            _is_recent_iso,
            _load_bg_health,
            _process_has_active_failure,
            _recent_incidents,
        )
    except Exception as exc:
        return [
            {
                "type": "audit_error",
                "severity": "warning",
                "description": f"Runtime health scan unavailable: {exc}",
            }
        ]

    try:
        health = _load_bg_health()
        for proc in health.get("processes", []) or []:
            if not _process_has_active_failure(proc):
                continue
            name = str(proc.get("name") or "unknown")
            failures = int(proc.get("consecutive_failures", 0) or 0)
            reason = str(proc.get("last_failure_reason") or "").strip()
            findings.append(
                {
                    "type": "scheduled_process_failure",
                    "severity": "critical" if failures >= 3 else "warning",
                    "description": f"Scheduled process '{name}' is failing" + (f": {reason}" if reason else ""),
                    "process": name,
                    "count": failures,
                    "last_exit": proc.get("last_exit", ""),
                    "last_success": proc.get("last_success", ""),
                    "last_failure_reason": reason,
                }
            )

        for inc in _recent_incidents():
            count = int(inc.get("count", 0) or 0)
            if count < 3 or not _is_recent_iso(inc.get("timestamp", ""), hours=_ALERT_REPEAT_HOURS):
                continue
            pipeline = str(inc.get("pipeline") or "unknown")
            step = str(inc.get("step") or "unknown")
            error_type = str(inc.get("error_type") or "unknown_error")
            message = str(inc.get("error_message") or "").strip()
            findings.append(
                {
                    "type": "repeated_pipeline_incident",
                    "severity": "critical",
                    "description": f"Repeated pipeline incident {pipeline}/{step}: {error_type}"
                    + (f" — {message[:180]}" if message else ""),
                    "pipeline": pipeline,
                    "step": step,
                    "error_type": error_type,
                    "count": count,
                    "timestamp": inc.get("timestamp", ""),
                }
            )
    except Exception as exc:
        findings.append(
            {
                "type": "audit_error",
                "severity": "warning",
                "description": f"Runtime health scan failed: {exc}",
            }
        )
    return findings


def scan_integration_config() -> list[dict]:
    """Report enabled integration codepaths that cannot actually authenticate."""
    findings: list[dict] = []
    try:
        from bluesky.client import is_configured as bluesky_is_configured
    except Exception as exc:
        findings.append(
            {
                "type": "integration_config_missing",
                "severity": "warning",
                "description": f"Bluesky integration cannot be checked: {exc}",
                "integration": "bluesky",
            }
        )
        return findings

    if not bluesky_is_configured():
        findings.append(
            {
                "type": "integration_config_missing",
                "severity": "warning",
                "description": (
                    "Bluesky integration is enabled in social workflows but cannot authenticate: "
                    "missing api_keys.bluesky.handle/app_password or reusable session cache"
                ),
                "integration": "bluesky",
            }
        )
    return findings


# ---------------------------------------------------------------------------
# Step 2: Run test suite
# ---------------------------------------------------------------------------


def run_tests() -> list[dict]:
    """Run the test suite and report failures."""
    findings = []
    test_runner = _AGENTS_DIR / "run_tests.py"
    if not test_runner.exists():
        findings.append(
            {
                "type": "missing_infrastructure",
                "severity": "warning",
                "description": "Test runner not found at agents/run_tests.py",
            }
        )
        return findings

    try:
        result = subprocess.run(
            [sys.executable, str(test_runner)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_MIRA_ROOT),
        )
        if result.returncode != 0:
            # Extract failures
            for line in result.stdout.splitlines():
                if "FAIL" in line or "ERROR" in line:
                    findings.append(
                        {
                            "type": "test_failure",
                            "severity": "critical",
                            "description": line.strip(),
                        }
                    )
        else:
            # Extract pass count
            for line in result.stdout.splitlines():
                if "passed" in line:
                    log.info("Tests: %s", line.strip())
    except subprocess.TimeoutExpired:
        findings.append(
            {
                "type": "test_timeout",
                "severity": "warning",
                "description": "Test suite timed out after 120s",
            }
        )
    except Exception as e:
        findings.append(
            {
                "type": "test_error",
                "severity": "warning",
                "description": f"Failed to run tests: {e}",
            }
        )

    return findings


# ---------------------------------------------------------------------------
# Step 3: Scan codebase for anti-patterns
# ---------------------------------------------------------------------------

_ANTI_PATTERNS = [
    {
        "name": "hardcoded_path",
        "pattern": r'(?:Path\.home\(\)|"/Users/|~/Sandbox)',
        "description": "Hardcoded path — should use config variables",
        "exclude": ["config.py", "test_", "__pycache__"],
    },
    {
        "name": "bare_except",
        "pattern": r"except\s*:",
        "description": "Bare except — should catch specific exceptions",
        "exclude": ["__pycache__"],
    },
    {
        "name": "truncation_limit",
        "pattern": r"\[:(?:5000|8000|10000)\]",
        "description": "Suspicious truncation limit — may cause data loss",
        "exclude": ["__pycache__", "test_"],
    },
]


def scan_codebase() -> list[dict]:
    """Scan Python files for known anti-patterns."""
    findings = []

    for py_file in _AGENTS_DIR.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        rel_path = py_file.relative_to(_AGENTS_DIR)

        for ap in _ANTI_PATTERNS:
            if any(exc in str(rel_path) for exc in ap.get("exclude", [])):
                continue

            matches = list(re.finditer(ap["pattern"], content))
            if matches:
                # Find line numbers
                for m in matches[:3]:  # Max 3 per file per pattern
                    line_num = content[: m.start()].count("\n") + 1
                    findings.append(
                        {
                            "type": "anti_pattern",
                            "severity": "info",
                            "pattern_name": ap["name"],
                            "description": ap["description"],
                            "file": str(rel_path),
                            "line": line_num,
                            "match": content[m.start() : m.end()][:60],
                        }
                    )

    return findings


# ---------------------------------------------------------------------------
# Step 4: Check manifest coverage
# ---------------------------------------------------------------------------


def check_manifests() -> list[dict]:
    """Verify all agent directories have valid manifests."""
    findings = []

    for agent_dir in sorted(_AGENTS_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        if agent_dir.name.startswith("."):
            continue
        if agent_dir.name in ("shared", "super", "__pycache__"):
            continue

        manifest = agent_dir / "manifest.json"
        if not manifest.exists():
            findings.append(
                {
                    "type": "missing_manifest",
                    "severity": "warning",
                    "description": f"Agent '{agent_dir.name}' has no manifest.json",
                }
            )
            continue

        try:
            data = json.loads(manifest.read_text())
            required = ["name", "description", "entry_point"]
            for field in required:
                if field not in data:
                    findings.append(
                        {
                            "type": "incomplete_manifest",
                            "severity": "warning",
                            "description": f"{agent_dir.name}/manifest.json missing '{field}'",
                        }
                    )
        except json.JSONDecodeError as e:
            findings.append(
                {
                    "type": "invalid_manifest",
                    "severity": "critical",
                    "description": f"{agent_dir.name}/manifest.json parse error: {e}",
                }
            )

    return findings


# ---------------------------------------------------------------------------
# Step 4b: Auto-fix low-risk issues, queue high-risk for user approval
# ---------------------------------------------------------------------------

# Low-risk: can be auto-applied if tests pass after fix
_LOW_RISK_PATTERNS = {"hardcoded_path", "missing_manifest"}

# High-risk: always needs user approval
_HIGH_RISK_TYPES = {"test_failure", "recurring_error", "anti_pattern"}


def _can_auto_fix_finding(finding: dict) -> bool:
    """Return True only for findings with a concrete, implemented auto-fix path."""
    pattern = finding.get("pattern_name", finding.get("type", ""))
    if pattern == "hardcoded_path":
        file_path = _AGENTS_DIR / str(finding.get("file") or "")
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8")
            except OSError:
                return False
            return any(
                re.search(supported, content)
                for supported in (
                    r'Path\.home\(\)\s*/\s*"Sandbox/Mira/artifacts/photos"',
                    r'Path\.home\(\)\s*/\s*"Sandbox/Mira/artifacts"',
                )
            )
        return "Sandbox/Mira/artifacts" in str(finding.get("match") or "")
    if pattern == "missing_manifest":
        desc = str(finding.get("description") or "")
        return re.match(r"Agent '[A-Za-z0-9_]+' has no manifest\.json", desc) is not None
    return False


def _finding_description(finding: dict) -> str:
    return str(finding.get("description") or finding.get("pattern") or finding.get("match") or "").strip()


def _finding_subject(finding: dict) -> str:
    parts = [
        str(finding.get("type") or "unknown"),
        str(finding.get("pattern_name") or ""),
        _finding_description(finding),
        str(finding.get("match") or ""),
        str(finding.get("file") or ""),
        str(finding.get("line") or ""),
    ]
    subject = "|".join(parts).lower()
    subject = re.sub(r"\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "timestamp", subject)
    subject = re.sub(r"\b[0-9a-f]{8,}\b", "id", subject)
    subject = re.sub(r"\s+", " ", subject).strip()
    return subject


def _finding_backlog_id(finding: dict) -> str:
    digest = hashlib.sha1(_finding_subject(finding).encode("utf-8")).hexdigest()[:16]
    kind = re.sub(r"[^a-z0-9_]+", "_", str(finding.get("type") or "finding").lower()).strip("_")
    return f"self_audit:{kind}:{digest}"


def _finding_priority(finding: dict) -> str:
    severity = finding.get("severity")
    if severity == "critical":
        return "high"
    if severity == "warning":
        return "medium"
    return "low"


def _finding_owner(finding: dict) -> str:
    ftype = str(finding.get("type") or "")
    pattern = str(finding.get("pattern_name") or "")
    if ftype in {
        "pipeline_error",
        "parked_publish_item",
        "stale_publish_manifest_error",
        "stuck_pipeline",
        "missing_podcast",
        "incomplete_podcast",
    }:
        return "publishing"
    if ftype.startswith("test_"):
        return "test-infra"
    if ftype in {"missing_manifest", "incomplete_manifest", "invalid_manifest"}:
        return "agent-registry"
    if ftype == "anti_pattern" or pattern:
        return "mira-core"
    if ftype == "integration_config_missing":
        return "integrations"
    if ftype == "audit_error":
        return "self-audit"
    return "ops"


def _finding_executor(finding: dict) -> tuple[str, bool]:
    if _can_auto_fix_finding(finding):
        return "self_audit.apply_low_risk", True
    return "manual_review.required", False


def _finding_verification_criteria(finding: dict) -> list[str]:
    ftype = str(finding.get("type") or "")
    if ftype == "recurring_error":
        return ["The normalized error pattern is absent or below warning threshold for two consecutive audits."]
    if ftype.startswith("test_"):
        return ["The relevant test command exits 0 within its configured timeout."]
    if ftype == "anti_pattern":
        return ["The finding pattern is no longer detected at the reported location.", "Focused tests still pass."]
    if ftype in {"missing_manifest", "incomplete_manifest", "invalid_manifest"}:
        return ["The agent manifest exists, parses as JSON, and includes required fields."]
    if ftype == "stuck_pipeline":
        return [
            "The publish manifest no longer reports this article as stuck.",
            "The article has a valid terminal state.",
        ]
    if ftype == "pipeline_error":
        return ["The publish manifest error is cleared or replaced by an intentional reviewed resolution."]
    if ftype == "parked_publish_item":
        return [
            "The article remains intentionally parked, or a human resolves the blocker and clears the manifest error."
        ]
    if ftype == "stale_publish_manifest_error":
        return ["The published article keeps a valid Substack URL and the stale manifest error is cleared."]
    if ftype in {"missing_podcast", "incomplete_podcast"}:
        return ["The expected podcast artifact exists, or the article is explicitly marked auto_podcast=false."]
    if ftype == "scheduled_process_failure":
        return [
            "The scheduled process has a successful run after the reported failure.",
            "If the process is obsolete, it is disabled in both launchd and Mira scheduler state.",
        ]
    if ftype == "repeated_pipeline_incident":
        return ["The incident is resolved or drops below repeated-incident threshold for two consecutive audits."]
    if ftype == "integration_config_missing":
        return ["The integration either authenticates successfully or is explicitly disabled in the relevant workflow."]
    if ftype == "audit_error":
        return ["The next self-audit completes this check without raising the same audit error."]
    return ["The next self-audit no longer reports this finding."]


def build_backlog_record(
    finding: dict,
    *,
    user_id: str = "ang",
    audit_date: str | None = None,
    resolved: bool = False,
    verification_summary: str | None = None,
) -> dict:
    """Build a deterministic control-plane backlog record for one audit finding."""
    audit_date = audit_date or datetime.now().strftime("%Y-%m-%d")
    description = _finding_description(finding)
    executor, executor_eligible = _finding_executor(finding)
    severity = str(finding.get("severity") or "info")
    ftype = str(finding.get("type") or "finding")
    title_bits = [severity.upper(), ftype.replace("_", " ")]
    if finding.get("file"):
        title_bits.append(str(finding["file"]))
    title = ": ".join(title_bits)
    if description:
        title = f"{title} — {description[:90]}"

    payload = {
        "source": "self_audit",
        "audit_date": audit_date,
        "severity": severity,
        "owner": _finding_owner(finding),
        "executor_eligible": executor_eligible,
        "verification_criteria": _finding_verification_criteria(finding),
        "fingerprint": _finding_subject(finding),
        "finding": finding,
    }
    return {
        "item_id": _finding_backlog_id(finding),
        "user_id": user_id,
        "kind": "self_audit_finding",
        "executor": executor,
        "status": "verified" if resolved else "proposed",
        "priority": _finding_priority(finding),
        "title": title[:220],
        "description": description or f"Self-audit finding: {ftype}",
        "payload": payload,
        "verification_summary": verification_summary if resolved else None,
        "last_error": None if resolved else description if severity in {"critical", "warning"} else None,
    }


def upsert_self_audit_backlog(
    findings: list[dict], *, user_id: str = "ang", auto_fixed: list[dict] | None = None
) -> list[dict]:
    """Mirror audit findings into the control-plane backlog.

    This is intentionally best-effort. Self-audit must still produce a report if
    Postgres is offline or migrations lag behind.
    """
    if not findings:
        return []
    try:
        from control.db import transaction
        from control.repository import ControlRepository
    except Exception as exc:
        log.warning("Self-audit backlog unavailable: %s", exc)
        return []

    audit_date = datetime.now().strftime("%Y-%m-%d")
    fixed_by_id = {
        str(fix.get("backlog_id")): str(fix.get("action") or "auto-fixed")
        for fix in (auto_fixed or [])
        if fix.get("backlog_id")
    }
    records = [
        build_backlog_record(
            f,
            user_id=user_id,
            audit_date=audit_date,
            resolved=_finding_backlog_id(f) in fixed_by_id,
            verification_summary=fixed_by_id.get(_finding_backlog_id(f)),
        )
        for f in findings
    ]
    upserted = []
    try:
        with transaction() as conn:
            repo = ControlRepository(conn)
            for record in records:
                upserted.append(repo.upsert_backlog_item(**record))
    except Exception as exc:
        log.warning("Self-audit backlog update failed: %s", exc)
        return []
    return upserted


def _attempt_auto_fix(finding: dict) -> dict | None:
    """Try to auto-fix a low-risk finding. Returns fix record or None."""
    pattern = finding.get("pattern_name", finding.get("type", ""))

    if pattern == "hardcoded_path":
        return _fix_hardcoded_path(finding)
    elif pattern == "missing_manifest":
        return _fix_missing_manifest(finding)

    return None


def _fix_hardcoded_path(finding: dict) -> dict | None:
    """Replace Path.home() / "Sandbox/Mira/artifacts/..." with config import."""
    file_path = _AGENTS_DIR / finding.get("file", "")
    if not file_path.exists():
        return None

    try:
        content = file_path.read_text(encoding="utf-8")
        original = content

        # Replace common hardcoded patterns with config imports
        # Only safe replacements — path to artifacts
        replacements = [
            (r'Path\.home\(\)\s*/\s*"Sandbox/Mira/artifacts/photos"', 'ARTIFACTS_DIR / "photos"'),
            (r'Path\.home\(\)\s*/\s*"Sandbox/Mira/artifacts"', "ARTIFACTS_DIR"),
        ]

        import re as _re

        changed = False
        for old_pattern, new_val in replacements:
            if _re.search(old_pattern, content):
                content = _re.sub(old_pattern, new_val, content)
                changed = True

        if not changed:
            return None

        # Ensure config import exists
        if "from config import" in content and "ARTIFACTS_DIR" not in content:
            content = content.replace("from config import", "from config import ARTIFACTS_DIR,", 1)
        elif "from config import" not in content:
            # Add import at top (after docstring)
            lines = content.split("\n")
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("import ") or line.startswith("from "):
                    insert_idx = i
                    break
            lines.insert(insert_idx, "from config import ARTIFACTS_DIR")
            content = "\n".join(lines)

        # Write fix, test, revert if failed
        file_path.write_text(content, encoding="utf-8")

        if _run_tests_quick():
            return {
                "file": finding.get("file", ""),
                "action": "auto-fixed hardcoded path",
                "risk": "low",
                "applied": True,
            }
        else:
            # Revert
            file_path.write_text(original, encoding="utf-8")
            return {
                "file": finding.get("file", ""),
                "action": "fix attempted but tests failed — reverted",
                "risk": "low",
                "applied": False,
            }
    except Exception as e:
        log.warning("Auto-fix failed for %s: %s", finding.get("file"), e)
        return None


def _fix_missing_manifest(finding: dict) -> dict | None:
    """Create a basic manifest.json for an agent directory."""
    desc = finding.get("description", "")
    # Extract agent name from description
    import re as _re

    m = _re.match(r"Agent '(\w+)' has no manifest.json", desc)
    if not m:
        return None

    agent_name = m.group(1)
    agent_dir = _AGENTS_DIR / agent_name
    if not agent_dir.exists():
        return None

    # Check if there's a handler.py
    has_handler = (agent_dir / "handler.py").exists()
    entry = "handler.py:handle" if has_handler else "handler.py:handle"

    manifest = {
        "name": agent_name,
        "description": f"{agent_name} agent (auto-generated manifest — needs review)",
        "keywords": [],
        "handles": [],
        "tier": "light",
        "timeout_category": "short",
        "entry_point": entry,
        "requires_workspace": True,
    }

    manifest_path = agent_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "file": f"{agent_name}/manifest.json",
        "action": "auto-created basic manifest (needs review)",
        "risk": "low",
        "applied": True,
    }


_PARKED_PUBLISH_STATUSES = {"blocked_language", "blocked_writer_gate", "blocked_security_claim"}


def _publish_manifest_error_finding(slug: str, entry: dict) -> dict | None:
    """Classify manifest errors without turning parked historical rows into critical incidents."""
    error = str(entry.get("error") or "").strip()
    if not error:
        return None

    status = str(entry.get("status") or "").strip()
    title = entry.get("title") or slug
    if status == "skip":
        return None
    if status in _PARKED_PUBLISH_STATUSES:
        return {
            "type": "parked_publish_item",
            "severity": "warning",
            "status": status,
            "slug": slug,
            "description": f"Article '{title}' is parked at '{status}': {error}",
        }
    if status == "published" and entry.get("substack_url"):
        return {
            "type": "stale_publish_manifest_error",
            "severity": "warning",
            "status": status,
            "slug": slug,
            "description": f"Published article '{title}' still has a stale manifest error: {error}",
        }
    return {
        "type": "pipeline_error",
        "severity": "critical",
        "status": status,
        "slug": slug,
        "description": f"Article '{title}' has error: {error}",
    }


def check_publish_pipeline() -> list[dict]:
    """Check publish pipeline integrity — articles vs podcasts vs RSS sync."""
    findings = []
    try:
        from config import ARTIFACTS_DIR

        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))

        audio_base = ARTIFACTS_DIR / "audio" / "podcast"

        # 1. Check published articles have podcasts
        try:
            from substack import get_recent_posts

            posts = get_recent_posts(limit=50)
        except Exception:
            posts = []

        from publish.manifest import load_manifest

        manifest = load_manifest()
        skip_slugs = {slug for slug, e in manifest.get("articles", {}).items() if not e.get("auto_podcast", True)}

        for post in posts:
            substack_slug = post.get("slug", "")
            title = post.get("title", "")

            # Map Substack slug to local directory
            # Try exact match, then search audio dirs
            for lang in ["en", "zh"]:
                lang_dir = audio_base / lang
                if not lang_dir.exists():
                    continue
                # Find matching episode dir
                found = False
                for ep_dir in lang_dir.iterdir():
                    if not ep_dir.is_dir():
                        continue
                    # Match by slug substring (Substack sometimes appends suffixes)
                    if ep_dir.name in substack_slug or substack_slug.startswith(ep_dir.name):
                        if (ep_dir / "episode.mp3").exists():
                            found = True
                        else:
                            findings.append(
                                {
                                    "type": "incomplete_podcast",
                                    "severity": "warning",
                                    "description": f"'{title}' [{lang.upper()}]: directory exists but no episode.mp3",
                                }
                            )
                        break
                if not found and substack_slug not in skip_slugs:
                    # Check if any local slug matches
                    local_match = any(
                        d.name in substack_slug or substack_slug.startswith(d.name)
                        for d in lang_dir.iterdir()
                        if d.is_dir()
                    )
                    if not local_match:
                        findings.append(
                            {
                                "type": "missing_podcast",
                                "severity": "warning",
                                "description": f"Published article '{title}' has no {lang.upper()} podcast",
                            }
                        )

        # 2. Check manifest for stuck articles
        from publish.manifest import get_stuck_articles

        stuck = get_stuck_articles(timeout_minutes=240)
        for entry in stuck:
            findings.append(
                {
                    "type": "stuck_pipeline",
                    "severity": "critical",
                    "description": f"Article '{entry.get('title', entry['slug'])}' stuck at '{entry.get('status')}' for >4h",
                }
            )

        # 3. Check manifest for errors
        for slug, entry in manifest.get("articles", {}).items():
            finding = _publish_manifest_error_finding(slug, entry)
            if finding:
                findings.append(finding)

    except Exception as e:
        log.warning("Pipeline integrity check failed: %s", e)
        findings.append(
            {
                "type": "audit_error",
                "severity": "warning",
                "description": f"Pipeline check itself failed: {e}",
            }
        )

    return findings


def _run_tests_quick() -> bool:
    """Run test suite, return True if all pass."""
    test_runner = _AGENTS_DIR / "run_tests.py"
    if not test_runner.exists():
        return True  # No tests = assume OK
    try:
        result = subprocess.run(
            [sys.executable, str(test_runner)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(_MIRA_ROOT),
        )
        return result.returncode == 0
    except Exception:
        return False


def attempt_fixes(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Attempt auto-fixes on low-risk findings. Returns (applied, pending_approval)."""
    applied = []
    pending = []

    for f in findings:
        if _can_auto_fix_finding(f):
            fix = _attempt_auto_fix(f)
            if fix and fix.get("applied"):
                fix["backlog_id"] = _finding_backlog_id(f)
                applied.append(fix)
                log.info("Auto-fixed: %s — %s", fix["file"], fix["action"])
            elif fix:
                fix["backlog_id"] = _finding_backlog_id(f)
                pending.append(fix)
        elif f.get("severity") in ("critical", "warning"):
            pending.append(
                {
                    "description": f.get("description", f.get("pattern", "")),
                    "action": "needs manual review",
                    "risk": "high",
                    "applied": False,
                }
            )

    return applied, pending


# ---------------------------------------------------------------------------
# Step 5: Generate report + notify user
# ---------------------------------------------------------------------------


def generate_report(
    all_findings: list[dict],
    auto_fixed: list[dict] | None = None,
    pending_fixes: list[dict] | None = None,
    backlog_records: list[dict] | None = None,
) -> str:
    """Format findings + fix results into a readable report."""
    auto_fixed = auto_fixed or []
    pending_fixes = pending_fixes or []
    backlog_records = backlog_records or []

    if not all_findings and not auto_fixed:
        return "自检完成，未发现问题。"

    critical = [f for f in all_findings if f.get("severity") == "critical"]
    warnings = [f for f in all_findings if f.get("severity") == "warning"]
    info = [f for f in all_findings if f.get("severity") == "info"]

    sections = []
    sections.append(f"自检报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    sections.append(f"发现 {len(all_findings)} 个问题：{len(critical)} 严重，{len(warnings)} 警告，{len(info)} 建议")
    sections.append("")

    if backlog_records:
        executor_ready = [
            r
            for r in backlog_records
            if r.get("executor") == "self_audit.apply_low_risk" and r.get("status") == "proposed"
        ]
        manual = [
            r
            for r in backlog_records
            if r.get("executor") == "manual_review.required" and r.get("status") == "proposed"
        ]
        verified = [r for r in backlog_records if r.get("status") == "verified"]
        sections.append("== 行动闭环 ==")
        sections.append(f"  - 已写入结构化 backlog: {len(backlog_records)} 项")
        sections.append(f"  - 可自动执行: {len(executor_ready)} 项")
        sections.append(f"  - 需要人工判断: {len(manual)} 项")
        sections.append(f"  - 已验证/已修复: {len(verified)} 项")
        sections.append("")

    if critical:
        sections.append("== 严重 ==")
        for f in critical:
            desc = f.get("description", f.get("pattern", ""))
            sections.append(f"  - {desc}")
        sections.append("")

    if warnings:
        sections.append("== 警告 ==")
        for f in warnings:
            desc = f.get("description", f.get("pattern", ""))
            if "count" in f:
                desc = f"({f['count']}次) {desc}"
            sections.append(f"  - {desc}")
        sections.append("")

    if info:
        sections.append(f"== 建议 ({len(info)} 项) ==")
        # Group by pattern name
        by_pattern = {}
        for f in info:
            key = f.get("pattern_name", "other")
            by_pattern.setdefault(key, []).append(f)
        for pattern, items in by_pattern.items():
            sections.append(f"  {pattern}: {len(items)} 处")
            for item in items[:3]:
                sections.append(f"    {item.get('file', '')}:{item.get('line', '')} — {item.get('match', '')}")
            if len(items) > 3:
                sections.append(f"    ...和另外 {len(items) - 3} 处")

    if auto_fixed:
        sections.append("")
        sections.append(f"== 已自动修复 ({len(auto_fixed)} 项) ==")
        for fix in auto_fixed:
            sections.append(f"  - {fix['file']}: {fix['action']}")

    if pending_fixes:
        sections.append("")
        sections.append(f"== 需要你审批 ({len(pending_fixes)} 项) ==")
        for fix in pending_fixes:
            desc = fix.get("description", fix.get("file", ""))
            sections.append(f"  - [{fix['risk']}] {desc}: {fix['action']}")

    return "\n".join(sections)


def notify_user(report: str):
    """Send report to user via Mira bridge."""
    try:
        from config import MIRA_BRIDGE_DIR
        from bridge import Mira

        bridge = Mira(MIRA_BRIDGE_DIR)
        today = datetime.now().strftime("%Y-%m-%d")
        item_id = f"self_audit_{today.replace('-', '')}"
        title = f"自检报告 {today}"
        if bridge.item_exists(item_id):
            item = bridge._read_item(item_id) or {}
            item["type"] = "discussion"
            item["title"] = title
            item["status"] = "done"
            item["origin"] = "agent"
            item["tags"] = list(dict.fromkeys(["self-audit", "system", *item.get("tags", [])]))
            bridge._write_item(item)
            bridge._update_manifest(item)
            bridge.append_message(item_id, "agent", report)
            item = bridge._read_item(item_id)
            if item:
                item["status"] = "done"
                bridge._write_item(item)
                bridge._update_manifest(item)
        else:
            item = bridge.create_item(
                item_id,
                "discussion",
                title,
                report,
                sender="agent",
                tags=["self-audit", "system"],
                origin="agent",
            )
            item["status"] = "done"
            bridge._write_item(item)
            bridge._update_manifest(item)
        try:
            from control.db import transaction
            from control.repository import ControlRepository

            item = bridge._read_item(item_id)
            if item:
                with transaction() as conn:
                    ControlRepository(conn).upsert_bridge_item(bridge.user_id, item)
        except Exception as exc:
            log.warning("Self-audit report DB projection failed: %s", exc)
        log.info("Report sent to user via bridge")
    except Exception as e:
        log.error("Failed to notify user: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_audit(logs_only: bool = False, tests_only: bool = False) -> list[dict]:
    """Run full audit pipeline. Returns all findings."""
    log.info("=== Mira Self-Audit ===")
    all_findings = []

    if not tests_only:
        log.info("Step 1: Scanning logs...")
        all_findings.extend(scan_logs(days=2))
        log.info("  Found %d log issues", len(all_findings))

        log.info("Step 1a: Scanning runtime health...")
        runtime_findings = scan_runtime_health()
        all_findings.extend(runtime_findings)
        log.info("  Found %d runtime health issues", len(runtime_findings))

        log.info("Step 1b: Checking integration config...")
        integration_findings = scan_integration_config()
        all_findings.extend(integration_findings)
        log.info("  Found %d integration config issues", len(integration_findings))

    if not logs_only:
        log.info("Step 2: Running tests...")
        test_findings = run_tests()
        all_findings.extend(test_findings)
        log.info("  Found %d test issues", len(test_findings))

    if not tests_only and not logs_only:
        log.info("Step 3: Scanning codebase...")
        code_findings = scan_codebase()
        all_findings.extend(code_findings)
        log.info("  Found %d code issues", len(code_findings))

        log.info("Step 4: Checking manifests...")
        manifest_findings = check_manifests()
        all_findings.extend(manifest_findings)
        log.info("  Found %d manifest issues", len(manifest_findings))

        log.info("Step 4a: Checking publish pipeline integrity...")
        pipeline_findings = check_publish_pipeline()
        all_findings.extend(pipeline_findings)
        log.info("  Found %d pipeline issues", len(pipeline_findings))

    # Step 4b: Attempt auto-fixes on low-risk issues
    auto_fixed, pending_fixes = [], []
    if not tests_only and not logs_only:
        log.info("Step 4b: Attempting auto-fixes...")
        auto_fixed, pending_fixes = attempt_fixes(all_findings)
        log.info("  Auto-fixed: %d, Pending approval: %d", len(auto_fixed), len(pending_fixes))

    log.info("Step 4c: Updating structured backlog...")
    backlog_records = upsert_self_audit_backlog(all_findings, auto_fixed=auto_fixed)
    log.info("  Backlog records updated: %d", len(backlog_records))

    log.info("Step 5: Generating report...")
    report = generate_report(all_findings, auto_fixed, pending_fixes, backlog_records)
    print(report)

    # Save report locally
    report_dir = _LOGS_DIR / "audits"
    report_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (report_dir / f"{today}.txt").write_text(report, encoding="utf-8")

    # Notify user (only if significant findings)
    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")
    if critical_count > 0:
        notify_user(report)

    try:
        from core import load_state, save_state

        state = load_state()
        today_key = datetime.now().strftime("%Y-%m-%d")
        state[f"self_audit_{today_key}"] = datetime.now().isoformat()
        save_state(state)
    except Exception as exc:
        log.warning("Self-audit state update failed: %s", exc)

    log.info("=== Audit complete: %d findings ===", len(all_findings))
    return all_findings


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-only", action="store_true")
    parser.add_argument("--tests-only", action="store_true")
    args = parser.parse_args()
    run_audit(logs_only=args.logs_only, tests_only=args.tests_only)
