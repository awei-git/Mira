from __future__ import annotations

from pathlib import Path
from typing import Any


def _check(name: str, passed: bool, message: str) -> dict:
    return {"name": name, "passed": passed, "message": message}


def output_file_min_size(
    *,
    workspace: Path,
    task_id: str,
    status: str,
    task_type: str,
    expected_observable_outcome: str,
    min_size_bytes: int = 1,
    **_: Any,
) -> dict:
    output_path = workspace / "output.md"
    exists = output_path.exists() and output_path.is_file()
    size = output_path.stat().st_size if exists else 0
    min_size = max(1, int(min_size_bytes or 1))
    verified = exists and size >= min_size
    if verified:
        summary = f"{task_type}: output.md exists ({size} bytes)."
    else:
        summary = f"{task_type}: output.md missing or below {min_size} bytes."
    return {
        "status": "verified" if verified else "failed",
        "verified": verified,
        "artifact_type": "file",
        "target": str(output_path),
        "summary": summary,
        "checks": [
            _check("output.md exists", exists, str(output_path)),
            _check("minimum size", size >= min_size, f"{size} >= {min_size} bytes"),
        ],
        "proxy_checked": "output.md exists + minimum size",
        "property_assumed": expected_observable_outcome,
        "unverified_assumptions": ["content quality", "full user intent fulfilled"],
        "task_type": task_type,
    }
