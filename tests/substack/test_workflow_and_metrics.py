"""Tests for Substack article workflow and pilot metrics review."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_build_article_records_include_article_packet():
    from models import EditorialPackage, TopicCandidate
    from workflow import build_article_records

    topic = TopicCandidate(
        id="topic-1",
        title="Mira reliability",
        thesis="Mira should prove outcomes before saying done.",
        source="ideas/mira.md",
        pillar="Agent reliability",
        story_score=8,
        priority_score=9,
        mira_edge="Use Mira's status failure as the evidence.",
    )
    package = EditorialPackage(
        topic_id="topic-1",
        recommended_title="My Agent Said Done Before It Had Proof",
        subject_line_candidates=["My Agent Said Done Before It Had Proof"],
        abstract="The app looked settled because activity was mistaken for proof.",
        hook_candidates=["Mira looked busy for hours, and that was the bug."],
        format_blueprint=[],
        quality_scores={"title_intrigue": 9},
        pass_gate=True,
    )

    records = build_article_records([topic], [package])

    assert len(records) == 1
    assert records[0].state == "approval_required"
    packet = records[0].metadata["article_packet"]
    assert packet["title"] == "My Agent Said Done Before It Had Proof"
    assert packet["evidence_ledger"]


def test_pilot_review_builds_actions_from_local_state(tmp_path: Path):
    from metrics_review import build_pilot_review

    now = datetime(2026, 5, 8, tzinfo=timezone.utc)
    stats_path = tmp_path / "publication_stats.json"
    growth_path = tmp_path / "growth_state.json"
    notes_path = tmp_path / "notes_state.json"
    metrics_path = tmp_path / "comment_metrics.json"
    manifest_path = tmp_path / "publish_manifest.json"

    stats_path.write_text(
        json.dumps(
            {
                "articles": [
                    {
                        "title": "My Agent Said Done",
                        "post_date": "2026-05-07T10:00:00Z",
                        "views": 20,
                        "likes": 2,
                        "comments": 1,
                    }
                ],
                "subscribers": {"total": 12, "delta_30d": 2},
            }
        ),
        encoding="utf-8",
    )
    growth_path.write_text(
        json.dumps(
            {
                "comment_history": [
                    {"date": "2026-05-07T12:00:00", "url": "https://example.substack.com/p/x", "text": "good"}
                ],
                "relationship_targets": {
                    "example": {"last_interaction_at": "2026-05-07T12:00:00Z", "response_quality": "commented"}
                },
            }
        ),
        encoding="utf-8",
    )
    notes_path.write_text(json.dumps({"history": []}), encoding="utf-8")
    metrics_path.write_text(json.dumps({}), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "articles": {
                    "my-agent-said-done": {
                        "slug": "my-agent-said-done",
                        "title": "My Agent Said Done",
                        "status": "published",
                        "auto_podcast": True,
                        "timestamps": {"published": "2026-05-07T10:00:00Z"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    review = build_pilot_review(
        stats_path=stats_path,
        growth_state_path=growth_path,
        notes_state_path=notes_path,
        comment_metrics_path=metrics_path,
        publish_manifest_path=manifest_path,
        now=now,
    )

    assert review.published_count == 1
    assert review.subscribers_total == 12
    assert review.status in {"watch", "revise"}
    assert any("Notes" in action for action in review.actions)
    assert review.podcast_followthrough["required_articles"] == 1
    assert any("podcast" in action for action in review.actions)
