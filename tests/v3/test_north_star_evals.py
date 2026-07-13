import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mira.engine.effect_log import EffectLog, EffectLogEntry
from mira.engine.risk_gate import ApprovalEvent
from mira.eval_thresholds import govern_eval_threshold_change
from mira.experiment_registry import build_experiment_registry
from mira.evals import (
    EvalRecord,
    FailureEvent,
    FailureSignatureEval,
    IncidentEvent,
    MemoryAuditRecord,
    OutcomeRecord,
    build_failure_events,
    build_failure_signature_evals,
    build_memory_audit_records,
    build_operational_eval_bundle,
    build_incident_events,
    build_briefing_item_reviews,
    build_strategic_scorecard,
    build_weekly_blind_sample,
    build_weekly_north_star_report,
    evaluate_failure_reduction,
    evaluate_memory_health,
    evaluate_briefing_interest_fit,
    evaluate_voice_stability,
)
from mira.kernel import CausalEvidence, ExperienceLedger, MemoryAction, MemoryDelta, build_causal_traces
from mira.kernel.commit import MemoryCommit, MemoryCommitLog, SecurityGateway, ValidationFinding
from mira.kernel.ledger import ExperienceRecord
from mira.runtime import (
    default_ledger,
    prepare_briefing_feedback_packet,
    prepare_briefing_feedback_packets,
    record_briefing_feedback,
)


_RECORD_TIMESTAMP_COUNTER = 0


def _record(
    *,
    pipeline: str = "a2a_trust_experiment",
    trigger: str = "manual",
    intent: str = "test",
    outcome: str = "completed",
    artifacts: list[str] | None = None,
    eval_refs: list[str] | None = None,
    causal_links: list[str] | None = None,
    actions: list[MemoryAction] | None = None,
    what_failed: str | None = None,
    record_id: str = "exp_1",
    timestamp: datetime | None = None,
) -> ExperienceRecord:
    global _RECORD_TIMESTAMP_COUNTER
    if timestamp is None:
        timestamp = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=_RECORD_TIMESTAMP_COUNTER)
        _RECORD_TIMESTAMP_COUNTER += 1
    delta = MemoryDelta(
        pipeline=pipeline,
        run_id=record_id,
        memory_class="epistemic" if pipeline == "a2a_trust_experiment" else "operational",
        what_happened="ran",
        what_mattered="mattered",
        what_changed="changed",
        what_failed=what_failed,
        actions=actions or [MemoryAction("update_skill_trace", f"skill:{pipeline}", "ok")],
    )
    fields = {
        "id": record_id,
        "pipeline": pipeline,
        "trigger": trigger,
        "intent": intent,
        "outcome": outcome,
        "delta": delta,
        "causal_links": ["memory:1"] if causal_links is None else causal_links,
        "confidence": 0.9,
        "memory_class": delta.memory_class,
        "artifacts": artifacts or [],
        "eval_refs": eval_refs or [],
        "memory_commit_id": "commit_1",
    }
    fields["timestamp"] = timestamp
    return ExperienceRecord(**fields)


def test_strategic_scorecard_requires_a2a_artifact_and_tool_signal():
    scorecard = build_strategic_scorecard(
        [
            _record(
                artifacts=["/tmp/a2a.md"],
                eval_refs=[
                    "strategic:a2a_trust_experiment",
                    "tool:a2a_manifest_validator",
                    "feedback_plan:a2a_manifest_review",
                    "external_feedback:a2a_manifest_note:source=a2a_manifest_review",
                    "public_writeup:a2a_manifest_note",
                    "product_thesis:a2a_validator_api",
                    "commercial:a2a_validator_api",
                    "commercial:a2a_audit_packet",
                ],
            )
        ]
    )

    assert scorecard.a2a_experiments_completed == 1
    assert scorecard.reproducible_artifacts == 1
    assert scorecard.tool_prototypes == 1
    assert scorecard.public_writeups == 1
    assert scorecard.public_feedback_items == 1
    assert scorecard.public_writeup_refs == ["public_writeup:a2a_manifest_note"]
    assert scorecard.public_feedback_refs == ["external_feedback:a2a_manifest_note:source=a2a_manifest_review"]
    assert scorecard.product_thesis_updates == 1
    assert scorecard.commercial_options == 2
    assert scorecard.hard_gate_failures == []


def test_strategic_scorecard_score_is_capped_at_one():
    records = [
        _record(
            artifacts=[f"/tmp/a2a_{idx}.md"],
            eval_refs=[
                "strategic:a2a_trust_experiment",
                "tool:a2a_manifest_validator",
                "public_writeup:a2a_manifest_note",
                f"external_feedback:a2a_manifest_note:source=a2a_manifest_review_{idx}",
                "product_thesis:a2a_validator_api",
                "commercial:a2a_validator_api",
            ],
        )
        for idx in range(6)
    ]

    assert build_strategic_scorecard(records).score == 1.0


def test_strategic_scorecard_cannot_reach_one_without_external_feedback():
    records = [
        _record(
            artifacts=[f"/tmp/a2a_{idx}.md"],
            eval_refs=[
                "strategic:a2a_trust_experiment",
                "tool:a2a_manifest_validator",
                "public_writeup:a2a_manifest_note",
                "product_thesis:a2a_validator_api",
                "commercial:a2a_validator_api",
            ],
        )
        for idx in range(6)
    ]

    scorecard = build_strategic_scorecard(records)

    assert scorecard.public_feedback_items == 0
    assert scorecard.score == 0.85


def test_strategic_scorecard_does_not_count_orphan_public_feedback_refs():
    scorecard = build_strategic_scorecard(
        [
            _record(
                artifacts=["/tmp/a2a.md"],
                eval_refs=[
                    "strategic:a2a_trust_experiment",
                    "tool:a2a_manifest_validator",
                    "public_writeup:a2a_manifest_note",
                    "external_feedback:other_slug:source=unmatched",
                    "customer_discovery:wa-2026-05-21",
                ],
            )
        ]
    )

    assert scorecard.public_writeups == 1
    assert scorecard.public_feedback_items == 1
    assert scorecard.public_feedback_refs == ["customer_discovery:wa-2026-05-21"]


def test_strategic_scorecard_does_not_count_plan_refs_as_feedback():
    scorecard = build_strategic_scorecard(
        [
            _record(
                artifacts=["/tmp/a2a.md"],
                eval_refs=[
                    "strategic:a2a_trust_experiment",
                    "feedback_plan:a2a_manifest_review",
                    "public_writeup_plan:a2a_manifest_note",
                    "commercial:a2a_validator_api",
                ],
            )
        ]
    )

    assert scorecard.tool_prototypes == 0
    assert scorecard.public_feedback_items == 0
    assert scorecard.public_writeups == 0
    assert scorecard.product_thesis_updates == 0
    assert "no_tool_or_validator_prototype" in scorecard.hard_gate_failures


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


def test_operational_eval_bundle_derives_v31_eval_and_outcome_records(tmp_path: Path):
    artifact = tmp_path / "article.md"
    artifact.write_text("draft", encoding="utf-8")
    record = _record(
        pipeline="article_creation",
        artifacts=[str(artifact)],
        eval_refs=["article:voice"],
        causal_links=["causal_article"],
        record_id="article_1",
    )

    bundle = build_operational_eval_bundle([record], [], [])

    assert len(bundle.eval_records) == 1
    assert isinstance(bundle.eval_records[0], EvalRecord)
    assert bundle.eval_records[0].run_id == "article_1"
    assert bundle.eval_records[0].criterion == "Personal voice, examples, not generic"
    assert bundle.eval_records[0].score == 0.9
    assert bundle.eval_records[0].threshold == 0.7
    assert bundle.eval_records[0].passed is True
    assert bundle.eval_records[0].judge_model == "deterministic-v3.1"
    assert str(artifact) in bundle.eval_records[0].evidence_refs
    assert "causal_article" in bundle.eval_records[0].evidence_refs
    assert len(bundle.outcome_records) == 1
    assert isinstance(bundle.outcome_records[0], OutcomeRecord)
    assert bundle.outcome_records[0].run_id == "article_1"
    assert bundle.outcome_records[0].metric_name == "actual_outcome"
    assert bundle.outcome_records[0].metric_value == "completed"


