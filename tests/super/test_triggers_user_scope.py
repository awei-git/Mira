from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENTS = Path(__file__).resolve().parent.parent.parent / "agents"


def test_should_spark_check_uses_user_scoped_state(monkeypatch):
    from runtime import triggers

    captured = {}

    def fake_load_state(user_id=None):
        captured["user_id"] = user_id
        return {"spark_memory_lines": 1}

    monkeypatch.setattr(triggers, "_load_state", fake_load_state)
    monkeypatch.setattr("memory.soul.get_memory_size", lambda: 2)

    assert triggers.should_spark_check(user_id="liquan") is True
    assert captured["user_id"] == "liquan"


def test_should_daily_collab_uses_user_scoped_state(monkeypatch):
    from datetime import datetime as real_datetime
    from runtime import triggers

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 27, 12, 0, 0)

        @classmethod
        def combine(cls, date, time):
            return real_datetime.combine(date, time)

    captured = {}

    def fake_load_state(user_id=None):
        captured["user_id"] = user_id
        return {}

    monkeypatch.setattr(triggers, "datetime", FakeDateTime)
    monkeypatch.setattr(triggers, "DAILY_COLLAB_TIME", real_datetime(2026, 6, 27, 11, 30).time())
    monkeypatch.setattr(triggers, "_load_state", fake_load_state)

    assert triggers.should_daily_collab(user_id="liquan") is True
    assert captured["user_id"] == "liquan"


def test_should_daily_collab_repairs_state_marker_without_visible_message(monkeypatch, tmp_path):
    from datetime import datetime as real_datetime
    from runtime import triggers

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 27, 12, 0, 0)

        @classmethod
        def combine(cls, date, time):
            return real_datetime.combine(date, time)

    bridge_dir = tmp_path / "bridge"
    items_dir = bridge_dir / "users" / "ang" / "items"
    items_dir.mkdir(parents=True)
    (items_dir / "disc_daily_collab.json").write_text(json.dumps({"messages": []}), encoding="utf-8")

    monkeypatch.setattr(triggers, "datetime", FakeDateTime)
    monkeypatch.setattr(triggers, "DAILY_COLLAB_TIME", real_datetime(2026, 6, 27, 11, 30).time())
    monkeypatch.setattr(triggers, "MIRA_DIR", bridge_dir)
    monkeypatch.setattr(triggers, "_load_state", lambda user_id=None: {"daily_collab_2026-06-27": "done"})

    assert triggers.should_daily_collab(user_id="ang") is True


def test_should_daily_collab_operator_brief_requires_actionable_signal(monkeypatch):
    from datetime import datetime as real_datetime
    from runtime import triggers
    import daily_collab

    class FakeDateTime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 1, 19, 0, 0)

    monkeypatch.setattr(triggers, "datetime", FakeDateTime)
    monkeypatch.setattr(triggers, "_load_state", lambda user_id=None: {})
    monkeypatch.setattr(
        daily_collab,
        "build_daily_collab_operator_brief",
        lambda: ("brief", {"act_signals": 1, "budget_signals": 0, "candidate_article_seeds": 0}),
    )
    monkeypatch.setattr(daily_collab, "operator_delivery_key", lambda _metrics: "delivery-key")
    monkeypatch.setattr(daily_collab, "has_operator_delivery", lambda _key: False)

    assert triggers._should_daily_collab_operator_brief() is True

    monkeypatch.setattr(daily_collab, "has_operator_delivery", lambda _key: True)
    assert triggers._should_daily_collab_operator_brief() is False

    monkeypatch.setattr(
        daily_collab,
        "build_daily_collab_operator_brief",
        lambda: (
            "brief",
            {
                "act_signals": 0,
                "budget_signals": 0,
                "candidate_article_seeds": 0,
                "recent_incidents": 0,
                "writing_triage": {"parked_count": 2},
            },
        ),
    )
    monkeypatch.setattr(daily_collab, "has_operator_delivery", lambda _key: False)
    assert triggers._should_daily_collab_operator_brief() is True
