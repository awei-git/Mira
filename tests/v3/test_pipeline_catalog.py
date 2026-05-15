from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES, SOFT_POLICY_SPECS


def test_catalog_contains_all_v31_pipelines():
    assert len(PIPELINE_CATALOG) == 21
    assert PIPELINE_CATALOG["communication"].memory_class == "operational"
    assert PIPELINE_CATALOG["self_evolution"].memory_class == "self_modification"
    assert PIPELINE_CATALOG["a2a_trust_experiment"].memory_class == "epistemic"


def test_v31_catalog_uses_governed_memory_flow():
    article_steps = [step.name for step in PIPELINE_CATALOG["article_creation"].steps]
    evolution_steps = [step.name for step in PIPELINE_CATALOG["self_evolution"].steps]
    health_steps = [step.name for step in PIPELINE_CATALOG["system_health"].steps]

    assert article_steps[:2] == ["preflight_substack_twitter", "snapshot_voice_scars_audience_outcomes"]
    assert article_steps[-1] == "experience_record_proposal_gateway"
    assert "experiment_record" in evolution_steps
    assert "canary_observe_confirm_reject_rollback" in evolution_steps
    assert health_steps[-1] == "experience_record_on_change"


def test_policy_catalog_matches_architecture_counts():
    hard_count = sum(len(policies) for policies in HARD_POLICY_NAMES.values())

    assert hard_count == 43
    assert len(SOFT_POLICY_SPECS) == 9