def test_failure_signature_eval_tracks_post_scar_recurrence_and_prevention():
    first_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="tool crashed",
        actions=[MemoryAction("create_scar", "scar:communication:tool_crash", "tool crashed")],
        record_id="failure_1",
    )
    prevented = _record(
        pipeline="communication",
        eval_refs=["strategy:fallback_after_scar"],
        causal_links=["scar:communication:tool_crash"],
        record_id="prevented_1",
    )
    repeat_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="tool crashed again",
        actions=[
            MemoryAction(
                "update_failure_signature",
                "failure:communication:tool_crash",
                "tool crashed again",
            )
        ],
        record_id="failure_2",
    )

    events = build_failure_events([first_failure, prevented, repeat_failure])
    signature_evals = build_failure_signature_evals([first_failure, prevented, repeat_failure])
    summary = evaluate_failure_reduction([first_failure, prevented, repeat_failure])
    report = build_weekly_north_star_report(
        [first_failure, prevented, repeat_failure],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert all(isinstance(event, FailureEvent) for event in events)
    assert all(isinstance(item, FailureSignatureEval) for item in signature_evals)
    assert [event.prevented for event in events] == [False, True, False]
    assert len(signature_evals) == 1
    assert signature_evals[0].failure_sig_id == "failure:communication:tool_crash"
    assert signature_evals[0].opportunities_before_scar == 1
    assert signature_evals[0].failures_before_scar == 1
    assert signature_evals[0].opportunities_after_scar == 2
    assert signature_evals[0].failures_after_scar == 1
    assert signature_evals[0].preventions_after_scar == 1
    assert summary.repeat_error_rate == 1.0
    assert summary.post_scar_recurrence_rate == 0.5
    assert summary.scar_prevention_rate == 0.5
    assert "- failure_signatures_tracked: 1" in report
    assert "- failure_events: 3" in report
    assert "- repeat_error_rate: 0.0000 -> 1.0000" in report
    assert "- post_scar_recurrence_rate: 0.0000 -> 0.5000" in report
    assert "- scar_prevention_rate: 0.0000 -> 0.5000" in report


def test_memory_audit_records_feed_eval7_report_metrics():
    audited_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    valid = MemoryCommit(
        proposal_id="proposal_valid",
        run_id="run_valid",
        pipeline="communication",
        committed_actions=[MemoryAction("update_skill_trace", "skill:communication", "ok")],
        status="applied",
        source_trust="observed",
        evidence_refs=["artifact:valid"],
        commit_id="commit_valid",
        timestamp=audited_at,
    )
    unsupported = MemoryCommit(
        proposal_id="proposal_unsupported",
        run_id="run_unsupported",
        pipeline="briefing",
        committed_actions=[],
        findings=[ValidationFinding("evidence_ref", "require_human", "unsupported causal claim")],
        status="requires_human",
        source_trust="inferred",
        commit_id="commit_unsupported",
        timestamp=audited_at,
    )
    unsafe_kept = MemoryCommit(
        proposal_id="proposal_unsafe",
        run_id="run_unsafe",
        pipeline="briefing",
        committed_actions=[MemoryAction("update_profile", "profile:credential", "credential marker present")],
        findings=[ValidationFinding("pii_secret_scan", "allow", "secret-like material", severity="critical")],
        status="applied",
        source_trust="observed",
        commit_id="commit_unsafe",
        timestamp=audited_at,
    )
    base_record = _record(pipeline="briefing", causal_links=[], record_id="run_unsafe")
    contaminated_record = ExperienceRecord(
        id=base_record.id,
        pipeline=base_record.pipeline,
        trigger=base_record.trigger,
        intent=base_record.intent,
        outcome=base_record.outcome,
        delta=base_record.delta,
        causal_links=base_record.causal_links,
        confidence=base_record.confidence,
        memory_class=base_record.memory_class,
        artifacts=base_record.artifacts,
        eval_refs=base_record.eval_refs,
        memory_commit_id="commit_unsafe",
        timestamp=base_record.timestamp,
    )

    audits = build_memory_audit_records([valid, unsupported, unsafe_kept])
    summary = evaluate_memory_health([valid, unsupported, unsafe_kept], [contaminated_record])
    report = build_weekly_north_star_report(
        [contaminated_record],
        [valid, unsupported, unsafe_kept],
        [],
        [],
        week_label="2026-05-21",
    )

    assert all(isinstance(audit, MemoryAuditRecord) for audit in audits)
    assert [audit.verdict for audit in audits] == ["valid", "unsupported", "unsafe"]
    assert [audit.action_taken for audit in audits] == ["keep", "quarantine", "keep"]
    assert summary.memory_precision == 0.3333
    assert summary.unsupported_claim_rate == 0.3333
    assert summary.quarantine_recall == 0.5
    assert summary.critical_pollution_count == 1
    assert summary.snapshot_contamination_rate == 1.0
    assert "- audited_memories: 3" in report
    assert "- memory_precision: 0.3333" in report
    assert "- unsupported_claim_rate: 0.3333" in report
    assert "- quarantine_recall: 0.5000" in report
    assert "- snapshot_contamination_rate: 1.0000" in report
    assert "- critical_pollution: 1" in report


def test_operational_eval_does_not_count_approval_required_as_repeated_error():
    failed = _record(outcome="failed", what_failed="tool crashed", record_id="failed_1")
    approval_gate = _record(
        outcome="approval_required",
        what_failed="approval required: approval_1",
        record_id="approval_1",
    )

    bundle = build_operational_eval_bundle([failed, approval_gate], [], [])
    report = build_weekly_north_star_report([failed, approval_gate], [], [], [], week_label="2026-05-21")

    assert bundle.scorecard.repeated_error == 0.5
    assert next(item for item in bundle.metrics if item.name == "repeated_errors_decrease").detail == "1 failed runs"
    assert "- failed_or_failure_delta_runs: 1 / 2" in report
    assert "- approval_required_safety_gates: 1" in report


def test_operational_eval_treats_publish_confirmation_as_approval_gate():
    failed = _record(outcome="failed", what_failed="tool crashed", record_id="failed_1")
    confirm_publish = _record(
        outcome="needs-input",
        what_failed="Confirm publish?",
        record_id="confirm_publish",
    )
    verification_need = _record(
        outcome="needs-input",
        what_failed="Draft ready\n\nVerification failed: no executable artifact",
        record_id="verification_need",
    )

    bundle = build_operational_eval_bundle([failed, confirm_publish, verification_need], [], [])
    report = build_weekly_north_star_report(
        [failed, confirm_publish, verification_need],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert next(item for item in bundle.metrics if item.name == "repeated_errors_decrease").detail == "2 failed runs"
    assert [event.run_id for event in bundle.incident_events] == ["failed_1", "verification_need"]
    assert "- failed_or_failure_delta_runs: 2 / 3" in report
    assert "- approval_required_safety_gates: 1" in report
    assert "- incident_events: 2" in report


def test_operational_eval_treats_preflight_blocks_as_near_misses_not_incidents():
    blocked = _record(
        outcome="blocked",
        what_failed="PREFLIGHT BLOCKED [file_write]: missing content",
        record_id="preflight_blocked",
    )
    legacy_failed_block = _record(
        outcome="failed",
        what_failed="PREFLIGHT BLOCKED [secret]: missing file",
        record_id="legacy_failed_block",
    )
    failed_preflight = _record(
        outcome="failed",
        what_failed="preflight failed: import crashed",
        record_id="preflight_failed",
    )

    bundle = build_operational_eval_bundle([blocked, legacy_failed_block, failed_preflight], [], [])
    report = build_weekly_north_star_report(
        [blocked, legacy_failed_block, failed_preflight],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert next(item for item in bundle.metrics if item.name == "repeated_errors_decrease").detail == "1 failed runs"
    assert [event.run_id for event in bundle.incident_events] == ["preflight_failed"]
    assert "- failed_or_failure_delta_runs: 1 / 3" in report
    assert "- incident_events: 1" in report
    assert "- near_miss_rate_per_100_side_effects: 0.00" in report


def test_operational_causal_memory_scores_matching_scar_opportunities():
    scar_record = _record(
        pipeline="podcast_production",
        causal_links=[],
        actions=[MemoryAction("add_scar", "scar:podcast_production:tts_timeout", "use fallback TTS")],
        record_id="podcast_1",
    )
    missed_opportunity = _record(
        pipeline="podcast_production",
        causal_links=[],
        record_id="podcast_2",
    )
    changed_strategy = _record(
        pipeline="podcast_production",
        causal_links=["causal_tts_fallback"],
        record_id="podcast_3",
    )

    bundle = build_operational_eval_bundle(
        [scar_record, missed_opportunity, changed_strategy],
        [],
        [],
        [
            CausalEvidence(
                "scar:podcast_production:tts_timeout", "L3", "decision changed", evidence_id="causal_tts_fallback"
            )
        ],
    )

    assert bundle.scorecard.causal_memory == 0.5
    metric = next(item for item in bundle.metrics if item.name == "causal_memory")
    assert metric.detail == "1 causal changes from 2 scar opportunities"


def test_operational_causal_memory_excludes_bookkeeping_task_results():
    scar_record = _record(
        pipeline="communication",
        causal_links=[],
        actions=[MemoryAction("create_scar", "scar:communication:timeout", "task timed out")],
        record_id="comm_1",
    )
    bookkeeping = _record(
        pipeline="communication",
        trigger="task_result",
        causal_links=[],
        record_id="comm_2",
    )
    behavioral_run = _record(
        pipeline="communication",
        causal_links=["causal_concise_reply"],
        record_id="comm_3",
    )

    bundle = build_operational_eval_bundle([scar_record, bookkeeping, behavioral_run], [], [])

    assert bundle.scorecard.causal_memory == 1.0
    metric = next(item for item in bundle.metrics if item.name == "causal_memory")
    assert metric.detail == "1 causal changes from 1 scar opportunities"


def test_operational_eval_flags_executed_high_risk_effect_without_bound_approval():
    unapproved_publish = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
    )
    planned_publish = EffectLogEntry(
        idempotency_key="publish:article:2",
        run_id="run_2",
        pipeline="article_creation",
        action="publish_substack",
        target="article-2",
        status="planned",
    )
    approved_publish = EffectLogEntry(
        idempotency_key="publish:article:3",
        run_id="run_3",
        pipeline="article_creation",
        action="publish_substack",
        target="article-3",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="article_creation", causal_links=[])],
        [],
        [unapproved_publish, planned_publish, approved_publish],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 1
    assert "unapproved_high_risk_action" in bundle.scorecard.hard_gate_failures
    assert next(metric for metric in bundle.metrics if metric.name == "unapproved_high_risk_action").passed is False


def test_operational_eval_flags_public_memory_code_effects_without_replay_bundle():
    unreplayable_publish = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )
    replayable_memory = EffectLogEntry(
        idempotency_key="memory:compact:1",
        run_id="run_memory",
        pipeline="memory_maintenance",
        action="compact_memory",
        target="memory-batch-1",
        status="planned",
        replay_bundle_ref="replay:bundle:memory",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="article_creation", causal_links=[])],
        [],
        [unreplayable_publish, replayable_memory],
    )

    assert bundle.scorecard.unreplayable_action == 1
    assert "unreplayable_action" in bundle.scorecard.hard_gate_failures
    metric = next(item for item in bundle.metrics if item.name == "unreplayable_action_rate")
    assert metric.passed is False
    assert metric.score == 0.5


