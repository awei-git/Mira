"""TrajectoryRecord — turn-level conversation trace for one task.

Mirrors Hermes Agent's `batch_runner.py` output shape so downstream
reward computation and reflect can operate on the same data model.

A trajectory is produced by `TrajectoryRecorder` (see
`evolution.trajectory_recorder`), optionally compressed by
`evolution.trajectory_compressor`, and appended to
`data/soul/trajectories.jsonl` by `record_task_outcome`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


Role = Literal["system", "human", "assistant", "tool"]


class Turn(BaseModel):
    """One conversation turn."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str
    # Tool calls are attached when role == "assistant" issues one, or when
    # role == "tool" returns its response.
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result_preview: str | None = None  # truncated for compactness
    tool_success: bool | None = None


class ToolStat(BaseModel):
    """Aggregate per-tool counters for one trajectory."""

    model_config = ConfigDict(extra="forbid")

    name: str
    count: int = 0
    success: int = 0
    failure: int = 0

    def record(self, success: bool) -> None:
        self.count += 1
        if success:
            self.success += 1
        else:
            self.failure += 1

    @property
    def success_rate(self) -> float:
        return self.success / self.count if self.count else 0.0


class TrajectoryRecord(BaseModel):
    """One task's full conversation + tool-use trace.

    Persisted per-task as `<workspace>/trajectory.jsonl` and
    aggregated into `data/soul/trajectories.jsonl` after compression.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    agent: str
    model: str = ""
    prompt_index: int = 0
    timestamp: datetime = Field(default_factory=_utcnow)

    conversations: list[Turn] = Field(default_factory=list)
    tool_stats: dict[str, ToolStat] = Field(default_factory=dict)

    api_calls: int = 0
    completed: bool = False
    partial: bool = False
    crashed: bool = False

    # Set by compressor when middle turns have been summarized.
    compressed: bool = False
    original_turn_count: int | None = None

    def add_turn(self, turn: Turn) -> None:
        self.conversations.append(turn)

    def record_tool(self, name: str, success: bool) -> None:
        stat = self.tool_stats.get(name) or ToolStat(name=name)
        stat.record(success)
        self.tool_stats[name] = stat

    def as_jsonl_line(self) -> str:
        """One-line JSON for append-only storage."""
        return self.model_dump_json(exclude_none=False)
