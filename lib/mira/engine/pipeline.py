"""Pipeline and step definitions with loop support."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from mira.kernel.schema import MemoryClass

StepType = Literal["agent", "policy", "eval", "human", "monitor", "parallel", "deterministic", "memory"]
OnFail = Literal["retry", "skip", "abort", "escalate"]
TriggerType = Literal["manual", "schedule", "event", "folder", "monitor"]


@dataclass(frozen=True)
class Trigger:
    type: TriggerType
    detail: str


@dataclass(frozen=True)
class Step:
    name: str
    type: StepType
    agent: str | None = None
    policies: list[str] = field(default_factory=list)
    eval_criteria: dict[str, Any] | None = None
    timeout_s: int = 300
    retries: int = 0
    on_fail: OnFail = "abort"
    input_schema: type | None = None
    output_schema: type | None = None
    loop_to: str | None = None
    loop_max: int = 0
    action: Callable[..., Any] | None = None


@dataclass(frozen=True)
class Pipeline:
    name: str
    trigger: Trigger
    steps: list[Step]
    priority: int
    version: int
    max_duration_s: int
    checkpoint_every: int
    memory_class: MemoryClass
    involved_skills: list[str] = field(default_factory=list)

    def step_index(self, name: str) -> int:
        for i, step in enumerate(self.steps):
            if step.name == name:
                return i
        raise KeyError(f"No such step in pipeline {self.name}: {name}")
