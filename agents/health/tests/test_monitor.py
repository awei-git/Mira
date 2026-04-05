"""Tests for health monitor timestamp handling."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_AGENTS / "health"))


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
