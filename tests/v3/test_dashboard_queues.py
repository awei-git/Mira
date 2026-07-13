from pathlib import Path

from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalRequest, ApprovalStore
from mira.kernel import ExperienceLedger, ExperienceRecord, MemoryAction, MemoryDelta, MemoryKernel
from mira.kernel.commit import MemoryCommitLog, SecurityGateway
from mira.web.dashboard import build_dashboard_snapshot


def test_dashboard_exposes_approval_capacity_and_effect_reconciliation_queue(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    approvals.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish public article",
            run_id="run_1",
            preview_hash="approval-preview",
        )
    )
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.plan(
        idempotency_key="publish:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
        preview_hash="preview-sha256",
        approval_token_id="grant_1",
        replay_bundle_ref="replay:publish:1",
    )
    effects.mark_unknown("publish:1")

    snapshot = build_dashboard_snapshot(
        MemoryKernel(),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
        approval_store=approvals,
    )

    assert snapshot.approval_capacity["pending"] == 1
    assert snapshot.approval_capacity["daily_budget_minutes"] == 15
    assert snapshot.approval_capacity["auto_pause_noncritical"] is False
    assert snapshot.review_queues["approval"][0]["request_id"]
    assert snapshot.review_queues["approval"][0]["preview_hash"] == "approval-preview"
    assert snapshot.review_queues["approval"][0]["expires_at"]
    assert snapshot.review_queues["approval"][0]["rollback"]
    assert snapshot.review_queues["approval"][0]["evidence"] == "publish public article"
    assert snapshot.review_queues["approval"][0]["decision"] == "pending"
    assert snapshot.review_queues["approval"][0]["why_now"]
    assert snapshot.review_queues["approval"][0]["what_will_change"]
    assert snapshot.review_queues["approval"][0]["what_can_go_wrong"]
    assert snapshot.review_queues["effect_reconciliation"][0]["status"] == "unknown"
    assert snapshot.review_queues["effect_reconciliation"][0]["preview_hash"] == "preview-sha256"
    assert snapshot.review_queues["effect_reconciliation"][0]["approval_token_id"] == "grant_1"
    assert snapshot.review_queues["effect_reconciliation"][0]["replay_bundle_ref"] == "replay:publish:1"
    assert "external_ref" in snapshot.review_queues["effect_reconciliation"][0]
    assert "reconciliation_ref" in snapshot.review_queues["effect_reconciliation"][0]
    assert (
        "v3_effect_reconciliation.py --effect-id"
        in snapshot.review_queues["effect_reconciliation"][0]["inspection_command_template"]
    )
    assert snapshot.review_queues["effect_reconciliation"][0]["why_now"]
    assert snapshot.review_queues["effect_reconciliation"][0]["what_will_change"]
    assert snapshot.review_queues["effect_reconciliation"][0]["evidence"]
    assert snapshot.review_queues["effect_reconciliation"][0]["what_can_go_wrong"]
    assert snapshot.review_queues["effect_reconciliation"][0]["rollback"]
    assert "no_a2a_trust_experiment" in snapshot.strategic_scorecard["hard_gate_failures"]


def test_dashboard_exposes_low_risk_approval_digest(tmp_path: Path):
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    approvals.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish public article",
            run_id="run_digest_1",
            preview_hash="preview-one",
        )
    )
    approvals.request(
        ApprovalRequest(
            action="post_social",
            risk="publish_public",
            scope="social_proactive",
            reason="post public note",
            run_id="run_digest_2",
            preview_hash="preview-two",
        )
    )
    approvals.request(
        ApprovalRequest(
            action="health_write",
            risk="health_external",
            scope="health_wellness",
            reason="write external health state",
            run_id="run_digest_3",
            preview_hash="preview-health",
        )
    )

    snapshot = build_dashboard_snapshot(
        MemoryKernel(),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        approval_store=approvals,
    )

    digest = snapshot.review_queues["approval_digest"][0]
    assert digest["request_count"] == "2"
    assert digest["risks"] == "publish_public"
    assert "preview-one" in digest["preview_hashes"]
    assert "preview-two" in digest["preview_hashes"]
    assert "preview-health" not in digest["preview_hashes"]
    assert digest["decision"] == "batch_review_only"
    assert digest["why_now"]
    assert digest["what_will_change"]
    assert digest["evidence"]
    assert digest["what_can_go_wrong"]
    assert digest["rollback"]


