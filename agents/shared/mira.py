"""Shared utilities for Mira agent system."""

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import config

__path__ = [str(Path(__file__).resolve().parents[2] / "lib" / "mira")]

_SCAFFOLDING_AUDIT_LOG = Path(config.MIRA_ROOT) / "logs" / "scaffolding_audit.jsonl"
_SCAFFOLD_REJECTIONS_DIR = Path(config.MIRA_ROOT) / "logs" / "scaffold_rejections"
_GUARD_FIRES_LOG = Path(config.MIRA_ROOT) / "logs" / "guard_fires.jsonl"
_INTERFACE_LATENCY_FILE = Path(config.MIRA_ROOT) / "logs" / "interface_latency.json"
_MEMORY_INJECTION_LOG = Path(config.MIRA_ROOT) / "agents" / "shared" / "soul" / "memory_injection_log.jsonl"
BACKGROUND_STALENESS_THRESHOLD_HOURS = 4
_log = logging.getLogger("scaffolding_audit")

_TRUST_POSITIONING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:i\s+am|i'm|mira\s+is)\s+(?:designed\s+to\s+be\s+)?safe\b", re.I),
    re.compile(r"\b(?:mira\s+is|i\s+am|i'm)\s+(?:built|designed)\s+(?:to\s+be\s+)?safe\b", re.I),
    re.compile(r"\byou\s+can\s+trust\s+(?:me|mira)\b", re.I),
    re.compile(r"\bunlike\s+(?:other\s+)?(?:ais?|llms?|models?)\b", re.I),
    re.compile(r"\bwhile\s+most\s+(?:ais?|llms?|models?)\b.{0,120}\bi\s+prioritize\b", re.I | re.S),
    re.compile(r"\bi\s+(?:am|'m)\s+aligned\b|\bmira\s+is\s+aligned\b", re.I),
    re.compile(r"\bmy\s+values\s+ensure\b", re.I),
    re.compile(r"\bi\s+would\s+never\b|\bmira\s+would\s+never\b", re.I),
    re.compile(r"\bsafety\s+is\s+my\s+top\s+priority\b", re.I),
    re.compile(r"\b(?:i|mira)\s+was\s+(?:built|designed)\s+with\s+safety\s+in\s+mind\b", re.I),
)


def _content_has_trust_positioning_claim(content: str) -> bool:
    text = content or ""
    return any(pattern.search(text) for pattern in _TRUST_POSITIONING_PATTERNS)


def log_scaffolding_audit(
    guard_name: str,
    trigger_reason: str,
    content_length: int,
    severity: str,
    task_id: str = "",
    content_hash: str = "",
    matched_snippet_hash: str = "",
    outcome: str = "",
    retried: bool | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guard_name": guard_name,
        "trigger_reason": trigger_reason,
        "content_length": content_length,
        "severity": severity,
        "task_id": task_id,
        "content_hash": content_hash,
        "outcome": outcome or severity,
    }
    if matched_snippet_hash:
        entry["matched_snippet_hash"] = matched_snippet_hash
    if retried is not None:
        entry["retried"] = retried
    try:
        _SCAFFOLDING_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SCAFFOLDING_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning("scaffolding_audit write failed: %s", e)


def short_content_hash(content: str, length: int = 8) -> str:
    return hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:length]


def verify_task_handoff(output_path: str) -> tuple[bool, str | None]:
    path = Path(output_path)
    if not path.exists():
        return False, f"handoff output missing: {path}"
    if not path.is_file():
        return False, f"handoff output is not a file: {path}"
    try:
        size = path.stat().st_size
    except OSError as e:
        return False, f"handoff output stat failed: {e}"
    min_size = getattr(config, "HANDOFF_VERIFY_MIN_SIZE_BYTES", 50)
    if size < min_size:
        return False, f"handoff output too small: {size} bytes"

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"handoff output read failed: {e}"

    edge_text = f"{text[:500]}\n{text[-500:]}"
    error_patterns = getattr(
        config,
        "HANDOFF_VERIFY_ERROR_PATTERNS",
        ["I cannot", "I am unable", "Error:", "Traceback", "failed to"],
    )
    for pattern in error_patterns:
        if re.search(re.escape(pattern), edge_text, re.I):
            return False, f"handoff output matched error pattern: {pattern}"
    return True, None


def log_guard_fired(
    logger,
    guard: str,
    agent: str,
    task_id: str,
    reason: str = "",
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "GUARD_FIRED",
        "guard": guard,
        "agent": agent,
        "task_id": task_id,
        "reason": reason,
    }
    logger.warning("GUARD_FIRED", extra={k: v for k, v in entry.items() if k != "event"})
    try:
        _GUARD_FIRES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _GUARD_FIRES_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning("guard_fires write failed: %s", e)


def log_memory_injection(task_id: str, keys: list[str], reason: str) -> None:
    keys = [str(key).strip() for key in keys if str(key).strip()]
    if not keys:
        return
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "keys": keys,
        "reason": reason,
    }
    try:
        _MEMORY_INJECTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _MEMORY_INJECTION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        lines = _MEMORY_INJECTION_LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > 1000:
            _MEMORY_INJECTION_LOG.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
    except Exception as e:
        _log.warning("memory_injection_log write failed: %s", e)


def update_interface_latency(latency_ms: int | float) -> int:
    """Append dispatch latency to a rolling 5-sample buffer and return the average."""
    samples: list[int] = []
    try:
        if _INTERFACE_LATENCY_FILE.exists():
            data = json.loads(_INTERFACE_LATENCY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                samples = [int(sample) for sample in data if isinstance(sample, (int, float))]
    except Exception:
        samples = []
    samples.append(int(round(latency_ms)))
    samples = samples[-5:]
    try:
        _INTERFACE_LATENCY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _INTERFACE_LATENCY_FILE.write_text(json.dumps(samples), encoding="utf-8")
    except Exception:
        pass
    return round(sum(samples) / len(samples))


def write_scaffold_rejection(
    agent_id: str,
    pipeline_stage: str,
    rejection_reason: str,
    content_preview: str,
) -> None:
    from datetime import date as _date

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": agent_id,
        "pipeline_stage": pipeline_stage,
        "rejection_reason": rejection_reason,
        "content_preview": content_preview[:200],
    }
    try:
        _SCAFFOLD_REJECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        day_file = _SCAFFOLD_REJECTIONS_DIR / f"{_date.today().isoformat()}.jsonl"
        with day_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning("scaffold_rejection write failed: %s", e)
