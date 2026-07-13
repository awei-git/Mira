"""Track per-file agent edit churn."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("agent_edit_churn")

_MIRA_ROOT = Path(__file__).resolve().parents[2]
_LOG_PATH = _MIRA_ROOT / "logs" / "agent_edit_churn.json"


def _normalize_filepath(filepath) -> str:
    raw = str(filepath or "").strip().strip("\"'")
    if not raw:
        return ""
    path = Path(raw).expanduser()
    try:
        if path.is_absolute():
            return path.resolve().relative_to(_MIRA_ROOT).as_posix()
    except (OSError, ValueError):
        pass
    raw = raw.replace("\\", "/").lstrip("./")
    if raw.startswith("Mira/"):
        raw = raw[len("Mira/") :]
    return raw


def _coerce_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _blank_record() -> dict:
    return {
        "total_agent_edits": 0,
        "edits_since_last_human_review": 0,
        "last_agent": "",
        "last_edit_ts": "",
        "flagged": False,
    }


def _coerce_record(value) -> dict:
    record = _blank_record()
    if not isinstance(value, dict):
        return record
    record["total_agent_edits"] = _coerce_int(value.get("total_agent_edits", 0))
    record["edits_since_last_human_review"] = _coerce_int(value.get("edits_since_last_human_review", 0))
    record["last_agent"] = str(value.get("last_agent", "") or "")
    record["last_edit_ts"] = str(value.get("last_edit_ts", "") or "")
    record["flagged"] = bool(value.get("flagged", False))
    return record


def _load_log() -> dict:
    try:
        data = json.loads(_LOG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not load agent edit churn log %s: %s", _LOG_PATH, e)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(path): _coerce_record(record) for path, record in data.items() if str(path)}


def _write_log(data: dict) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def record_agent_edit(filepath, agent_name) -> None:
    key = _normalize_filepath(filepath)
    if not key:
        return
    data = _load_log()
    record = _coerce_record(data.get(key))
    record["total_agent_edits"] += 1
    record["edits_since_last_human_review"] += 1
    record["last_agent"] = str(agent_name or "unknown")
    record["last_edit_ts"] = datetime.now(timezone.utc).isoformat()
    data[key] = record
    _write_log(data)


def check_churn_threshold(filepath, threshold=7) -> bool:
    key = _normalize_filepath(filepath)
    if not key:
        return True
    try:
        limit = max(0, int(threshold))
    except (TypeError, ValueError):
        limit = 7
    data = _load_log()
    record = _coerce_record(data.get(key))
    if record["flagged"] or record["edits_since_last_human_review"] >= limit:
        record["flagged"] = True
        data[key] = record
        _write_log(data)
        log.warning(
            "Agent edit churn threshold blocked %s at %s/%s", key, record["edits_since_last_human_review"], limit
        )
        return False
    return True
