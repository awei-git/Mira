from pathlib import Path

from mira.engine.risk_gate import ApprovalRequest, ApprovalStore


def test_approval_store_tracks_pending_and_granted_requests(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    request = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish public artifact",
            run_id="run_1",
        )
    )

    assert store.pending(action="publish_substack", risk="publish_public", scope="article_creation", run_id="run_1")

    grant = store.grant(request.request_id, granted_by="wa")

    assert store.find_grant(action="publish_substack", risk="publish_public", scope="article_creation") == grant
    assert store.list_requests(status="pending") == []
    assert store.list_requests(status="approved")[0].request_id == request.request_id


def test_approval_store_deduplicates_pending_request(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    first = store.request(
        ApprovalRequest(
            action="write_config",
            risk="code_config",
            scope="self_evolution",
            reason="change config",
            run_id="run_2",
        )
    )
    second = store.request(
        ApprovalRequest(
            action="write_config",
            risk="code_config",
            scope="self_evolution",
            reason="same change",
            run_id="run_2",
        )
    )

    assert second.request_id == first.request_id
    assert len(store.list_requests(status="pending")) == 1