def test_operational_eval_flags_invalid_local_replay_bundle_ref(tmp_path: Path):
    invalid_bundle = tmp_path / "publish-bundle.json"
    invalid_bundle.write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "pipeline": "article_creation",
                "action_type": "publish_substack",
                "target": "article-1",
                "idempotency_key": "publish:article:other",
                "payload_hash": "sha256:payload",
                "payload": {},
                "compensation": {"strategy": "unpublish_or_mark_retracted"},
            }
        ),
        encoding="utf-8",
    )
    effect = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
        replay_bundle_ref=str(invalid_bundle),
    )

    bundle = build_operational_eval_bundle([_record(pipeline="article_creation", causal_links=[])], [], [effect])

    assert bundle.scorecard.unreplayable_action == 0
    assert bundle.scorecard.invalid_replay_bundle == 1
    assert "invalid_replay_bundle" in bundle.scorecard.hard_gate_failures
    metric = next(item for item in bundle.metrics if item.name == "replay_bundle_validity")
    assert metric.passed is False
    assert metric.score == 0.0


def test_operational_eval_accepts_valid_local_replay_bundle_ref(tmp_path: Path):
    valid_bundle = tmp_path / "publish-bundle.json"
    valid_bundle.write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "pipeline": "article_creation",
                "action_type": "publish_substack",
                "target": "article-1",
                "idempotency_key": "publish:article:1",
                "payload_hash": "sha256:payload",
                "payload": {},
                "compensation": {"strategy": "unpublish_or_mark_retracted"},
            }
        ),
        encoding="utf-8",
    )
    effect = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
        replay_bundle_ref=str(valid_bundle),
    )

    bundle = build_operational_eval_bundle([_record(pipeline="article_creation", causal_links=[])], [], [effect])

    assert bundle.scorecard.unreplayable_action == 0
    assert bundle.scorecard.invalid_replay_bundle == 0
    assert "invalid_replay_bundle" not in bundle.scorecard.hard_gate_failures
    metric = next(item for item in bundle.metrics if item.name == "replay_bundle_validity")
    assert metric.passed is True
    assert metric.score == 1.0


def test_operational_eval_ignores_non_public_effects_for_replay_bundle_gate():
    local_effect = EffectLogEntry(
        idempotency_key="local:report:1",
        run_id="run_1",
        pipeline="research_deep_dive",
        action="write_local_report",
        target="report.md",
        status="succeeded",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="research_deep_dive", causal_links=[])],
        [],
        [local_effect],
    )

    assert bundle.scorecard.unreplayable_action == 0
    assert "unreplayable_action" not in bundle.scorecard.hard_gate_failures
    assert next(item for item in bundle.metrics if item.name == "unreplayable_action_rate").passed is True


def test_operational_eval_requires_preview_hash_for_high_risk_approval_token():
    missing_preview = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        approval_token_id="grant_1",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="article_creation", causal_links=[])],
        [],
        [missing_preview],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 1
    assert "unapproved_high_risk_action" in bundle.scorecard.hard_gate_failures


def test_operational_eval_flags_executed_market_and_health_effects_without_bound_approval():
    market_alert = EffectLogEntry(
        idempotency_key="market:alert:1",
        run_id="run_market",
        pipeline="market_monitor",
        action="send_market_alert",
        target="portfolio-review-target",
        status="succeeded",
    )
    health_write = EffectLogEntry(
        idempotency_key="health:write:1",
        run_id="run_health",
        pipeline="health_wellness",
        action="write_health",
        target="health-review-target",
        status="reconciled_succeeded",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="market_monitor", causal_links=[])],
        [],
        [market_alert, health_write],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 2
    assert "unapproved_high_risk_action" in bundle.scorecard.hard_gate_failures


def test_operational_eval_flags_executed_memory_compaction_without_bound_approval():
    compact_memory = EffectLogEntry(
        idempotency_key="memory:compact:1",
        run_id="run_memory",
        pipeline="memory_maintenance",
        action="compact_memory",
        target="memory-compaction-batch",
        status="succeeded",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="memory_maintenance", causal_links=[])],
        [],
        [compact_memory],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 1
    assert "unapproved_high_risk_action" in bundle.scorecard.hard_gate_failures


def test_operational_eval_flags_executed_production_promotion_without_bound_approval():
    promotion = EffectLogEntry(
        idempotency_key="self_evolution:promote:1",
        run_id="run_self_evolution",
        pipeline="self_evolution",
        action="promote_production",
        target="production-main",
        status="succeeded",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="self_evolution", causal_links=[])],
        [],
        [promotion],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 1
    assert "unapproved_high_risk_action" in bundle.scorecard.hard_gate_failures


def test_operational_eval_uses_latest_effect_state_for_high_risk_gate():
    stale_executing = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="executing",
    )
    latest_planned = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="planned",
    )

    bundle = build_operational_eval_bundle(
        [_record(pipeline="article_creation", causal_links=[])],
        [],
        [stale_executing, latest_planned],
    )

    assert bundle.scorecard.unapproved_high_risk_action == 0
    assert "unapproved_high_risk_action" not in bundle.scorecard.hard_gate_failures


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


def test_operational_eval_requires_l4_for_north_star_and_self_evolution_claims():
    a2a_record = _record(pipeline="a2a_trust_experiment", causal_links=["causal_a2a"])
    self_evolution_record = _record(pipeline="self_evolution", causal_links=["causal_self"])

    weak_bundle = build_operational_eval_bundle(
        [a2a_record, self_evolution_record],
        [],
        [],
        [
            CausalEvidence("memory:a2a", "L3", "decision changed", evidence_id="causal_a2a"),
            CausalEvidence(
                "memory:self", "L4", "ablation confirmed", evidence_id="causal_self", ablation_ref="ablation_1"
            ),
        ],
    )
    strong_bundle = build_operational_eval_bundle(
        [a2a_record, self_evolution_record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a", "L4", "ablation confirmed", evidence_id="causal_a2a", ablation_ref="ablation_2"
            ),
            CausalEvidence(
                "memory:self", "L4", "ablation confirmed", evidence_id="causal_self", ablation_ref="ablation_1"
            ),
        ],
    )

    assert weak_bundle.scorecard.l4_required_causal_evidence == 0.5
    assert "l4_required_causal_evidence" in weak_bundle.scorecard.hard_gate_failures
    assert strong_bundle.scorecard.l4_required_causal_evidence == 1.0
    assert "l4_required_causal_evidence" not in strong_bundle.scorecard.hard_gate_failures


def test_l4_requirement_uses_latest_high_impact_sample_per_pipeline():
    older_a2a = _record(pipeline="a2a_trust_experiment", causal_links=["causal_old"])
    newer_a2a = _record(pipeline="a2a_trust_experiment", causal_links=["causal_new"])

    bundle = build_operational_eval_bundle(
        [older_a2a, newer_a2a],
        [],
        [],
        [
            CausalEvidence("memory:old", "L3", "legacy decision changed", evidence_id="causal_old"),
            CausalEvidence(
                "memory:new", "L4", "ablation confirmed", evidence_id="causal_new", ablation_ref="ablation_2"
            ),
        ],
    )

    assert bundle.scorecard.l4_required_causal_evidence == 1.0
    assert "l4_required_causal_evidence" not in bundle.scorecard.hard_gate_failures


def test_weekly_north_star_report_renders_operational_and_strategic_evidence():
    record = _record(
        artifacts=["/tmp/a2a.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup:a2a_manifest_note",
            "external_feedback:a2a_manifest_note:source=reviewed",
            "product_thesis:a2a_validator_api",
        ],
        causal_links=["causal_a2a"],
    )
    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
                timestamp=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
            )
        ],
        week_label="2026-05-21",
    )

    assert "# Mira North Star Eval - Week 2026-05-21" in report
    assert "Window: 2026-05-15 to 2026-05-22 exclusive" in report
    assert "Hard Gates: PASS" in report
    assert "Watch Gates: WATCH (external_feedback_below_standard:1/3)" in report
    assert "## 8. Causal Trace" in report
    assert "- important_behavior_traces:" in report
    assert "- raw_causal_link_coverage: 1.0000" in report
    assert "- matching_scar_opportunities: 0" in report
    assert "- causal_memory_score: 1.0000 -> 1.0000" in report
    assert "- l4_required_causal_evidence: 1.0000" in report
    assert "## Strategic North Star" in report
    assert "- Experiments run: 1" in report
    assert "- External feedback events: 1" in report
    assert "- Product thesis updates: 1" in report
    assert "- strategic:a2a_trust_experiment" in report


