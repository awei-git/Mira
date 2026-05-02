"""Tests for the Substack publisher-operator topic workflow."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_discover_topics_from_writer_ideas_scores_mira_specific_topics(tmp_path: Path):
    from models import PublicationStrategy
    from topic_backlog import discover_topics_from_writer_ideas

    ideas = tmp_path / "ideas"
    ideas.mkdir()
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"articles": []}), encoding="utf-8")
    (ideas / "mira-reliability.md").write_text(
        """---
platform: Substack
---

# I Am The Bug I Study

- **Thesis**: Mira's own failed task loop shows why agent reliability must be measured by verified outcomes, not clean logs.
""",
        encoding="utf-8",
    )

    topics = discover_topics_from_writer_ideas(PublicationStrategy(), ideas_dir=ideas, stats_path=stats)

    assert len(topics) == 1
    assert topics[0].title == "I Am The Bug I Study"
    assert topics[0].pillar == "Agent reliability"
    assert topics[0].priority_score >= 7.0
    assert "Mira" in topics[0].mira_edge


def test_editorial_calendar_uses_highest_priority_topics():
    from datetime import date

    from models import TopicCandidate
    from topic_backlog import build_editorial_calendar

    topics = [
        TopicCandidate(id="low", title="Low", thesis="x", source="test", pillar="Building Mira", priority_score=3),
        TopicCandidate(
            id="high", title="High", thesis="x", source="test", pillar="Agent reliability", priority_score=9
        ),
    ]

    calendar = build_editorial_calendar(topics, weeks=1, start=date(2026, 5, 4))

    assert calendar["weeks"][0]["week_start"] == "2026-05-04"
    assert calendar["weeks"][0]["primary_article"]["title"] == "High"
    assert calendar["weeks"][0]["publish_policy"].startswith("approval_required")
