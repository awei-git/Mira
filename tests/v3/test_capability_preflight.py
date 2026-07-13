from mira.capabilities import Capability, preflight_for_pipeline, run_preflight


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
    assert result.missing_optional == ["twitter"]
    assert "substack: write_output_folder" in result.degradation_notes
    assert "twitter: skip_social_promo" in result.degradation_notes
    substack = next(check for check in result.checks if check.name == "substack")
    assert isinstance(substack, Capability)
    assert substack.connector == "substack"
    assert substack.status == "degraded"
    assert substack.scopes == ["publish"]
    assert substack.risk_tier == "publish"
    assert substack.last_checked_at.tzinfo is not None


def test_registry_preflight_blocks_when_required_connector_has_no_fallback():
    result = preflight_for_pipeline("a2a_trust_experiment", {"local_files": False})

    assert result.ok is False
    assert result.missing == ["local_files"]