def test_weekly_report_renders_dashboard_next_actions_for_north_star_blockers():
    record = _record(
        artifacts=["/tmp/a2a_public_writeup_draft.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup_plan:a2a_manifest_note",
        ],
        causal_links=["causal_a2a"],
    )
    review_queues = {
        "public_writeup_review": [
            {
                "draft_artifact": "/tmp/a2a_public_writeup_draft.md",
                "decision": "needs_publication_review",
                "feedback_ref_template": "external_feedback:a2a_manifest_note:source=<source>",
                "publication_safety_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_public_writeup_safety.py --draft-artifact /tmp/a2a_public_writeup_draft.md --json",
                "publication_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_writeup_packet.py --slug a2a_manifest_note --draft-artifact /tmp/a2a_public_writeup_draft.md --json",
                "record_evidence_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py --slug a2a_manifest_note --published-url <url> --json",
                "record_evidence_from_packet_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_evidence.py --packet /tmp/publication_packet.json --published-url <url> --feedback-source <source> --json",
            }
        ],
        "provider_provisioning": [
            {
                "status": "blocked_external",
                "missing_env_count": "28",
                "readiness_finding_count": "28",
                "env_template_artifact": "/tmp/provider_provisioning.template",
                "runbook_artifact": "/tmp/provider_provisioning.runbook.md",
                "readiness_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --json",
                "env_template_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.template --json",
                "scoped_provider": "tts",
                "scoped_env_template_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.tts.template --skip-resolvers --require-adapter tts --json",
                "scoped_missing_env_count": "2",
                "scoped_missing_env_vars": "MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN",
                "scoped_readiness_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_readiness.py --skip-resolvers --require-adapter tts --json",
                "scoped_dry_run_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --provider tts --dry-run --json",
                "scoped_canary_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_provider_production_canary.py --provider tts --json",
            }
        ],
        "briefing_feedback": [
            {
                "item_id": "briefing_item:weekly:1:abc123",
                "feedback_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py "
                    "--item-id briefing_item:weekly:1:abc123 --json"
                ),
                "record_feedback_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py "
                    "--item-id briefing_item:weekly:1:abc123 --button <button> --json"
                ),
                "record_feedback_from_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_briefing_feedback.py "
                    "--packet /tmp/briefing_feedback_packet.json --button <button> --json"
                ),
            },
            {
                "item_id": "briefing_item:weekly:2:def456",
                "feedback_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_briefing_feedback_packet.py "
                    "--item-id briefing_item:weekly:2:def456 --json"
                ),
            },
        ],
        "effect_reconciliation": [
            {
                "effect_id": "effectlog_123",
                "status": "planned",
                "replay_bundle_ref": "replay:publish:1",
                "inspection_command_template": "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_effect_reconciliation.py --effect-id effectlog_123 --json",
            }
        ],
    }

    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            )
        ],
        week_label="2026-05-21",
        review_queues=review_queues,
    )

    assert "## North Star Next Actions" in report
    assert "v3_status.py --actions" in report
    assert "Watch Gates: WATCH (external_feedback_below_standard:0/3" in report
    assert "provider_production_readiness_blocked" in report
    assert "- Publish/review public writeup draft: /tmp/a2a_public_writeup_draft.md" in report
    assert "v3_public_writeup_safety.py --draft-artifact /tmp/a2a_public_writeup_draft.md --json" in report
    assert "v3_prepare_public_writeup_packet.py --slug a2a_manifest_note" in report
    assert "v3_record_public_evidence.py --packet /tmp/publication_packet.json" in report
    assert (
        "- Collect 3 more external feedback events and record it with `external_feedback:a2a_manifest_note:source=<source>`."
        in report
    )
    assert (
        "- Record operator feedback on weekly briefing blind-sample item `briefing_item:weekly:1:abc123` (2 queued)."
        in report
    )
    assert "v3_prepare_briefing_feedback_packet.py --item-id briefing_item:weekly:1:abc123 --json" in report
    assert "v3_prepare_briefing_feedback_packet.py --all --json" in report
    assert (
        "v3_record_briefing_feedback.py --packet /tmp/briefing_feedback_packet.json --button <button> --json" in report
    )
    assert "v3_record_briefing_feedback.py --item-id briefing_item:weekly:1:abc123 --button <button> --json" in report
    assert "- Inspect unresolved effect `effectlog_123` before retrying or reconciling provider state." in report
    assert "- effect status: planned" in report
    assert "- replay bundle: replay:publish:1" in report
    assert (
        "effect inspection command: `PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_effect_reconciliation.py --effect-id effectlog_123 --json`"
        in report
    )
    assert "--publish-manifest <path>" in report
    assert "--provider-state-manifest <path>" in report
    assert "- Unblock provider production readiness: 28 env vars missing across 28 readiness findings." in report
    assert "- env template: /tmp/provider_provisioning.template" in report
    assert (
        "- smallest canary scope: tts (2 missing env vars: MIRA_TTS_ADAPTER_ENDPOINT, MIRA_TTS_ADAPTER_TOKEN)" in report
    )
    assert "v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.template --json" in report
    assert (
        "v3_provider_readiness.py --write-env-template /tmp/provider_provisioning.tts.template --skip-resolvers --require-adapter tts --json"
        in report
    )
    assert "v3_provider_readiness.py --skip-resolvers --require-adapter tts --json" in report
    assert "v3_provider_production_canary.py --provider tts --dry-run --json" in report
    assert "v3_provider_production_canary.py --provider tts --json" in report


def test_weekly_report_feedback_next_action_uses_recorded_public_writeup_slug():
    record = _record(
        artifacts=["/tmp/published.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1",
            "public_writeup_plan:a2a_manifest_note",
        ],
        causal_links=["causal_a2a"],
    )
    review_queues = {
        "public_writeup_review": [
            {
                "draft_artifact": "/tmp/a2a_public_writeup_draft.md",
                "decision": "needs_publication_review",
                "feedback_ref_template": "external_feedback:a2a_manifest_note:source=<source>",
            }
        ]
    }

    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            )
        ],
        week_label="2026-05-21",
        review_queues=review_queues,
    )

    assert (
        "- Collect 3 more external feedback events on the recorded public writeup or through customer discovery, "
        "then record with `external_feedback:v31_green_dot_is_not_evidence:source=<source>` or `customer_discovery:<source>`."
    ) in report
    assert "v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest" in report
    assert "external_feedback:a2a_manifest_note:source=<source>`." not in report


def test_weekly_report_feedback_next_action_uses_followup_queue_stats_and_command():
    record = _record(
        artifacts=["/tmp/published.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1",
        ],
        causal_links=["causal_a2a"],
    )
    review_queues = {
        "public_feedback_followup": [
            {
                "slug": "v31_green_dot_is_not_evidence",
                "comments": "0",
                "likes": "0",
                "restacks": "0",
                "record_feedback_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py "
                    "--slug v31_green_dot_is_not_evidence --feedback-source <source> --json"
                ),
                "record_feedback_from_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_public_feedback.py "
                    "--packet /tmp/feedback_packet.json --feedback-source <source> --json"
                ),
                "feedback_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_public_feedback_packet.py "
                    "--slug v31_green_dot_is_not_evidence --published-url https://example.com/p/1 --json"
                ),
            }
        ],
        "customer_discovery_feedback": [
            {
                "feedback_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py "
                    "--topic a2a_trust_manifest --json"
                ),
                "record_feedback_from_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py "
                    "--packet /tmp/customer_discovery_packet.json --source <source> --insight <insight> --json"
                ),
                "record_feedback_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py "
                    "--source <source> --insight <insight> --json"
                ),
            }
        ],
    }

    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            )
        ],
        week_label="2026-05-21",
        review_queues=review_queues,
    )

    assert (
        "- Collect external feedback on the recorded public writeup `v31_green_dot_is_not_evidence` "
        "(comments=0, likes=0, restacks=0; need 3 more feedback events)."
    ) in report
    assert "v3_prepare_public_feedback_packet.py --slug v31_green_dot_is_not_evidence" in report
    assert "v3_record_public_feedback.py --packet /tmp/feedback_packet.json" in report
    assert "v3_record_public_feedback.py --slug v31_green_dot_is_not_evidence" in report
    assert (
        "Collect parallel customer-discovery feedback while the external-feedback gate still needs 3 more events."
        in report
    )
    assert "v3_record_customer_discovery_feedback.py --packet /tmp/customer_discovery_packet.json" in report
    assert "v3_record_customer_discovery_feedback.py --source <source> --insight <insight> --json" in report


def test_weekly_report_keeps_feedback_next_action_until_three_events():
    record = _record(
        artifacts=["/tmp/published.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1",
            "external_feedback:v31_green_dot_is_not_evidence:source=reader-reply-1",
        ],
        causal_links=["causal_a2a"],
    )

    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            )
        ],
        week_label="2026-05-21",
        review_queues={},
    )

    assert "Watch Gates: WATCH (external_feedback_below_standard:1/3)" in report
    assert "Collect 2 more external feedback events" in report
    assert "v3_prepare_customer_discovery_packet.py --topic a2a_trust_manifest" in report
    assert (
        "v3_record_customer_discovery_feedback.py --packet data/v3/artifacts/customer_discovery_packets/a2a_trust_manifest/6ee9815b4bcb/customer_discovery_packet.json"
        in report
    )


