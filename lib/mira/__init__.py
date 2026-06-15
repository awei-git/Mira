"""Mira V3 memory-first architecture package.

This package also preserves the legacy top-level ``mira`` utility API that
previously lived in ``agents/shared/mira.py``. Existing agents import those
helpers as ``from mira import ...``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

BACKGROUND_STALENESS_THRESHOLD_HOURS = 4
_SCAFFOLDING_AUDIT_LOG = Path(config.MIRA_ROOT) / "logs" / "scaffolding_audit.jsonl"
_SCAFFOLD_REJECTIONS_DIR = Path(config.MIRA_ROOT) / "logs" / "scaffold_rejections"
_INTERFACE_LATENCY_FILE = Path(config.MIRA_ROOT) / "logs" / "interface_latency.json"
_MEMORY_INJECTION_LOG = Path(config.MIRA_ROOT) / "agents" / "shared" / "soul" / "memory_injection_log.jsonl"
_log = logging.getLogger("scaffolding_audit")


def log_scaffolding_audit(
    guard_name: str,
    trigger_reason: str,
    content_length: int,
    severity: str,
    task_id: str = "",
    content_hash: str = "",
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guard_name": guard_name,
        "trigger_reason": trigger_reason,
        "content_length": content_length,
        "severity": severity,
        "task_id": task_id,
        "content_hash": content_hash,
    }
    try:
        _SCAFFOLDING_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SCAFFOLDING_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("scaffolding_audit write failed: %s", exc)


def update_interface_latency(latency_ms: int) -> float:
    samples: list[int] = []
    try:
        if _INTERFACE_LATENCY_FILE.exists():
            data = json.loads(_INTERFACE_LATENCY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                samples = data
    except Exception:
        samples = []
    samples.append(latency_ms)
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
    except Exception as exc:
        _log.warning("scaffold_rejection write failed: %s", exc)


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
    except Exception as exc:
        _log.warning("memory_injection_log write failed: %s", exc)


__all__ = [
    "BACKGROUND_STALENESS_THRESHOLD_HOURS",
    "agents",
    "engine",
    "kernel",
    "log_memory_injection",
    "log_scaffolding_audit",
    "policies",
    "pipelines",
    "update_interface_latency",
    "write_scaffold_rejection",
]
