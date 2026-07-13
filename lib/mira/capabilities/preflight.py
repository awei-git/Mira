"""Preflight checks surface missing connectors before a pipeline run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

DegradationMode = Literal["block", "draft_only", "skip_optional"]
CapabilityStatus = Literal["available", "missing", "degraded", "rate_limited", "disabled"]
CapabilityRiskTier = Literal["read", "draft", "write", "publish", "destructive"]


@dataclass(frozen=True)
class Capability:
    name: str
    connector: str | None
    status: CapabilityStatus
    scopes: list[str] = field(default_factory=list)
    risk_tier: CapabilityRiskTier = "read"
    last_checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fallback: str | None = None
    detail: str = ""

    @property
    def available(self) -> bool:
        return self.status == "available"


@dataclass(frozen=True)
class CapabilityCheck(Capability):
    required: bool = True


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
    def missing_optional(self) -> list[str]:
        return [check.name for check in self.checks if not check.required and not check.available]

    @property
    def degraded(self) -> bool:
        return any(not check.available for check in self.checks)

    @property
    def fallback_plan(self) -> dict[str, str]:
        return {check.name: check.fallback for check in self.checks if not check.available and check.fallback}

    @property
    def required(self) -> list[CapabilityCheck]:
        return [check for check in self.checks if check.required]

    @property
    def optional(self) -> list[CapabilityCheck]:
        return [check for check in self.checks if not check.required]

    @property
    def degradation_notes(self) -> list[str]:
        notes: list[str] = []
        for check in self.checks:
            if check.available:
                continue
            if check.fallback:
                notes.append(f"{check.name}: {check.fallback}")
            elif not check.required:
                notes.append(f"{check.name}: skipped optional connector")
        return notes


@dataclass(frozen=True)
class ConnectorRequirement:
    name: str
    required: bool = True
    fallback: str | None = None
    detail: str = ""
    scopes: list[str] = field(default_factory=list)
    risk_tier: CapabilityRiskTier = "read"
    status_when_missing: CapabilityStatus = "missing"


class CapabilityRegistry:
    def __init__(self, requirements: dict[str, list[ConnectorRequirement]] | None = None):
        self.requirements = requirements or DEFAULT_REQUIREMENTS

    def check(self, pipeline: str, available: dict[str, bool] | None = None) -> PreflightResult:
        available = available or {}
        requirements = self.requirements.get(pipeline, [])
        checks = [
            CapabilityCheck(
                name=req.name,
                connector=req.name,
                status="available" if available.get(req.name, False) else req.status_when_missing,
                detail=req.detail,
                required=req.required,
                fallback=req.fallback,
                scopes=req.scopes,
                risk_tier=req.risk_tier,
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
        ConnectorRequirement(
            "substack",
            required=True,
            fallback="write_output_folder",
            detail="publish target",
            scopes=["publish"],
            risk_tier="publish",
            status_when_missing="degraded",
        ),
        ConnectorRequirement(
            "twitter",
            required=False,
            fallback="skip_social_promo",
            detail="optional promo",
            scopes=["post"],
            risk_tier="publish",
            status_when_missing="degraded",
        ),
    ],
    "intelligence_briefing": [
        ConnectorRequirement(
            "rss",
            required=True,
            fallback="cached_sources",
            detail="feed source",
            scopes=["read"],
            risk_tier="read",
            status_when_missing="degraded",
        ),
        ConnectorRequirement(
            "hackernews",
            required=False,
            fallback="skip_source",
            detail="optional source",
            scopes=["read"],
            risk_tier="read",
            status_when_missing="degraded",
        ),
        ConnectorRequirement(
            "reddit",
            required=False,
            fallback="skip_source",
            detail="optional source",
            scopes=["read"],
            risk_tier="read",
            status_when_missing="degraded",
        ),
    ],
    "health_wellness": [
        ConnectorRequirement(
            "oura",
            required=True,
            fallback="manual_health_note",
            detail="health source",
            scopes=["read"],
            risk_tier="read",
            status_when_missing="degraded",
        ),
        ConnectorRequirement(
            "health_provider",
            required=True,
            fallback="stage_health_write_local",
            detail="external health write target",
            scopes=["write"],
            risk_tier="write",
            status_when_missing="degraded",
        ),
    ],
    "market_monitor": [
        ConnectorRequirement(
            "tetra",
            required=True,
            fallback="cached_tetra_report",
            detail="market source",
            scopes=["read"],
            risk_tier="read",
            status_when_missing="degraded",
        ),
        ConnectorRequirement(
            "market_alert",
            required=True,
            fallback="stage_market_alert_local",
            detail="portfolio-facing alert target",
            scopes=["write"],
            risk_tier="write",
            status_when_missing="degraded",
        ),
    ],
    "a2a_trust_experiment": [
        ConnectorRequirement(
            "local_files",
            required=True,
            detail="experiment artifact store",
            scopes=["write"],
            risk_tier="write",
        ),
    ],
    "social_reactive": [
        ConnectorRequirement(
            "social",
            required=True,
            fallback="stage_local_reply",
            detail="platform write target",
            scopes=["post"],
            risk_tier="publish",
            status_when_missing="degraded",
        ),
    ],
    "social_proactive": [
        ConnectorRequirement(
            "social",
            required=True,
            fallback="stage_local_note",
            detail="platform write target",
            scopes=["post"],
            risk_tier="publish",
            status_when_missing="degraded",
        ),
    ],
}


def run_preflight(pipeline: str, required: dict[str, bool]) -> PreflightResult:
    return PreflightResult(
        pipeline=pipeline,
        checks=[
            CapabilityCheck(
                name=name,
                connector=name,
                status="available" if available else "missing",
            )
            for name, available in sorted(required.items())
        ],
    )


def preflight_for_pipeline(pipeline: str, available: dict[str, bool] | None = None) -> PreflightResult:
    return CapabilityRegistry().check(pipeline, available)
