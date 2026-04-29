"""Boundary schema roundtrip + input tolerance."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas import AgentState, BridgeItem, TaskRequest, TaskResult


def test_bridge_item_accepts_minimal_dict():
    item = BridgeItem.from_dict({"id": "x1"})
    assert item.id == "x1"
    assert item.status == "new"
    assert item.created_at.tzinfo is not None


def test_bridge_item_normalizes_naive_timestamp():
    item = BridgeItem.from_dict({"id": "x1", "created_at": "2026-04-10T10:00:00"})
    assert item.created_at.tzinfo is not None


def test_bridge_item_preserves_extras():
    item = BridgeItem.from_dict({"id": "x1", "custom_field": 42})
    data = item.to_dict()
    assert data.get("custom_field") == 42


def test_task_request_roundtrip():
    src = {
        "task_id": "t1",
        "user_id": "ang",
        "content": "write about X",
        "tags": ["writing"],
        "created_at": "2026-04-17T12:00:00+00:00",
    }
    req = TaskRequest.from_dict(src)
    again = TaskRequest.from_dict(req.to_dict())
    assert again.task_id == "t1"
    assert again.content == "write about X"
    assert again.tags == ["writing"]


def test_task_result_accepts_epoch_timestamp():
    res = TaskResult.from_dict({"task_id": "t", "status": "done", "completed_at": 1713360000.5})
    assert res.completed_at.tzinfo is not None
    assert res.status == "done"


def test_task_result_custom_failure_class():
    res = TaskResult.from_dict({"task_id": "t", "status": "crashed", "failure_class": "worker_crash"})
    assert res.status == "crashed"
    assert res.failure_class == "worker_crash"


def test_agent_state_handles_none_last_tick():
    st = AgentState.from_dict({"user_id": "ang"})
    assert st.last_tick_at is None
    assert st.session_started_at.tzinfo is not None


def test_agent_state_roundtrip_with_session_context():
    src = {
        "user_id": "ang",
        "active_workflow_id": "w-42",
        "pending_tasks": ["t1", "t2"],
        "session_context": {"mode": "explore", "counter": 3},
    }
    st = AgentState.from_dict(src)
    again = AgentState.from_dict(st.to_dict())
    assert again.active_workflow_id == "w-42"
    assert again.pending_tasks == ["t1", "t2"]
    assert again.session_context["counter"] == 3
