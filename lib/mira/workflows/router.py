"""Conservative workflow routing for V3.1 workflow packs."""

from __future__ import annotations

from dataclasses import dataclass, field

from mira.capabilities import PreflightResult, preflight_for_pipeline


@dataclass(frozen=True)
class RouterContext:
    connectors: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteDecision:
    workflow: str
    reason: str
    required_connectors_missing: list[str] = field(default_factory=list)
    optional_connectors_missing: list[str] = field(default_factory=list)
    expected_degradation: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    confidence: float = 1.0


class WorkflowRouter:
    """Route requests without executing tools or pulling external data."""

    def __init__(
        self,
        *,
        task_tags: dict[str, str],
        background_jobs: dict[str, str],
        default_task_workflow: str = "communication",
        default_background_workflow: str = "memory_maintenance",
    ):
        self.task_tags = dict(task_tags)
        self.background_jobs = dict(background_jobs)
        self.default_task_workflow = default_task_workflow
        self.default_background_workflow = default_background_workflow

    def route_task(self, tags: list[str] | None, ctx: RouterContext | None = None) -> RouteDecision:
        ctx = ctx or RouterContext()
        matches = [self.task_tags[tag] for tag in _normalized(tags or []) if tag in self.task_tags]
        workflow = matches[0] if matches else self.default_task_workflow
        distinct_matches = sorted(set(matches))
        reason = f"matched task tag to {workflow}" if matches else f"no task tag matched; defaulted to {workflow}"
        if len(distinct_matches) > 1:
            reason = f"multiple task tags matched {', '.join(distinct_matches)}; selected {workflow}"
        return self._decision(
            workflow,
            reason,
            ctx,
            requires_confirmation=len(distinct_matches) > 1,
            confidence=0.65 if len(distinct_matches) > 1 else (0.9 if matches else 0.55),
        )

    def route_background_job(self, name: str, ctx: RouterContext | None = None) -> RouteDecision:
        ctx = ctx or RouterContext()
        normalized = name.strip()
        for prefix, workflow in self.background_jobs.items():
            if normalized == prefix or normalized.startswith(prefix + "-"):
                return self._decision(workflow, f"matched background job prefix {prefix}", ctx, confidence=0.9)
        return self._decision(
            self.default_background_workflow,
            f"no background job prefix matched; defaulted to {self.default_background_workflow}",
            ctx,
            requires_confirmation=True,
            confidence=0.5,
        )

    def route_named_workflow(self, name: str, ctx: RouterContext | None = None) -> RouteDecision:
        ctx = ctx or RouterContext()
        return self._decision(name, f"explicit workflow request: {name}", ctx, confidence=1.0)

    def _decision(
        self,
        workflow: str,
        reason: str,
        ctx: RouterContext,
        *,
        requires_confirmation: bool = False,
        confidence: float = 1.0,
    ) -> RouteDecision:
        preflight = preflight_for_pipeline(workflow, ctx.connectors)
        return _decision_from_preflight(
            workflow,
            reason,
            preflight,
            requires_confirmation=requires_confirmation or bool(preflight.missing),
            confidence=confidence,
        )


def _decision_from_preflight(
    workflow: str,
    reason: str,
    preflight: PreflightResult,
    *,
    requires_confirmation: bool,
    confidence: float,
) -> RouteDecision:
    optional_missing = [check.name for check in preflight.checks if not check.required and not check.available]
    degradation = [
        f"{check.name}: {check.fallback}" for check in preflight.checks if not check.available and check.fallback
    ]
    return RouteDecision(
        workflow=workflow,
        reason=reason,
        required_connectors_missing=preflight.missing,
        optional_connectors_missing=optional_missing,
        expected_degradation=degradation,
        requires_confirmation=requires_confirmation,
        confidence=confidence,
    )


def _normalized(values: list[str]) -> list[str]:
    return [str(value).strip().lower() for value in values if str(value).strip()]
