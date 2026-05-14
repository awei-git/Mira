"""Stateless agent protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from mira.kernel.snapshot import MemorySnapshot


@dataclass(frozen=True)
class StepInput:
    run_id: str
    pipeline: str
    step: str
    payload: dict[str, Any]
    prior_outputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StepOutput:
    payload: dict[str, Any]
    summary: str = ""
    succeeded: bool = True


class Agent(Protocol):
    name: str
    model: str
    skills: list[str]
    token_budget: int

    def execute(self, input: StepInput, memory: MemorySnapshot) -> StepOutput:
        """Run with read-only memory context and return disposable output."""
        ...
