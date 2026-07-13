from __future__ import annotations

import json


def _skill() -> dict:
    return {
        "name": "scene-to-claim",
        "description": "Move from a concrete scene to a general claim.",
        "content": "Start from a witnessed event, then earn the abstraction from its consequences.",
        "tags": ["writing", "craft"],
        "validation_test": "Use it in a second essay and compare reviewer opening scores.",
    }


def test_article_skill_is_audited_and_queued_not_enabled(monkeypatch, tmp_path):
    from evaluation import self_iteration

    audits = []
    saves = []
    monkeypatch.setattr(self_iteration, "_SKILL_CANDIDATES_DIR", tmp_path)
    monkeypatch.setattr(
        "memory.soul_skills.audit_skill",
        lambda name, content, **kwargs: audits.append((name, content)) or {"result": "PASS"},
    )
    monkeypatch.setattr("memory.soul_skills.save_skill", lambda *args, **kwargs: saves.append(args) or True)

    queued = self_iteration.queue_article_skill_candidates([_skill()], "A Field Note")

    assert len(queued) == 1
    assert len(audits) == 1
    assert saves == []
    row = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert row["status"] == "candidate"
    assert row["security_audit"]["passed"] is True
    assert row["validation_test"]


def test_blocked_article_skill_is_not_saved(monkeypatch, tmp_path):
    from evaluation import self_iteration
    from memory.soul_skills import SkillAuditFailedError

    monkeypatch.setattr(self_iteration, "_SKILL_CANDIDATES_DIR", tmp_path)
    monkeypatch.setattr(
        "memory.soul_skills.audit_skill",
        lambda *args, **kwargs: (_ for _ in ()).throw(SkillAuditFailedError("blocked")),
    )

    assert self_iteration.queue_article_skill_candidates([_skill()], "A Field Note") == []
    assert list(tmp_path.iterdir()) == []


def test_repeated_candidate_preserves_observation_history(monkeypatch, tmp_path):
    from evaluation import self_iteration

    monkeypatch.setattr(self_iteration, "_SKILL_CANDIDATES_DIR", tmp_path)
    monkeypatch.setattr("memory.soul_skills.audit_skill", lambda *args, **kwargs: {"result": "PASS"})

    self_iteration.queue_article_skill_candidates([_skill()], "First Essay")
    self_iteration.queue_article_skill_candidates([_skill()], "Second Essay")

    row = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert [item["source_title"] for item in row["observations"]] == ["First Essay", "Second Essay"]


def test_failure_lesson_is_queued_instead_of_enabled(monkeypatch):
    from evaluation import self_iteration

    captured = []
    monkeypatch.setattr(
        self_iteration,
        "queue_skill_candidates",
        lambda skills, source_title: captured.append((skills, source_title)) or skills,
    )

    assert self_iteration.save_failure_lesson(_skill())
    assert captured[0][0][0]["validation_test"]
    assert captured[0][1].startswith("task failure:")
