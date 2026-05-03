from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "agents" / "super"))

from workflows import daily  # noqa: E402


def test_performance_assessment_includes_models_cost_and_task_roi():
    assessment = {
        "aggregate": {
            "overall_success_rate": 0.5,
            "crash_rate": 0.032,
            "heartbeat_ok": True,
        }
    }
    usage_records = [
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "agent": "discussion",
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "total_tokens": 1500,
            "cost_usd": 0.0105,
        },
        {
            "provider": "openai",
            "model": "gpt-5",
            "agent": "health",
            "prompt_tokens": 2000,
            "completion_tokens": 1000,
            "total_tokens": 3000,
            "cost_usd": 0.012,
        },
    ]
    task_records = [
        {
            "agent": "discussion",
            "status": "done",
            "completed_at": "2026-05-02T09:00:00Z",
            "content_preview": "answer health question",
        },
        {
            "agent": "analyst",
            "status": "failed",
            "completed_at": "2026-05-01T09:00:00Z",
            "content_preview": "market follow-up",
        },
    ]

    summary = daily._format_performance_assessment_summary(
        assessment,
        plan=True,
        usage_records=usage_records,
        task_records=task_records,
    )

    assert "Model Usage And Cost" in summary
    assert "anthropic/claude-sonnet-4" in summary
    assert "openai/gpt-5" in summary
    assert "ROI proxy" in summary
    assert "discussion: 1/1 completed" in summary
    assert "analyst: failed" in summary
    assert "Improvement plan" in summary
