from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
for path in (
    ROOT / "agents" / "super",
    ROOT / "agents" / "explorer",
    ROOT / "agents" / "shared",
    ROOT / "lib",
):
    sys.path.insert(0, str(path))

from workflows import explore  # noqa: E402
import config  # noqa: E402


def test_empty_fetch_diagnostic_is_visible_and_actionable():
    diagnostic = explore._format_empty_fetch_diagnostic(
        ["hackernews", "lobsters"],
        "dev_sources",
        [{"message": "hackernews produced 0 items; rolling baseline is 20.00 +/- 3.00"}],
        "2026-05-26T12:00:00Z",
    )

    assert "# Explore source check" in diagnostic
    assert "Sources checked: hackernews, lobsters" in diagnostic
    assert "no feed items were fetched" in diagnostic
    assert "not evidence that nothing interesting happened" in diagnostic
    assert "hackernews produced 0 items" in diagnostic


def test_empty_fetch_updates_rotation_and_counts_attempt(monkeypatch):
    monkeypatch.setattr(explore, "EXPLORE_SOURCE_GROUPS", [["hackernews", "lobsters"], ["arxiv"]])
    state = {"explore_recent_groups": [0, 1], "explore_count_2026-05-26": 1}

    explore._update_explore_state_for_sources(
        state,
        ["lobsters", "hackernews"],
        "dev_sources",
        datetime(2026, 5, 26, 12, 0, 0),
        increment_count=True,
    )

    assert state["explore_count_2026-05-26"] == 2
    assert state["explored_2026-05-26_dev_sources"] == "2026-05-26T12:00:00"
    assert state["explore_recent_groups"] == [1, 0]


def test_explorer_stale_threshold_matches_scheduled_cadence():
    assert config.STALE_THRESHOLDS["explorer"] >= 20 * 60 * 60
