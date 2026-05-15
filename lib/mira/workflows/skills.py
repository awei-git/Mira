"""Workflow-pack skill loader."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .security import audit_workflow_pack


@dataclass(frozen=True)
class WorkflowSkill:
    name: str
    description: str
    path: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    body: str = ""


class SkillLoadError(ValueError):
    pass


def load_workflow_skill(path: Path | str) -> WorkflowSkill:
    root = Path(path)
    metadata_path = root / "skill.yaml"
    body_path = root / "SKILL.md"
    if not metadata_path.exists() or not body_path.exists():
        raise SkillLoadError(f"Workflow skill needs skill.yaml and SKILL.md: {root}")
    for target in (metadata_path, body_path):
        audit = audit_workflow_pack(target)
        if not audit.passed:
            reasons = "; ".join(finding.reason for finding in audit.findings)
            raise SkillLoadError(f"Workflow skill failed security audit: {reasons}")
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
    return WorkflowSkill(
        name=metadata["name"],
        description=metadata.get("description", ""),
        path=str(root),
        inputs=list(metadata.get("inputs", [])),
        outputs=list(metadata.get("outputs", [])),
        body=body_path.read_text(encoding="utf-8"),
    )


def load_workflow_skills(root: Path | str) -> dict[str, WorkflowSkill]:
    base = Path(root)
    skills: dict[str, WorkflowSkill] = {}
    for skill_dir in sorted(base.glob("*/")):
        if (skill_dir / "skill.yaml").exists():
            skill = load_workflow_skill(skill_dir)
            skills[skill.name] = skill
    return skills
