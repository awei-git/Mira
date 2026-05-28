"""Checkpoint state for resumable pipeline runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    run_id: str
    pipeline: str
    step: str
    outputs: dict[str, Any] = field(default_factory=dict)
    phase: str = "after_step"


class CheckpointStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: Checkpoint) -> None:
        target = self.path / f"{checkpoint.run_id}.json"
        target.write_text(json.dumps(asdict(checkpoint), sort_keys=True, indent=2), encoding="utf-8")

    def load(self, run_id: str) -> Checkpoint | None:
        target = self.path / f"{run_id}.json"
        if not target.exists():
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        data.setdefault("phase", "after_step")
        return Checkpoint(**data)
