from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent
sys.path.insert(0, str(_SHARED))


def test_save_and_load_emptiness_are_user_scoped(tmp_path, monkeypatch):
    import evaluation.emptiness as emptiness
    monkeypatch.setattr(emptiness, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(emptiness, "EMPTINESS_FILE", tmp_path / "legacy.json")

    emptiness.save_emptiness({"emptiness_value": 10, "pending_questions": []}, user_id="ang")
    emptiness.save_emptiness({"emptiness_value": 99, "pending_questions": []}, user_id="liquan")

    ang = emptiness.load_emptiness(user_id="ang")
    liquan = emptiness.load_emptiness(user_id="liquan")

    assert ang["emptiness_value"] == 10
    assert liquan["emptiness_value"] == 99
    assert (tmp_path / "bridge" / "users" / "ang" / "state" / "emptiness.json").exists()
    assert (tmp_path / "bridge" / "users" / "liquan" / "state" / "emptiness.json").exists()


def test_load_emptiness_falls_back_to_legacy_file(tmp_path, monkeypatch):
    import evaluation.emptiness as emptiness
    monkeypatch.setattr(emptiness, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(emptiness, "EMPTINESS_FILE", tmp_path / "legacy.json")
    emptiness.EMPTINESS_FILE.write_text(
        json.dumps({"emptiness_value": 42, "pending_questions": []}),
        encoding="utf-8",
    )

    state = emptiness.load_emptiness(user_id="missing-user")

    assert state["emptiness_value"] == 42


def test_add_question_does_not_leak_between_users(tmp_path, monkeypatch):
    import evaluation.emptiness as emptiness
    monkeypatch.setattr(emptiness, "MIRA_DIR", tmp_path / "bridge")
    monkeypatch.setattr(emptiness, "EMPTINESS_FILE", tmp_path / "legacy.json")

    emptiness.add_question("Why does this matter?", user_id="liquan")
    ang_questions = emptiness.get_active_questions(user_id="ang")
    liquan_questions = emptiness.get_active_questions(user_id="liquan")

    assert ang_questions == []
    assert len(liquan_questions) == 1
    assert liquan_questions[0]["text"] == "Why does this matter?"
