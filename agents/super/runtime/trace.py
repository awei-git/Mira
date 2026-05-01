from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import MIRA_DIR


TRACE_DIR = MIRA_DIR / "data" / "traces"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_trace(task_id: str, event_type: str, payload: dict[str, Any] | None = None) -> Path:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{task_id}.jsonl"
    entry = {
        "ts": _utc_iso(),
        "task_id": task_id,
        "event_type": event_type,
        "payload": payload or {},
        "schema_version": 1,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return path
