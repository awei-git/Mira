import base64
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import httpx

from mira.runtime import (
    default_approval_store,
    default_causal_evidence_log,
    default_effect_log,
    default_ledger,
    default_v3_paths,
    pipeline_for_background_job,
    pipeline_for_task,
    record_background_completion,
    prepare_background_context,
    provider_adapter_config_template,
    provider_provisioning_env_template,
    provider_provisioning_runbook,
    provider_production_canary_surface,
    provider_production_readiness_report,
    provider_resolver_config_template,
    reconcile_provider_effects,
    record_task_completion,
    route_background_job,
    route_named_workflow,
    route_task,
    run_communication,
    run_memory_compaction_adapter,
    run_local_provider_dress_rehearsal,
    run_provider_effect_adapter,
    run_provider_production_canary,
    run_self_evolution_production_adapter,
    validate_provider_adapter_config,
    validate_provider_resolver_config,
    write_provider_adapter_config_template,
    write_provider_provisioning_runbook,
    write_provider_resolver_config_template,
)


def test_runtime_paths_are_under_data_v3(tmp_path: Path):
    paths = default_v3_paths(tmp_path)

    assert paths.root == tmp_path / "data" / "v3"
    assert paths.kernel.name == "kernel.json"
    assert paths.ledger.name == "experience_ledger.jsonl"
    assert paths.causal_evidence.name == "causal_evidence.jsonl"
    assert paths.approvals.name == "approvals.jsonl"
    assert paths.quarantine.name == "memory_quarantine.jsonl"
    assert paths.workflow_audits.name == "workflow_audits"
    assert paths.baselines.name == "baselines"
    assert paths.provider_resolvers.name == "provider_resolvers.json"
    assert paths.provider_adapters.name == "provider_adapters.json"
    assert paths.provider_state_manifests.name == "provider_state"


def test_legacy_job_and_task_mapping():
    assert pipeline_for_background_job("explore-morning") == "intelligence_briefing"
    assert pipeline_for_background_job("analyst-pre") == "market_monitor"
    assert pipeline_for_background_job("podcast-en-essay") == "podcast_production"
    assert pipeline_for_background_job("podcast-zh-essay") == "podcast_production"
    assert pipeline_for_background_job("voiceover-essay") == "podcast_production"
    assert pipeline_for_background_job("unknown") == "memory_maintenance"
    assert pipeline_for_task(["research"]) == "research_deep_dive"
    assert pipeline_for_task(["unknown"]) == "communication"


def test_runtime_router_exposes_missing_connectors_and_degradation():
    article = route_task(["writing"], connectors={"substack": False, "twitter": False})
    social = route_task(["social"], connectors={})
    a2a = route_named_workflow("a2a_trust_experiment", connectors={})
    ambiguous = route_task(["research", "writing"], connectors={})
    unknown_job = route_background_job("unknown-job")

    assert article.workflow == "article_creation"
    assert article.required_connectors_missing == []
    assert article.optional_connectors_missing == ["twitter"]
    assert "substack: write_output_folder" in article.expected_degradation
    assert article.requires_confirmation is False

    assert social.workflow == "social_reactive"
    assert social.required_connectors_missing == []
    assert "social: stage_local_reply" in social.expected_degradation
    assert social.requires_confirmation is False

    assert a2a.workflow == "a2a_trust_experiment"
    assert a2a.required_connectors_missing == ["local_files"]
    assert a2a.requires_confirmation is True

    assert ambiguous.workflow == "research_deep_dive"
    assert ambiguous.requires_confirmation is True
    assert ambiguous.confidence < 0.9

    assert unknown_job.workflow == "memory_maintenance"
    assert unknown_job.requires_confirmation is True


def test_registered_workflow_packs_cover_non_communication_catalog():
    from mira.runtime import PIPELINE_MEMORY_CLASS, WORKFLOW_PACK_PATHS

    assert set(WORKFLOW_PACK_PATHS) == set(PIPELINE_MEMORY_CLASS) - {"communication"}


