"""Tests for health monitor timestamp handling."""

from __future__ import annotations

import sys
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_is_stale_accepts_date_alias():
    from monitor import _is_stale

    fresh = {"date": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    stale = {"date": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()}

    assert not _is_stale(fresh, "sleep_hours")
    assert _is_stale(stale, "sleep_hours")


def test_check_weight_uses_date_field_for_day_comparison():
    from monitor import _check_weight

    latest = datetime.now(timezone.utc)
    previous_day = latest - timedelta(days=1)

    class StubStore:
        def get_recent_metrics(self, person_id: str, metric_type: str, days: int = 30):
            assert metric_type == "weight"
            return [
                {"value": 72.0, "date": latest.isoformat()},
                {"value": 70.0, "date": previous_day.isoformat()},
            ]

    alerts = _check_weight(
        StubStore(),
        "wei",
        {"weight": {"daily_change_kg": 1.5, "weekly_change_kg": 3.0}},
    )

    assert any(alert["title"] == "体重突变" for alert in alerts)


def test_health_query_formats_datetime_records(tmp_path, monkeypatch):
    health_handler_path = Path(__file__).resolve().parent.parent.parent / "agents" / "health" / "handler.py"
    spec = importlib.util.spec_from_file_location("health_handler_under_test", health_handler_path)
    handler = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(handler)

    captured = {}

    class StubStore:
        def get_recent_metrics(self, person_id: str, metric_type: str, days: int = 30):
            if metric_type == "sleep_hours":
                return [{"value": 7.2, "unit": "h", "date": datetime(2026, 5, 2, tzinfo=timezone.utc)}]
            return []

        def get_recent_notes(self, person_id: str, days: int = 30):
            return [{"category": "sleep", "date": datetime(2026, 5, 2, tzinfo=timezone.utc), "content": "slept ok"}]

        def get_recent_reports(self, person_id: str, limit: int = 3):
            return []

    def fake_omlx(model, prompt, timeout=60):
        captured["prompt"] = prompt
        return "昨晚睡眠 7.2 小时。"

    monkeypatch.setattr(handler, "_omlx_call", fake_omlx)

    result = handler._handle_query(StubStore(), tmp_path, "task_sleep", "昨晚睡眠", "default")

    assert result == "昨晚睡眠 7.2 小时。"
    assert "2026-05-02" in captured["prompt"]
    assert (tmp_path / "output.md").read_text(encoding="utf-8") == "昨晚睡眠 7.2 小时。"
