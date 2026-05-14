"""Soft policy wrapper.

The evaluator callable is injected so tests stay deterministic and production
can route to the selected model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mira.policies.base import PolicyContext, PolicyResult


@dataclass
class RubricPolicy:
    name: str
    rubric: str
    model: str
    threshold: float
    evaluator: Callable[[PolicyContext, str], float]

    def check(self, ctx: PolicyContext) -> PolicyResult:
        score = self.evaluator(ctx, self.rubric)
        return PolicyResult(score >= self.threshold, self.name, f"score={score:.2f}", score=score)
