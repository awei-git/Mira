"""Typed boundary schemas — Phase 0 pillar 2.

Four value types that cross module boundaries today as raw dicts:

- `BridgeItem` — one entry in `bridge_dir/users/<uid>/items/`
- `TaskRequest` — super → task_worker dispatch payload (message.json)
- `TaskResult` — task_worker → super result (result.json)
- `AgentState` — serialized slice of `state.py` (session_context.json,
  agent_state.json)

Each model ships with `from_dict` / `to_dict` so callers can adopt
incrementally: pass a dict through `BridgeItem.from_dict(...)` to
validate + normalise, then keep using the dict API downstream if they
need to. Nothing is forced.

Design notes:
- `extra="allow"` on every schema — dicts in the field may grow
  fields that haven't made it into the schema yet without breaking
  existing producers.
- All timestamps are normalised to timezone-aware UTC datetimes;
  producers can send naive ISO strings.
- No imports from agents/ — these types must be usable from any
  layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_ts(value: Any) -> datetime:
    """Accept ISO strings, epoch floats, naive/aware datetimes."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# BridgeItem — one message thread on the Notes/bridge surface.
# ---------------------------------------------------------------------------


class BridgeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str = "request"
    title: str = ""
    status: Literal["new", "processing", "done", "failed", "skipped", "approved", "published", "queued"] | str = "new"
    tags: list[str] = Field(default_factory=list)
    origin: str = "user"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    messages: list[dict] = Field(default_factory=list)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _normalize_ts(cls, v):
        return _parse_ts(v)

    @classmethod
    def from_dict(cls, data: dict) -> "BridgeItem":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# TaskRequest — super → worker
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    workflow_id: str = ""
    thread_id: str = ""
    user_id: str = "ang"
    user_role: str = "admin"
    sender: str = "user"
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_restriction: str | None = None
    allowed_agents: list[str] = Field(default_factory=list)
    content_filter: bool = False

    @field_validator("created_at", mode="before")
    @classmethod
    def _normalize_ts(cls, v):
        return _parse_ts(v)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskRequest":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# TaskResult — worker → super
# ---------------------------------------------------------------------------


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str
    status: Literal["done", "failed", "timeout", "error", "crashed"] | str
    agent: str = ""
    summary: str = ""
    output_path: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome_verified: bool | None = None
    failure_class: str | None = None

    @field_validator("completed_at", mode="before")
    @classmethod
    def _normalize_ts(cls, v):
        return _parse_ts(v)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskResult":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=False)


# ---------------------------------------------------------------------------
# AgentState — serialized state snapshot
# ---------------------------------------------------------------------------


class AgentState(BaseModel):
    model_config = ConfigDict(extra="allow")

    user_id: str = "ang"
    session_started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_tick_at: datetime | None = None
    active_workflow_id: str = ""
    pending_tasks: list[str] = Field(default_factory=list)
    session_context: dict = Field(default_factory=dict)

    @field_validator("session_started_at", "last_tick_at", mode="before")
    @classmethod
    def _normalize_ts(cls, v):
        return _parse_ts(v) if v is not None else None

    @classmethod
    def from_dict(cls, data: dict) -> "AgentState":
        return cls.model_validate(data)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json", exclude_none=False)
