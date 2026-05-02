"""Tests for Substack editorial packaging."""

from __future__ import annotations

import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_editorial_package_creates_title_abstract_hooks_and_format():
    from editorial import build_editorial_package
    from models import PublicationStrategy, TopicCandidate

    topic = TopicCandidate(
        id="topic-1",
        title="Mira reliability notes",
        thesis="Mira showed a task as working for hours after the worker had already failed.",
        source="test",
        pillar="Agent reliability",
        mira_edge="Use Mira's own operating evidence from the app status failure.",
    )

    package = build_editorial_package(topic, PublicationStrategy())

    assert package.recommended_title != topic.title
    assert package.subject_line_candidates
    assert "Mira" in package.abstract
    assert len(package.format_blueprint) >= 5
    assert package.quality_scores["title_intrigue"] >= 7
    assert package.quality_scores["format_strength"] >= 8


def test_editorial_package_blocks_generic_titles():
    from editorial import score_editorial_package
    from models import EditorialPackage, TopicCandidate

    topic = TopicCandidate(
        id="topic-1",
        title="Thoughts",
        thesis="Generic AI thoughts.",
        source="test",
        pillar="Building Mira",
    )
    package = EditorialPackage(
        topic_id="topic-1",
        recommended_title="Thoughts and Reflections",
        subject_line_candidates=["Thoughts and Reflections"],
        abstract="A generic essay about AI.",
        hook_candidates=["This is about AI."],
        format_blueprint=[{"section": "Intro", "job": "summarize", "target": "short"}],
        quality_scores={},
        pass_gate=False,
    )

    scores, blocking = score_editorial_package(package, topic)

    assert scores["title_intrigue"] < 7
    assert any("Title" in reason for reason in blocking)
