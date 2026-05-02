"""Tests for Substack agent storage and compatibility contracts."""

from __future__ import annotations

import sys
from pathlib import Path


_SUBSTACK_AGENT = Path(__file__).resolve().parents[2] / "agents" / "substack"
if str(_SUBSTACK_AGENT) not in sys.path:
    sys.path.insert(0, str(_SUBSTACK_AGENT))


def test_store_upserts_topics_without_duplicate_spam(tmp_path: Path):
    from models import TopicCandidate
    from storage import SubstackStore

    store = SubstackStore(root=tmp_path / "substack_agent")
    topic_v1 = TopicCandidate(
        id="topic-1",
        title="Reliable Agents",
        thesis="Version one",
        source="test",
        pillar="Agent reliability",
        priority_score=5,
    )
    topic_v2 = TopicCandidate(
        id="topic-1",
        title="Reliable Agents",
        thesis="Version two",
        source="test",
        pillar="Agent reliability",
        priority_score=8,
    )

    created, updated = store.upsert_topics([topic_v1])
    created2, updated2 = store.upsert_topics([topic_v2])

    topics = store.load_topics()
    assert (created, updated) == (1, 0)
    assert (created2, updated2) == (0, 1)
    assert len(topics) == 1
    assert topics[0].thesis == "Version two"
    assert topics[0].priority_score == 8


def test_store_upserts_editorial_packages(tmp_path: Path):
    from models import EditorialPackage
    from storage import SubstackStore

    store = SubstackStore(root=tmp_path / "substack_agent")
    package_v1 = EditorialPackage(
        topic_id="topic-1",
        recommended_title="Done Is Not A Status",
        subject_line_candidates=["Done Is Not A Status"],
        abstract="Mira proves why done must mean verified.",
        hook_candidates=["The most dangerous agent status is not failed."],
        format_blueprint=[{"section": "Hook", "job": "open", "target": "1 sentence"}],
        quality_scores={"title_intrigue": 8},
        pass_gate=True,
    )
    package_v2 = EditorialPackage(
        topic_id="topic-1",
        recommended_title="The Agent Has To Prove It Happened",
        subject_line_candidates=["The Agent Has To Prove It Happened"],
        abstract="Mira proves why reliable agents need outcome verification.",
        hook_candidates=["Mira looked busy for hours, and that was the bug."],
        format_blueprint=[{"section": "Hook", "job": "open", "target": "1 sentence"}],
        quality_scores={"title_intrigue": 9},
        pass_gate=True,
    )

    assert store.upsert_editorial_packages([package_v1]) == (1, 0)
    assert store.upsert_editorial_packages([package_v2]) == (0, 1)
    packages = store.load_editorial_packages()
    assert len(packages) == 1
    assert packages[0].recommended_title == "The Agent Has To Prove It Happened"


def test_current_socialmedia_stack_capabilities_are_visible():
    from compatibility import check_current_stack

    report = check_current_stack()

    assert report["ok"]
    for name in (
        "publish_article",
        "publication_stats",
        "subscriber_snapshot",
        "own_comment_replies",
        "article_notes_queue",
    ):
        assert report["capabilities"][name]["present"], name
