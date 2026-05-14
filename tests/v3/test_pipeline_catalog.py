from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES, SOFT_POLICY_SPECS


def test_catalog_contains_all_twenty_v3_pipelines():
    assert len(PIPELINE_CATALOG) == 20
    assert PIPELINE_CATALOG["communication"].memory_class == "operational"
    assert PIPELINE_CATALOG["self_evolution"].memory_class == "self_modification"


def test_policy_catalog_matches_architecture_counts():
    hard_count = sum(len(policies) for policies in HARD_POLICY_NAMES.values())

    # The architecture prose says 43, but the category table enumerates 45 names.
    # The implementation keeps the explicit named policies rather than dropping two.
    assert hard_count == 45
    assert len(SOFT_POLICY_SPECS) == 9