def test_weekly_report_uses_customer_discovery_queue_commands_for_feedback_gap():
    record = _record(
        artifacts=["/tmp/published.md"],
        eval_refs=[
            "strategic:a2a_trust_experiment",
            "tool:a2a_manifest_validator",
            "public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1",
            "external_feedback:v31_green_dot_is_not_evidence:source=reader-reply-1",
        ],
        causal_links=["causal_a2a"],
    )
    review_queues = {
        "customer_discovery_feedback": [
            {
                "feedback_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_prepare_customer_discovery_packet.py "
                    "--topic a2a_trust_manifest --json"
                ),
                "record_feedback_from_packet_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py "
                    "--packet /tmp/customer_discovery_packet.json --source <source> --insight <insight> --json"
                ),
                "record_feedback_command_template": (
                    "PYTHONPATH=lib .venv/bin/python agents/super/cli/v3_record_customer_discovery_feedback.py "
                    "--source <source> --insight <insight> --json"
                ),
            }
        ]
    }

    report = build_weekly_north_star_report(
        [record],
        [],
        [],
        [
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            )
        ],
        week_label="2026-05-21",
        review_queues=review_queues,
    )

    assert "Collect 2 more external feedback events" in report
    assert "v3_record_customer_discovery_feedback.py --packet /tmp/customer_discovery_packet.json" in report
    assert "v3_record_customer_discovery_feedback.py --source <source> --insight <insight> --json" in report


def test_weekly_report_scores_voice_and_briefing_artifacts(tmp_path: Path):
    article_path = tmp_path / "article.md"
    article_path.write_text(
        "# Artifact-First Autonomy\n\n"
        "Mira V3.1 should prove durable behavior through ledger entries, effect log receipts, approval binding, "
        "and causal trace evidence before public publishing. The useful threshold is autonomy with receipts.",
        encoding="utf-8",
    )
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [verified] A2A trust protocol drift (local:a2a-trust)\n"
        "- [observed] Agent memory poisoning incident pattern (local:memory-security)\n",
        encoding="utf-8",
    )
    article = _record(
        pipeline="article_creation",
        artifacts=[str(article_path)],
        causal_links=[],
        record_id="article_1",
    )
    briefing = _record(
        pipeline="intelligence_briefing",
        artifacts=[str(briefing_path)],
        causal_links=[],
        record_id="briefing_1",
    )

    voice = evaluate_voice_stability([article])
    briefing_interest = evaluate_briefing_interest_fit([briefing])
    report = build_weekly_north_star_report([article, briefing], [], [], [], week_label="2026-05-21")

    assert voice.sample_count == 1
    assert voice.voice_score_mean >= 0.70
    assert briefing_interest.sample_count == 1
    assert briefing_interest.precision_at_5 == 1.0
    assert "- voice_samples: 1" in report
    assert "- generic_failure_rate: 0.0000" in report
    assert "- briefing_samples: 1" in report
    assert "- briefing_items_scored: 2" in report
    assert "- precision_at_5: 1.0000" in report
    assert "- action_rate: not measured" in report
    assert "- feedback_coverage_rate: not measured" in report
    assert "Verdict: WATCH" in report


def test_briefing_feedback_buttons_and_blind_sample_feed_eval4(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Durable agent workflow audit trail (local:workflow)\n"
        "- [verified] Macro portfolio risk dashboard signal (local:market)\n"
        "- [observed] Low-signal entertainment item (local:misc)\n",
        encoding="utf-8",
    )
    probe_record = _record(
        pipeline="intelligence_briefing",
        artifacts=[str(briefing_path)],
        causal_links=[],
        record_id="briefing_feedback",
    )
    first_item_id = build_briefing_item_reviews([probe_record])[0].item_id
    feedback_record = _record(
        pipeline="intelligence_briefing",
        artifacts=[str(briefing_path)],
        causal_links=[],
        eval_refs=[
            f"briefing_feedback:item={first_item_id}:button=pursue_research",
            "briefing_feedback:item=2:button=not_useful",
        ],
        record_id="briefing_feedback",
    )

    item_reviews = build_briefing_item_reviews([feedback_record])
    summary = evaluate_briefing_interest_fit([feedback_record])
    blind_sample = build_weekly_blind_sample([feedback_record])
    report = build_weekly_north_star_report([feedback_record], [], [], [], week_label="2026-05-21")

    assert item_reviews[0].user_action == "promoted_to_research"
    assert item_reviews[1].user_action == "dismissed"
    assert summary.feedback_item_count == 2
    assert summary.promoted_item_count == 1
    assert summary.action_rate == 0.3333
    assert summary.dismiss_rate == 0.3333
    assert summary.feedback_coverage_rate == 0.6667
    assert summary.blind_sample_count == 1
    assert len(blind_sample) == 1
    assert "- feedback_items: 2" in report
    assert "- feedback_coverage_rate: 0.6667" in report
    assert "- promoted_items: 1" in report
    assert "- weekly_blind_sample_items: 1" in report


def test_briefing_feedback_parser_accepts_v31_button_aliases(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Agent workflow trace signal (local:workflow)\n"
        "- [verified] Memory security review pattern (local:memory)\n"
        "- [observed] Market dashboard drift signal (local:market)\n"
        "- [inferred] Causal eval threshold issue (local:eval)\n",
        encoding="utf-8",
    )
    record = _record(
        pipeline="intelligence_briefing",
        artifacts=[str(briefing_path)],
        causal_links=[],
        eval_refs=[
            "briefing_feedback:item=1:button=too_obvious",
            "briefing_feedback:item=2:button=surprising",
            "briefing_feedback:item=3:button=wrong",
            "briefing_feedback:item=4:button=follow_up",
        ],
        record_id="briefing_aliases",
    )

    item_reviews = build_briefing_item_reviews([record])

    assert [item.user_action for item in item_reviews] == [
        "dismissed",
        "saved",
        "dismissed",
        "asked_followup",
    ]


def test_record_briefing_feedback_validates_blind_sample_and_updates_eval4(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Agent workflow trace signal (local:workflow)\n"
        "- [verified] Memory security review pattern (local:memory)\n",
        encoding="utf-8",
    )
    briefing = _record(
        pipeline="intelligence_briefing",
        artifacts=[str(briefing_path)],
        causal_links=[],
        record_id="briefing_runtime_feedback",
    )
    default_ledger(tmp_path).append(briefing)
    item_id = build_weekly_blind_sample(default_ledger(tmp_path).list())[0].item_id

    result = record_briefing_feedback(
        root=tmp_path,
        item_id=item_id,
        button="pursue-research",
        notes="Operator wants a deeper research pass.",
    )

    records = default_ledger(tmp_path).list()
    item_reviews = build_briefing_item_reviews(records)
    summary = evaluate_briefing_interest_fit(records)
    manifest = json.loads(result.evidence_artifact.read_text(encoding="utf-8"))

    assert result.record in records
    assert result.eval_ref == f"briefing_feedback:item={item_id}:button=pursue_research"
    assert manifest["action"] == "promoted_to_research"
    assert manifest["notes"] == "Operator wants a deeper research pass."
    assert item_reviews[0].user_action == "promoted_to_research"
    assert summary.feedback_item_count == 1
    assert summary.promoted_item_count == 1


def test_record_briefing_feedback_rejects_placeholder_and_non_sample_items(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n" "- [reported] Agent workflow trace signal (local:workflow)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_runtime_reject",
        )
    )

    import pytest

    with pytest.raises(ValueError, match="item_id"):
        record_briefing_feedback(root=tmp_path, item_id="<item_id>", button="useful")
    with pytest.raises(ValueError, match="button"):
        record_briefing_feedback(root=tmp_path, item_id="briefing_item:missing:1:abc", button="<button>")
    with pytest.raises(ValueError, match="current weekly blind sample"):
        record_briefing_feedback(root=tmp_path, item_id="briefing_item:missing:1:abc", button="useful")


def test_record_briefing_feedback_cli_appends_validated_ref(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n" "- [reported] Agent workflow trace signal (local:workflow)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_cli_feedback",
        )
    )
    item_id = build_weekly_blind_sample(default_ledger(tmp_path).list())[0].item_id

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_briefing_feedback.py",
            "--root",
            str(tmp_path),
            "--item-id",
            item_id,
            "--button",
            "useful",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eval_refs"] == [f"briefing_feedback:item={item_id}:button=useful"]
    assert Path(payload["evidence_artifact"]).exists()


def test_record_briefing_feedback_cli_can_use_packet_metadata(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n" "- [reported] Agent workflow trace signal (local:workflow)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_cli_packet_feedback",
        )
    )
    item_id = build_weekly_blind_sample(default_ledger(tmp_path).list())[0].item_id
    packet = prepare_briefing_feedback_packet(root=tmp_path, item_id=item_id)

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_record_briefing_feedback.py",
            "--root",
            str(tmp_path),
            "--packet",
            str(packet.metadata_artifact),
            "--button",
            "useful",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eval_refs"] == [f"briefing_feedback:item={item_id}:button=useful"]
    assert Path(payload["evidence_artifact"]).exists()


def test_prepare_briefing_feedback_packet_creates_review_bundle(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n" "- [reported] Agent workflow trace signal (local:workflow)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_packet_feedback",
        )
    )
    item_id = build_weekly_blind_sample(default_ledger(tmp_path).list())[0].item_id

    packet = prepare_briefing_feedback_packet(root=tmp_path, item_id=item_id)
    metadata = json.loads(packet.metadata_artifact.read_text(encoding="utf-8"))
    review_text = packet.review_artifact.read_text(encoding="utf-8")

    assert packet.packet_dir.exists()
    assert metadata["item_id"] == item_id
    assert metadata["item_text"].startswith("- [reported] Agent workflow trace signal")
    assert "pursue_research" in metadata["available_buttons"]
    assert "--packet" in metadata["record_feedback_from_packet_command_template"]
    assert packet.record_feedback_from_packet_command == metadata["record_feedback_from_packet_command_template"]
    assert f"--item-id {item_id}" in packet.record_feedback_command
    assert "--button <button>" in packet.record_feedback_command
    assert "Briefing Feedback Review" in review_text
    assert metadata["record_feedback_from_packet_command_template"] in packet.checklist_artifact.read_text(
        encoding="utf-8"
    )
    assert Path(packet.checklist_artifact).exists()


