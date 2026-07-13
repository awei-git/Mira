"""Track AI-generated code lines that have not been reviewed."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_MIRA_ROOT = Path(__file__).resolve().parents[2]
_LOG_PATH = _MIRA_ROOT / "data" / "unreviewed_code_log.jsonl"


def log_code_change(file_path, lines_added, agent) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_path": str(file_path),
        "lines_added": max(0, int(lines_added)),
        "agent": str(agent),
        "reviewed": False,
    }
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_unreviewed_total() -> int:
    total = 0
    try:
        with _LOG_PATH.open("r", encoding="utf-8") as log_file:
            for line in log_file:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("reviewed", False):
                    continue
                try:
                    total += max(0, int(entry.get("lines_added", 0)))
                except (TypeError, ValueError):
                    continue
    except FileNotFoundError:
        return 0
    return total
