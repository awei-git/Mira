"""Policy interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PolicyContext:
    pipeline: str
    step: str
    payload: dict[str, Any]
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyResult:
    passed: bool
    policy: str
    detail: str = ""
    score: float | None = None


class HardPolicy(Protocol):
    name: str

    def check(self, ctx: PolicyContext) -> PolicyResult: ...


class SoftPolicy(Protocol):
    name: str
    rubric: str
    model: str
    threshold: float

    def check(self, ctx: PolicyContext) -> PolicyResult: ...