def test_prepare_briefing_feedback_packet_cli_writes_json_payload(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n" "- [reported] Agent workflow trace signal (local:workflow)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_packet_cli",
        )
    )
    item_id = build_weekly_blind_sample(default_ledger(tmp_path).list())[0].item_id

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_briefing_feedback_packet.py",
            "--root",
            str(tmp_path),
            "--item-id",
            item_id,
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["review_artifact"]).exists()
    assert Path(payload["metadata_artifact"]).exists()
    assert "v3_record_briefing_feedback.py" in payload["record_feedback_command"]
    assert "--packet" in payload["record_feedback_from_packet_command"]


def test_prepare_all_briefing_feedback_packets_creates_queue_bundle(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Agent workflow trace signal (local:workflow)\n"
        "- [reported] Trust manifest follow-up (local:a2a)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_packet_all",
        )
    )

    packets = prepare_briefing_feedback_packets(root=tmp_path)

    assert len(packets) == 2
    assert all(packet.review_artifact.exists() for packet in packets)
    assert all("v3_record_briefing_feedback.py" in packet.record_feedback_command for packet in packets)
    assert all("--packet" in packet.record_feedback_from_packet_command for packet in packets)


def test_prepare_briefing_feedback_packet_cli_all_writes_json_payload(tmp_path: Path):
    briefing_path = tmp_path / "briefing.md"
    briefing_path.write_text(
        "# Intelligence Briefing\n\n"
        "- [reported] Agent workflow trace signal (local:workflow)\n"
        "- [reported] Trust manifest follow-up (local:a2a)\n",
        encoding="utf-8",
    )
    default_ledger(tmp_path).append(
        _record(
            pipeline="intelligence_briefing",
            artifacts=[str(briefing_path)],
            causal_links=[],
            record_id="briefing_packet_all_cli",
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_prepare_briefing_feedback_packet.py",
            "--root",
            str(tmp_path),
            "--all",
            "--json",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert len(payload["packets"]) == 2
    assert all(Path(packet["review_artifact"]).exists() for packet in payload["packets"])
    assert all("v3_record_briefing_feedback.py" in packet["record_feedback_command"] for packet in payload["packets"])
    assert all("--packet" in packet["record_feedback_from_packet_command"] for packet in payload["packets"])


def test_weekly_report_shows_actionable_causal_coverage_gaps(tmp_path: Path):
    artifact_path = tmp_path / "artifact.md"
    artifact_path.write_text("artifact", encoding="utf-8")
    artifact_without_causal_link = _record(
        pipeline="article_creation",
        artifacts=[str(artifact_path)],
        causal_links=[],
        record_id="article_gap",
    )
    routine_without_causal_link = _record(
        pipeline="daily_journal",
        causal_links=[],
        record_id="routine_gap",
    )
    failed_without_causal_link = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="task failed",
        causal_links=[],
        record_id="communication_gap",
    )

    report = build_weekly_north_star_report(
        [artifact_without_causal_link, routine_without_causal_link, failed_without_causal_link],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert "## Causal Coverage Gaps" in report
    assert "- article_creation: 1 anchored records without causal links" in report
    assert "- communication: 1 anchored records without causal links" in report
    assert "daily_journal: 1 anchored records without causal links" not in report


def test_weekly_report_excludes_first_run_baselines_from_causal_gaps(tmp_path: Path):
    baseline_path = tmp_path / "baseline.md"
    baseline_path.write_text("baseline", encoding="utf-8")
    baseline = _record(
        pipeline="market_monitor",
        artifacts=[str(baseline_path)],
        causal_links=[],
        record_id="market_baseline",
    )
    baseline = ExperienceRecord(
        id=baseline.id,
        pipeline=baseline.pipeline,
        trigger=baseline.trigger,
        intent=baseline.intent,
        outcome=baseline.outcome,
        delta=MemoryDelta(
            pipeline=baseline.delta.pipeline,
            run_id=baseline.delta.run_id,
            memory_class=baseline.delta.memory_class,
            what_happened=baseline.delta.what_happened,
            what_mattered="first staged market artifact",
            what_changed="Future market_monitor runs can compare decisions against this staged artifact",
            actions=[],
        ),
        causal_links=[],
        confidence=baseline.confidence,
        memory_class=baseline.memory_class,
        artifacts=baseline.artifacts,
        eval_refs=baseline.eval_refs,
        memory_commit_id=baseline.memory_commit_id,
        timestamp=baseline.timestamp,
    )

    report = build_weekly_north_star_report([baseline], [], [], [], week_label="2026-05-21")

    assert "- baseline_artifacts_without_causal_links: 1" in report
    assert "- market_monitor: 1 anchored records without causal links" not in report
    assert "- none detected" in report


def test_weekly_report_excludes_operator_evidence_from_causal_gaps(tmp_path: Path):
    evidence_path = tmp_path / "public_evidence.json"
    evidence_path.write_text("{}", encoding="utf-8")
    operator_evidence = _record(
        pipeline="a2a_trust_experiment",
        trigger="operator_evidence",
        intent="record public writeup evidence",
        artifacts=[str(evidence_path)],
        eval_refs=["public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1"],
        causal_links=[],
        record_id="operator_public_evidence",
    )

    report = build_weekly_north_star_report([operator_evidence], [], [], [], week_label="2026-05-21")

    assert "a2a_trust_experiment: 1 anchored records without causal links" not in report
    assert "- none detected" in report


def test_weekly_report_excludes_operator_evidence_from_new_experiments(tmp_path: Path):
    experiment = _record(
        pipeline="a2a_trust_experiment",
        trigger="manual",
        eval_refs=["strategic:a2a_trust_experiment"],
        record_id="a2a_experiment_run",
    )
    operator_evidence = _record(
        pipeline="a2a_trust_experiment",
        trigger="operator_evidence",
        intent="record public writeup evidence",
        artifacts=[str(tmp_path / "public_evidence.json")],
        eval_refs=["public_writeup:v31_green_dot_is_not_evidence:url=https://example.com/p/1"],
        causal_links=[],
        record_id="a2a_public_evidence_1",
    )

    report = build_weekly_north_star_report(
        [experiment, operator_evidence],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert "- a2a_experiment_run" in report
    assert "- strategic:a2a_trust_experiment" in report
    assert "a2a_public_evidence_1" not in report


def test_weekly_report_separates_failure_memory_capture_from_causal_gaps():
    scarred_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="provider timed out",
        causal_links=[],
        actions=[MemoryAction("create_scar", "scar:communication:provider_timeout", "provider timed out")],
        record_id="scarred_failure",
    )
    signature_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="provider timed out again",
        causal_links=[],
        actions=[
            MemoryAction(
                "update_failure_signature",
                "failure:communication:provider_timeout",
                "provider timed out again",
            )
        ],
        record_id="signature_failure",
    )
    uncaptured_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="uncaptured failure",
        causal_links=[],
        actions=[MemoryAction("update_skill_trace", "skill:communication", "failed")],
        record_id="uncaptured_failure",
    )
    approval_gate = _record(
        pipeline="article_creation",
        outcome="approval_required",
        what_failed="approval required: publish",
        causal_links=[],
        record_id="approval_gate",
    )

    report = build_weekly_north_star_report(
        [scarred_failure, signature_failure, uncaptured_failure, approval_gate],
        [],
        [],
        [],
        week_label="2026-05-21",
    )

    assert "- failure_memory_captured: 2 / 3" in report
    assert "- communication: 1 anchored records without causal links" in report
    assert "article_creation: 1 anchored records without causal links" not in report


def test_weekly_report_counts_v31_eval_and_outcome_records():
    requested_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    approval_event = ApprovalEvent(
        id="approval_1",
        run_id="run_1",
        action_id="a2a_trust_experiment:review",
        action_type="review",
        risk_tier="memory_write",
        requested_at=requested_at,
        resolved_at=requested_at + timedelta(minutes=2),
        decision="approved",
        human_minutes=2.0,
    )
    report = build_weekly_north_star_report(
        [_record(record_id="run_1")],
        [],
        [],
        [],
        approval_events=[approval_event],
        week_label="2026-05-21",
    )

    assert "Eval records: 1" in report
    assert "Outcome records: 1" in report
    assert "Decision records: 1" in report
    assert "Behavioral effects: 1" in report
    assert "Approval events: 1" in report
    assert "Run evidence bundles: 1" in report


