from pathlib import Path

from mira.engine.effect_log import EffectLog
from mira.engine.risk_gate import ApprovalRequest, ApprovalStore
from mira.kernel import ExperienceLedger, MemoryKernel
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
        )
    )
    effects = EffectLog(tmp_path / "effects.jsonl")
    effects.plan(
        idempotency_key="publish:1",
        run_id="run_1",
        pipeline="article_creation",
        action="publish_substack",
        target="article-1",
    )
    effects.mark_unknown("publish:1")

    snapshot = build_dashboard_snapshot(
        MemoryKernel(),
        ExperienceLedger(tmp_path / "ledger.jsonl"),
        effect_log=effects,
        approval_store=approvals,
    )

    assert snapshot.approval_capacity["pending"] == 1
    assert snapshot.review_queues["approval"][0]["request_id"]
    assert snapshot.review_queues["effect_reconciliation"][0]["status"] == "unknown"
    assert "no_a2a_trust_experiment" in snapshot.strategic_scorecard["hard_gate_failures"]
