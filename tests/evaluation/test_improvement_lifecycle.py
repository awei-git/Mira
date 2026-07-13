from __future__ import annotations

import json

import pytest


def _diagnosis() -> dict:
    return {
        "low_scores": [{"dim": "writer.voice", "score": 2.5, "category": "writer"}],
        "declining": [],
        "calibration_insights": "",
        "needs_action": True,
    }


def test_equivalent_pending_plan_is_reused(monkeypatch, tmp_path):
    from evaluation import improvement

    target = tmp_path / "improvement_plan.json"
    calls = []

    monkeypatch.setattr(improvement, "_IMPROVEMENT_FILE", target)
    monkeypatch.setattr("llm.claude_think", lambda *args, **kwargs: calls.append(args) or "Try one change.")

    changed_score = _diagnosis()
    changed_score["low_scores"][0]["score"] = 2.1
    assert improvement.generate_improvement_plan(changed_score) == "Try one change."
    assert improvement.generate_improvement_plan(_diagnosis()) == "Try one change."
    assert len(calls) == 1

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["status"] == "proposed"
    assert saved["evidence_required"] is True
    assert saved["north_star_layer"] == "L2"


def test_verified_outcome_requires_evidence(monkeypatch, tmp_path):
    from evaluation import improvement

    target = tmp_path / "improvement_plan.json"
    target.write_text(json.dumps({"status": "trial", "plan": "change"}), encoding="utf-8")
    monkeypatch.setattr(improvement, "_IMPROVEMENT_FILE", target)

    with pytest.raises(ValueError, match="evidence"):
        improvement.record_improvement_outcome("verified", evidence=[])

    updated = improvement.record_improvement_outcome(
        "verified",
        evidence=[{"source": "review_record", "id": "review-1"}],
        observed_change="Voice score rose from 2.5 to 4.2 on a held-out sample.",
    )
    assert updated["status"] == "verified"
    assert updated["evidence"][0]["id"] == "review-1"


def test_active_improvement_is_labeled_unverified(monkeypatch, tmp_path):
    from evaluation import improvement

    target = tmp_path / "improvement_plan.json"
    target.write_text(json.dumps({"status": "trial", "plan": "change the prompt"}), encoding="utf-8")
    monkeypatch.setattr(improvement, "_IMPROVEMENT_FILE", target)

    text = improvement.get_active_improvements()

    assert "UNVERIFIED" in text
    assert "change the prompt" in text
