"""Calibration tracking — pre-mortem/post-mortem predictions and output quality.

Extracted from task_worker.py. Contains:
- _CALIBRATION_FILE, _QUALITY_LOG constants
- _record_premortem: record predicted difficulty before step execution
- _record_postmortem: record actual result after step execution
- _track_output_quality: append to global quality log
- detect_quality_regression: check if agent output quality is declining
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add shared directory to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))

log = logging.getLogger("task_worker")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Pre-mortem / post-mortem calibration tracking
# ---------------------------------------------------------------------------

_CALIBRATION_FILE = Path(__file__).resolve().parent.parent.parent / "shared" / "soul" / "calibration.jsonl"


def _record_premortem(task_id: str, step_index: int, agent: str,
                      instruction: str, prediction: dict | None):
    """Record predicted difficulty/failure modes before step execution."""
    if not prediction:
        return
    record = {
        "type": "premortem",
        "task_id": task_id,
        "step": step_index,
        "agent": agent,
        "instruction_preview": instruction[:150],
        "prediction": prediction,
        "timestamp": _utc_iso(),
    }
    try:
        with open(_CALIBRATION_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _record_postmortem(task_id: str, step_index: int, agent: str,
                       prediction: dict | None, actual_status: str,
                       actual_output_preview: str):
    """Record actual result after step execution; compare to prediction."""
    record = {
        "type": "postmortem",
        "task_id": task_id,
        "step": step_index,
        "agent": agent,
        "actual_status": actual_status,
        "actual_output_preview": actual_output_preview[:200],
        "timestamp": _utc_iso(),
    }
    if prediction:
        # Simple calibration delta: did the step succeed vs predicted difficulty?
        succeeded = actual_status in ("done", "completed")
        predicted_easy = prediction.get("difficulty") == "easy"
        record["calibration_note"] = (
            "expected_easy_succeeded" if (predicted_easy and succeeded) else
            "expected_easy_failed" if (predicted_easy and not succeeded) else
            "expected_hard_succeeded" if (not predicted_easy and succeeded) else
            "expected_hard_failed"
        )
    try:
        with open(_CALIBRATION_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


# Global output quality tracker
_QUALITY_LOG = Path(__file__).resolve().parent.parent.parent / ".output_quality.jsonl"


def _track_output_quality(agent: str, status: str, health: dict):
    """Append to global quality log for trend detection."""
    try:
        entry = {
            "agent": agent,
            "status": status,
            "length": health.get("length", 0),
            "has_content": health.get("has_content", False),
            "ts": _utc_iso(),
        }
        with open(_QUALITY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def detect_quality_regression(agent: str, window: int = 10) -> str | None:
    """Check if an agent's output quality is declining.

    Looks at last `window` outputs. Returns warning string if regression detected.
    """
    if not _QUALITY_LOG.exists():
        return None

    records = []
    for line in _QUALITY_LOG.read_text(encoding="utf-8").strip().splitlines()[-200:]:
        try:
            r = json.loads(line)
            if r.get("agent") == agent:
                records.append(r)
        except json.JSONDecodeError:
            continue

    recent = records[-window:]
    if len(recent) < 5:
        return None

    # Check: are recent outputs getting shorter?
    lengths = [r.get("length", 0) for r in recent]
    if len(lengths) >= 5:
        first_half = sum(lengths[:len(lengths)//2]) / (len(lengths)//2)
        second_half = sum(lengths[len(lengths)//2:]) / (len(lengths) - len(lengths)//2)
        if first_half > 0 and second_half / first_half < 0.5:
            return f"{agent}: output length dropped {second_half/first_half:.0%} vs earlier"

    # Check: are errors increasing?
    errors = [1 for r in recent if r.get("status") in {"failed", "error"}]
    if len(errors) >= 3 and len(errors) / len(recent) > 0.4:
        return f"{agent}: {len(errors)}/{len(recent)} recent outputs failed"

    return None
