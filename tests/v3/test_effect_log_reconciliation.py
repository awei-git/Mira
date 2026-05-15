from pathlib import Path

from mira.engine.effect_log import EffectLog, EffectLogEntry


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

    reconciled = log.reconcile("publish:article:42", succeeded=True, detail="remote post exists")
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
