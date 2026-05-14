"""Preflight checks surface missing connectors before a pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CapabilityCheck:
    name: str
    available: bool
    detail: str = ""


@dataclass(frozen=True)
class PreflightResult:
    pipeline: str
    checks: list[CapabilityCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.available for check in self.checks)

    @property
    def missing(self) -> list[str]:
        return [check.name for check in self.checks if not check.available]


def run_preflight(pipeline: str, required: dict[str, bool]) -> PreflightResult:
    return PreflightResult(
        pipeline=pipeline,
        checks=[CapabilityCheck(name=name, available=available) for name, available in sorted(required.items())],
    )
