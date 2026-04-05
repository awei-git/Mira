from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_AGENTS = _HERE.parent.parent
sys.path.insert(0, str(_AGENTS / "super"))
sys.path.insert(0, str(_AGENTS / "shared"))


def test_should_spark_check_uses_user_scoped_state(monkeypatch):
    from runtime import triggers

    captured = {}

    def fake_load_state(user_id=None):
        captured["user_id"] = user_id
        return {"spark_memory_lines": 1}

    monkeypatch.setattr(triggers, "_load_state", fake_load_state)
    monkeypatch.setattr("soul_manager.get_memory_size", lambda: 2)

    assert triggers.should_spark_check(user_id="liquan") is True
    assert captured["user_id"] == "liquan"
