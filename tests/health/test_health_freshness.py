from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_metric_age_label_marks_stale_rows():
    from agents.health.report import _metric_age_label

    now = datetime(2026, 5, 2, 12, 0, tzinfo=timezone.utc)
    fresh = {"date": now - timedelta(hours=12)}
    stale = {"date": now - timedelta(hours=50)}

    assert _metric_age_label(fresh, now=now) == "fresh"
    assert _metric_age_label(stale, now=now) == "stale 50h"
