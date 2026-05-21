from pathlib import Path

from mira.engine.effect_log import EffectLog, EffectLogEntry
from mira.evals import build_operational_eval_bundle, build_strategic_scorecard
from mira.kernel import CausalEvidence, ExperienceLedger, MemoryAction, MemoryDelta
from mira.kernel.commit import MemoryCommitLog, SecurityGateway
from mira.kernel.ledger import ExperienceRecord


def _record(
    *,
    pipeline: str = "a2a_trust_experiment",
    outcome: str = "completed",
    artifacts: list[str] | None = None,
    eval_refs: list[str] | None = None,
    causal_links: list[str] | None = None,
) -> ExperienceRecord:
    delta = MemoryDelta(
        pipeline=pipeline,
        run_id="exp_1",
        memory_class="epistemic" if pipeline == "a2a_trust_experiment" else "operational",
        what_happened="ran",
        what_mattered="mattered",
        what_changed="changed",
        actions=[MemoryAction("update_skill_trace", f"skill:{pipeline}", "ok")],
    )
    return ExperienceRecord(
        id="exp_1",
        pipeline=pipeline,
        trigger="manual",
        intent="test",
        outcome=outcome,
        delta=delta,
        causal_links=["memory:1"] if causal_links is None else causal_links,
        confidence=0.9,
        memory_class=delta.memory_class,
        artifacts=artifacts or [],
        eval_refs=eval_refs or [],
        memory_commit_id="commit_1",
    )


def test_strategic_scorecard_requires_a2a_artifact_and_tool_signal():
    scorecard = build_strategic_scorecard(
        [
            _record(
                artifacts=["/tmp/a2a.md"],
                eval_refs=["strategic:a2a_trust_experiment"],
            )
        ]
    )

    assert scorecard.a2a_experiments_completed == 1
    assert scorecard.reproducible_artifacts == 1
    assert scorecard.tool_prototypes == 1
    assert scorecard.hard_gate_failures == []


def test_operational_eval_bundle_flags_unknown_effects_and_pollution(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    record = _record()
    ledger.append(record)
    commits = MemoryCommitLog(tmp_path / "commits.jsonl")
    commits.append(SecurityGateway().validate(record.delta))
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.append(
        EffectLogEntry(
            idempotency_key="effect:1",
            run_id="exp_1",
            pipeline="article_creation",
            action="publish",
            target="article",
            status="unknown",
        )
    )

    bundle = build_operational_eval_bundle(ledger.list(), commits.list(), effects.list())

    assert bundle.scorecard.orphan_important_action == 1
    assert "orphan_important_action" in bundle.scorecard.hard_gate_failures


def test_operational_causal_link_validity_rates_asserted_links_only():
    routine_record = _record(pipeline="system_health", causal_links=[])
    invalid_claim = _record(pipeline="communication", causal_links=["memory:unsupported"])
    valid_claim = _record(
        pipeline="podcast_production",
        causal_links=["causal_valid"],
    )

    no_claim_bundle = build_operational_eval_bundle([routine_record], [], [])
    mixed_claim_bundle = build_operational_eval_bundle(
        [invalid_claim, valid_claim],
        [],
        [],
        [CausalEvidence("memory:tts_scar", "L3", "decision changed", evidence_id="causal_valid")],
    )

    assert no_claim_bundle.scorecard.causal_link_validity == 1.0
    assert "causal_link_validity" not in no_claim_bundle.scorecard.hard_gate_failures
    assert mixed_claim_bundle.scorecard.causal_link_validity == 0.5
    assert "causal_link_validity" in mixed_claim_bundle.scorecard.hard_gate_failures
