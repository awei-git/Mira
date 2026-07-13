"""Tests for the Substack growth recovery sprint tracker."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_growth_recovery_progress_counts_real_artifacts_without_subscriber_pii(tmp_path: Path):
    from growth_recovery import (
        ANCHOR_TITLE,
        build_growth_recovery_sprint,
        format_growth_recovery_report,
        refresh_growth_recovery_progress,
    )

    stats_path = tmp_path / "publication_stats.json"
    notes_path = tmp_path / "notes_state.json"
    growth_path = tmp_path / "growth_state.json"
    metrics_path = tmp_path / "comment_metrics.json"
    now = datetime(2026, 7, 9, 1, 0, tzinfo=timezone.utc)

    _write_json(
        stats_path,
        {
            "fetched_at": "2026-07-09T01:00:00+00:00",
            "articles": [
                {
                    "id": 206217249,
                    "title": ANCHOR_TITLE,
                    "slug": "can-an-agent-develop-taste",
                    "views": 4,
                    "likes": 1,
                    "comments": 0,
                    "restacks": 0,
                    "post_date": "2026-07-08T23:39:56Z",
                }
            ],
            "subscribers": {
                "total": 40,
                "paid": 0,
                "delta_30d": 7,
                "subscribers": [
                    {
                        "name": "Private Reader",
                        "email": "reader@example.com",
                        "signup_at": "2026-07-09T00:10:00Z",
                    }
                ],
            },
        },
    )
    _write_json(
        notes_path,
        {
            "history": [
                {
                    "date": "2026-07-09T00:20:00Z",
                    "text": "Taste note",
                    "likes": 2,
                    "comments": 1,
                    "restacks": 1,
                },
                {
                    "date": "2026-07-08T20:20:00",
                    "text": "Local timestamp taste note",
                    "likes": 1,
                    "comments": 0,
                    "restacks": 0,
                },
            ],
            "queue": [
                {
                    "article_title": ANCHOR_TITLE,
                    "post_url": "https://uncountablemira.substack.com/p/can-an-agent-develop-taste",
                    "text": "Queued follow-up",
                    "queued_at": "2026-07-09T00:30:00Z",
                }
            ],
        },
    )
    _write_json(
        growth_path,
        {
            "comment_history": [
                {"date": "2026-07-09T00:30:00Z", "url": "https://miguelconner.substack.com/p/x"},
                {"date": "2026-07-09T00:40:00Z", "url": "https://breakingmath.substack.com/p/y"},
            ],
            "relationship_targets": {
                "miguelconner": {"last_interaction_at": "2026-07-09T00:30:00Z"},
            },
        },
    )
    _write_json(
        metrics_path,
        {
            "comment-1": {
                "posted_at": "2026-07-09T00:30:10Z",
                "metrics": {
                    "author_reply": True,
                    "attributed_followers": [{"email": "private@example.com"}],
                },
            }
        },
    )

    sprint = build_growth_recovery_sprint(stats_path=stats_path, now=now)
    sprint = refresh_growth_recovery_progress(
        sprint,
        stats_path=stats_path,
        notes_state_path=notes_path,
        growth_state_path=growth_path,
        comment_metrics_path=metrics_path,
        now=now,
    )

    progress = sprint.weeks[0]["progress"]
    assert progress["articles_published"] == 1
    assert progress["notes_posted"] == 2
    assert progress["anchor_notes_queued"] == 1
    assert progress["relationship_comments"] == 2
    assert progress["relationship_targets_touched"] == 2
    assert progress["author_replies"] == 1
    assert progress["new_subscribers"] == 1

    serialized = json.dumps(sprint.to_dict())
    report = format_growth_recovery_report(sprint, now=now)
    assert "reader@example.com" not in serialized
    assert "private@example.com" not in serialized
    assert "reader@example.com" not in report
    assert "Private Reader" not in report


def test_store_round_trips_growth_recovery(tmp_path: Path):
    from growth_recovery import build_growth_recovery_sprint
    from storage import SubstackStore

    store = SubstackStore(root=tmp_path / "substack_agent")
    sprint = build_growth_recovery_sprint(now=datetime(2026, 7, 9, tzinfo=timezone.utc))

    store.save_growth_recovery(sprint)
    loaded = store.load_growth_recovery()

    assert loaded is not None
    assert loaded.id == sprint.id
    assert loaded.anchor_article["title"] == sprint.anchor_article["title"]
