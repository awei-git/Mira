from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace


class FakeBridge:
    user_id = "ang"

    def __init__(self, exists: bool = True):
        self.exists = exists
        self.statuses = []

    def item_exists(self, item_id: str) -> bool:
        return self.exists

    def update_status(self, item_id: str, status: str, **kwargs):
        self.statuses.append((item_id, status, kwargs))


def _isolated_intent_receipts(monkeypatch, talk):
    state = {}

    def load_state():
        return json.loads(json.dumps(state))

    def save_state(new_state):
        state.clear()
        state.update(json.loads(json.dumps(new_state)))

    monkeypatch.setattr(talk, "load_state", load_state)
    monkeypatch.setattr(talk, "save_state", save_state)
    return state


def test_daily_collab_bypasses_generic_intent_gate(monkeypatch):
    import talk

    called = False

    def unclear(_text: str) -> dict:
        nonlocal called
        called = True
        return {"is_clear": False, "question": "What do you want to achieve with this?"}

    bridge = FakeBridge()
    monkeypatch.setattr(talk, "check_intent_clarity", unclear)

    allowed = talk._intent_gate_allows(
        bridge,
        "disc_daily_collab",
        "one small thought",
        tags=["daily-collab", "mira", "conversation"],
        item_type="discussion",
    )

    assert allowed is True
    assert called is False
    assert bridge.statuses == []


def test_vague_non_collab_task_still_requests_clarification(monkeypatch):
    import talk

    _isolated_intent_receipts(monkeypatch, talk)

    def unclear(_text: str) -> dict:
        return {"is_clear": False, "question": "What do you want to achieve with this?"}

    bridge = FakeBridge()
    monkeypatch.setattr(talk, "check_intent_clarity", unclear)

    allowed = talk._intent_gate_allows(bridge, "req_vague", "one small thought")

    assert allowed is False
    assert bridge.statuses == [
        (
            "req_vague",
            "needs-input",
            {"agent_message": "What do you want to achieve with this?"},
        )
    ]


def test_daily_collab_terminal_record_is_conversation():
    import talk

    rec = SimpleNamespace(task_id="disc_daily_collab", tags=["daily-collab", "mira", "conversation"])

    assert talk._is_conversation_record(rec)


def test_intent_clarification_outbox_is_idempotent(monkeypatch, tmp_path):
    import talk

    _isolated_intent_receipts(monkeypatch, talk)
    monkeypatch.setattr(talk, "MIRA_DIR", tmp_path)
    bridge = SimpleNamespace(user_id="ang")
    question = "What do you want to achieve with this?"

    talk._write_intent_clarification_outbox(bridge, "req_vague", question)
    talk._write_intent_clarification_outbox(bridge, "req_vague", question)

    files = sorted((tmp_path / "outbox").glob("intent_clarify_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["thread_id"] == "req_vague"
    assert payload["content"] == question


def test_intent_gate_dedupes_existing_bridge_clarification(monkeypatch):
    import talk

    _isolated_intent_receipts(monkeypatch, talk)
    question = "What do you want to achieve with this?"

    def unclear(_text: str) -> dict:
        return {"is_clear": False, "question": question}

    class BridgeWithItem:
        user_id = "ang"

        def __init__(self):
            self.item = {"id": "req_vague", "status": "queued", "messages": []}

        def item_exists(self, item_id: str) -> bool:
            return item_id == self.item["id"]

        def _read_item(self, item_id: str):
            return self.item if item_id == self.item["id"] else None

        def update_status(self, item_id: str, status: str, agent_message: str = ""):
            self.item["status"] = status
            if agent_message:
                self.item["messages"].append({"sender": "agent", "content": agent_message})

    bridge = BridgeWithItem()
    monkeypatch.setattr(talk, "check_intent_clarity", unclear)

    assert talk._intent_gate_allows(bridge, "req_vague", "one small thought") is False
    assert talk._intent_gate_allows(bridge, "req_vague", "one small thought") is False

    agent_questions = [
        msg["content"] for msg in bridge.item["messages"] if msg.get("sender") == "agent" and msg.get("content")
    ]
    assert agent_questions == [question]
    assert bridge.item["status"] == "needs-input"


def test_intent_gate_creates_missing_bridge_item_and_marks_control_needs_input(monkeypatch):
    import control.db
    import control.repository
    import talk

    _isolated_intent_receipts(monkeypatch, talk)
    question = "What do you want to achieve with this?"
    repo_calls = []

    def unclear(_text: str) -> dict:
        return {"is_clear": False, "question": question}

    class BridgeWithoutItem:
        user_id = "ang"

        def __init__(self):
            self.items = {}

        def item_exists(self, item_id: str) -> bool:
            return item_id in self.items

        def create_task(self, item_id: str, title: str, first_message: str, sender="user", tags=None, origin="user"):
            self.items[item_id] = {
                "id": item_id,
                "title": title,
                "status": "queued",
                "messages": [{"sender": sender, "content": first_message}],
                "tags": tags or [],
                "origin": origin,
            }

        def _read_item(self, item_id: str):
            return self.items.get(item_id)

        def update_status(self, item_id: str, status: str, agent_message: str = ""):
            self.items[item_id]["status"] = status
            if agent_message:
                self.items[item_id]["messages"].append({"sender": "agent", "content": agent_message})

    class FakeRepo:
        def __init__(self, conn):
            self.conn = conn

        def update_task_status(self, user_id, task_id, status, **kwargs):
            repo_calls.append((user_id, task_id, status, kwargs))

    @contextmanager
    def fake_transaction():
        yield object()

    bridge = BridgeWithoutItem()
    monkeypatch.setattr(talk, "CONTROL_RUNTIME_DB_ENABLED", True)
    monkeypatch.setattr(talk, "check_intent_clarity", unclear)
    monkeypatch.setattr(control.db, "transaction", fake_transaction)
    monkeypatch.setattr(control.repository, "ControlRepository", FakeRepo)

    allowed = talk._intent_gate_allows(
        bridge,
        "req_vague",
        "one small thought",
        tags=["todo"],
        item_type="new_request",
    )

    assert allowed is False
    assert bridge.items["req_vague"]["status"] == "needs-input"
    assert bridge.items["req_vague"]["messages"][-1] == {"sender": "agent", "content": question}
    assert repo_calls == [
        (
            "ang",
            "req_vague",
            "needs-input",
            {
                "summary": "Intent unclear; waiting for human clarification.",
                "agent_message": question,
            },
        )
    ]
