from mira.capabilities import preflight_for_pipeline, run_preflight


def test_legacy_preflight_contract_still_blocks_required_missing():
    result = run_preflight("article_creation", {"substack": True, "twitter": False})

    assert result.ok is False
    assert result.missing == ["twitter"]


def test_registry_preflight_can_degrade_to_draft_only():
    result = preflight_for_pipeline("article_creation", {"substack": False, "twitter": False})

    assert result.ok is True
    assert result.degraded is True
    assert result.degradation == "draft_only"
    assert result.fallback_plan["substack"] == "write_output_folder"


def test_registry_preflight_blocks_when_required_connector_has_no_fallback():
    result = preflight_for_pipeline("a2a_trust_experiment", {"local_files": False})

    assert result.ok is False
    assert result.missing == ["local_files"]