def test_operational_bundle_derives_v31_decisions_and_behavioral_effects():
    record = _record(
        pipeline="podcast_production",
        causal_links=["causal_tts_fallback"],
        actions=[MemoryAction("route_tool", "tts:fallback", "used fallback TTS")],
        record_id="podcast_1",
    )
    causal_evidence = CausalEvidence(
        "scar:podcast_production:tts_timeout",
        "L4",
        "without this scar the pipeline would have tried the failing TTS provider",
        evidence_id="causal_tts_fallback",
        ablation_ref="ablation_tts_1",
    )
    requested_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    approval_event = ApprovalEvent(
        id="approval_tts_1",
        run_id=record.id,
        action_id="podcast_production:synthesize_tts",
        action_type="synthesize_tts",
        risk_tier="external_provider",
        requested_at=requested_at,
        resolved_at=requested_at + timedelta(minutes=3),
        decision="approved",
        human_minutes=3.0,
    )

    bundle = build_operational_eval_bundle([record], [], [], [causal_evidence], [approval_event])

    assert len(bundle.decision_records) == 1
    assert bundle.decision_records[0].decision_id == "decision:podcast_1:ledger_outcome"
    assert bundle.decision_records[0].memory_trace_ids[0].startswith("memtrace:podcast_1:")
    assert len(bundle.behavioral_effects) == 1
    assert bundle.behavioral_effects[0].decision_id == bundle.decision_records[0].decision_id
    assert bundle.behavioral_effects[0].memory_id == "scar:podcast_production:tts_timeout"
    assert bundle.behavioral_effects[0].effect_type == "changed_tool"
    assert bundle.approval_events == [approval_event]
    assert len(bundle.run_evidence_bundles) == 1
    run_bundle = bundle.run_evidence_bundles[0]
    assert run_bundle.run_id == "podcast_1"
    assert run_bundle.pipeline == "podcast_production"
    assert run_bundle.workflow == "workflow:podcast_production"
    assert run_bundle.intent == "test"
    assert run_bundle.expected_outcome == "completed"
    assert run_bundle.actual_outcome == "completed"
    assert run_bundle.snapshot_id == "snapshot:podcast_1"
    assert run_bundle.retrieved_memory_ids == ["causal_tts_fallback"]
    assert run_bundle.included_memory_ids == ["causal_tts_fallback"]
    assert run_bundle.decision_records == bundle.decision_records
    assert run_bundle.behavioral_effects == bundle.behavioral_effects
    assert run_bundle.approval_events == [approval_event]
    assert run_bundle.memory_delta_proposal_id == record.memory_delta_proposal_id
    assert run_bundle.memory_commit_id == "commit_1"
    assert run_bundle.causal_links == ["causal_tts_fallback"]


def test_weekly_report_pairs_approval_burden_with_safety_and_throughput():
    requested_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    approval_events = [
        ApprovalEvent(
            id="approval_1",
            run_id="approval_gate",
            action_id="article_creation:publish_substack",
            action_type="publish_substack",
            risk_tier="publish_public",
            requested_at=requested_at,
            resolved_at=requested_at + timedelta(minutes=4),
            decision="approved",
            human_minutes=4.0,
        ),
        ApprovalEvent(
            id="approval_2",
            run_id="approval_gate_2",
            action_id="article_creation:publish_substack",
            action_type="publish_substack",
            risk_tier="publish_public",
            requested_at=requested_at + timedelta(hours=1),
            resolved_at=requested_at + timedelta(hours=1, minutes=8),
            decision="rejected",
            human_minutes=8.0,
        ),
    ]
    approval_gate = _record(
        pipeline="article_creation",
        outcome="approval_required",
        what_failed="approval required: publish",
        causal_links=[],
        record_id="approval_gate",
    )
    preflight_block = _record(
        pipeline="podcast_production",
        outcome="blocked_preflight",
        what_failed="preflight blocked: missing capabilities",
        causal_links=[],
        record_id="preflight_block",
    )
    approved_publish = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id="approval_gate",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
        replay_bundle_ref="replay:bundle:publish",
        timestamp=requested_at,
    )
    failed_social_post = EffectLogEntry(
        idempotency_key="post:social:1",
        run_id="social_failed",
        pipeline="social_proactive",
        action="post_social",
        target="post-1",
        status="failed",
        timestamp=requested_at,
    )
    rollback = EffectLogEntry(
        idempotency_key="deploy:rollback:1",
        run_id="rollback_1",
        pipeline="self_evolution",
        action="deployment_rollback",
        target="production",
        status="compensated",
        timestamp=requested_at,
    )

    report = build_weekly_north_star_report(
        [approval_gate, preflight_block],
        [],
        [approved_publish, failed_social_post, rollback],
        [],
        approval_events=approval_events,
        week_label="2026-05-21",
    )

    assert "- approval_requests_per_100_runs: 100.00" in report
    assert "- approvals_per_100_side_effects: 33.33" in report
    assert "- median_approval_time_minutes: 6.0000" in report
    assert "- human_minutes_per_week: 12.0000" in report
    assert "- rejection_rate: 0.5000" in report
    assert "- side_effects_completed_per_week: 1" in report
    assert "- publication_side_effects_completed_per_week: 1" in report
    assert "- side_effect_incident_rate_per_100: 33.33" in report
    assert "- near_miss_rate_per_100_side_effects: 66.67" in report
    assert "- unreplayable_action_rate: 0.5000" in report
    assert "- unreplayable_actions: 1 / 2" in report
    assert "- replay_bundle_validity: 1.0000" in report
    assert "- invalid_replay_bundles: 0 / 1" in report
    assert "- rollback_count: 1" in report


def test_experiment_registry_derives_v31_eval5_metrics():
    tracked = _record(
        pipeline="self_evolution",
        record_id="tracked_change",
        eval_refs=["self_evolution:experiment_record", "self_evolution:production_promotion_staged"],
        actions=[
            MemoryAction(
                "form_hypothesis",
                "hypothesis:self_evolution_pack_coverage",
                "Workflow-pack canaries reduce implementation drift.",
                metadata={
                    "mismatch_cluster_id": "self_evolution:workflow_pack_coverage",
                    "intervention": "stage a guarded production promotion after canary confirmation",
                    "target_pipeline": "self_evolution",
                    "target_metric": "self_evolution_experiment_coverage",
                    "baseline_window": "prior self_evolution runs",
                    "test_window": "current canary window",
                    "min_n": "3",
                    "expected_effect": "production promotion stays auditable",
                    "risk_level": "high",
                    "rollback_plan": "revert production to the recorded rollback ref",
                },
            )
        ],
    )
    effect = EffectLogEntry(
        idempotency_key="self_evolution:promote:tracked",
        run_id="tracked_change",
        pipeline="self_evolution",
        action="promote_production",
        target="production",
        status="planned",
        approval_token_id="grant_1",
        preview_hash="preview-sha256",
    )

    summary = build_experiment_registry([tracked], [effect])

    assert len(summary.experiments) == 1
    assert summary.self_evolution_change_count == 1
    assert summary.experiment_coverage == 1.0
    assert summary.testability_rate == 1.0
    assert summary.untracked_change_count == 0
    assert summary.auto_change_without_rollback_count == 0
    assert summary.high_risk_without_approval_count == 0
    assert summary.eval_threshold_policy_violation_count == 0


def test_eval_threshold_change_governance_blocks_anecdotes_and_large_deltas():
    one_anecdote = govern_eval_threshold_change(
        current_threshold=0.70,
        proposed_threshold=0.71,
        sample_count=1,
    )
    large_delta = govern_eval_threshold_change(
        current_threshold=0.70,
        proposed_threshold=0.78,
        sample_count=30,
    )
    regression = govern_eval_threshold_change(
        current_threshold=0.70,
        proposed_threshold=0.72,
        sample_count=30,
        golden_set_regression=True,
    )

    assert one_anecdote.allowed is False
    assert "insufficient_evidence" in one_anecdote.reasons
    assert large_delta.allowed is False
    assert large_delta.bounded_threshold == 0.73
    assert "delta_exceeds_0.03" in large_delta.reasons
    assert regression.allowed is False
    assert "golden_set_regression" in regression.reasons


def test_eval_threshold_change_governance_requires_approval_for_public_effects():
    public_without_approval = govern_eval_threshold_change(
        current_threshold=0.70,
        proposed_threshold=0.72,
        sample_count=30,
        affects_publish_send_post=True,
    )
    approved_public = govern_eval_threshold_change(
        current_threshold=0.70,
        proposed_threshold=0.72,
        sample_count=1,
        affects_publish_send_post=True,
        approval_token_id="approval:threshold:1",
    )

    assert public_without_approval.allowed is False
    assert public_without_approval.requires_human_approval is True
    assert "human_approval_required" in public_without_approval.reasons
    assert approved_public.allowed is True
    assert approved_public.auto_allowed is False
    assert "human_approved" in approved_public.reasons


def test_experiment_registry_flags_eval_threshold_policy_violations():
    violation = _record(
        pipeline="self_evolution",
        record_id="threshold_violation",
        eval_refs=["self_evolution:eval_threshold_change"],
        actions=[
            MemoryAction(
                "form_hypothesis",
                "hypothesis:threshold",
                "One anecdote should not lower a threshold.",
                metadata={
                    "change_type": "eval_threshold_change",
                    "current_threshold": "0.70",
                    "proposed_threshold": "0.72",
                    "min_n": "1",
                    "golden_set_regression": "false",
                    "rollback_plan": "restore threshold 0.70",
                },
            )
        ],
    )
    approved = _record(
        pipeline="self_evolution",
        record_id="threshold_approved",
        eval_refs=["self_evolution:eval_threshold_change"],
        actions=[
            MemoryAction(
                "form_hypothesis",
                "hypothesis:threshold",
                "Approved public threshold change.",
                metadata={
                    "change_type": "eval_threshold_change",
                    "current_threshold": "0.70",
                    "proposed_threshold": "0.72",
                    "min_n": "1",
                    "affects_publish_send_post": "true",
                    "approval_token_id": "approval:threshold:1",
                    "rollback_plan": "restore threshold 0.70",
                },
            )
        ],
    )

    violation_summary = build_experiment_registry([violation], [])
    approved_summary = build_experiment_registry([approved], [])

    assert violation_summary.eval_threshold_change_count == 1
    assert violation_summary.eval_threshold_policy_violation_count == 1
    assert approved_summary.eval_threshold_change_count == 1
    assert approved_summary.eval_threshold_policy_violation_count == 0


