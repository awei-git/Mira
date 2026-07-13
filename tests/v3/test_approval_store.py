from datetime import datetime, timedelta, timezone
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
            preview_hash="preview-sha256",
        )
    )

    assert store.pending(action="publish_substack", risk="publish_public", scope="article_creation", run_id="run_1")

    grant = store.grant(request.request_id, granted_by="wa")

    assert grant.preview_hash == "preview-sha256"
    assert (
        store.find_grant(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            preview_hash="preview-sha256",
        )
        == grant
    )
    assert (
        store.find_grant(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            preview_hash="changed-preview",
        )
        is None
    )
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


def test_approval_store_keeps_distinct_pending_requests_for_distinct_previews(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    first = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish draft one",
            run_id="run_3",
            preview_hash="preview-one",
        )
    )
    second = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish draft two",
            run_id="run_3",
            preview_hash="preview-two",
        )
    )

    assert second.request_id != first.request_id
    assert len(store.list_requests(status="pending")) == 2


def test_approval_store_exposes_v31_approval_events(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    approved_request = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish draft",
            run_id="run_approved",
            preview_hash="preview-sha256",
        )
    )
    denied_request = store.request(
        ApprovalRequest(
            action="post_social",
            risk="publish_public",
            scope="social_proactive",
            reason="post draft",
            run_id="run_denied",
        )
    )

    store.grant(approved_request.request_id, granted_by="wa")
    denied_event = store.deny(denied_request.request_id, decided_by="wa", reason="needs edit")
    events = {event.id: event for event in store.list_events()}

    assert events[approved_request.request_id].decision == "approved"
    assert events[approved_request.request_id].resolved_at is not None
    assert events[approved_request.request_id].human_minutes is not None
    assert events[approved_request.request_id].action_id == "article_creation:publish_substack"
    assert denied_event.decision == "rejected"
    assert events[denied_request.request_id].decision == "rejected"
    assert store.list_requests(status="denied")[0].request_id == denied_request.request_id
    assert store.list_requests(status="pending") == []


def test_approval_store_marks_expired_requests(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    request = store.request(
        ApprovalRequest(
            action="write_config",
            risk="code_config",
            scope="self_evolution",
            reason="change config",
            run_id="run_expired",
        )
    )

    event = store.expire(request.request_id)

    assert event.decision == "expired"
    assert store.list_events()[0].decision == "expired"
    assert store.list_requests(status="expired")[0].request_id == request.request_id


def test_approval_store_defaults_overdue_requests_to_no(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    created_at = datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    request = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish public article",
            run_id="run_expired_pending",
            created_at=created_at,
            expires_at=created_at + timedelta(hours=24),
        )
    )

    assert store.list_requests(status="pending") == []
    expired = store.list_requests(status="expired")
    assert expired[0].request_id == request.request_id
    assert store.list_events()[0].decision == "expired"

    try:
        store.grant(request.request_id, granted_by="wa")
    except PermissionError:
        pass
    else:
        raise AssertionError("expired approval request must not grant")


def test_approval_store_can_persist_overdue_expiration_decisions(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    created_at = datetime(2020, 1, 1, 10, 0, tzinfo=timezone.utc)
    request = store.request(
        ApprovalRequest(
            action="write_config",
            risk="code_config",
            scope="self_evolution",
            reason="change config",
            run_id="run_overdue",
            created_at=created_at,
            expires_at=created_at + timedelta(hours=1),
        )
    )

    events = store.expire_overdue(now=created_at + timedelta(hours=2))

    assert events[0].id == request.request_id
    assert events[0].decision == "expired"
    assert store.list_requests(status="expired")[0].request_id == request.request_id


def test_approval_store_reports_capacity_pressure(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    created_at = datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc)
    for index in range(11):
        store.request(
            ApprovalRequest(
                action="publish_substack",
                risk="publish_public",
                scope="article_creation",
                reason="publish public article",
                run_id=f"run_{index}",
                created_at=created_at,
                expires_at=created_at + timedelta(hours=48),
            )
        )

    state = store.capacity_state(now=created_at + timedelta(hours=25))

    assert state["pending"] == 11
    assert state["remaining"] == 0
    assert state["queue_age_p95_minutes"] == 1500.0
    assert state["over_budget"] is True
    assert state["auto_pause_noncritical"] is True


def test_approval_store_batches_low_risk_preview_bound_requests(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    first = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish article one",
            run_id="run_digest_1",
            preview_hash="preview-one",
        )
    )
    second = store.request(
        ApprovalRequest(
            action="post_social",
            risk="publish_public",
            scope="social_proactive",
            reason="post note",
            run_id="run_digest_2",
            preview_hash="preview-two",
        )
    )
    store.request(
        ApprovalRequest(
            action="health_write",
            risk="health_external",
            scope="health_wellness",
            reason="write health state",
            run_id="run_digest_3",
            preview_hash="preview-health",
        )
    )

    digest = store.low_risk_digest()

    assert digest is not None
    assert digest["request_count"] == 2
    assert digest["request_ids"] == [first.request_id, second.request_id]
    assert digest["risks"] == ["publish_public"]
    assert digest["preview_hashes"] == ["preview-one", "preview-two"]
    assert store.list_requests(status="pending")[0].status == "pending"


def test_approval_store_marks_edited_requests(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    request = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish draft",
            run_id="run_edited",
            preview_hash="preview-before-edit",
        )
    )

    event = store.edit(request.request_id, decided_by="wa", reason="approve after shortening title")

    assert event.decision == "edited"
    assert event.resolved_at is not None
    assert event.human_minutes is not None
    assert store.list_events()[0].decision == "edited"
    assert store.list_requests(status="edited")[0].request_id == request.request_id
    assert (
        store.find_grant(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            preview_hash="preview-before-edit",
        )
        is None
    )


def test_approval_events_use_latest_append_only_resolution(tmp_path: Path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    request = store.request(
        ApprovalRequest(
            action="publish_substack",
            risk="publish_public",
            scope="article_creation",
            reason="publish draft",
            run_id="run_later_edit",
            preview_hash="preview-before-edit",
        )
    )

    store.grant(request.request_id, granted_by="wa")
    store.edit(request.request_id, decided_by="wa", reason="edited after first approval")

    assert store.list_requests(status="edited")[0].request_id == request.request_id
    assert store.list_events()[0].decision == "edited"