def test_runtime_reconciles_default_effect_log_from_provider_state(tmp_path: Path):
    from mira.runtime import default_effect_log

    effects = default_effect_log(tmp_path)
    effects.plan(
        idempotency_key="effect:test-article",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="test-article",
    )
    effects.mark_unknown("effect:test-article", "process died after provider call")
    manifest = tmp_path / "publish_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "articles": {
                    "test-article": {
                        "slug": "test-article",
                        "status": "published",
                        "substack_url": "https://test.substack.com/p/test-article",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    reconciled = reconcile_provider_effects(root=tmp_path, publish_manifest_path=manifest)

    assert len(reconciled) == 1
    assert default_effect_log(tmp_path).get_by_idempotency_key("effect:test-article").status == "reconciled_succeeded"


def test_runtime_reconciles_default_effect_log_from_provider_connector(tmp_path: Path):
    from mira.runtime import default_effect_log

    effects = default_effect_log(tmp_path)
    effects.plan(
        idempotency_key="effect:connector-article",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="connector-article",
    )
    effects.mark_unknown("effect:connector-article", "process died after provider call")

    reconciled = reconcile_provider_effects(
        root=tmp_path,
        provider_resolvers={
            "substack": lambda entry: {
                "status": "published",
                "provider_id": "post_123",
                "url": f"https://test.substack.com/p/{entry.target}",
            }
        },
    )

    assert len(reconciled) == 1
    latest = default_effect_log(tmp_path).get_by_idempotency_key("effect:connector-article")
    assert latest.status == "reconciled_succeeded"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:post_123"


def test_runtime_reconciles_default_effect_log_from_provider_config(tmp_path: Path, monkeypatch):
    from mira.runtime import default_effect_log

    effects = default_effect_log(tmp_path)
    effects.plan(
        idempotency_key="effect:configured-article",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="configured-article",
    )
    effects.mark_unknown("effect:configured-article", "process died after provider call")
    provider_config = tmp_path / "provider_resolvers.json"
    provider_config.write_text(
        json.dumps(
            {
                "provider_effect_resolvers": {
                    "substack": {
                        "type": "http_json",
                        "endpoint_template": "https://provider.example/posts/{target}",
                        "bearer_token_env": "TEST_SUBSTACK_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_SUBSTACK_TOKEN", "token_123")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token_123"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "published",
                    "provider_id": "post_configured",
                    "url": "https://test.substack.com/p/configured-article",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        reconciled = reconcile_provider_effects(
            root=tmp_path,
            provider_config_path=provider_config,
            provider_http_clients={"substack": client},
        )
    finally:
        client.close()

    latest = default_effect_log(tmp_path).get_by_idempotency_key("effect:configured-article")
    assert len(reconciled) == 1
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://test.substack.com/p/configured-article"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:post_configured"


def test_runtime_reconciles_default_effect_log_from_provider_state_manifest_dir(tmp_path: Path):
    from mira.runtime import default_effect_log

    paths = default_v3_paths(tmp_path)
    paths.provider_state_manifests.mkdir(parents=True)
    effects = default_effect_log(tmp_path)
    effects.plan(
        idempotency_key="effect:market-alert",
        run_id="run_1",
        pipeline="market_monitor",
        action="send_market_alert",
        target="market-alert",
    )
    manifest = paths.provider_state_manifests / "market.json"
    manifest.write_text(
        json.dumps(
            {
                "market_alerts": {
                    "market_provider_123": {
                        "target": "market-alert",
                        "status": "sent",
                        "external_ref": "tetra-alert:market-alert",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    reconciled = reconcile_provider_effects(root=tmp_path)
    latest = default_effect_log(tmp_path).get_by_idempotency_key("effect:market-alert")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "tetra-alert:market-alert"
    assert latest.reconciliation_ref == f"provider_state:{manifest}:market:market_provider_123"


def test_provider_resolver_config_rejects_inline_tokens_and_unsafe_endpoints(tmp_path: Path):
    from mira.runtime import load_provider_resolvers_from_config

    provider_config = tmp_path / "provider_resolvers.json"
    provider_config.write_text(
        json.dumps(
            {
                "provider_effect_resolvers": {
                    "substack": {
                        "type": "http_json",
                        "endpoint_template": "http://provider.example/posts/{target}",
                        "bearer_token": "inline-secret",
                    },
                    "social": {
                        "type": "http_json",
                        "endpoint_template": "https://provider.example/static",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    findings = validate_provider_resolver_config(provider_config)

    assert "endpoint_template must use https" in findings["substack"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in findings["substack"]
    assert "endpoint_template must include an effect identity field" in findings["social"]
    assert load_provider_resolvers_from_config(provider_config) == {}


def test_provider_resolver_template_defines_env_based_production_profiles(tmp_path: Path, monkeypatch):
    from mira.runtime import load_provider_resolvers_from_config

    config = provider_resolver_config_template(providers=("social", "market", "health"))
    assert set(config["provider_effect_resolvers"]) == {"social", "market", "health"}
    assert config["provider_effect_resolvers"]["social"]["endpoint_template_env"] == "MIRA_SOCIAL_RESOLVER_ENDPOINT"
    assert config["provider_effect_resolvers"]["market"]["bearer_token_env"] == "MIRA_MARKET_RESOLVER_TOKEN"

    config_path = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social", "market", "health"),
    )
    missing = validate_provider_resolver_config(config_path)
    assert "endpoint_template_env MIRA_SOCIAL_RESOLVER_ENDPOINT is not set" in missing["social"]
    assert "endpoint_template_env MIRA_MARKET_RESOLVER_ENDPOINT is not set" in missing["market"]
    assert "endpoint_template_env MIRA_HEALTH_RESOLVER_ENDPOINT is not set" in missing["health"]

    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_MARKET_RESOLVER_ENDPOINT", "https://providers.example/market/{idempotency_key}")
    monkeypatch.setenv("MIRA_HEALTH_RESOLVER_ENDPOINT", "https://providers.example/health/{external_ref}")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_TOKEN", "social-token")

    assert validate_provider_resolver_config(config_path) == {}
    resolvers = load_provider_resolvers_from_config(config_path)
    assert set(resolvers) == {"social", "market", "health"}


def test_provider_resolver_production_example_matches_runtime_template():
    example_path = Path("config/v3/provider_resolvers.production.example.json")
    example = json.loads(example_path.read_text(encoding="utf-8"))

    assert example == provider_resolver_config_template()
    findings = validate_provider_resolver_config(example_path)
    assert set(findings) == {"substack", "rss", "social", "market", "health"}
    assert all(
        any("endpoint_template_env" in item for item in provider_findings) for provider_findings in findings.values()
    )


def test_provider_resolver_template_rejects_unsafe_endpoint_env(tmp_path: Path, monkeypatch):
    config_path = write_provider_resolver_config_template(tmp_path / "provider_resolvers.json", providers=("social",))
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "http://providers.example/social/{target}")

    findings = validate_provider_resolver_config(config_path)

    assert "endpoint_template must use https" in findings["social"]


def test_provider_adapter_template_defines_env_based_production_profiles(tmp_path: Path, monkeypatch):
    from mira.runtime import load_provider_adapters_from_config

    config = provider_adapter_config_template(
        providers=("social", "market", "health", "deployment", "deployment_health", "deployment_rollback")
    )
    assert set(config["provider_effect_adapters"]) == {
        "social",
        "market",
        "health",
        "deployment",
        "deployment_health",
        "deployment_rollback",
    }
    assert config["provider_effect_adapters"]["social"]["endpoint_template_env"] == "MIRA_SOCIAL_ADAPTER_ENDPOINT"
    assert config["provider_effect_adapters"]["market"]["bearer_token_env"] == "MIRA_MARKET_ADAPTER_TOKEN"
    assert config["provider_effect_adapters"]["deployment"]["preview_filename"] == (
        "self_evolution_production_promotion_preview.json"
    )
    assert config["provider_effect_adapters"]["deployment_health"]["endpoint_template_env"] == (
        "MIRA_DEPLOYMENT_HEALTH_ADAPTER_ENDPOINT"
    )
    assert config["provider_effect_adapters"]["deployment_rollback"]["endpoint_template_env"] == (
        "MIRA_DEPLOYMENT_ROLLBACK_ADAPTER_ENDPOINT"
    )

    config_path = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social", "market", "health", "deployment", "deployment_health", "deployment_rollback"),
    )
    missing = validate_provider_adapter_config(config_path)
    assert "endpoint_template_env MIRA_SOCIAL_ADAPTER_ENDPOINT is not set" in missing["social"]
    assert "endpoint_template_env MIRA_MARKET_ADAPTER_ENDPOINT is not set" in missing["market"]
    assert "endpoint_template_env MIRA_HEALTH_ADAPTER_ENDPOINT is not set" in missing["health"]
    assert "endpoint_template_env MIRA_DEPLOYMENT_ADAPTER_ENDPOINT is not set" in missing["deployment"]
    assert "endpoint_template_env MIRA_DEPLOYMENT_HEALTH_ADAPTER_ENDPOINT is not set" in missing["deployment_health"]
    assert (
        "endpoint_template_env MIRA_DEPLOYMENT_ROLLBACK_ADAPTER_ENDPOINT is not set" in missing["deployment_rollback"]
    )

    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_MARKET_ADAPTER_ENDPOINT", "https://providers.example/market/{idempotency_key}")
    monkeypatch.setenv("MIRA_HEALTH_ADAPTER_ENDPOINT", "https://providers.example/health/{effect_id}")
    monkeypatch.setenv("MIRA_DEPLOYMENT_ADAPTER_ENDPOINT", "https://providers.example/deploy/{target}")
    monkeypatch.setenv("MIRA_DEPLOYMENT_HEALTH_ADAPTER_ENDPOINT", "https://providers.example/deploy/health/{target}")
    monkeypatch.setenv(
        "MIRA_DEPLOYMENT_ROLLBACK_ADAPTER_ENDPOINT",
        "https://providers.example/deploy/rollback/{external_ref}",
    )
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "social-token")

    assert validate_provider_adapter_config(config_path) == {}
    adapters = load_provider_adapters_from_config(config_path)
    assert set(adapters) == {
        "social",
        "market",
        "health",
        "deployment",
        "deployment_health",
        "deployment_rollback",
    }


def test_provider_adapter_production_example_matches_runtime_template():
    example_path = Path("config/v3/provider_adapters.production.example.json")
    example = json.loads(example_path.read_text(encoding="utf-8"))

    assert example == provider_adapter_config_template()
    findings = validate_provider_adapter_config(example_path)
    assert set(findings) == {
        "deployment",
        "deployment_health",
        "deployment_rollback",
        "substack",
        "rss",
        "social",
        "market",
        "health",
        "tts",
    }
    assert all(
        any("endpoint_template_env" in item for item in provider_findings) for provider_findings in findings.values()
    )


def test_provider_production_readiness_reports_missing_files(tmp_path: Path):
    report = provider_production_readiness_report(
        resolver_config_path=tmp_path / "missing_resolvers.json",
        adapter_config_path=tmp_path / "missing_adapters.json",
        required_resolvers=("social",),
        required_adapters=("deployment",),
    )

    assert report["ready"] is False
    assert "provider_resolvers config file is missing" in report["findings"]["provider_resolvers"]["_config"][0]
    assert "provider_adapters config file is missing" in report["findings"]["provider_adapters"]["_config"][0]


def test_provider_production_readiness_requires_env_credentials_and_local_commands(tmp_path: Path, monkeypatch):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "deployment": {
                        "type": "local_deployment_command",
                        "command": [str(tmp_path / "missing-deploy"), "{input_json_path}", "{result_json_path}"],
                    },
                    "social": {
                        "type": "hosted_social_http",
                        "endpoint_template_env": "MIRA_TEST_SOCIAL_ADAPTER_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_SOCIAL_ADAPTER_TOKEN",
                        "payload_path": ["data"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_TEST_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")

    report = provider_production_readiness_report(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("deployment", "social"),
    )

    assert report["ready"] is False
    assert (
        "bearer_token_env MIRA_SOCIAL_RESOLVER_TOKEN is not set" in report["findings"]["provider_resolvers"]["social"]
    )
    assert (
        report["findings"]["provider_resolvers"]["social"].count(
            "endpoint_template_env MIRA_SOCIAL_RESOLVER_ENDPOINT is not set"
        )
        == 0
    )
    assert "local command executable is not a file" in report["findings"]["provider_adapters"]["deployment"][0]
    assert (
        "bearer_token_env MIRA_TEST_SOCIAL_ADAPTER_TOKEN is not set"
        in report["findings"]["provider_adapters"]["social"]
    )


def test_provider_production_readiness_passes_for_env_backed_and_local_command_configs(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "deployment": {
                        "type": "local_deployment_command",
                        "command": [sys.executable, "{input_json_path}", "{result_json_path}"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_TOKEN", "social-token")

    report = provider_production_readiness_report(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("deployment",),
    )

    assert report["ready"] is True
    assert report["configured_resolvers"] == ["social"]
    assert report["configured_adapters"] == ["deployment"]
    assert report["findings"] == {"provider_resolvers": {}, "provider_adapters": {}}


def test_provider_production_readiness_rejects_placeholder_env_values(tmp_path: Path, monkeypatch):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social",),
    )
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://provider.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_TOKEN", "REPLACE_WITH_MIRA_SOCIAL_RESOLVER_TOKEN_SECRET")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://provider.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "REPLACE_WITH_MIRA_SOCIAL_ADAPTER_TOKEN_SECRET")

    report = provider_production_readiness_report(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("social",),
    )

    assert report["ready"] is False
    assert (
        "endpoint_template_env MIRA_SOCIAL_RESOLVER_ENDPOINT still contains a placeholder value"
        in report["findings"]["provider_resolvers"]["social"]
    )
    assert (
        "bearer_token_env MIRA_SOCIAL_RESOLVER_TOKEN still contains a placeholder value"
        in report["findings"]["provider_resolvers"]["social"]
    )
    assert (
        "endpoint_template_env MIRA_SOCIAL_ADAPTER_ENDPOINT still contains a placeholder value"
        in report["findings"]["provider_adapters"]["social"]
    )
    assert (
        "bearer_token_env MIRA_SOCIAL_ADAPTER_TOKEN still contains a placeholder value"
        in report["findings"]["provider_adapters"]["social"]
    )


def test_provider_production_readiness_scopes_findings_to_required_providers(tmp_path: Path, monkeypatch):
    resolver_config = write_provider_resolver_config_template(tmp_path / "provider_resolvers.json")
    adapter_config = write_provider_adapter_config_template(tmp_path / "provider_adapters.json")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_TOKEN", "resolver-token")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "adapter-token")

    report = provider_production_readiness_report(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("social",),
    )

    assert report["ready"] is True
    assert report["findings"] == {"provider_resolvers": {}, "provider_adapters": {}}


def test_provider_provisioning_env_template_lists_no_secret_required_env_vars(tmp_path: Path):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social",),
    )

    text = provider_provisioning_env_template(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("social",),
        root=tmp_path,
    )

    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT=" in text
    assert "MIRA_SOCIAL_RESOLVER_TOKEN=" in text
    assert "MIRA_SOCIAL_ADAPTER_ENDPOINT=" in text
    assert "MIRA_SOCIAL_ADAPTER_TOKEN=" in text
    assert "REPLACE_WITH_MIRA_SOCIAL_ADAPTER_TOKEN_SECRET" in text
    assert "social-token" not in text
    assert '"bearer_token":' not in text


def test_provider_provisioning_runbook_lists_scoped_no_secret_commands(tmp_path: Path):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social", "tts"),
    )

    text = provider_provisioning_runbook(
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("social", "tts"),
        root=tmp_path,
    )

    assert "Status: blocked" in text
    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT=" in text
    assert "MIRA_SOCIAL_ADAPTER_TOKEN=" in text
    assert "MIRA_TTS_ADAPTER_ENDPOINT=" in text
    assert "REPLACE_WITH_MIRA_TTS_ADAPTER_TOKEN_SECRET" in text
    assert "## Recommended First Canary" in text
    assert "- Provider: tts" in text
    assert "- Missing env vars: MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN" in text
    assert "--require-resolver social --require-adapter social --json" in text
    assert "--skip-resolvers --require-adapter tts --json" in text
    assert "--provider tts --dry-run --json" in text
    assert "v3_provider_production_canary.py" in text
    assert "--provider social --json" in text
    assert "--provider tts --json" in text
    assert '"bearer_token":' not in text
    assert "social-token" not in text


def test_write_provider_provisioning_runbook_refuses_overwrite(tmp_path: Path):
    runbook = tmp_path / "provider_runbook.md"
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social",),
    )

    written = write_provider_provisioning_runbook(
        runbook,
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=("social",),
        required_adapters=("social",),
        root=tmp_path,
    )

    assert written == runbook
    assert "Mira V3 Provider Provisioning Runbook" in runbook.read_text(encoding="utf-8")
    try:
        write_provider_provisioning_runbook(
            runbook,
            resolver_config_path=resolver_config,
            adapter_config_path=adapter_config,
            required_resolvers=("social",),
            required_adapters=("social",),
            root=tmp_path,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")


def test_provider_readiness_cli_returns_nonzero_when_not_ready(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--resolver-config",
            str(tmp_path / "missing_resolvers.json"),
            "--adapter-config",
            str(tmp_path / "missing_adapters.json"),
            "--require-resolver",
            "social",
            "--require-adapter",
            "deployment",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["ready"] is False


def test_provider_readiness_cli_writes_missing_no_secret_templates(tmp_path: Path):
    resolver_config = tmp_path / "provider_resolvers.json"
    adapter_config = tmp_path / "provider_adapters.json"

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--write-missing-templates",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    resolver_payload = json.loads(resolver_config.read_text(encoding="utf-8"))
    adapter_payload = json.loads(adapter_config.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert payload["ready"] is False
    assert payload["created_templates"] == [str(resolver_config), str(adapter_config)]
    assert (
        payload["findings"]["provider_resolvers"]["social"].count(
            "endpoint_template_env MIRA_SOCIAL_RESOLVER_ENDPOINT is not set"
        )
        == 1
    )
    assert resolver_payload == provider_resolver_config_template()
    assert adapter_payload == provider_adapter_config_template()
    assert '"bearer_token":' not in json.dumps(resolver_payload)
    assert '"bearer_token":' not in json.dumps(adapter_payload)


def test_provider_readiness_cli_does_not_overwrite_existing_configs(tmp_path: Path):
    resolver_config = tmp_path / "provider_resolvers.json"
    adapter_config = tmp_path / "provider_adapters.json"
    resolver_config.write_text(
        json.dumps({"provider_effect_resolvers": {"social": {"type": "http_json"}}}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--write-missing-templates",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    resolver_payload = json.loads(resolver_config.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert payload["created_templates"] == [str(adapter_config)]
    assert resolver_payload == {"provider_effect_resolvers": {"social": {"type": "http_json"}}}
    assert json.loads(adapter_config.read_text(encoding="utf-8")) == provider_adapter_config_template()


def test_provider_readiness_cli_writes_no_secret_env_template(tmp_path: Path):
    resolver_config = tmp_path / "provider_resolvers.json"
    adapter_config = tmp_path / "provider_adapters.json"
    env_template = tmp_path / "provider_provisioning.template"

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--write-missing-templates",
            "--write-env-template",
            str(env_template),
            "--require-resolver",
            "social",
            "--require-adapter",
            "social",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    text = env_template.read_text(encoding="utf-8")

    assert result.returncode == 1
    assert payload["env_template"] == str(env_template)
    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT=" in text
    assert "MIRA_SOCIAL_ADAPTER_TOKEN=" in text
    assert "REPLACE_WITH_MIRA_SOCIAL_ADAPTER_TOKEN_SECRET" in text


def test_provider_readiness_cli_writes_no_secret_runbook(tmp_path: Path):
    resolver_config = tmp_path / "provider_resolvers.json"
    adapter_config = tmp_path / "provider_adapters.json"
    runbook = tmp_path / "provider_runbook.md"

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--root",
            str(tmp_path),
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--write-missing-templates",
            "--write-runbook",
            str(runbook),
            "--require-resolver",
            "social",
            "--require-adapter",
            "social",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)
    text = runbook.read_text(encoding="utf-8")

    assert result.returncode == 1
    assert payload["runbook"] == str(runbook)
    assert "MIRA_SOCIAL_RESOLVER_ENDPOINT=" in text
    assert "--require-resolver social --require-adapter social --json" in text
    assert "--provider social --json" in text
    assert "REPLACE_WITH_MIRA_SOCIAL_ADAPTER_TOKEN_SECRET" in text


def test_provider_readiness_cli_can_scope_to_adapter_only_tts(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=(),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("tts",),
    )
    monkeypatch.setenv("MIRA_TTS_ADAPTER_ENDPOINT", "https://providers.example/tts/{target}")
    monkeypatch.setenv("MIRA_TTS_ADAPTER_TOKEN", "tts-adapter-token")

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_readiness.py",
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--skip-resolvers",
            "--require-adapter",
            "tts",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["ready"] is True
    assert payload["configured_resolvers"] == []
    assert payload["findings"]["provider_resolvers"] == {}


def test_local_provider_dress_rehearsal_runs_approval_adapter_and_reconciliation(tmp_path: Path):
    report = run_local_provider_dress_rehearsal(root=tmp_path, providers=("social", "market", "health"))
    paths = default_v3_paths(tmp_path)

    assert report["ready"] is True
    assert report["providers"] == ["social", "market", "health"]
    assert {item["effect_status"] for item in report["rehearsals"]} == {"reconciled_succeeded"}
    assert all(item["approval_token_id"] for item in report["rehearsals"])
    assert all(item["planned_effect_id"] != item["effect_id"] for item in report["rehearsals"])
    assert all(item["reconciled_effects"] for item in report["rehearsals"])
    assert (paths.provider_state_manifests / "social_dress_rehearsal.json").exists()
    assert (paths.provider_state_manifests / "market_dress_rehearsal.json").exists()
    assert (paths.provider_state_manifests / "health_dress_rehearsal.json").exists()


def test_provider_dress_rehearsal_cli_runs_selected_provider(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_dress_rehearsal.py",
            "--root",
            str(tmp_path),
            "--provider",
            "social",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["ready"] is True
    assert payload["providers"] == ["social"]
    assert payload["rehearsals"][0]["effect_status"] == "reconciled_succeeded"


def test_provider_production_canary_refuses_when_readiness_fails(tmp_path: Path):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social",),
    )

    report = run_provider_production_canary(
        root=tmp_path,
        providers=("social",),
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
    )

    assert report["ready"] is False
    assert report["canaries"] == []
    assert default_approval_store(tmp_path).list_requests() == []
    assert default_effect_log(tmp_path).list() == []


def test_provider_production_canary_runs_approval_and_configured_adapter(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=("social",),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("social",),
    )
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_RESOLVER_TOKEN", "resolver-token")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "adapter-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer adapter-token"
        assert body["action"] == "post_social"
        assert body["approval_token_id"]
        assert body["preview_hash"]
        assert body["preview"]["content"] == "Mira V3 local provider dress rehearsal social canary"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "posted",
                    "provider_id": "canary_1",
                    "url": "https://social.example/canary-1",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        report = run_provider_production_canary(
            root=tmp_path,
            providers=("social",),
            resolver_config_path=resolver_config,
            adapter_config_path=adapter_config,
            provider_http_clients={"social": client},
        )
    finally:
        client.close()

    assert report["ready"] is True
    assert report["providers"] == ["social"]
    assert report["canaries"][0]["effect_status"] == "succeeded"
    assert report["canaries"][0]["external_ref"] == "https://social.example/canary-1"
    assert report["canaries"][0]["approval_token_id"]


def test_provider_production_canary_runs_substack_and_rss_configured_adapters(
    tmp_path: Path,
    monkeypatch,
):
    cases = {
        "substack": {
            "resolver_token": "substack-resolver-token",
            "adapter_token": "substack-adapter-token",
            "expected_action": "publish_substack",
            "response": {
                "status": "published",
                "provider_id": "substack_canary_1",
                "url": "https://substack.example/p/mira-v3-production-substack-canary",
            },
        },
        "rss": {
            "resolver_token": "rss-resolver-token",
            "adapter_token": "rss-adapter-token",
            "expected_action": "publish_rss",
            "response": {
                "status": "published",
                "provider_id": "rss_canary_1",
                "feed_url": "https://rss.example/feed.xml",
                "episode_url": "https://rss.example/episodes/mira-v3-production-rss-canary",
            },
        },
    }
    for provider, case in cases.items():
        root = tmp_path / provider
        resolver_config = write_provider_resolver_config_template(
            root / "provider_resolvers.json",
            providers=(provider,),
        )
        adapter_config = write_provider_adapter_config_template(
            root / "provider_adapters.json",
            providers=(provider,),
        )
        env_prefix = provider.upper()
        monkeypatch.setenv(f"MIRA_{env_prefix}_RESOLVER_ENDPOINT", f"https://providers.example/{provider}/{{target}}")
        monkeypatch.setenv(f"MIRA_{env_prefix}_RESOLVER_TOKEN", str(case["resolver_token"]))
        monkeypatch.setenv(f"MIRA_{env_prefix}_ADAPTER_ENDPOINT", f"https://providers.example/{provider}/{{target}}")
        monkeypatch.setenv(f"MIRA_{env_prefix}_ADAPTER_TOKEN", str(case["adapter_token"]))

        def handler(
            request: httpx.Request,
            *,
            expected_action=case["expected_action"],
            token=case["adapter_token"],
            response=case["response"],
        ) -> httpx.Response:
            body = json.loads(request.content)
            assert request.headers["authorization"] == f"Bearer {token}"
            assert body["action"] == expected_action
            assert body["approval_token_id"]
            assert body["preview_hash"]
            return httpx.Response(200, json={"data": response})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            report = run_provider_production_canary(
                root=root,
                providers=(provider,),
                resolver_config_path=resolver_config,
                adapter_config_path=adapter_config,
                provider_http_clients={provider: client},
            )
        finally:
            client.close()

        assert report["ready"] is True
        assert report["providers"] == [provider]
        assert report["canaries"][0]["effect_status"] == "succeeded"
        assert report["canaries"][0]["approval_token_id"]
        assert report["canaries"][0]["external_ref"]


def test_provider_production_canary_surface_covers_content_providers():
    surface = provider_production_canary_surface()

    assert set(surface) >= {"substack", "rss", "tts", "social", "market", "health"}
    assert surface["substack"]["effect_action"] == "publish_substack"
    assert surface["rss"]["effect_action"] == "publish_rss"
    assert surface["tts"]["effect_action"] == "synthesize_tts"
    assert surface["tts"]["requires_adapter"] is True
    assert surface["tts"]["requires_resolver"] is False
    assert all(surface[provider]["requires_resolver"] for provider in ("substack", "rss", "social", "market", "health"))


def test_provider_production_canary_runs_tts_adapter_without_resolver(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=(),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("tts",),
    )
    monkeypatch.setenv("MIRA_TTS_ADAPTER_ENDPOINT", "https://providers.example/tts/{target}")
    monkeypatch.setenv("MIRA_TTS_ADAPTER_TOKEN", "tts-adapter-token")

    readiness = provider_production_readiness_report(
        root=tmp_path,
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        required_resolvers=(),
        required_adapters=("tts",),
    )
    assert readiness["ready"] is True
    assert readiness["configured_resolvers"] == []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer tts-adapter-token"
        assert body["action"] == "synthesize_tts"
        assert body["script_text"] == "Mira V3 production TTS provider canary."
        assert body["voice"] == "mira-canary"
        assert body["preview"]["audio_output_name"] == "mira-v3-production-tts-canary.wav"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "synthesized",
                    "provider_id": "tts_canary_1",
                    "audio_base64": base64.b64encode(b"HOSTED-WAV:canary").decode("ascii"),
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        report = run_provider_production_canary(
            root=tmp_path,
            providers=("tts",),
            resolver_config_path=resolver_config,
            adapter_config_path=adapter_config,
            provider_http_clients={"tts": client},
        )
    finally:
        client.close()

    assert report["ready"] is True
    assert report["providers"] == ["tts"]
    assert report["readiness"]["configured_resolvers"] == []
    assert report["canaries"][0]["effect_status"] == "succeeded"
    assert report["canaries"][0]["external_ref"].endswith("mira-v3-production-tts-canary.wav")


def test_provider_production_canary_dry_run_does_not_mutate_state(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=(),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("tts",),
    )
    monkeypatch.setenv("MIRA_TTS_ADAPTER_ENDPOINT", "https://providers.example/tts/{target}")
    monkeypatch.setenv("MIRA_TTS_ADAPTER_TOKEN", "tts-adapter-token")

    report = run_provider_production_canary(
        root=tmp_path,
        providers=("tts",),
        resolver_config_path=resolver_config,
        adapter_config_path=adapter_config,
        dry_run=True,
    )

    assert report["ready"] is True
    assert report["dry_run"] is True
    assert report["canaries"] == [
        {
            "provider": "tts",
            "workflow": "podcast_production",
            "target": "production-tts-canary-<dry-run>",
            "approval_action": "synthesize_tts_idempotent",
            "effect_action": "synthesize_tts",
            "payload_keys": ["audio_output_name", "connectors", "script_text", "title", "voice"],
        }
    ]
    assert default_approval_store(tmp_path).list_requests() == []
    assert default_effect_log(tmp_path).list() == []


def test_provider_production_canary_cli_returns_nonzero_when_readiness_fails(tmp_path: Path):
    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_production_canary.py",
            "--root",
            str(tmp_path),
            "--provider",
            "social",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["ready"] is False
    assert payload["canaries"] == []


def test_provider_production_canary_cli_dry_run_succeeds_without_mutation(
    tmp_path: Path,
    monkeypatch,
):
    resolver_config = write_provider_resolver_config_template(
        tmp_path / "provider_resolvers.json",
        providers=(),
    )
    adapter_config = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("tts",),
    )
    monkeypatch.setenv("MIRA_TTS_ADAPTER_ENDPOINT", "https://providers.example/tts/{target}")
    monkeypatch.setenv("MIRA_TTS_ADAPTER_TOKEN", "tts-adapter-token")

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_provider_production_canary.py",
            "--root",
            str(tmp_path),
            "--resolver-config",
            str(resolver_config),
            "--adapter-config",
            str(adapter_config),
            "--provider",
            "tts",
            "--dry-run",
            "--json",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["ready"] is True
    assert payload["dry_run"] is True
    assert payload["providers"] == ["tts"]
    assert payload["canaries"][0]["provider"] == "tts"
    assert payload["canaries"][0]["target"] == "production-tts-canary-<dry-run>"
    assert payload["canaries"][0]["effect_action"] == "synthesize_tts"
    assert default_approval_store(tmp_path).list_requests() == []
    assert default_effect_log(tmp_path).list() == []


def test_provider_effect_adapter_can_load_http_adapter_from_config(tmp_path: Path, monkeypatch):
    from mira.runtime import default_effect_log

    config_path = write_provider_adapter_config_template(tmp_path / "provider_adapters.json", providers=("social",))
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "social-token")
    planned = default_effect_log(tmp_path).plan(
        idempotency_key="social_reactive:post_reply_idempotent:reply-456",
        run_id="run_social",
        pipeline="social_reactive",
        action="post_social",
        target="reply-456",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )
    preview_dir = default_v3_paths(tmp_path).artifacts / "social_reactive" / "run_social"
    preview_dir.mkdir(parents=True)
    (preview_dir / "social_publish_preview.json").write_text(
        json.dumps(
            {
                "pipeline": "social_reactive",
                "kind": "reply",
                "platform": "substack_comments",
                "target": "reply-456",
                "content": "approved reply body",
                "status": "staged",
                "live_publish": False,
            }
        ),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer social-token"
        body = json.loads(request.content)
        assert body["idempotency_key"] == planned.idempotency_key
        assert body["approval_token_id"] == "grant_1"
        assert body["preview"]["content"] == "approved reply body"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "posted",
                    "provider_id": "post_456",
                    "url": "https://social.example/posts/reply-456",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=planned.idempotency_key,
            provider_config_path=config_path,
            provider_http_clients={"social": client},
        )
    finally:
        client.close()

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://social.example/posts/reply-456"


def test_social_provider_http_adapter_posts_approved_preview_from_workflow(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    config_path = write_provider_adapter_config_template(tmp_path / "provider_adapters.json", providers=("social",))
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_ENDPOINT", "https://providers.example/social/{target}")
    monkeypatch.setenv("MIRA_SOCIAL_ADAPTER_TOKEN", "social-token")
    payload = {
        "connectors": {"social": True},
        "target": "note-789",
        "content": "approved workflow note",
        "platform": "substack_notes",
    }
    run_named_workflow("social_proactive", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("social_proactive", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer social-token"
        assert body["action"] == "post_social"
        assert body["target"] == "note-789"
        assert body["preview"]["platform"] == "substack_notes"
        assert body["preview"]["content"] == "approved workflow note"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "posted",
                    "provider_id": "note_789",
                    "url": "https://social.example/notes/note-789",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=config_path,
            provider_http_clients={"social": client},
        )
    finally:
        client.close()

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://social.example/notes/note-789"


def test_hosted_social_http_adapter_posts_approved_preview(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"social": True},
        "target": "note-123",
        "content": "approved social body",
        "platform": "substack_notes",
    }
    run_named_workflow("social_proactive", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("social_proactive", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "social": {
                        "type": "hosted_social_http",
                        "endpoint_template_env": "MIRA_TEST_SOCIAL_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_SOCIAL_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_TEST_SOCIAL_ENDPOINT", "https://social.example/post/{target}")
    monkeypatch.setenv("MIRA_TEST_SOCIAL_TOKEN", "social-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer social-token"
        assert body["action"] == "post_social"
        assert body["target"] == "note-123"
        assert body["platform"] == "substack_notes"
        assert body["content"] == "approved social body"
        assert body["preview"]["platform"] == "substack_notes"
        assert body["preview"]["content"] == "approved social body"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "posted",
                    "provider_id": "social_123",
                    "url": "https://social.example/posts/social_123",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=adapter_config,
            provider_http_clients={"social": client},
        )
    finally:
        client.close()
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "social_proactive" / effect.run_id / "social_publish_result.json"
    )
    manifest = json.loads(result_manifest.read_text(encoding="utf-8"))

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://social.example/posts/social_123"
    assert manifest["provider_id"] == "social_123"
    assert manifest["platform"] == "substack_notes"
    assert manifest["content"] == "approved social body"


def test_hosted_market_http_adapter_sends_approved_preview(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"market_alert": True},
        "target": "portfolio-review-target",
        "message": "Risk exposure changed",
        "severity": "high",
        "tetra_report_id": "tetra-42",
    }
    run_named_workflow("market_monitor", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("market_monitor", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "market": {
                        "type": "hosted_market_http",
                        "endpoint_template_env": "MIRA_TEST_MARKET_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_MARKET_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_TEST_MARKET_ENDPOINT", "https://market.example/alert/{target}")
    monkeypatch.setenv("MIRA_TEST_MARKET_TOKEN", "market-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer market-token"
        assert body["action"] == "send_market_alert"
        assert body["target"] == "portfolio-review-target"
        assert body["message"] == "Risk exposure changed"
        assert body["severity"] == "high"
        assert body["tetra_report_id"] == "tetra-42"
        assert body["preview"]["message"] == "Risk exposure changed"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "sent",
                    "provider_id": "market_123",
                    "alert_url": "https://market.example/alerts/market_123",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=adapter_config,
            provider_http_clients={"market": client},
        )
    finally:
        client.close()
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "market_monitor" / effect.run_id / "market_alert_result.json"
    )
    manifest = json.loads(result_manifest.read_text(encoding="utf-8"))

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://market.example/alerts/market_123"
    assert manifest["provider_id"] == "market_123"
    assert manifest["message"] == "Risk exposure changed"
    assert manifest["severity"] == "high"


def test_hosted_health_http_adapter_syncs_approved_preview(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"health_provider": True},
        "target": "health-review-target",
        "operation": "sync_sleep_record",
        "record": {"sleep_hours": 7, "source": "manual_review"},
    }
    run_named_workflow("health_wellness", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("health_wellness", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "health": {
                        "type": "hosted_health_http",
                        "endpoint_template_env": "MIRA_TEST_HEALTH_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_HEALTH_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_TEST_HEALTH_ENDPOINT", "https://health.example/write/{target}")
    monkeypatch.setenv("MIRA_TEST_HEALTH_TOKEN", "health-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer health-token"
        assert body["action"] == "write_health"
        assert body["target"] == "health-review-target"
        assert body["operation"] == "sync_sleep_record"
        assert body["record"] == {"sleep_hours": 7, "source": "manual_review"}
        assert body["preview"]["operation"] == "sync_sleep_record"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "synced",
                    "provider_id": "health_123",
                    "record_url": "https://health.example/records/health_123",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=adapter_config,
            provider_http_clients={"health": client},
        )
    finally:
        client.close()
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "health_wellness" / effect.run_id / "health_write_result.json"
    )
    manifest = json.loads(result_manifest.read_text(encoding="utf-8"))

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://health.example/records/health_123"
    assert manifest["provider_id"] == "health_123"
    assert manifest["operation"] == "sync_sleep_record"
    assert manifest["record"] == {"sleep_hours": 7, "source": "manual_review"}


def test_provider_adapter_config_validates_local_rss_feed_path(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "rss": {
                        "type": "local_rss_feed",
                        "feed_path_env": "MIRA_TEST_RSS_FEED",
                    },
                    "bad_rss": {
                        "type": "local_rss_feed",
                        "feed_path": "https://podcast.example/feed.xml",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "feed_path_env MIRA_TEST_RSS_FEED is not set" in missing["rss"]
    assert "local RSS feed_path must be a filesystem path" in missing["bad_rss"]

    monkeypatch.setenv("MIRA_TEST_RSS_FEED", str(tmp_path / "feed.xml"))
    fixed = validate_provider_adapter_config(config_path)
    assert "rss" not in fixed
    assert "bad_rss" in fixed


def test_provider_adapter_config_validates_hosted_rss_http(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "rss": {
                        "type": "hosted_rss_http",
                        "endpoint_template_env": "MIRA_TEST_RSS_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_RSS_TOKEN",
                    },
                    "bad_rss": {
                        "type": "hosted_rss_http",
                        "endpoint_template": "http://rss.example/publish/{target}",
                        "bearer_token": "inline-secret",
                        "method": "GET",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "endpoint_template_env MIRA_TEST_RSS_ENDPOINT is not set" in missing["rss"]
    assert "endpoint_template must use https" in missing["bad_rss"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in missing["bad_rss"]
    assert "hosted RSS adapter method must be POST" in missing["bad_rss"]

    monkeypatch.setenv("MIRA_TEST_RSS_ENDPOINT", "https://rss.example/publish/{target}")
    fixed = validate_provider_adapter_config(config_path)
    assert "rss" not in fixed
    assert "bad_rss" in fixed


def test_provider_adapter_config_validates_hosted_social_http(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "social": {
                        "type": "hosted_social_http",
                        "endpoint_template_env": "MIRA_TEST_SOCIAL_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_SOCIAL_TOKEN",
                    },
                    "bad_social": {
                        "type": "hosted_social_http",
                        "endpoint_template": "http://social.example/post/{target}",
                        "bearer_token": "inline-secret",
                        "method": "GET",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "endpoint_template_env MIRA_TEST_SOCIAL_ENDPOINT is not set" in missing["social"]
    assert "endpoint_template must use https" in missing["bad_social"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in missing["bad_social"]
    assert "hosted social adapter method must be POST" in missing["bad_social"]

    monkeypatch.setenv("MIRA_TEST_SOCIAL_ENDPOINT", "https://social.example/post/{target}")
    fixed = validate_provider_adapter_config(config_path)
    assert "social" not in fixed
    assert "bad_social" in fixed


def test_provider_adapter_config_validates_hosted_market_and_health_http(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "market": {
                        "type": "hosted_market_http",
                        "endpoint_template_env": "MIRA_TEST_MARKET_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_MARKET_TOKEN",
                    },
                    "health": {
                        "type": "hosted_health_http",
                        "endpoint_template_env": "MIRA_TEST_HEALTH_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_HEALTH_TOKEN",
                    },
                    "bad_market": {
                        "type": "hosted_market_http",
                        "endpoint_template": "http://market.example/alert/{target}",
                        "bearer_token": "inline-secret",
                        "method": "GET",
                    },
                    "bad_health": {
                        "type": "hosted_health_http",
                        "endpoint_template": "http://health.example/write/{target}",
                        "bearer_token": "inline-secret",
                        "method": "GET",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "endpoint_template_env MIRA_TEST_MARKET_ENDPOINT is not set" in missing["market"]
    assert "endpoint_template_env MIRA_TEST_HEALTH_ENDPOINT is not set" in missing["health"]
    assert "endpoint_template must use https" in missing["bad_market"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in missing["bad_market"]
    assert "hosted market adapter method must be POST" in missing["bad_market"]
    assert "endpoint_template must use https" in missing["bad_health"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in missing["bad_health"]
    assert "hosted health adapter method must be POST" in missing["bad_health"]

    monkeypatch.setenv("MIRA_TEST_MARKET_ENDPOINT", "https://market.example/alert/{target}")
    monkeypatch.setenv("MIRA_TEST_HEALTH_ENDPOINT", "https://health.example/write/{target}")
    fixed = validate_provider_adapter_config(config_path)
    assert "market" not in fixed
    assert "health" not in fixed
    assert "bad_market" in fixed
    assert "bad_health" in fixed


def test_provider_adapter_config_validates_local_provider_state_path(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "social": {
                        "type": "local_provider_state",
                        "provider": "social",
                        "manifest_path_env": "MIRA_TEST_SOCIAL_STATE",
                    },
                    "bad": {
                        "type": "local_provider_state",
                        "provider": "unknown",
                        "manifest_path": "https://provider.example/state.json",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "manifest_path_env MIRA_TEST_SOCIAL_STATE is not set" in missing["social"]
    assert "local provider-state provider must be substack, social, market, or health" in missing["bad"]
    assert "local provider-state manifest_path must be a filesystem path" in missing["bad"]

    monkeypatch.setenv("MIRA_TEST_SOCIAL_STATE", str(tmp_path / "social_state.json"))
    fixed = validate_provider_adapter_config(config_path)
    assert "social" not in fixed
    assert "bad" in fixed


def test_provider_adapter_config_validates_local_tts_command(tmp_path: Path):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "tts": {"type": "local_tts_command", "command": []},
                    "valid_tts": {
                        "type": "local_tts_command",
                        "command": [sys.executable, "tts_stub.py", "{input_text_path}", "{output_audio_path}"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    findings = validate_provider_adapter_config(config_path)

    assert "local TTS command must be a non-empty list" in findings["tts"]
    assert "valid_tts" not in findings


def test_provider_adapter_config_validates_local_deployment_command(tmp_path: Path):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "deployment": {"type": "local_deployment_command", "command": []},
                    "bad_role": {
                        "type": "local_deployment_command",
                        "role": "unknown",
                        "command": ["deploy"],
                        "timeout_s": 0,
                    },
                    "deployment_health": {
                        "type": "local_deployment_command",
                        "command": [sys.executable, "deploy_health_stub.py", "{input_json_path}", "{result_json_path}"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    findings = validate_provider_adapter_config(config_path)

    assert "local deployment command must be a non-empty list" in findings["deployment"]
    assert (
        "local deployment command role must be deployment, deployment_health, or deployment_rollback"
        in findings["bad_role"]
    )
    assert "local deployment command timeout_s must be a positive number" in findings["bad_role"]
    assert "deployment_health" not in findings


def test_provider_adapter_config_validates_hosted_tts_http(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "provider_adapters.json"
    config_path.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "tts": {
                        "type": "hosted_tts_http",
                        "endpoint_template_env": "MIRA_TEST_TTS_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_TTS_TOKEN",
                    },
                    "bad_tts": {
                        "type": "hosted_tts_http",
                        "endpoint_template": "http://tts.example/synthesize/{target}",
                        "bearer_token": "inline-secret",
                        "method": "GET",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    missing = validate_provider_adapter_config(config_path)
    assert "endpoint_template_env MIRA_TEST_TTS_ENDPOINT is not set" in missing["tts"]
    assert "endpoint_template must use https" in missing["bad_tts"]
    assert "inline bearer_token is not allowed; use bearer_token_env" in missing["bad_tts"]
    assert "hosted TTS adapter method must be POST" in missing["bad_tts"]

    monkeypatch.setenv("MIRA_TEST_TTS_ENDPOINT", "https://tts.example/synthesize/{target}")
    fixed = validate_provider_adapter_config(config_path)
    assert "tts" not in fixed
    assert "bad_tts" in fixed


def test_record_task_completion_writes_v3_experience(tmp_path: Path):
    record = record_task_completion(
        task_id="task_1",
        status="done",
        summary="Finished a research task",
        tags=["research"],
        root=tmp_path,
    )

    records = default_ledger(tmp_path).list()
    assert record.pipeline == "research_deep_dive"
    assert records[0].id == record.id
    assert records[0].delta.actions[0].target == "skill:research_deep_dive"


def test_record_background_completion_writes_v3_experience(tmp_path: Path):
    record = record_background_completion("substack-comments", root=tmp_path)

    assert record.pipeline == "social_reactive"
    assert default_ledger(tmp_path).list()[0].pipeline == "social_reactive"


def test_record_background_completion_skips_writing_pipeline_noop_ticks(tmp_path: Path):
    record = record_background_completion("writing-pipeline", root=tmp_path)

    assert record is None
    assert default_ledger(tmp_path).list() == []


def test_prepare_background_context_exports_snapshot(tmp_path: Path):
    env = prepare_background_context("explore-morning", root=tmp_path)

    assert env["MIRA_V3_PIPELINE"] == "intelligence_briefing"
    route = json.loads(env["MIRA_V3_ROUTE_DECISION"])
    assert route["workflow"] == "intelligence_briefing"
    assert "required_connectors_missing" in route
    snapshot_path = Path(env["MIRA_V3_MEMORY_SNAPSHOT"])
    assert snapshot_path.exists()
    assert snapshot_path.name == "explore-morning.json"


def test_run_communication_uses_memory_between_runs(tmp_path: Path):
    first = run_communication("Implement the next piece and give me status.", root=tmp_path)
    second = run_communication("Implement the next piece and give me status.", root=tmp_path)

    assert first.startswith("I read this as:")
    assert second.startswith("Short answer:")
    evidence = default_causal_evidence_log(tmp_path).list()
    assert len(evidence) == 1
    assert evidence[0].level == "L4"
    assert evidence[0].ablation_ref


def test_run_named_workflow_uses_default_v31_stores(tmp_path: Path):
    from mira.runtime import default_commit_log, default_kernel_store, run_named_workflow

    result = run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    assert result.record.pipeline == "a2a_trust_experiment"
    assert result.record.artifacts
    assert any(path.endswith("a2a_public_writeup_draft.md") for path in result.record.artifacts)
    assert any(path.endswith("a2a_commercial_options.md") for path in result.record.artifacts)
    assert any(path.endswith("a2a_product_thesis.md") for path in result.record.artifacts)
    assert "public_writeup_plan:a2a_manifest_note" in result.record.eval_refs
    assert not any(ref.startswith("public_writeup:") for ref in result.record.eval_refs)
    assert "product_thesis:a2a_validator_api" in result.record.eval_refs
    assert "commercial:a2a_validator_api" in result.record.eval_refs
    assert "commercial:a2a_audit_packet" in result.record.eval_refs
    hypothesis = default_kernel_store(tmp_path).load().hypothesis("hypothesis:a2a_trust_manifest")
    assert hypothesis is not None
    assert hypothesis.evidence_for == [
        f"A2A trust experiment {result.record.id} produced a manifest validator artifact and commercial option map."
    ]
    assert default_commit_log(tmp_path).list()[0].status == "applied"
    audit_artifacts = list(default_v3_paths(tmp_path).workflow_audits.glob("a2a_trust_experiment-*.json"))
    assert len(audit_artifacts) == 1
    audit = json.loads(audit_artifacts[0].read_text(encoding="utf-8"))["workflow_pack_audit"]
    assert audit["result"] == "pass"
    assert audit["audit_hash"]
    assert audit["enabled_at"]
    assert any(path.endswith("a2a_trust/SKILL.md") for path in audit["files_checked"])


def test_article_publish_guard_leaves_mvp_publish_effect_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    blocked = run_named_workflow("article_creation", payload={"connectors": {"substack": True}}, root=tmp_path)
    assert blocked.record.outcome == "approval_required"

    approvals = default_approval_store(tmp_path)
    pending = approvals.list_requests(status="pending")
    assert len(pending) == 1
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    result = run_named_workflow("article_creation", payload={"connectors": {"substack": True}}, root=tmp_path)

    unresolved = default_effect_log(tmp_path).unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].pipeline == "article_creation"
    assert unresolved[0].action == "publish_substack"
    assert unresolved[0].status == "planned"
    assert unresolved[0].approval_token_id == grant.grant_id
    assert unresolved[0].effect_id in result.record.side_effect_refs


def test_podcast_rss_publish_guard_leaves_mvp_publish_effect_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    blocked = run_named_workflow(
        "podcast_production",
        payload={"connectors": {"rss": True}, "title": "Governed RSS episode"},
        root=tmp_path,
    )
    assert blocked.record.outcome == "approval_required"

    approvals = default_approval_store(tmp_path)
    pending = approvals.list_requests(status="pending")
    assert len(pending) == 1
    assert pending[0].action == "publish_rss_idempotent"
    assert pending[0].scope == "podcast_production"
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    result = run_named_workflow(
        "podcast_production",
        payload={"connectors": {"rss": True}, "title": "Governed RSS episode"},
        root=tmp_path,
    )

    unresolved = default_effect_log(tmp_path).unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].pipeline == "podcast_production"
    assert unresolved[0].action == "publish_rss"
    assert unresolved[0].target == "Governed RSS episode"
    assert unresolved[0].status == "planned"
    assert unresolved[0].approval_token_id == grant.grant_id
    assert unresolved[0].effect_id in result.record.side_effect_refs
    assert "podcast:rss_publish_staged" in result.record.eval_refs


def test_podcast_tts_synthesis_guard_leaves_effect_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"tts": True, "rss": False},
        "title": "Governed TTS episode",
        "script_text": "Approved synthesis script",
    }
    blocked = run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    assert blocked.record.outcome == "approval_required"

    approvals = default_approval_store(tmp_path)
    pending = approvals.list_requests(status="pending")
    assert len(pending) == 1
    assert pending[0].action == "synthesize_tts_idempotent"
    assert pending[0].risk == "external_provider"
    assert pending[0].scope == "podcast_production"
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    result = run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    unresolved = default_effect_log(tmp_path).unresolved()
    preview_path = next(path for path in result.record.artifacts if path.endswith("tts_synthesis_preview.json"))
    preview = json.loads(Path(preview_path).read_text(encoding="utf-8"))

    assert len(unresolved) == 1
    assert unresolved[0].pipeline == "podcast_production"
    assert unresolved[0].action == "synthesize_tts"
    assert unresolved[0].status == "planned"
    assert unresolved[0].approval_token_id == grant.grant_id
    assert preview["script_text"] == "Approved synthesis script"
    assert "podcast:tts_synthesis_staged" in result.record.eval_refs


def test_social_publish_guards_leave_mvp_publish_effects_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    cases = [
        ("social_reactive", "post_reply_idempotent", "reply-123", "social:publish_staged:reply"),
        ("social_proactive", "post_note_idempotent", "note-123", "social:publish_staged:note"),
    ]
    for workflow, approval_action, target, eval_ref in cases:
        root = tmp_path / workflow
        payload = {"connectors": {"social": True}, "target": target}

        blocked = run_named_workflow(workflow, payload=payload, root=root)
        assert blocked.record.outcome == "approval_required"

        approvals = default_approval_store(root)
        pending = approvals.list_requests(status="pending")
        assert len(pending) == 1
        assert pending[0].action == approval_action
        assert pending[0].scope == workflow
        grant = approvals.grant(pending[0].request_id, granted_by="wa")

        result = run_named_workflow(workflow, payload=payload, root=root)

        unresolved = default_effect_log(root).unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].pipeline == workflow
        assert unresolved[0].action == "post_social"
        assert unresolved[0].target == target
        assert unresolved[0].status == "planned"
        assert unresolved[0].approval_token_id == grant.grant_id
        assert unresolved[0].effect_id in result.record.side_effect_refs
        assert eval_ref in result.record.eval_refs


def test_market_and_health_external_write_guards_leave_effects_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    cases = [
        (
            "market_monitor",
            {"connectors": {"market_alert": True}, "target": "portfolio-review-target"},
            "send_market_alert_idempotent",
            "financial_external",
            "send_market_alert",
            "portfolio-review-target",
            "market:alert_staged",
        ),
        (
            "health_wellness",
            {"connectors": {"health_provider": True}, "target": "health-review-target"},
            "write_health_idempotent",
            "health_external",
            "write_health",
            "health-review-target",
            "health:write_staged",
        ),
    ]
    for workflow, payload, approval_action, risk, effect_action, target, eval_ref in cases:
        root = tmp_path / workflow

        blocked = run_named_workflow(workflow, payload=payload, root=root)
        assert blocked.record.outcome == "approval_required"

        approvals = default_approval_store(root)
        pending = approvals.list_requests(status="pending")
        assert len(pending) == 1
        assert pending[0].action == approval_action
        assert pending[0].risk == risk
        assert pending[0].scope == workflow
        grant = approvals.grant(pending[0].request_id, granted_by="wa")

        result = run_named_workflow(workflow, payload=payload, root=root)

        unresolved = default_effect_log(root).unresolved()
        assert len(unresolved) == 1
        assert unresolved[0].pipeline == workflow
        assert unresolved[0].action == effect_action
        assert unresolved[0].target == target
        assert unresolved[0].status == "planned"
        assert unresolved[0].approval_token_id == grant.grant_id
        assert unresolved[0].effect_id in result.record.side_effect_refs
        assert eval_ref in result.record.eval_refs


def test_market_and_health_external_write_guards_degrade_without_connectors(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    for workflow in ("market_monitor", "health_wellness"):
        root = tmp_path / workflow
        result = run_named_workflow(workflow, payload={}, root=root)

        assert result.record.outcome == "completed"
        assert default_approval_store(root).list_requests(status="pending") == []
        assert default_effect_log(root).unresolved() == []


def test_memory_compaction_guard_leaves_destructive_effect_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "compaction_enabled": True,
        "compaction_candidates": ["scar:old_duplicate", "skill:stale_trace"],
        "target": "memory-compaction-batch",
    }
    blocked = run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)
    assert blocked.record.outcome == "approval_required"

    approvals = default_approval_store(tmp_path)
    pending = approvals.list_requests(status="pending")
    assert len(pending) == 1
    assert pending[0].action == "compact_memory_idempotent"
    assert pending[0].risk == "destructive"
    assert pending[0].scope == "memory_maintenance"
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    result = run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)

    unresolved = default_effect_log(tmp_path).unresolved()
    assert len(unresolved) == 1
    assert unresolved[0].pipeline == "memory_maintenance"
    assert unresolved[0].action == "compact_memory"
    assert unresolved[0].target == "memory-compaction-batch"
    assert unresolved[0].status == "planned"
    assert unresolved[0].approval_token_id == grant.grant_id
    assert unresolved[0].effect_id in result.record.side_effect_refs
    assert "memory_maintenance:compaction_staged" in result.record.eval_refs


def test_memory_compaction_guard_defaults_to_review_only(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    result = run_named_workflow("memory_maintenance", payload={}, root=tmp_path)

    assert result.record.outcome == "completed"
    assert default_approval_store(tmp_path).list_requests(status="pending") == []
    assert default_effect_log(tmp_path).unresolved() == []
    assert any(path.endswith("memory_compaction_preview.json") for path in result.record.artifacts)


def test_memory_compaction_preview_recommends_candidates_without_staging(tmp_path: Path):
    from mira.kernel import FailureSignature, MemoryKernel, Scar, SkillTrace
    from mira.runtime import default_approval_store, default_effect_log, default_kernel_store, run_named_workflow

    default_kernel_store(tmp_path).save(
        MemoryKernel(
            scars=[
                Scar(
                    scar_id="scar:superseded_behavior",
                    incident="old duplicate workflow",
                    root_cause="superseded by newer policy",
                    behavioral_change="superseded duplicate behavior should be archived",
                )
            ],
            skill_traces=[
                SkillTrace(
                    skill_name="memory_consolidation",
                    times_used=5,
                    success_rate=1.0,
                    last_outcome="old trace",
                    decay_score=0.1,
                )
            ],
            failure_signatures=[
                FailureSignature(pattern="stale_failure", detection_rule="obsolete detector", occurrences=0)
            ],
        )
    )

    result = run_named_workflow("memory_maintenance", payload={}, root=tmp_path)
    preview_path = next(path for path in result.record.artifacts if path.endswith("memory_compaction_preview.json"))
    preview = json.loads(Path(preview_path).read_text(encoding="utf-8"))

    assert result.record.outcome == "completed"
    assert preview["status"] == "review_only"
    assert preview["candidates"] == []
    assert [item["item_id"] for item in preview["recommended_candidates"]] == [
        "skill:memory_consolidation",
        "failure:stale_failure",
        "scar:superseded_behavior",
    ]
    assert default_approval_store(tmp_path).list_requests(status="pending") == []
    assert default_effect_log(tmp_path).unresolved() == []


def test_memory_compaction_enabled_promotes_recommended_candidates_to_planned_effect(tmp_path: Path):
    from mira.kernel import MemoryKernel, SkillTrace
    from mira.runtime import default_approval_store, default_effect_log, default_kernel_store, run_named_workflow

    default_kernel_store(tmp_path).save(
        MemoryKernel(
            skill_traces=[
                SkillTrace(
                    skill_name="memory_consolidation",
                    times_used=5,
                    success_rate=1.0,
                    last_outcome="old trace",
                    decay_score=0.1,
                )
            ]
        )
    )
    payload = {"compaction_enabled": True, "target": "auto-compaction"}
    blocked = run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")

    result = run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)
    preview_path = next(path for path in result.record.artifacts if path.endswith("memory_compaction_preview.json"))
    preview = json.loads(Path(preview_path).read_text(encoding="utf-8"))
    effect = default_effect_log(tmp_path).unresolved()[0]

    assert blocked.record.outcome == "approval_required"
    assert preview["candidates"] == ["skill:memory_consolidation"]
    assert preview["recommended_candidates"][0]["item_id"] == "skill:memory_consolidation"
    assert effect.action == "compact_memory"
    assert effect.target == "auto-compaction"


def test_memory_compaction_adapter_archives_approved_candidates(tmp_path: Path):
    from mira.kernel import MemoryKernel, Scar, SkillTrace
    from mira.runtime import default_approval_store, default_effect_log, default_kernel_store, run_named_workflow

    kernel = MemoryKernel(
        scars=[
            Scar(
                scar_id="scar:old_duplicate",
                incident="old duplicate behavior",
                root_cause="superseded by newer policy",
                behavioral_change="old duplicate behavior should be archived",
            )
        ],
        skill_traces=[
            SkillTrace(
                skill_name="memory_consolidation",
                times_used=2,
                success_rate=1.0,
                last_outcome="stale trace ready for archive",
            )
        ],
    )
    default_kernel_store(tmp_path).save(kernel)
    payload = {
        "compaction_enabled": True,
        "compaction_candidates": ["scar:old_duplicate", "skill:memory_consolidation"],
        "target": "memory-compaction-batch",
    }
    blocked = run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")

    run_named_workflow("memory_maintenance", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    succeeded = run_memory_compaction_adapter(root=tmp_path, idempotency_key=effect.idempotency_key)
    compacted = default_kernel_store(tmp_path).load()

    assert blocked.record.outcome == "approval_required"
    assert succeeded.status == "succeeded"
    assert succeeded.external_ref == f"kernel_archive:{effect.run_id}:2"
    assert all(scar.scar_id != "scar:old_duplicate" for scar in compacted.scars)
    assert all(trace.skill_name != "memory_consolidation" for trace in compacted.skill_traces)
    assert [item.item_id for item in compacted.archived_memories] == [
        "scar:old_duplicate",
        "skill:memory_consolidation",
    ]
    assert compacted.archived_memories[0].effect_id == effect.effect_id


def test_memory_compaction_adapter_requires_approval_metadata(tmp_path: Path):
    from mira.runtime import default_effect_log

    default_effect_log(tmp_path).plan(
        idempotency_key="memory_maintenance:compact_memory_idempotent:unsafe",
        run_id="run_1",
        pipeline="memory_maintenance",
        action="compact_memory",
        target="unsafe",
        preview_hash="",
        approval_token_id=None,
    )

    try:
        run_memory_compaction_adapter(
            root=tmp_path,
            idempotency_key="memory_maintenance:compact_memory_idempotent:unsafe",
        )
    except ValueError as exc:
        assert "approval token and preview hash" in str(exc)
    else:
        raise AssertionError("memory compaction adapter should require approval metadata")


def test_self_evolution_production_promotion_guard_leaves_effect_planned(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "production_promotion_enabled": True,
        "repo_path": str(tmp_path / "repo"),
        "production_branch": "main",
        "canary_branch": "codex/self-evolution-test",
        "target": "production-main",
    }
    blocked = run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    assert blocked.record.outcome == "approval_required"

    approvals = default_approval_store(tmp_path)
    pending = approvals.list_requests(status="pending")
    assert len(pending) == 1
    assert pending[0].action == "promote_production_idempotent"
    assert pending[0].risk == "code_config"
    assert pending[0].scope == "self_evolution"
    grant = approvals.grant(pending[0].request_id, granted_by="wa")

    result = run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    preview_path = next(
        path for path in result.record.artifacts if path.endswith("self_evolution_production_promotion_preview.json")
    )
    preview = json.loads(Path(preview_path).read_text(encoding="utf-8"))
    effect = default_effect_log(tmp_path).unresolved()[0]

    assert preview["status"] == "staged"
    assert preview["canary_branch"] == "codex/self-evolution-test"
    assert effect.pipeline == "self_evolution"
    assert effect.action == "promote_production"
    assert effect.target == "production-main"
    assert effect.status == "planned"
    assert effect.approval_token_id == grant.grant_id
    assert "self_evolution:production_promotion_staged" in result.record.eval_refs
    assert effect.effect_id in result.record.side_effect_refs


def test_self_evolution_auto_promotion_stages_after_confirmed_canary_window(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    for _ in range(3):
        run_named_workflow("self_evolution", payload={"canary_min_n": 3}, root=tmp_path)

    payload = {
        "auto_promotion_enabled": True,
        "auto_promotion_min_n": 3,
        "repo_path": str(tmp_path / "repo"),
        "production_branch": "main",
        "canary_branch": "codex/self-evolution-auto",
        "target": "production-main",
    }
    blocked = run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    pending = default_approval_store(tmp_path).list_requests(status="pending")
    assert blocked.record.outcome == "approval_required"
    assert len(pending) == 1
    assert pending[0].action == "promote_production_idempotent"

    default_approval_store(tmp_path).grant(pending[0].request_id, granted_by="wa")
    result = run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    preview_path = next(
        path for path in result.record.artifacts if path.endswith("self_evolution_production_promotion_preview.json")
    )
    preview = json.loads(Path(preview_path).read_text(encoding="utf-8"))
    effect = default_effect_log(tmp_path).unresolved()[0]

    assert preview["status"] == "staged"
    assert preview["promotion_mode"] == "automatic"
    assert preview["auto_promotion_eligible"] is True
    assert preview["auto_promotion_status"] == "confirmed"
    assert preview["auto_promotion_observed_n"] >= 3
    assert effect.status == "planned"
    assert effect.action == "promote_production"


def test_self_evolution_production_adapter_fast_forwards_and_can_roll_back(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "mira@example.test")
    _git(repo, "config", "user.name", "Mira Test")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    production_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    rollback_ref = _git(repo, "rev-parse", production_branch)
    _git(repo, "checkout", "-b", "codex/self-evolution-test")
    (repo / "README.md").write_text("initial\ncanary\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "canary")
    canary_sha = _git(repo, "rev-parse", "codex/self-evolution-test")
    _git(repo, "checkout", production_branch)

    payload = {
        "production_promotion_enabled": True,
        "rollback_after_promotion": True,
        "repo_path": str(repo),
        "production_branch": production_branch,
        "canary_branch": "codex/self-evolution-test",
        "target": "production-main",
    }
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]

    succeeded = run_self_evolution_production_adapter(root=tmp_path, idempotency_key=effect.idempotency_key)
    result_path = (
        default_v3_paths(tmp_path).artifacts
        / "self_evolution"
        / effect.run_id
        / "self_evolution_production_promotion_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert succeeded.status == "succeeded"
    assert succeeded.external_ref == f"git_promotion_rolled_back:{production_branch}:{rollback_ref}"
    assert result["promoted_sha"] == canary_sha
    assert result["rollback_ref"] == rollback_ref
    assert result["rollback_executed"] is True
    assert _git(repo, "rev-parse", production_branch) == rollback_ref


def test_self_evolution_production_adapter_pushes_remote_and_remote_rollback(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "mira@example.test")
    _git(repo, "config", "user.name", "Mira Test")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    production_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    rollback_ref = _git(repo, "rev-parse", production_branch)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", f"{production_branch}:refs/heads/{production_branch}")
    _git(repo, "checkout", "-b", "codex/self-evolution-test")
    (repo / "README.md").write_text("initial\ncanary\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "canary")
    canary_sha = _git(repo, "rev-parse", "codex/self-evolution-test")
    _git(repo, "checkout", production_branch)

    payload = {
        "production_promotion_enabled": True,
        "rollback_after_promotion": True,
        "remote_promotion_enabled": True,
        "remote_name": "origin",
        "remote_branch": production_branch,
        "repo_path": str(repo),
        "production_branch": production_branch,
        "canary_branch": "codex/self-evolution-test",
        "target": "production-main",
    }
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]

    succeeded = run_self_evolution_production_adapter(root=tmp_path, idempotency_key=effect.idempotency_key)
    result_path = (
        default_v3_paths(tmp_path).artifacts
        / "self_evolution"
        / effect.run_id
        / "self_evolution_production_promotion_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert succeeded.status == "succeeded"
    assert result["remote_pushed"] is True
    assert result["remote_rollback_pushed"] is True
    assert result["remote_ref_before"] == rollback_ref
    assert result["remote_ref_after"] == canary_sha
    assert result["remote_rollback_ref_after"] == rollback_ref
    assert _git(remote, "rev-parse", f"refs/heads/{production_branch}") == rollback_ref


def test_self_evolution_production_adapter_calls_deployment_service(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "mira@example.test")
    _git(repo, "config", "user.name", "Mira Test")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    production_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    rollback_ref = _git(repo, "rev-parse", production_branch)
    _git(repo, "checkout", "-b", "codex/self-evolution-deploy")
    (repo / "README.md").write_text("initial\ndeploy\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "deploy")
    canary_sha = _git(repo, "rev-parse", "codex/self-evolution-deploy")
    _git(repo, "checkout", production_branch)

    payload = {
        "production_promotion_enabled": True,
        "deployment_service_enabled": True,
        "deployment_health_check_enabled": True,
        "repo_path": str(repo),
        "production_branch": production_branch,
        "canary_branch": "codex/self-evolution-deploy",
        "target": "production-main",
    }
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    config_path = write_provider_adapter_config_template(
        tmp_path / "provider_adapters.json",
        providers=("deployment", "deployment_health"),
    )
    monkeypatch.setenv("MIRA_DEPLOYMENT_ADAPTER_ENDPOINT", "https://deploy.example/releases/{target}")
    monkeypatch.setenv("MIRA_DEPLOYMENT_ADAPTER_TOKEN", "deploy-token")
    monkeypatch.setenv("MIRA_DEPLOYMENT_HEALTH_ADAPTER_ENDPOINT", "https://deploy.example/health/{effect_id}")
    monkeypatch.setenv("MIRA_DEPLOYMENT_HEALTH_ADAPTER_TOKEN", "health-token")

    def deploy_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer deploy-token"
        assert body["action"] == "promote_production"
        assert body["target"] == f"{production_branch}:{canary_sha}"
        assert body["preview"]["deployment_service_enabled"] is True
        assert body["preview"]["deployment_health_check_enabled"] is True
        assert body["preview"]["production_branch"] == production_branch
        assert body["preview"]["canary_branch"] == "codex/self-evolution-deploy"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "deployed",
                    "provider_id": "deploy_123",
                    "url": "https://deploy.example/releases/deploy_123",
                }
            },
        )

    def health_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer health-token"
        assert body["target"] == "https://deploy.example/releases/deploy_123"
        assert body["external_ref"] == "https://deploy.example/releases/deploy_123"
        assert body["preview"]["deployment_health_check_enabled"] is True
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "healthy",
                    "provider_id": "health_123",
                    "url": "https://deploy.example/health/health_123",
                }
            },
        )

    deploy_client = httpx.Client(transport=httpx.MockTransport(deploy_handler))
    health_client = httpx.Client(transport=httpx.MockTransport(health_handler))
    try:
        succeeded = run_self_evolution_production_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=config_path,
            provider_http_clients={"deployment": deploy_client, "deployment_health": health_client},
        )
    finally:
        deploy_client.close()
        health_client.close()
    result_path = (
        default_v3_paths(tmp_path).artifacts
        / "self_evolution"
        / effect.run_id
        / "self_evolution_production_promotion_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert succeeded.status == "succeeded"
    assert "deployment:https://deploy.example/releases/deploy_123" in succeeded.external_ref
    assert "health:https://deploy.example/health/health_123" in succeeded.external_ref
    assert result["rollback_ref"] == rollback_ref
    assert result["deployment"]["status"] == "succeeded"
    assert result["deployment"]["external_ref"] == "https://deploy.example/releases/deploy_123"
    assert result["deployment_health"]["status"] == "succeeded"
    assert result["deployment_health"]["external_ref"] == "https://deploy.example/health/health_123"
    assert _git(repo, "rev-parse", production_branch) == canary_sha


def test_self_evolution_production_adapter_runs_local_deployment_commands(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "mira@example.test")
    _git(repo, "config", "user.name", "Mira Test")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    production_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-b", "codex/self-evolution-local-deploy")
    (repo / "README.md").write_text("initial\nlocal deploy\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "local deploy")
    canary_sha = _git(repo, "rev-parse", "codex/self-evolution-local-deploy")
    _git(repo, "checkout", production_branch)

    command_script = tmp_path / "local_deploy_command.py"
    command_script.write_text(
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "input_path = Path(sys.argv[1])\n"
        "result_path = Path(sys.argv[2])\n"
        "payload = json.loads(input_path.read_text(encoding='utf-8'))\n"
        "role = payload['role']\n"
        "statuses = {'deployment': 'deployed', 'deployment_health': 'healthy'}\n"
        "refs = {\n"
        "    'deployment': 'local-deploy://' + payload['target'],\n"
        "    'deployment_health': 'local-health://' + payload['target'],\n"
        "}\n"
        "result_path.write_text(json.dumps({\n"
        "    'status': statuses[role],\n"
        "    'provider_id': role + '_123',\n"
        "    'external_ref': refs[role],\n"
        "    'detail': role + ' command ok',\n"
        "}) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    payload = {
        "production_promotion_enabled": True,
        "deployment_service_enabled": True,
        "deployment_health_check_enabled": True,
        "repo_path": str(repo),
        "production_branch": production_branch,
        "canary_branch": "codex/self-evolution-local-deploy",
        "target": "production-main",
    }
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "deployment": {
                        "type": "local_deployment_command",
                        "command": [sys.executable, str(command_script), "{input_json_path}", "{result_json_path}"],
                    },
                    "deployment_health": {
                        "type": "local_deployment_command",
                        "command": [sys.executable, str(command_script), "{input_json_path}", "{result_json_path}"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    succeeded = run_self_evolution_production_adapter(
        root=tmp_path,
        idempotency_key=effect.idempotency_key,
        provider_config_path=adapter_config,
    )
    result_path = (
        default_v3_paths(tmp_path).artifacts
        / "self_evolution"
        / effect.run_id
        / "self_evolution_production_promotion_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert succeeded.status == "succeeded"
    assert result["promoted_sha"] == canary_sha
    assert result["deployment"]["status"] == "succeeded"
    assert result["deployment"]["external_ref"] == f"local-deploy://{production_branch}:{canary_sha}"
    assert result["deployment_health"]["status"] == "succeeded"
    assert (
        result["deployment_health"]["external_ref"] == f"local-health://local-deploy://{production_branch}:{canary_sha}"
    )


def test_self_evolution_production_adapter_keeps_effect_unknown_when_deployment_health_fails(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "mira@example.test")
    _git(repo, "config", "user.name", "Mira Test")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    production_branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-b", "codex/self-evolution-unhealthy")
    (repo / "README.md").write_text("initial\nunhealthy\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "unhealthy")
    _git(repo, "checkout", production_branch)

    payload = {
        "production_promotion_enabled": True,
        "deployment_service_enabled": True,
        "deployment_health_check_enabled": True,
        "deployment_rollback_enabled": True,
        "repo_path": str(repo),
        "production_branch": production_branch,
        "canary_branch": "codex/self-evolution-unhealthy",
        "target": "production-main",
    }
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("self_evolution", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]

    executed = run_self_evolution_production_adapter(
        root=tmp_path,
        idempotency_key=effect.idempotency_key,
        provider_adapters={
            "deployment": lambda entry: {
                "status": "deployed",
                "provider_id": "deploy_unhealthy",
                "url": "https://deploy.example/releases/unhealthy",
            },
            "deployment_health": lambda entry: {
                "status": "unhealthy",
                "provider_id": "health_unhealthy",
                "message": "post-deployment health check failed",
            },
            "deployment_rollback": lambda entry: {
                "status": "rolled_back",
                "provider_id": "rollback_unhealthy",
                "external_ref": f"rollback:{entry.external_ref}",
                "detail": "deployment rollback completed",
            },
        },
    )
    result_path = (
        default_v3_paths(tmp_path).artifacts
        / "self_evolution"
        / effect.run_id
        / "self_evolution_production_promotion_result.json"
    )
    result = json.loads(result_path.read_text(encoding="utf-8"))

    assert executed.status == "unknown"
    assert "deployment health failed" in executed.detail
    assert "deployment rollback succeeded" in executed.detail
    assert result["deployment"]["status"] == "succeeded"
    assert result["deployment_health"]["status"] == "failed"
    assert result["deployment_rollback"]["status"] == "succeeded"
    assert result["deployment_rollback"]["external_ref"] == "rollback:https://deploy.example/releases/unhealthy"


def test_provider_effect_adapter_executes_approved_live_effect(tmp_path: Path):
    from mira.runtime import default_effect_log

    effects = default_effect_log(tmp_path)
    planned = effects.plan(
        idempotency_key="social_reactive:post_reply_idempotent:reply-123",
        run_id="run_social",
        pipeline="social_reactive",
        action="post_social",
        target="reply-123",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )

    executed = run_provider_effect_adapter(
        root=tmp_path,
        idempotency_key=planned.idempotency_key,
        provider_adapters={
            "social": lambda effect: {
                "status": "posted",
                "provider_id": "post_123",
                "url": f"https://social.example/posts/{effect.target}",
            }
        },
    )

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://social.example/posts/reply-123"
    assert default_effect_log(tmp_path).get_by_idempotency_key(planned.idempotency_key).status == "succeeded"


def test_provider_effect_adapter_requires_approval_metadata(tmp_path: Path):
    from mira.runtime import default_effect_log

    planned = default_effect_log(tmp_path).plan(
        idempotency_key="podcast_production:publish_rss_idempotent:episode-1",
        run_id="run_podcast",
        pipeline="podcast_production",
        action="publish_rss",
        target="episode-1",
    )

    try:
        run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=planned.idempotency_key,
            provider_adapters={"rss": lambda effect: {"status": "published"}},
        )
    except ValueError as exc:
        assert "approval token and preview hash" in str(exc)
    else:
        raise AssertionError("provider effect adapter should require approval metadata")


def test_local_rss_provider_adapter_publishes_approved_podcast_preview(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"rss": True},
        "title": "Governed RSS episode",
        "episode_id": "episode-1",
        "description": "A governed RSS publication test",
        "audio_url": "https://podcast.example/episode-1.mp3",
        "episode_url": "https://podcast.example/episode-1",
    }
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    feed_path = tmp_path / "public" / "podcast.xml"
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "rss": {
                        "type": "local_rss_feed",
                        "feed_path": str(feed_path),
                        "channel_title": "Mira Test Podcast",
                        "channel_link": "https://podcast.example",
                        "channel_description": "Test feed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    executed = run_provider_effect_adapter(
        root=tmp_path,
        idempotency_key=effect.idempotency_key,
        provider_config_path=adapter_config,
    )
    tree = ET.parse(feed_path)
    item = tree.getroot().find("./channel/item")

    assert executed.status == "succeeded"
    assert executed.external_ref == f"rss_feed:{feed_path}:episode-1"
    assert item is not None
    assert item.findtext("guid") == "episode-1"
    assert item.findtext("title") == "Governed RSS episode"
    assert item.findtext("description") == "A governed RSS publication test"
    assert item.find("enclosure").attrib["url"] == "https://podcast.example/episode-1.mp3"


def test_hosted_rss_http_adapter_publishes_approved_podcast_preview(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"rss": True},
        "title": "Hosted RSS episode",
        "episode_id": "hosted-episode-1",
        "description": "A hosted RSS publication test",
        "audio_url": "https://podcast.example/hosted-episode-1.mp3",
        "episode_url": "https://podcast.example/hosted-episode-1",
    }
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "rss": {
                        "type": "hosted_rss_http",
                        "endpoint_template_env": "MIRA_TEST_RSS_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_RSS_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_TEST_RSS_ENDPOINT", "https://rss.example/publish/{target}")
    monkeypatch.setenv("MIRA_TEST_RSS_TOKEN", "rss-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer rss-token"
        assert body["action"] == "publish_rss"
        assert body["episode_id"] == "hosted-episode-1"
        assert body["title"] == "Hosted RSS episode"
        assert body["audio_url"] == "https://podcast.example/hosted-episode-1.mp3"
        assert body["preview"]["description"] == "A hosted RSS publication test"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "published",
                    "provider_id": "rss_123",
                    "feed_url": "https://rss.example/feed.xml",
                    "episode_url": "https://rss.example/episodes/hosted-episode-1",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=adapter_config,
            provider_http_clients={"rss": client},
        )
    finally:
        client.close()
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "podcast_production" / effect.run_id / "rss_publish_result.json"
    )
    manifest = json.loads(result_manifest.read_text(encoding="utf-8"))

    assert executed.status == "succeeded"
    assert executed.external_ref == "https://rss.example/episodes/hosted-episode-1"
    assert manifest["provider_id"] == "rss_123"
    assert manifest["feed_url"] == "https://rss.example/feed.xml"
    assert manifest["episode_url"] == "https://rss.example/episodes/hosted-episode-1"


def test_local_tts_command_adapter_executes_approved_preview(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    tts_script = tmp_path / "tts_stub.py"
    tts_script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "text = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "Path(sys.argv[2]).write_bytes(b'FAKE-WAV:' + text.encode('utf-8'))\n",
        encoding="utf-8",
    )
    payload = {
        "connectors": {"tts": True, "rss": False},
        "title": "Local TTS episode",
        "script_text": "Local command synthesis script",
        "audio_output_name": "local-tts.wav",
    }
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "tts": {
                        "type": "local_tts_command",
                        "command": [sys.executable, str(tts_script), "{input_text_path}", "{output_audio_path}"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    executed = run_provider_effect_adapter(
        root=tmp_path,
        idempotency_key=effect.idempotency_key,
        provider_config_path=adapter_config,
    )
    output_audio = default_v3_paths(tmp_path).artifacts / "podcast_production" / effect.run_id / "local-tts.wav"
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "podcast_production" / effect.run_id / "tts_synthesis_result.json"
    )

    assert executed.status == "succeeded"
    assert executed.external_ref == str(output_audio)
    assert output_audio.read_bytes().startswith(b"FAKE-WAV:Local command synthesis script")
    assert json.loads(result_manifest.read_text(encoding="utf-8"))["output_audio_path"] == str(output_audio)


def test_hosted_tts_http_adapter_executes_approved_preview(tmp_path: Path, monkeypatch):
    from mira.runtime import default_approval_store, default_effect_log, run_named_workflow

    payload = {
        "connectors": {"tts": True, "rss": False},
        "title": "Hosted TTS episode",
        "script_text": "Hosted synthesis script",
        "voice": "mira-test",
        "audio_output_name": "hosted-tts.wav",
    }
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("podcast_production", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "tts": {
                        "type": "hosted_tts_http",
                        "endpoint_template_env": "MIRA_TEST_TTS_ENDPOINT",
                        "bearer_token_env": "MIRA_TEST_TTS_TOKEN",
                        "payload_path": ["data"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MIRA_TEST_TTS_ENDPOINT", "https://tts.example/synthesize/{target}")
    monkeypatch.setenv("MIRA_TEST_TTS_TOKEN", "tts-token")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer tts-token"
        assert body["action"] == "synthesize_tts"
        assert body["script_text"] == "Hosted synthesis script"
        assert body["voice"] == "mira-test"
        assert body["preview"]["audio_output_name"] == "hosted-tts.wav"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "synthesized",
                    "provider_id": "tts_123",
                    "audio_base64": base64.b64encode(b"HOSTED-WAV:Hosted synthesis script").decode("ascii"),
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        executed = run_provider_effect_adapter(
            root=tmp_path,
            idempotency_key=effect.idempotency_key,
            provider_config_path=adapter_config,
            provider_http_clients={"tts": client},
        )
    finally:
        client.close()
    output_audio = default_v3_paths(tmp_path).artifacts / "podcast_production" / effect.run_id / "hosted-tts.wav"
    result_manifest = (
        default_v3_paths(tmp_path).artifacts / "podcast_production" / effect.run_id / "tts_synthesis_result.json"
    )
    manifest = json.loads(result_manifest.read_text(encoding="utf-8"))

    assert executed.status == "succeeded"
    assert executed.external_ref == str(output_audio)
    assert output_audio.read_bytes() == b"HOSTED-WAV:Hosted synthesis script"
    assert manifest["provider_id"] == "tts_123"
    assert manifest["output_audio_path"] == str(output_audio)


def test_local_provider_state_adapter_executes_market_and_reconciles_manifest(tmp_path: Path):
    from mira.runtime import default_approval_store, default_effect_log, reconcile_provider_effects, run_named_workflow

    payload = {
        "connectors": {"market_alert": True},
        "target": "portfolio-review-target",
        "message": "Risk exposure changed",
        "severity": "high",
    }
    run_named_workflow("market_monitor", payload=payload, root=tmp_path)
    request = default_approval_store(tmp_path).list_requests(status="pending")[0]
    default_approval_store(tmp_path).grant(request.request_id, granted_by="wa")
    run_named_workflow("market_monitor", payload=payload, root=tmp_path)
    effect = default_effect_log(tmp_path).unresolved()[0]
    manifest_path = tmp_path / "provider_state" / "market.json"
    adapter_config = tmp_path / "provider_adapters.json"
    adapter_config.write_text(
        json.dumps(
            {
                "provider_effect_adapters": {
                    "market": {
                        "type": "local_provider_state",
                        "provider": "market",
                        "manifest_path": str(manifest_path),
                        "preview_filename": "market_alert_preview.json",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    executed = run_provider_effect_adapter(
        root=tmp_path,
        idempotency_key=effect.idempotency_key,
        provider_config_path=adapter_config,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = manifest["market_alerts"]["portfolio-review-target"]

    assert executed.status == "succeeded"
    assert executed.external_ref == "local_provider_state:market:portfolio-review-target"
    assert payload["status"] == "sent"
    assert payload["preview"]["message"] == "Risk exposure changed"

    second_root = tmp_path / "reconcile"
    second_effects = default_effect_log(second_root)
    second_effects.plan(
        idempotency_key=effect.idempotency_key,
        run_id=effect.run_id,
        pipeline=effect.pipeline,
        action=effect.action,
        target=effect.target,
        preview_hash=effect.preview_hash,
        approval_token_id=effect.approval_token_id,
    )
    reconciled = reconcile_provider_effects(root=second_root, provider_state_manifest_paths=[manifest_path])
    latest = second_effects.get_by_idempotency_key(effect.idempotency_key)

    assert len(reconciled) == 1
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "local_provider_state:market:portfolio-review-target"


def test_new_representative_workflows_use_runtime_audit_and_artifacts(tmp_path: Path):
    from mira.runtime import default_commit_log, run_named_workflow

    for name in (
        "podcast_production",
        "book_reading_notes",
        "social_reactive",
        "social_proactive",
        "weekly_growth_report",
        "research_deep_dive",
        "daily_thought_discussion",
        "daily_journal",
        "weekly_reflection",
        "market_monitor",
        "incident_response",
        "health_wellness",
        "self_evolution",
        "skill_learning",
        "memory_maintenance",
        "deterministic_reference",
    ):
        result = run_named_workflow(name, payload={}, root=tmp_path)
        assert result.record.pipeline == name
        assert result.record.artifacts
        if name == "self_evolution":
            assert any(path.endswith("self_evolution_canary.md") for path in result.record.artifacts)
            assert any(path.endswith("self_evolution_experiment.md") for path in result.record.artifacts)
            assert "self_evolution:canary_rollback" in result.record.eval_refs
            assert "self_evolution:experiment_record" in result.record.eval_refs
        audit_artifacts = list(default_v3_paths(tmp_path).workflow_audits.glob(f"{name}-*.json"))
        assert len(audit_artifacts) == 1

    commits = default_commit_log(tmp_path).list()
    assert [commit.pipeline for commit in commits] == [
        "podcast_production",
        "book_reading_notes",
        "social_reactive",
        "social_proactive",
        "weekly_growth_report",
        "research_deep_dive",
        "daily_thought_discussion",
        "daily_journal",
        "weekly_reflection",
        "market_monitor",
        "incident_response",
        "health_wellness",
        "self_evolution",
        "skill_learning",
        "memory_maintenance",
        "deterministic_reference",
    ]
    assert {commit.status for commit in commits}.issubset({"applied", "noop"})
    assert [commit.status for commit in commits if commit.pipeline == "self_evolution"] == ["applied"]


def test_self_evolution_canary_confirms_after_n_run_observation_window(tmp_path: Path):
    from mira.runtime import run_named_workflow

    for _ in range(3):
        observing = run_named_workflow("self_evolution", payload={"canary_min_n": 3}, root=tmp_path)
        canary = next(path for path in observing.record.artifacts if path.endswith("self_evolution_canary.md"))
        assert "Observation status: observing." in Path(canary).read_text(encoding="utf-8")

    confirmed = run_named_workflow("self_evolution", payload={"canary_min_n": 3}, root=tmp_path)
    canary = next(path for path in confirmed.record.artifacts if path.endswith("self_evolution_canary.md"))
    body = Path(canary).read_text(encoding="utf-8")

    assert "Observed prior self_evolution runs: 3/3." in body
    assert "Observation status: confirmed." in body
    assert "self_evolution:canary_observation:confirmed" in confirmed.record.eval_refs


def test_representative_workflows_emit_l3_or_l4_causal_evidence_on_second_run(tmp_path: Path):
    from mira.runtime import run_named_workflow

    run_named_workflow("system_health", payload={}, root=tmp_path)
    second_health = run_named_workflow("system_health", payload={}, root=tmp_path)
    run_named_workflow("intelligence_briefing", payload={}, root=tmp_path)
    second_briefing = run_named_workflow("intelligence_briefing", payload={}, root=tmp_path)
    run_named_workflow("article_creation", payload={"connectors": {"substack": False, "twitter": False}}, root=tmp_path)
    second_article = run_named_workflow(
        "article_creation",
        payload={"connectors": {"substack": False, "twitter": False}},
        root=tmp_path,
    )
    run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )
    second_a2a = run_named_workflow(
        "a2a_trust_experiment",
        payload={"connectors": {"local_files": True}},
        root=tmp_path,
    )

    evidence = default_causal_evidence_log(tmp_path).list()
    assert len(evidence) == 4
    assert {item.pipeline for item in evidence} == {
        "system_health",
        "intelligence_briefing",
        "article_creation",
        "a2a_trust_experiment",
    }
    assert {item.level for item in evidence if item.pipeline == "system_health"} == {"L4"}
    assert {item.level for item in evidence if item.pipeline == "intelligence_briefing"} == {"L4"}
    assert {item.level for item in evidence if item.pipeline == "article_creation"} == {"L4"}
    assert {item.level for item in evidence if item.pipeline == "a2a_trust_experiment"} == {"L4"}
    assert all(item.ablation_ref for item in evidence if item.pipeline == "system_health")
    assert all(item.ablation_ref for item in evidence if item.pipeline == "intelligence_briefing")
    assert all(item.ablation_ref for item in evidence if item.pipeline == "article_creation")
    assert all(item.ablation_ref for item in evidence if item.pipeline == "a2a_trust_experiment")
    assert second_health.record.causal_links == [
        item.evidence_id for item in evidence if item.pipeline == "system_health"
    ]
    assert second_briefing.record.causal_links == [
        item.evidence_id for item in evidence if item.pipeline == "intelligence_briefing"
    ]
    assert second_article.record.causal_links == [
        item.evidence_id for item in evidence if item.pipeline == "article_creation"
    ]
    assert second_a2a.record.causal_links == [
        item.evidence_id for item in evidence if item.pipeline == "a2a_trust_experiment"
    ]


def test_new_representative_workflows_emit_l3_or_l4_causal_evidence_on_second_run(tmp_path: Path):
    from mira.runtime import run_named_workflow

    for name in (
        "podcast_production",
        "book_reading_notes",
        "social_reactive",
        "social_proactive",
        "weekly_growth_report",
        "research_deep_dive",
        "daily_thought_discussion",
        "daily_journal",
        "weekly_reflection",
        "market_monitor",
        "incident_response",
        "health_wellness",
        "self_evolution",
        "skill_learning",
        "memory_maintenance",
        "deterministic_reference",
    ):
        run_named_workflow(name, payload={}, root=tmp_path)
        second = run_named_workflow(name, payload={}, root=tmp_path)
        assert second.record.causal_links

    evidence = default_causal_evidence_log(tmp_path).list()
    assert {item.pipeline for item in evidence} == {
        "podcast_production",
        "book_reading_notes",
        "social_reactive",
        "social_proactive",
        "weekly_growth_report",
        "research_deep_dive",
        "daily_thought_discussion",
        "daily_journal",
        "weekly_reflection",
        "market_monitor",
        "incident_response",
        "health_wellness",
        "self_evolution",
        "skill_learning",
        "memory_maintenance",
        "deterministic_reference",
    }
    assert {item.level for item in evidence} == {"L4"}
    assert all(item.ablation_ref for item in evidence)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