def test_weekly_report_fails_eval5_for_untracked_self_evolution_change():
    untracked = _record(
        pipeline="self_evolution",
        record_id="untracked_change",
        eval_refs=["self_evolution:production_promotion_staged"],
        actions=[MemoryAction("update_skill_trace", "skill:self_evolution", "staged promotion")],
    )

    report = build_weekly_north_star_report([untracked], [], [], [], week_label="2026-05-21")

    assert "- self_evolution_change_count: 1" in report
    assert "- experiment_coverage: 0.0000" in report
    assert "- untracked_change_count: 1" in report
    assert "- high_risk_without_approval_count: 0" in report
    section = report.split("## 5. Self-Evolution Experiments", maxsplit=1)[1].split("## 6.", maxsplit=1)[0]
    assert "Verdict: FAIL" in section


def test_weekly_report_measures_edited_approval_events():
    requested_at = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    report = build_weekly_north_star_report(
        [_record(record_id="approval_edit_run")],
        [],
        [],
        [],
        approval_events=[
            ApprovalEvent(
                id="approval_edited",
                run_id="approval_edit_run",
                action_id="article_creation:publish_substack",
                action_type="publish_substack",
                risk_tier="publish_public",
                requested_at=requested_at,
                resolved_at=requested_at + timedelta(minutes=3),
                decision="edited",
                human_minutes=3.0,
            )
        ],
        week_label="2026-05-21",
    )

    assert "- median_approval_time_minutes: 3.0000" in report
    assert "- human_minutes_per_week: 3.0000" in report
    assert "- edit_after_approval_rate: 1.0000" in report
    assert "- rejection_rate: 0.0000" in report


def test_build_incident_events_derives_v31_shape_from_effects_and_records():
    failed_publish = EffectLogEntry(
        idempotency_key="publish:article:failed",
        run_id="publish_run",
        pipeline="article_creation",
        action="publish_substack",
        target="article",
        status="failed",
        detail="provider returned 503",
        approval_token_id="grant_1",
    )
    unapproved_deploy = EffectLogEntry(
        idempotency_key="deploy:production:1",
        run_id="deploy_run",
        pipeline="self_evolution",
        action="deploy_production",
        target="production",
        status="succeeded",
    )
    failed_record = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="handler load failed: module import error",
        causal_links=[],
        record_id="handler_failure",
    )

    events = build_incident_events([failed_record], [failed_publish, unapproved_deploy])

    assert all(isinstance(event, IncidentEvent) for event in events)
    assert {
        (event.run_id, event.severity, event.action_id, event.was_approved, event.preventable) for event in events
    } == {
        ("publish_run", "high", "publish:article:failed", True, False),
        ("deploy_run", "high", "deploy:production:1", False, True),
        ("handler_failure", "medium", None, None, True),
    }

    bundle = build_operational_eval_bundle([failed_record], [], [failed_publish, unapproved_deploy])
    assert bundle.incident_events == events


def test_weekly_report_reports_incident_events_severity_and_preventability():
    failed_social_post = EffectLogEntry(
        idempotency_key="post:social:failed",
        run_id="social_failed",
        pipeline="social_proactive",
        action="post_social",
        target="post-1",
        status="failed",
        detail="provider returned 503",
        timestamp=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc),
    )
    preflight_failure = _record(
        pipeline="communication",
        outcome="failed",
        what_failed="preflight failed: missing auth token",
        causal_links=[],
        record_id="preflight_failure",
    )

    report = build_weekly_north_star_report(
        [preflight_failure],
        [],
        [failed_social_post],
        [],
        week_label="2026-05-21",
    )

    assert "- incident_events: 2" in report
    assert "- incident_rate_per_100_side_effects: 200.00" in report
    assert "- high_or_critical_incidents: 1" in report
    assert "- preventable_incidents: 1" in report
    assert "## Incident Breakdown" in report
    assert "- provider returned 503 failed post_social post-1: 1 events, high_or_critical=1, preventable=0" in report
    assert "- preflight_failed: 1 events, high_or_critical=0, preventable=1" in report


def test_weekly_report_groups_failed_records_by_actionable_mode():
    records = [
        _record(
            pipeline="communication",
            outcome="failed",
            what_failed="preflight failed: missing auth token",
            causal_links=[],
            record_id="communication_preflight",
        ),
        _record(
            pipeline="communication",
            outcome="failed",
            what_failed="handler load failed: module import error",
            causal_links=[],
            record_id="communication_handler",
        ),
        _record(
            pipeline="communication",
            outcome="failed",
            what_failed="handler load failed: module import error",
            causal_links=[],
            record_id="communication_handler_2",
        ),
    ]

    report = build_weekly_north_star_report(records, [], [], [], week_label="2026-05-21")

    assert "## Failure Mode Breakdown" in report
    assert "- handler_load_failed: 2 records" in report
    assert "- preflight_failed: 1 records" in report


def test_weekly_report_excludes_synthetic_task_fixture_records_from_live_scorecard():
    fixture_failure = _record(
        pipeline="communication",
        trigger="task_result",
        intent="complete task task126",
        outcome="failed",
        what_failed="general produced no verifiable output: generic_request: output.md missing or below 1 bytes.",
        causal_links=[],
        record_id="fixture_failure",
    )
    live_failure = _record(
        pipeline="communication",
        trigger="task_result",
        intent="complete task live_1",
        outcome="failed",
        what_failed="provider timed out",
        causal_links=[],
        record_id="live_failure",
    )

    report = build_weekly_north_star_report([fixture_failure, live_failure], [], [], [], week_label="2026-05-21")

    assert "Synthetic task fixture records excluded: 1 current / 0 previous" in report
    assert "- failed_or_failure_delta_runs: 1 / 1" in report
    assert "- incident_events: 1" in report
    assert "- provider_unavailable: 1 events" in report
    assert "no_verifiable_output: 1 events" not in report


def test_weekly_report_can_limit_to_first_stage_eval_scope():
    timestamp = datetime(2026, 5, 21, tzinfo=timezone.utc)
    article = _record(pipeline="article_creation", record_id="article_1", causal_links=[], timestamp=timestamp)
    daily = _record(pipeline="daily_journal", record_id="journal_1", causal_links=[], timestamp=timestamp)

    report = build_weekly_north_star_report(
        [article, daily],
        [],
        [],
        [],
        week_label="2026-05-21",
        first_stage_scope=True,
    )

    assert "Eval scope: first-stage workflows" in report
    assert "Current records: 1" in report
    assert "First-stage scope records excluded: 1 current / 0 previous" in report
    assert "- article_or_social_records: 1" in report
    assert "- traced_records: 1 / 1" in report


def test_north_star_report_cli_writes_weekly_artifact(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "data/v3/experience_ledger.jsonl")
    ledger.append(
        _record(
            artifacts=["/tmp/a2a.md"],
            eval_refs=["strategic:a2a_trust_experiment"],
            causal_links=["causal_a2a"],
        )
    )
    causal_log = tmp_path / "data/v3/causal_evidence.jsonl"
    causal_log.parent.mkdir(parents=True, exist_ok=True)
    causal_log.write_text(
        json.dumps(
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            ).to_dict(),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_north_star_report.py",
            "--root",
            str(tmp_path),
            "--week",
            "2026-05-21",
            "--first-stage-scope",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    report_path = Path(json.loads(result.stdout)["report_path"])

    assert report_path.exists()
    assert report_path.name == "north-star-week-2026-05-21.md"
    report_text = report_path.read_text(encoding="utf-8")
    assert "Strategic Score:" in report_text
    assert "Eval scope: first-stage workflows" in report_text
    assert "## North Star Next Actions" in report_text


def test_north_star_report_cli_accepts_output_dir(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "data/v3/experience_ledger.jsonl")
    ledger.append(
        _record(
            artifacts=["/tmp/a2a.md"],
            eval_refs=["strategic:a2a_trust_experiment"],
            causal_links=["causal_a2a"],
        )
    )
    causal_log = tmp_path / "data/v3/causal_evidence.jsonl"
    causal_log.parent.mkdir(parents=True, exist_ok=True)
    causal_log.write_text(
        json.dumps(
            CausalEvidence(
                "memory:a2a",
                "L4",
                "ablation confirmed",
                evidence_id="causal_a2a",
                ablation_ref="ablation_a2a",
            ).to_dict(),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "custom_reports"

    result = subprocess.run(
        [
            sys.executable,
            "agents/super/cli/v3_north_star_report.py",
            "--root",
            str(tmp_path),
            "--week",
            "2026-05-21",
            "--output-dir",
            str(output_dir),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    report_path = Path(json.loads(result.stdout)["report_path"])

    assert report_path.parent == output_dir
    assert report_path.exists()
    assert report_path.name == "north-star-week-2026-05-21.md"


def test_causal_trace_builder_scores_important_behavior_completeness():
    record = _record(
        pipeline="article_creation",
        artifacts=["/tmp/article.md"],
        causal_links=["causal_article"],
        eval_refs=["article:voice"],
    )
    approved_effect = EffectLogEntry(
        idempotency_key="publish:article:1",
        run_id=record.id,
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        status="succeeded",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
    )
    traces = build_causal_traces([record], [approved_effect])

    assert len(traces) == 1
    assert traces[0].behavior_type == "publish_public"
    assert traces[0].approval_ref == "grant_1"
    assert traces[0].effect_ref == approved_effect.effect_id
    assert traces[0].memory_refs == ["causal_article"]
    assert traces[0].completeness_score == 1.0
