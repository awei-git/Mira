"""Preflight checks surface missing connectors before a pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DegradationMode = Literal["block", "draft_only", "skip_optional"]


@dataclass(frozen=True)
class CapabilityCheck:
    name: str
    available: bool
    detail: str = ""
    required: bool = True
    fallback: str | None = None


@dataclass(frozen=True)
class PreflightResult:
    pipeline: str
    checks: list[CapabilityCheck] = field(default_factory=list)
    degradation: DegradationMode = "block"

    @property
    def ok(self) -> bool:
        return all(check.available or not check.required or check.fallback for check in self.checks)

    @property
    def missing(self) -> list[str]:
        return [check.name for check in self.checks if check.required and not check.available and not check.fallback]

    @property
    def degraded(self) -> bool:
        return any(not check.available for check in self.checks)

    @property
    def fallback_plan(self) -> dict[str, str]:
        return {check.name: check.fallback for check in self.checks if not check.available and check.fallback}


@dataclass(frozen=True)
class ConnectorRequirement:
    name: str
    required: bool = True
    fallback: str | None = None
    detail: str = ""


class CapabilityRegistry:
    def __init__(self, requirements: dict[str, list[ConnectorRequirement]] | None = None):
        self.requirements = requirements or DEFAULT_REQUIREMENTS

    def check(self, pipeline: str, available: dict[str, bool] | None = None) -> PreflightResult:
        available = available or {}
        requirements = self.requirements.get(pipeline, [])
        checks = [
            CapabilityCheck(
                name=req.name,
                available=available.get(req.name, False),
                detail=req.detail,
                required=req.required,
                fallback=req.fallback,
            )
            for req in requirements
        ]
        degradation: DegradationMode = "block"
        if checks and all(check.available or check.fallback or not check.required for check in checks):
            degradation = (
                "draft_only" if any(check.fallback for check in checks if not check.available) else "skip_optional"
            )
        return PreflightResult(pipeline=pipeline, checks=checks, degradation=degradation)


DEFAULT_REQUIREMENTS: dict[str, list[ConnectorRequirement]] = {
    "article_creation": [
        ConnectorRequirement("substack", required=True, fallback="write_output_folder", detail="publish target"),
        ConnectorRequirement("twitter", required=False, fallback="skip_social_promo", detail="optional promo"),
    ],
    "intelligence_briefing": [
        ConnectorRequirement("rss", required=True, fallback="cached_sources", detail="feed source"),
        ConnectorRequirement("hackernews", required=False, fallback="skip_source", detail="optional source"),
        ConnectorRequirement("reddit", required=False, fallback="skip_source", detail="optional source"),
    ],
    "health_wellness": [
        ConnectorRequirement("oura", required=True, fallback="manual_health_note", detail="health source"),
    ],
    "a2a_trust_experiment": [
        ConnectorRequirement("local_files", required=True, detail="experiment artifact store"),
    ],
}


def run_preflight(pipeline: str, required: dict[str, bool]) -> PreflightResult:
    return PreflightResult(
        pipeline=pipeline,
        checks=[CapabilityCheck(name=name, available=available) for name, available in sorted(required.items())],
    )


def preflight_for_pipeline(pipeline: str, available: dict[str, bool] | None = None) -> PreflightResult:
    return CapabilityRegistry().check(pipeline, available)
