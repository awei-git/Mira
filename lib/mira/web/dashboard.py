"""Data model for the V3 monitor/config dashboard."""

from __future__ import annotations

from dataclasses import dataclass

from mira.engine.effect_log import OPEN_STATUSES, EffectLog
from mira.engine.risk_gate import ApprovalStore
from mira.evals import build_operational_eval_bundle, build_strategic_scorecard
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.ledger import ExperienceLedger
from mira.kernel.schema import MemoryKernel
from mira.pipelines import PIPELINE_CATALOG
from mira.policies.catalog import HARD_POLICY_NAMES, SOFT_POLICY_SPECS


@dataclass(frozen=True)
class DashboardSnapshot:
    active_pipelines: list[str]
    scars: list[str]
    active_hypotheses: list[str]
    skill_traces: dict[str, float]
    recent_experience_ids: list[str]
    hard_policy_count: int
    soft_policy_count: int
    review_queues: dict[str, list[dict[str, str]]]
    effect_log_ids: list[str]
    approval_capacity: dict[str, int]
    operational_scorecard: dict[str, object]
    strategic_scorecard: dict[str, object]


def build_dashboard_snapshot(
    kernel: MemoryKernel,
    ledger: ExperienceLedger,
    commit_log: MemoryCommitLog | None = None,
    effect_log: EffectLog | None = None,
    approval_store: ApprovalStore | None = None,
) -> DashboardSnapshot:
    all_records = ledger.list(limit=500)
    recent = all_records[-20:]
    commits = commit_log.list(limit=50) if commit_log else []
    effects = effect_log.list(limit=20) if effect_log else []
    pending_approvals = approval_store.list_requests(status="pending") if approval_store else []
    memory_queue = [
        {
            "commit_id": commit.commit_id,
            "proposal_id": commit.proposal_id,
            "pipeline": commit.pipeline,
            "status": commit.status,
            "reason": "; ".join(f.reason for f in commit.findings),
        }
        for commit in commits
        if commit.status in {"quarantined", "requires_human", "rejected"}
    ]
    incident_queue = [
        {
            "run_id": record.id,
            "pipeline": record.pipeline,
            "status": record.outcome,
            "proposal_id": record.memory_delta_proposal_id,
        }
        for record in recent
        if record.outcome == "failed" or record.delta.what_failed
    ]
    latest_effects = {effect.idempotency_key: effect for effect in effects}
    effect_queue = [
        {
            "effect_id": effect.effect_id,
            "pipeline": effect.pipeline,
            "action": effect.action,
            "target": effect.target,
            "status": effect.status,
            "idempotency_key": effect.idempotency_key,
        }
        for effect in latest_effects.values()
        if effect.status in OPEN_STATUSES
    ]
    approval_queue = [
        {
            "request_id": request.request_id,
            "pipeline": request.scope,
            "action": request.action,
            "risk": request.risk,
            "reason": request.reason,
        }
        for request in pending_approvals
    ]
    operational = build_operational_eval_bundle(all_records, commits, effects).scorecard
    strategic = build_strategic_scorecard(all_records)
    return DashboardSnapshot(
        active_pipelines=sorted(PIPELINE_CATALOG),
        scars=[scar.scar_id for scar in kernel.scars],
        active_hypotheses=[h.hypothesis_id for h in kernel.pending_hypotheses if h.status == "testing"],
        skill_traces={trace.skill_name: trace.success_rate for trace in kernel.skill_traces},
        recent_experience_ids=[record.id for record in recent],
        hard_policy_count=sum(len(names) for names in HARD_POLICY_NAMES.values()),
        soft_policy_count=len(SOFT_POLICY_SPECS),
        review_queues={
            "approval": approval_queue,
            "memory_commit": memory_queue,
            "experiment": [
                {
                    "hypothesis_id": h.hypothesis_id,
                    "pipeline": h.test_pipeline,
                    "status": h.status,
                    "claim": h.claim,
                }
                for h in kernel.pending_hypotheses
            ],
            "incident_dlq": incident_queue,
            "effect_reconciliation": effect_queue,
        },
        effect_log_ids=[entry.effect_id for entry in effects],
        approval_capacity={
            "pending": len(pending_approvals),
            "budget": 10,
            "remaining": max(0, 10 - len(pending_approvals)),
        },
        operational_scorecard={
            "score": operational.score,
            "hard_gate_failures": operational.hard_gate_failures,
        },
        strategic_scorecard={
            "score": strategic.score,
            "hard_gate_failures": strategic.hard_gate_failures,
            "a2a_experiments_completed": strategic.a2a_experiments_completed,
            "reproducible_artifacts": strategic.reproducible_artifacts,
            "tool_prototypes": strategic.tool_prototypes,
            "public_feedback_items": strategic.public_feedback_items,
        },
    )