def test_dashboard_memory_queue_exposes_structured_gateway_finding(tmp_path: Path):
    commits = MemoryCommitLog(tmp_path / "commits.jsonl")
    delta = MemoryDelta(
        pipeline="communication",
        run_id="run_1",
        memory_class="operational",
        what_happened="processed message",
        what_mattered="untrusted memory proposal",
        what_changed="gateway should quarantine it",
        actions=[
            MemoryAction(
                "update_relationship",
                "relationship:wa",
                "WA does not want long-form architecture reviews.",
                metadata={"evidence_ref": "message_1"},
            )
        ],
        trust_tier="observed",
    )
    commits.append(SecurityGateway(existing_memory=["WA wants long-form architecture reviews."]).validate(delta))

    snapshot = build_dashboard_snapshot(
        MemoryKernel(),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        commit_log=commits,
    )

    item = snapshot.review_queues["memory_commit"][0]
    assert item["status"] == "requires_human"
    assert item["finding_type"] == "contradiction"
    assert item["severity"] == "high"
    assert item["source_trust"] == "observed"
    assert item["memory_class"] == "operational"
    assert item["risk_level"] == "low"
    assert item["privacy_tier"] == "normal"
    assert item["evidence_refs"] == "message_1"
    assert item["contradictions"] == "new memory appears to contradict existing memory"
    assert item["available_decisions"] == "allow, reject, quarantine, edit, merge"
    assert item["why_now"]
    assert item["what_will_change"]
    assert item["evidence"]
    assert item["what_can_go_wrong"]
    assert item["rollback"]


def test_dashboard_does_not_route_approval_gates_to_incident_dlq(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    delta = MemoryDelta(
        pipeline="article_creation",
        run_id="run_approval",
        memory_class="creative",
        what_happened="approval gate reached",
        what_mattered="public publish requires explicit approval",
        what_changed="publish waits for approval",
        what_failed="approval required: approval_1",
        actions=[],
    )
    ledger.append(
        ExperienceRecord(
            id="run_approval",
            pipeline="article_creation",
            trigger="manual",
            intent="publish",
            outcome="approval_required",
            delta=delta,
            causal_links=[],
            confidence=0.4,
            memory_class="creative",
        )
    )

    snapshot = build_dashboard_snapshot(MemoryKernel(), ledger)

    assert snapshot.review_queues["incident_dlq"] == []


def test_dashboard_incident_queue_answers_review_questions(tmp_path: Path):
    ledger = ExperienceLedger(tmp_path / "ledger.jsonl")
    delta = MemoryDelta(
        pipeline="communication",
        run_id="run_failed",
        memory_class="operational",
        what_happened="task failed",
        what_mattered="handler failed before output",
        what_changed="incident review should triage root cause",
        what_failed="handler load failed: module import error",
        actions=[],
    )
    ledger.append(
        ExperienceRecord(
            id="run_failed",
            pipeline="communication",
            trigger="manual",
            intent="handle message",
            outcome="failed",
            delta=delta,
            causal_links=[],
            confidence=0.2,
            memory_class="operational",
        )
    )

    snapshot = build_dashboard_snapshot(MemoryKernel(), ledger)
    item = snapshot.review_queues["incident_dlq"][0]

    assert item["run_id"] == "run_failed"
    assert item["evidence"] == "handler load failed: module import error"
    assert item["why_now"]
    assert item["what_will_change"]
    assert item["what_can_go_wrong"]
    assert item["rollback"]
