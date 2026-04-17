from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent

try:
    import evaluation.soul_question as soul_question

    _HAS_SOUL_QUESTION = True
except (ImportError, ModuleNotFoundError):
    soul_question = None
    _HAS_SOUL_QUESTION = False

_skip_no_bridge = pytest.mark.skipif(not _HAS_SOUL_QUESTION, reason="mira_bridge not available (CI)")


@_skip_no_bridge
def test_soul_question_history_is_user_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(soul_question, "STATE_FILE", tmp_path / "legacy_history.json")
    monkeypatch.setattr(
        soul_question,
        "user_soul_question_history_file",
        lambda user_id: tmp_path / user_id / "soul_questions_history.json",
    )

    soul_question._save_history(["q1"], user_id="ang")
    soul_question._save_history(["q2"], user_id="liquan")

    assert soul_question._load_history(user_id="ang") == ["q1"]
    assert soul_question._load_history(user_id="liquan") == ["q2"]


@_skip_no_bridge
def test_soul_question_send_to_user_uses_requested_bridge(monkeypatch):
    captured = {}

    class FakeBridge:
        def __init__(self, bridge_dir, user_id):
            captured["user_id"] = user_id

        def item_exists(self, item_id):
            return False

        def create_discussion(self, disc_id, title, question_text, sender="agent", tags=None):
            captured["disc_id"] = disc_id
            captured["title"] = title
            captured["question_text"] = question_text

    monkeypatch.setattr(soul_question, "Mira", FakeBridge)

    assert soul_question.send_to_user("Question?", user_id="liquan") is True
    assert captured["user_id"] == "liquan"
    assert captured["question_text"] == "Question?"
