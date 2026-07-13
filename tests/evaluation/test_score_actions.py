"""Tests for score-driven self-improvement actions."""

from pathlib import Path


def test_build_score_action_items_has_stable_titles_and_criteria():
    from evaluation.actions import build_score_action_items

    diagnosis = {
        "low_scores": [
            {"category": "interests", "dim": "interests.reading_volume", "score": 0.01},
        ],
        "declining": [
            {"dim": "thinking.insight_depth", "scores": [7.8, 6.2, 5.4], "delta": -2.4},
        ],
    }

    items = build_score_action_items(diagnosis, plan_text="Read and verify more.")

    assert [item.title for item in items] == [
        "Score improvement: interests.reading_volume",
        "Score decline: thinking.insight_depth",
    ]
    assert items[0].priority == "high"
    assert items[0].target_dimension == "interests.reading_volume"
    assert "verification_criteria" in items[0].payload
    assert any("Reading-note ingestion" in criterion for criterion in items[0].payload["verification_criteria"])
    assert items[1].payload["kind"] == "score_decline"


def test_upsert_score_action_items_refreshes_existing_active_item(tmp_path: Path):
    from evaluation.actions import upsert_score_action_items
    from ops.backlog import ActionBacklog

    backlog = ActionBacklog(path=tmp_path / "backlog.json")
    diagnosis_v1 = {
        "low_scores": [{"dim": "implementation.hallucination_rate", "score": 1.68}],
        "declining": [],
    }
    diagnosis_v2 = {
        "low_scores": [{"dim": "implementation.hallucination_rate", "score": 2.50}],
        "declining": [],
    }

    first = upsert_score_action_items(diagnosis_v1, plan_text="first", backlog=backlog)
    second = upsert_score_action_items(diagnosis_v2, plan_text="second", backlog=backlog)

    active = backlog.get_active()
    assert len(first) == 1
    assert len(second) == 1
    assert len(active) == 1
    assert active[0].title == "Score improvement: implementation.hallucination_rate"
    assert active[0].payload["baseline_score"] == 2.5
    assert "second" in active[0].payload["plan_excerpt"]
