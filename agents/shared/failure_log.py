"""Structured failure logging for Mira pipeline.

Failures are logged to LOGS_DIR/pipeline_failures.jsonl as one JSON object per line.
Each record captures: what failed, why, what was expected, what actually happened,
and enough context for a future instance to diagnose without re-reading all code.
"""

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import LOGS_DIR

log = logging.getLogger("mira")

FAILURE_LOG = LOGS_DIR / "pipeline_failures.jsonl"


def record_failure(
    pipeline: str,           # "publish", "podcast", "rss", "notes"
    step: str,               # "substack_publish", "tts_zh", "script_generation", etc.
    slug: str,               # article/episode slug
    error_type: str,         # "api_timeout", "tts_quota", "validation_failed", etc.
    error_message: str,      # human-readable error description
    input_summary: str = "", # what was fed in (e.g. "9200 chars ZH script, 42 turns")
    expected_output: str = "", # what we expected (e.g. "episode.mp3 >= 30min")
    actual_output: str = "", # what we got (e.g. "partial file 12min")
    context: Optional[dict] = None,  # extra diagnostic info
    resolution: Optional[str] = None, # how it was resolved (filled later)
) -> dict:
    """Record a pipeline failure with full diagnostic context.

    Returns the failure record dict.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pipeline": pipeline,
        "step": step,
        "slug": slug,
        "error_type": error_type,
        "error_message": error_message,
        "input_summary": input_summary,
        "expected_output": expected_output,
        "actual_output": actual_output,
        "context": context or {},
        "resolution": resolution,
    }

    try:
        FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FAILURE_LOG, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        log.info("Recorded pipeline failure: %s/%s for '%s': %s",
                 pipeline, step, slug, error_type)
    except Exception as e:
        log.error("Failed to write failure log: %s", e)

    return record


def load_recent_failures(
    pipeline: str = None,
    step: str = None,
    slug: str = None,
    days: int = 7,
    limit: int = 50,
) -> list[dict]:
    """Load recent failure records, optionally filtered.

    Args:
        pipeline: Filter by pipeline name (e.g. "podcast")
        step: Filter by step name (e.g. "tts_zh")
        slug: Filter by article/episode slug
        days: Only return failures from last N days
        limit: Max records to return (newest first)

    Returns:
        List of failure dicts, newest first.
    """
    if not FAILURE_LOG.exists():
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    results = []

    try:
        with open(FAILURE_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Time filter
                try:
                    ts = datetime.fromisoformat(rec["timestamp"]).timestamp()
                    if ts < cutoff:
                        continue
                except (KeyError, ValueError):
                    continue

                # Field filters
                if pipeline and rec.get("pipeline") != pipeline:
                    continue
                if step and rec.get("step") != step:
                    continue
                if slug and rec.get("slug") != slug:
                    continue

                results.append(rec)
    except Exception as e:
        log.error("Failed to read failure log: %s", e)
        return []

    # Newest first, limited
    results.reverse()
    return results[:limit]


def get_failure_summary(days: int = 7) -> str:
    """Human-readable summary of recent failures for diagnosis.

    Returns a markdown-formatted summary suitable for injecting into
    an agent's context so it can understand what went wrong recently.
    """
    failures = load_recent_failures(days=days)
    if not failures:
        return "No pipeline failures in the last %d days." % days

    # Group by pipeline/step
    groups: dict[str, list[dict]] = {}
    for f in failures:
        key = f"{f['pipeline']}/{f['step']}"
        groups.setdefault(key, []).append(f)

    lines = [f"## Pipeline Failures (last {days} days): {len(failures)} total\n"]
    for key, records in sorted(groups.items()):
        lines.append(f"### {key} ({len(records)} failures)")
        for rec in records[:3]:  # Show up to 3 per group
            lines.append(f"- [{rec['timestamp'][:16]}] **{rec['error_type']}**: "
                         f"{rec['error_message'][:200]}")
            if rec.get("input_summary"):
                lines.append(f"  Input: {rec['input_summary'][:150]}")
            if rec.get("actual_output"):
                lines.append(f"  Got: {rec['actual_output'][:150]}")
            if rec.get("resolution"):
                lines.append(f"  Resolved: {rec['resolution']}")
        if len(records) > 3:
            lines.append(f"  ... and {len(records) - 3} more")
        lines.append("")

    return "\n".join(lines)


def resolve_failure(slug: str, step: str, resolution: str) -> bool:
    """Mark the most recent matching failure as resolved.

    Rewrites the log file atomically with the resolution added.
    Use sparingly.
    """
    if not FAILURE_LOG.exists():
        return False

    lock_path = FAILURE_LOG.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                lines = FAILURE_LOG.read_text(encoding="utf-8").splitlines(keepends=True)

                # Find most recent matching unresolved failure
                resolved = False
                for i in range(len(lines) - 1, -1, -1):
                    try:
                        rec = json.loads(lines[i])
                        if (rec.get("slug") == slug
                                and rec.get("step") == step
                                and not rec.get("resolution")):
                            rec["resolution"] = resolution
                            lines[i] = json.dumps(rec, ensure_ascii=False) + "\n"
                            resolved = True
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue

                if resolved:
                    # Atomic write via tmp + rename (same pattern as soul_manager)
                    fd, tmp_path = tempfile.mkstemp(
                        dir=FAILURE_LOG.parent, suffix=".tmp",
                        prefix=f".{FAILURE_LOG.stem}_"
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp_path, FAILURE_LOG)
                    except BaseException:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise
                    log.info("Resolved failure for %s/%s: %s", slug, step, resolution)

                return resolved
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except Exception as e:
        log.error("Failed to resolve failure: %s", e)
        return False
