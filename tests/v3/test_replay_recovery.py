import json
from pathlib import Path

from mira.engine.effect_log import EffectLog
from mira.engine.replay_recovery import recover_missing_replay_bundles


def test_replay_recovery_builds_bundle_from_checkpoint_and_annotates_log(tmp_path: Path):
    effects = EffectLog(tmp_path / "effect_log.jsonl")
    effects.plan(
        idempotency_key="article_creation:publish_substack_idempotent:legacy-title",
        run_id="run_article",
        pipeline="article_creation",
        action="publish_substack",
        target="legacy-title",
    )
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "run_article.json").write_text(
        json.dumps(
            {
                "run_id": "run_article",
                "pipeline": "article_creation",
                "step": "publish_substack_idempotent",
                "outputs": {
                    "_artifacts": [str(tmp_path / "artifacts" / "article.md")],
                    "draft": {"title": "legacy-title", "draft": ["# legacy-title", "body"]},
                },
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_missing_replay_bundles(
        effects,
        artifact_root=tmp_path,
        checkpoint_dir=checkpoint_dir,
    )
    latest = effects.get_by_idempotency_key("article_creation:publish_substack_idempotent:legacy-title")
    bundle = json.loads(Path(recovered[0].replay_bundle_ref).read_text(encoding="utf-8"))

    assert len(recovered) == 1
    assert latest is not None
    assert latest.replay_bundle_ref == recovered[0].replay_bundle_ref
    assert bundle["bundle_version"] == "v3.1-recovered"
    assert bundle["payload_reconstructable"] is True
    assert bundle["payload"]["title"] == "legacy-title"
    assert bundle["artifact_refs"] == [str(tmp_path / "artifacts" / "article.md")]


def test_replay_recovery_builds_bundle_from_provider_state_for_succeeded_effect(tmp_path: Path):
    effects = EffectLog(tmp_path / "effect_log.jsonl")
    effects.plan(
        idempotency_key="social_proactive:post_note_idempotent:target-1",
        run_id="run_social",
        pipeline="social_proactive",
        action="post_social",
        target="target-1",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )
    effects.reconcile(
        "social_proactive:post_note_idempotent:target-1",
        succeeded=True,
        detail="provider posted",
        external_ref="local_provider_state:social:target-1",
        reconciliation_ref=f"provider_state:{tmp_path / 'provider_state' / 'social.json'}:social:target-1",
    )
    provider_state = tmp_path / "provider_state"
    provider_state.mkdir()
    (provider_state / "social.json").write_text(
        json.dumps(
            {
                "social_posts": {
                    "target-1": {
                        "idempotency_key": "social_proactive:post_note_idempotent:target-1",
                        "target": "target-1",
                        "status": "posted",
                        "preview": {"target": "target-1", "content": "hello", "platform": "local_provider_state"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    recovered = recover_missing_replay_bundles(
        effects,
        artifact_root=tmp_path,
        checkpoint_dir=tmp_path / "checkpoints",
        provider_state_dir=provider_state,
    )
    latest = effects.get_by_idempotency_key("social_proactive:post_note_idempotent:target-1")
    bundle = json.loads(Path(recovered[0].replay_bundle_ref).read_text(encoding="utf-8"))

    assert len(recovered) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.replay_bundle_ref == recovered[0].replay_bundle_ref
    assert bundle["payload"]["content"] == "hello"
    assert bundle["provider_evidence"]["status"] == "posted"
