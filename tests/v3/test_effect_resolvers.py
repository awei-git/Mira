import json
from pathlib import Path

import httpx

from mira.engine.effect_log import EffectLog
from mira.engine.effect_resolvers import HttpJsonProviderResolver, reconcile_effects_from_provider_state


def _unknown_effect(log: EffectLog, *, action: str, target: str):
    log.plan(
        idempotency_key=f"effect:{target}",
        run_id="run_1",
        pipeline="article_creation",
        action=action,
        target=target,
    )
    return log.mark_unknown(f"effect:{target}", "process died after provider call")


def _planned_effect(log: EffectLog, *, action: str, target: str, pipeline: str = "article_creation"):
    return log.plan(
        idempotency_key=f"effect:{target}",
        run_id="run_1",
        pipeline=pipeline,
        action=action,
        target=target,
    )


def test_substack_resolver_reconciles_from_publish_manifest(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="test-article")
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

    reconciled = reconcile_effects_from_provider_state(effects, publish_manifest_path=manifest)
    latest = effects.get_by_idempotency_key("effect:test-article")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://test.substack.com/p/test-article"
    assert latest.reconciliation_ref == f"publish_manifest:{manifest}:test-article"


def test_substack_resolver_reconciles_manifest_error_as_failed(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="broken-article")
    manifest = tmp_path / "publish_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "articles": {
                    "broken-article": {
                        "slug": "broken-article",
                        "status": "approved",
                        "error": "Substack API returned 504",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    reconcile_effects_from_provider_state(effects, publish_manifest_path=manifest)
    latest = effects.get_by_idempotency_key("effect:broken-article")

    assert latest is not None
    assert latest.status == "reconciled_failed"
    assert latest.reconciliation_ref == f"publish_manifest:{manifest}:broken-article:error"


def test_resolver_leaves_effect_unresolved_without_provider_evidence(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    unknown = _unknown_effect(effects, action="publish_substack", target="missing-article")
    manifest = tmp_path / "publish_manifest.json"
    manifest.write_text(json.dumps({"articles": {}}), encoding="utf-8")

    reconciled = reconcile_effects_from_provider_state(effects, publish_manifest_path=manifest)

    assert reconciled == []
    assert effects.get_by_idempotency_key("effect:missing-article") == unknown


def test_rss_resolver_reconciles_from_feed_guid(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_rss", target="episode-1")
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """
<rss><channel>
  <item>
    <title>Episode One</title>
    <guid>episode-1</guid>
    <enclosure url="https://podcast.example/episode-1.mp3" />
  </item>
</channel></rss>
""".strip(),
        encoding="utf-8",
    )

    reconcile_effects_from_provider_state(effects, rss_feed_paths=[feed])
    latest = effects.get_by_idempotency_key("effect:episode-1")

    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://podcast.example/episode-1.mp3"
    assert latest.reconciliation_ref == f"rss_feed:{feed}:episode-1"


def test_rss_resolver_reconciles_planned_staged_effect_from_feed_guid(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _planned_effect(effects, action="publish_rss", target="episode-planned", pipeline="podcast_production")
    feed = tmp_path / "feed.xml"
    feed.write_text(
        """
<rss><channel>
  <item>
    <title>Episode Planned</title>
    <guid>episode-planned</guid>
    <enclosure url="https://podcast.example/episode-planned.mp3" />
  </item>
</channel></rss>
""".strip(),
        encoding="utf-8",
    )

    reconciled = reconcile_effects_from_provider_state(effects, rss_feed_paths=[feed])
    latest = effects.get_by_idempotency_key("effect:episode-planned")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://podcast.example/episode-planned.mp3"
    assert latest.reconciliation_ref == f"rss_feed:{feed}:episode-planned"


def test_connector_resolver_reconciles_provider_success(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="connector-article")

    reconciled = reconcile_effects_from_provider_state(
        effects,
        provider_resolvers={
            "substack": lambda entry: {
                "status": "published",
                "provider_id": "post_123",
                "url": f"https://test.substack.com/p/{entry.target}",
                "detail": "substack connector found published post",
            }
        },
    )
    latest = effects.get_by_idempotency_key("effect:connector-article")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://test.substack.com/p/connector-article"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:post_123"


def test_connector_resolver_reconciles_planned_social_effect_from_provider_success(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _planned_effect(effects, action="post_social", target="note-123", pipeline="social_proactive")

    reconciled = reconcile_effects_from_provider_state(
        effects,
        provider_resolvers={
            "social": lambda entry: {
                "status": "published",
                "provider_id": "note_provider_123",
                "url": f"https://social.example/notes/{entry.target}",
                "detail": "social connector found published note",
            }
        },
    )
    latest = effects.get_by_idempotency_key("effect:note-123")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://social.example/notes/note-123"
    assert latest.reconciliation_ref == "provider:social:post_social:note_provider_123"


def test_provider_state_manifest_reconciles_social_market_and_health_effects(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _planned_effect(effects, action="post_social", target="note-123", pipeline="social_proactive")
    _planned_effect(effects, action="send_market_alert", target="alert-123", pipeline="market_monitor")
    _planned_effect(effects, action="write_health", target="health-123", pipeline="health_wellness")
    manifest = tmp_path / "provider_state.json"
    manifest.write_text(
        json.dumps(
            {
                "social_posts": {
                    "note_provider_123": {
                        "target": "note-123",
                        "status": "posted",
                        "url": "https://social.example/notes/note-123",
                    }
                },
                "market_alerts": [
                    {
                        "target": "alert-123",
                        "status": "delivered",
                        "provider_id": "alert_provider_123",
                        "external_ref": "tetra-alert:alert-123",
                    }
                ],
                "health_writes": {
                    "health_provider_123": {
                        "target": "health-123",
                        "status": "synced",
                        "external_ref": "health-provider:health-123",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    reconciled = reconcile_effects_from_provider_state(effects, provider_state_manifest_paths=[manifest])

    assert len(reconciled) == 3
    social = effects.get_by_idempotency_key("effect:note-123")
    market = effects.get_by_idempotency_key("effect:alert-123")
    health = effects.get_by_idempotency_key("effect:health-123")
    assert social is not None
    assert social.status == "reconciled_succeeded"
    assert social.external_ref == "https://social.example/notes/note-123"
    assert social.reconciliation_ref == f"provider_state:{manifest}:social:note_provider_123"
    assert market is not None
    assert market.status == "reconciled_succeeded"
    assert market.external_ref == "tetra-alert:alert-123"
    assert market.reconciliation_ref == f"provider_state:{manifest}:market:alert_provider_123"
    assert health is not None
    assert health.status == "reconciled_succeeded"
    assert health.external_ref == "health-provider:health-123"
    assert health.reconciliation_ref == f"provider_state:{manifest}:health:health_provider_123"


def test_provider_state_manifest_reconciles_failure_evidence(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _planned_effect(effects, action="write_health", target="health-failed", pipeline="health_wellness")
    manifest = tmp_path / "provider_state.json"
    manifest.write_text(
        json.dumps(
            {
                "health_writes": {
                    "health_failed_provider": {
                        "target": "health-failed",
                        "status": "rejected",
                        "message": "provider rejected stale health payload",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    reconcile_effects_from_provider_state(effects, provider_state_manifest_paths=[manifest])
    latest = effects.get_by_idempotency_key("effect:health-failed")

    assert latest is not None
    assert latest.status == "reconciled_failed"
    assert latest.detail == "provider rejected stale health payload"
    assert latest.reconciliation_ref == f"provider_state:{manifest}:health:health_failed_provider"


def test_connector_resolver_reconciles_provider_error(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="connector-error")

    reconcile_effects_from_provider_state(
        effects,
        provider_resolvers={
            "substack": lambda _entry: {
                "status": "error",
                "provider_id": "post_500",
                "message": "provider confirms publish failed",
            }
        },
    )
    latest = effects.get_by_idempotency_key("effect:connector-error")

    assert latest is not None
    assert latest.status == "reconciled_failed"
    assert latest.detail == "provider confirms publish failed"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:post_500"


def test_connector_resolver_requires_structured_provider_evidence(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    unknown = _unknown_effect(effects, action="publish_substack", target="connector-unknown")

    reconciled = reconcile_effects_from_provider_state(
        effects,
        provider_resolvers={
            "substack": lambda _entry: {
                "status": "draft",
                "provider_id": "post_draft",
                "url": "https://test.substack.com/p/connector-unknown",
            }
        },
    )

    assert reconciled == []
    assert effects.get_by_idempotency_key("effect:connector-unknown") == unknown


def test_http_json_provider_resolver_reconciles_success_from_api_payload(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="api-article")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/effects/api-article"
        assert request.headers["authorization"] == "Bearer token_123"
        return httpx.Response(
            200,
            json={
                "data": {
                    "status": "published",
                    "provider_id": "post_api_123",
                    "url": "https://test.substack.com/p/api-article",
                    "detail": "provider API found published post",
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    resolver = HttpJsonProviderResolver(
        "https://provider.example/effects/{target}",
        bearer_token="token_123",
        payload_path=("data",),
        client=client,
    )

    reconciled = reconcile_effects_from_provider_state(effects, provider_resolvers={"substack": resolver})
    latest = effects.get_by_idempotency_key("effect:api-article")

    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.external_ref == "https://test.substack.com/p/api-article"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:post_api_123"
    client.close()


def test_http_json_provider_resolver_reconciles_not_found_as_failed(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    _unknown_effect(effects, action="publish_substack", target="missing-api-article")
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(404)))
    resolver = HttpJsonProviderResolver("https://provider.example/effects/{target}", client=client)

    reconcile_effects_from_provider_state(effects, provider_resolvers={"substack": resolver})
    latest = effects.get_by_idempotency_key("effect:missing-api-article")

    assert latest is not None
    assert latest.status == "reconciled_failed"
    assert latest.reconciliation_ref == "provider:substack:publish_substack:missing-api-article"
    client.close()


def test_http_json_provider_resolver_leaves_server_error_unresolved(tmp_path: Path):
    effects = EffectLog(tmp_path / "effects.jsonl")
    unknown = _unknown_effect(effects, action="publish_substack", target="provider-down")
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(503)))
    resolver = HttpJsonProviderResolver("https://provider.example/effects/{target}", client=client)

    reconciled = reconcile_effects_from_provider_state(effects, provider_resolvers={"substack": resolver})

    assert reconciled == []
    assert effects.get_by_idempotency_key("effect:provider-down") == unknown
    client.close()
