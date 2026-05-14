"""Deterministic monitor helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HeartbeatStatus:
    status: str
    detail: str


def check_heartbeat(path: Path | str, stale_after_s: int = 300) -> HeartbeatStatus:
    target = Path(path)
    if not target.exists():
        return HeartbeatStatus("degraded", f"missing heartbeat: {target}")
    age = max(0.0, __import__("time").time() - target.stat().st_mtime)
    if age > stale_after_s:
        return HeartbeatStatus("degraded", f"heartbeat stale: {age:.0f}s")
    return HeartbeatStatus("ok", "heartbeat fresh")
