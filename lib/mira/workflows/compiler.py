"""Workflow pack compiler for V3.1 YAML-authored pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mira.engine.pipeline import Pipeline, Step, Trigger

from .actions import action_for
from .security import WorkflowAuditResult, audit_workflow_pack


class WorkflowCompileError(ValueError):
    pass


def compile_workflow_pack(path: Path | str, *, audit: bool = True) -> Pipeline:
    target = Path(path)
    audit_result: WorkflowAuditResult | None = audit_workflow_pack(target) if audit else None
    if audit_result and not audit_result.passed:
        reasons = "; ".join(f"{finding.reason}: {finding.pattern}" for finding in audit_result.findings)
        raise WorkflowCompileError(f"Workflow pack failed security audit: {reasons}")
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return pipeline_from_dict(data)


def pipeline_from_dict(data: dict[str, Any]) -> Pipeline:
    required = ["name", "memory_class", "trigger", "steps"]
    missing = [field for field in required if field not in data]
    if missing:
        raise WorkflowCompileError(f"Workflow pack missing required fields: {', '.join(missing)}")
    trigger = data["trigger"] or {}
    steps = [_step_from_dict(item) for item in data.get("steps", [])]
    return Pipeline(
        name=data["name"],
        trigger=Trigger(trigger.get("type", "manual"), trigger.get("detail", "")),
        steps=steps,
        priority=int(data.get("priority", 50)),
        version=int(data.get("version", 1)),
        max_duration_s=int(data.get("max_duration_s", 3600)),
        checkpoint_every=int(data.get("checkpoint_every", 1)),
        memory_class=data["memory_class"],
        involved_skills=list(data.get("involved_skills", [])),
        required_capabilities=dict(data.get("required_capabilities", {})),
        risk_actions=dict(data.get("risk_actions", {})),
        effect_steps=dict(data.get("effect_steps", {})),
    )


def _step_from_dict(data: dict[str, Any]) -> Step:
    if "name" not in data:
        raise WorkflowCompileError("Workflow step missing required field: name")
    action_name = data.get("action") or data["name"]
    return Step(
        name=data["name"],
        type=data.get("type", "deterministic"),
        agent=data.get("agent"),
        policies=list(data.get("policies", [])),
        timeout_s=int(data.get("timeout_s", 300)),
        retries=int(data.get("retries", 0)),
        on_fail=data.get("on_fail", "abort"),
        loop_to=data.get("loop_to"),
        loop_max=int(data.get("loop_max", 0)),
        action=action_for(action_name),
    )
