from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


GATE_FILE = ".writer_gate.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_writer_gate(
    workspace: Path,
    *,
    channel: str,
    task_id: str = "",
    artifact_path: str = "",
    source: str = "writer",
) -> dict:
    record = {
        "writer_gate_passed": True,
        "channel": channel,
        "task_id": task_id,
        "artifact_path": artifact_path,
        "source": source,
        "checked_at": _utc_iso(),
    }
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / GATE_FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return record


def read_writer_gate(workspace: Path) -> dict | None:
    path = workspace / GATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("writer_gate_passed") is True:
        return data
    return None


def require_writer_gate(workspace: Path, *, channel: str) -> tuple[bool, str, dict | None]:
    record = read_writer_gate(workspace)
    if not record:
        return False, f"writer gate missing for {channel}", None
    gate_channel = str(record.get("channel") or "")
    if gate_channel not in {channel, "publish", "substack", "all"}:
        return False, f"writer gate channel mismatch: {gate_channel} != {channel}", record
    return True, "writer gate passed", record
