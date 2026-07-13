import importlib.util
import json
from pathlib import Path

from mira.runtime import default_effect_log


def _load_cli_module():
    module_path = Path(__file__).resolve().parents[2] / "agents" / "super" / "cli" / "v3_effect_reconciliation.py"
    spec = importlib.util.spec_from_file_location("v3_effect_reconciliation_cli_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_effect_reconciliation_cli_inspects_open_effect_without_mutating(tmp_path: Path):
    module = _load_cli_module()
    replay_bundle = tmp_path / "data" / "v3" / "effect_replay_bundles" / "publish.json"
    replay_bundle.parent.mkdir(parents=True)
    replay_bundle.write_text(
        json.dumps(
            {
                "idempotency_key": "publish:1",
                "action": "publish_substack",
                "compensation_strategy": {"strategy": "manual_reconcile"},
            }
        ),
        encoding="utf-8",
    )
    effects = default_effect_log(tmp_path)
    effect = effects.plan(
        idempotency_key="publish:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        replay_bundle_ref=str(replay_bundle),
    )

    payload = module.inspect_effect(root=tmp_path, effect_id=effect.effect_id)
    text = module.render_effect_inspection(payload)

    assert payload["effect"]["effect_id"] == effect.effect_id
    assert payload["effect"]["status"] == "planned"
    assert payload["replay_bundle"]["status"] == "readable_json"
    assert payload["replay_bundle"]["compensation_strategy"]["strategy"] == "manual_reconcile"
    assert payload["provider_evidence"]["replay_bundle_provider_evidence"] == "missing"
    assert payload["provider_evidence"]["publish_manifest"]["checked"] == 0
    assert payload["provider_evidence"]["rss_feeds"]["checked"] == 0
    assert payload["provider_evidence"]["provider_state"]["checked"] == 0
    assert payload["provider_evidence"]["provider_state"]["result"]["status"] == "no_match"
    assert "Publish manifest result: no_match" in text
    assert "RSS feed result: no_match" in text
    assert "Provider state result: no_match" in text
    assert "Do not mark this effect complete from local intent alone." in text
    assert effects.get_by_idempotency_key("publish:1").status == "planned"


def test_effect_reconciliation_cli_summarizes_provider_state_without_mutating(tmp_path: Path):
    module = _load_cli_module()
    effects = default_effect_log(tmp_path)
    effect = effects.plan(
        idempotency_key="publish:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    provider_state = tmp_path / "data" / "v3" / "provider_state" / "substack.json"
    provider_state.parent.mkdir(parents=True)
    provider_state.write_text(
        json.dumps(
            {
                "effects": [
                    {
                        "idempotency_key": "publish:1",
                        "status": "published",
                        "external_ref": "https://example.substack.com/p/article-1",
                        "detail": "provider state confirms publication",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = module.inspect_effect(root=tmp_path, effect_id=effect.effect_id)
    text = module.render_effect_inspection(payload)

    result = payload["provider_evidence"]["provider_state"]["result"]
    assert payload["provider_evidence"]["provider_state"]["checked"] == 1
    assert result["status"] == "proven_succeeded"
    assert result["external_ref"] == "https://example.substack.com/p/article-1"
    assert result["reconciliation_ref"] == f"provider_state:{provider_state}:substack:article-1"
    assert "Provider state result: proven_succeeded" in text
    assert effects.get_by_idempotency_key("publish:1").status == "planned"


def test_effect_reconciliation_cli_accepts_explicit_publication_manifest_without_mutating(tmp_path: Path):
    module = _load_cli_module()
    effects = default_effect_log(tmp_path)
    effect = effects.plan(
        idempotency_key="publish:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    publish_manifest = tmp_path / "operator_publish_manifest.json"
    publish_manifest.write_text(
        json.dumps(
            {
                "articles": {
                    "article-1": {
                        "slug": "article-1",
                        "status": "published",
                        "substack_url": "https://example.substack.com/p/article-1",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    payload = module.inspect_effect(
        root=tmp_path,
        effect_id=effect.effect_id,
        publish_manifest_path=publish_manifest,
    )
    text = module.render_effect_inspection(payload)

    result = payload["provider_evidence"]["publish_manifest"]["result"]
    assert payload["provider_evidence"]["publish_manifest"]["checked"] == 1
    assert result["status"] == "proven_succeeded"
    assert result["external_ref"] == "https://example.substack.com/p/article-1"
    assert result["reconciliation_ref"] == f"publish_manifest:{publish_manifest}:article-1"
    assert "Publish manifest result: proven_succeeded" in text
    assert effects.get_by_idempotency_key("publish:1").status == "planned"
