from pathlib import Path

from mira.engine.risk_gate import grant_required
from mira.policies.action_risk import load_action_risk_catalog
from mira.workflows import load_workflow_skill, load_workflow_skills


ROOT = Path(__file__).resolve().parents[2]


def test_workflow_skill_loader_reads_metadata_and_body():
    skill = load_workflow_skill(ROOT / "workflow_packs/creative/skills/article_writing")

    assert skill.name == "article_writing"
    assert "article.md" in skill.outputs
    assert "Draft" in skill.body


def test_workflow_skill_loader_discovers_skill_directory():
    skills = load_workflow_skills(ROOT / "workflow_packs/epistemic/skills")

    assert {"briefing", "a2a_trust"}.issubset(skills)


def test_action_risk_catalog_drives_grant_requirement():
    catalog = load_action_risk_catalog()

    assert catalog["publish_public"]["grant_required"] is True
    assert catalog["external_provider"]["grant_required"] is True
    assert grant_required("publish_public") is True
    assert grant_required("external_provider") is True
    assert grant_required("draft") is False
