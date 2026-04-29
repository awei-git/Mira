"""Shared utilities for Mira agent system."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

_SCAFFOLDING_AUDIT_LOG = Path(config.MIRA_ROOT) / "logs" / "scaffolding_audit.jsonl"
_SCAFFOLD_REJECTIONS_DIR = Path(config.MIRA_ROOT) / "logs" / "scaffold_rejections"
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
    except Exception as e:
        _log.warning("scaffolding_audit write failed: %s", e)


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
