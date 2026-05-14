"""V3 eval criteria and calibration history."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EVAL_CRITERIA: dict[str, dict[str, object]] = {
    "article": {"criteria": "Personal voice, examples, not generic", "threshold": 0.7},
    "podcast": {"criteria": "Podcastability: depth, audio-friendly", "threshold": 0.6},
    "briefing": {"criteria": "Signal: actionable and concrete", "threshold": 3},
    "journal": {"criteria": "Threads not list, connections", "threshold": 0.6},
    "zhesi": {"criteria": "Convergence: new insight", "threshold": "present"},
    "reflection": {"criteria": "Depth: change vs last week", "threshold": "delta detected"},
    "evolution": {"criteria": "Hypothesis quality: testable, evidenced", "threshold": "measurable"},
    "research": {"criteria": "Novel insight, not summary", "threshold": 0.7},
    "book": {"criteria": "Novelty of viewpoint", "threshold": 0.7},
}


@dataclass(frozen=True)
class EvalEvent:
    pipeline: str
    score: float
    passed: bool
    outcome_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvalHistory:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: EvalEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    def list(self, pipeline: str | None = None) -> list[EvalEvent]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                if pipeline is None or data["pipeline"] == pipeline:
                    events.append(EvalEvent(**data))
        return events


def bounded_threshold_adjustment(current: float, desired: float, max_weekly_delta: float = 0.05) -> float:
    delta = max(-max_weekly_delta, min(max_weekly_delta, desired - current))
    return round(current + delta, 4)
