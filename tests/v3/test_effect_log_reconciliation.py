from pathlib import Path

from mira.engine.effect_log import EffectLog, EffectLogEntry, ReconciliationResult


def test_effect_log_unknown_reconciliation_blocks_duplicate_success(tmp_path: Path):
    log = EffectLog(tmp_path / "effects.jsonl")
    log.plan(
        idempotency_key="publish:article:42",
        run_id="run_42",
        pipeline="article_creation",
        action="publish_substack",
        target="article-42",
    )
    log.mark_executing("publish:article:42")
    unknown = log.mark_unknown("publish:article:42", "process died after API call")

    assert unknown.status == "unknown"
    assert log.unresolved()[0].idempotency_key == "publish:article:42"

    reconciled = log.reconcile(
        "publish:article:42",
        succeeded=True,
        detail="remote post exists",
        external_ref="substack:post:42",
        reconciliation_ref="substack:title-search:42",
    )
    duplicate = log.append(
        EffectLogEntry(
            idempotency_key="publish:article:42",
            run_id="run_42",
            pipeline="article_creation",
            action="publish_substack",
            target="article-42",
            status="succeeded",
        )
    )

    assert reconciled.status == "reconciled_succeeded"
    assert reconciled.external_ref == "substack:post:42"
    assert reconciled.reconciliation_ref == "substack:title-search:42"
    assert reconciled.executed_at is not None
    assert duplicate.effect_id == reconciled.effect_id
    assert log.unresolved() == []


def test_effect_log_reconciles_unknowns_with_resolver(tmp_path: Path):
    log = EffectLog(tmp_path / "effects.jsonl")
    log.plan(
        idempotency_key="tweet:1",
        run_id="run_1",
        pipeline="article_creation",
        action="post_tweet",
        target="tweet-1",
    )
    log.mark_unknown("tweet:1")

    reconciled = log.reconcile_unknowns(lambda entry: entry.target == "tweet-1")

    assert len(reconciled) == 1
    assert log.get_by_idempotency_key("tweet:1").status == "reconciled_succeeded"


def test_effect_log_reconciles_with_provider_refs_from_resolver(tmp_path: Path):
    log = EffectLog(tmp_path / "effects.jsonl")
    planned = log.plan(
        idempotency_key="publish:article:99",
        run_id="run_99",
        pipeline="article_creation",
        action="publish_substack",
        target="article-99",
        step_id="publish_substack_idempotent",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
        replay_bundle_ref="replay:bundle:99",
    )
    log.mark_unknown("publish:article:99", "process died after API call")

    reconciled = log.reconcile_unknowns(
        lambda entry: ReconciliationResult(
            succeeded=True,
            detail="remote post found by idempotency tag",
            external_ref="substack:post:99",
            reconciliation_ref="substack:idempotency-tag:preview-sha256",
        )
    )
    latest = log.get_by_idempotency_key("publish:article:99")

    assert planned.preview_hash == "preview-sha256"
    assert len(reconciled) == 1
    assert latest is not None
    assert latest.status == "reconciled_succeeded"
    assert latest.step_id == "publish_substack_idempotent"
    assert latest.action_type == "publish_substack"
    assert latest.approval_token_id == "grant_1"
    assert latest.replay_bundle_ref == "replay:bundle:99"
    assert latest.external_ref == "substack:post:99"
    assert latest.reconciliation_ref == "substack:idempotency-tag:preview-sha256"


def test_effect_log_can_attach_replay_bundle_to_succeeded_legacy_effect(tmp_path: Path):
    log = EffectLog(tmp_path / "effects.jsonl")
    log.plan(
        idempotency_key="publish:article:legacy",
        run_id="run_legacy",
        pipeline="article_creation",
        action="publish_substack",
        target="article-legacy",
    )
    succeeded = log.mark_succeeded("publish:article:legacy", "remote post exists")

    recovered = log.attach_replay_bundle("publish:article:legacy", "recovered:bundle:legacy")
    latest = log.get_by_idempotency_key("publish:article:legacy")

    assert succeeded.status == "succeeded"
    assert recovered.status == "succeeded"
    assert recovered.effect_id != succeeded.effect_id
    assert latest is not None
    assert latest.replay_bundle_ref == "recovered:bundle:legacy"
