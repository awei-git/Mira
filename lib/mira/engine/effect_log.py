"""Idempotent side-effect log for V3.1."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from mira.kernel.ledger_ids import new_id
from mira.kernel.schema import to_jsonable, utc_now

EffectStatus = Literal["started", "succeeded", "failed", "compensated"]


@dataclass(frozen=True)
class EffectLogEntry:
    idempotency_key: str
    run_id: str
    pipeline: str
    action: str
    target: str
    status: EffectStatus
    detail: str = ""
    effect_id: str = field(default_factory=lambda: new_id("effectlog"))
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EffectLogEntry":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return cls(
            idempotency_key=data["idempotency_key"],
            run_id=data["run_id"],
            pipeline=data["pipeline"],
            action=data["action"],
            target=data["target"],
            status=data["status"],
            detail=data.get("detail", ""),
            effect_id=data.get("effect_id") or new_id("effectlog"),
            timestamp=timestamp,
        )


class EffectLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: EffectLogEntry) -> EffectLogEntry:
        existing = self.get_by_idempotency_key(entry.idempotency_key)
        if existing is not None and existing.status == "succeeded":
            return existing
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        return entry

    def list(self, limit: int | None = None) -> list[EffectLogEntry]:
        if not self.path.exists():
            return []
        rows: list[EffectLogEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(EffectLogEntry.from_dict(json.loads(line)))
        rows.sort(key=lambda row: row.timestamp)
        if limit is not None:
            return rows[-limit:]
        return rows

    def get_by_idempotency_key(self, key: str) -> EffectLogEntry | None:
        for entry in reversed(self.list()):
            if entry.idempotency_key == key:
                return entry
        return None
