"""Tests for Substack pilot action backlog generation."""

from __future__ import annotations

import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_build_pilot_action_items_tracks_podcast_and_promotion_gaps():
    from action_backlog import build_pilot_action_items
    from models import PilotReview

    review = PilotReview(
        id="pilot-1",
        period_start="2026-05-01",
        period_end="2026-05-08",
        status="watch",
        published_count=1,
        notes_count=1,
        comments_count=2,
        podcast_followthrough={
            "required_articles": 1,
            "incomplete": [{"slug": "article", "status": "published"}],
        },
    )

    items = build_pilot_action_items(review)
    titles = [item.title for item in items]

    assert "Substack pilot: reach weekly Notes floor" in titles
    assert "Substack pilot: reach relationship comment floor" in titles
    assert "Substack pilot: complete podcast follow-through" in titles
    podcast = next(item for item in items if item.title == "Substack pilot: complete podcast follow-through")
    assert podcast.priority == "high"
    assert podcast.executor == "substack.podcast_followthrough"
    assert podcast.payload["verification_criteria"]


def test_upsert_pilot_action_items_refreshes_active_items(tmp_path: Path):
    from action_backlog import upsert_pilot_action_items
    from models import PilotReview
    from ops.backlog import ActionBacklog

    backlog = ActionBacklog(path=tmp_path / "backlog.json")
    first = PilotReview(
        id="pilot-1",
        period_start="2026-05-01",
        period_end="2026-05-08",
        status="watch",
        notes_count=1,
    )
    second = PilotReview(
        id="pilot-2",
        period_start="2026-05-08",
        period_end="2026-05-15",
        status="watch",
        notes_count=2,
    )

    upsert_pilot_action_items(first, backlog=backlog)
    upsert_pilot_action_items(second, backlog=backlog)

    active = [item for item in backlog.get_active() if item.title == "Substack pilot: reach weekly Notes floor"]
    assert len(active) == 1
    assert active[0].payload["review_id"] == "pilot-2"
    assert active[0].payload["notes_count"] == 2
