"""TelegramBridgeAdapter — graceful degrade + mocked happy path."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

from bridge_gateway.adapters.telegram import TelegramBridgeAdapter


def test_disabled_without_token():
    adapter = TelegramBridgeAdapter(token=None, chat_ids={})
    assert adapter.disabled_reason == "no bot_token in secrets.yml"
    assert adapter.read_incoming() == []
    assert adapter.send_outgoing(_msg()) is False


def test_disabled_when_library_missing(monkeypatch):
    """If `telegram` can't be imported, adapter permanently disables on first use."""
    adapter = TelegramBridgeAdapter(token="faketoken", chat_ids={"ang": 42})
    # Simulate no library by injecting an import error.
    monkeypatch.setitem(sys.modules, "telegram", None)
    # First operation triggers lazy load which fails.
    assert adapter.read_incoming() == []
    assert adapter.disabled_reason is not None


def test_send_outgoing_uses_chat_id_mapping(monkeypatch):
    adapter = TelegramBridgeAdapter(token="faketoken", chat_ids={"ang": 42})

    sent = {}

    class FakeBot:
        def __init__(self, token):
            sent["init_token"] = token

        def send_message(self, chat_id, text):
            sent["chat_id"] = chat_id
            sent["text"] = text

        def get_updates(self, offset=None, timeout=None):
            return []

    fake_module = types.SimpleNamespace(Bot=FakeBot)
    monkeypatch.setitem(sys.modules, "telegram", fake_module)

    ok = adapter.send_outgoing(_msg(user_id="ang", content="hello"))
    assert ok is True
    assert sent["chat_id"] == 42 and sent["text"] == "hello"


def test_send_outgoing_drops_unknown_user(monkeypatch):
    adapter = TelegramBridgeAdapter(token="faketoken", chat_ids={"ang": 42})

    class FakeBot:
        def __init__(self, token):
            pass

        def send_message(self, **kwargs):
            raise AssertionError("should not be called")

    monkeypatch.setitem(sys.modules, "telegram", types.SimpleNamespace(Bot=FakeBot))
    assert adapter.send_outgoing(_msg(user_id="liquan", content="x")) is False


def test_read_incoming_decodes_and_dedups_by_offset(monkeypatch):
    adapter = TelegramBridgeAdapter(token="faketoken", chat_ids={"ang": 42})

    class FakeChat:
        id = 42

    class FakeMessage:
        def __init__(self, text, mid):
            self.text = text
            self.message_id = mid
            self.chat = FakeChat()
            self.date = datetime.now(timezone.utc)

    class FakeUpdate:
        def __init__(self, update_id, text, mid):
            self.update_id = update_id
            self.message = FakeMessage(text, mid)

    captured_offsets = []

    class FakeBot:
        def __init__(self, token):
            pass

        def get_updates(self, offset=None, timeout=None):
            captured_offsets.append(offset)
            if offset is None:
                return [FakeUpdate(10, "first", 1001), FakeUpdate(11, "second", 1002)]
            return [FakeUpdate(12, "third", 1003)]

    monkeypatch.setitem(sys.modules, "telegram", types.SimpleNamespace(Bot=FakeBot))

    first = adapter.read_incoming()
    second = adapter.read_incoming()

    assert [m.content for m in first] == ["first", "second"]
    assert [m.content for m in second] == ["third"]
    # Offset advanced between calls
    assert captured_offsets[0] is None
    assert captured_offsets[1] == 12


def test_read_incoming_ignores_unregistered_chat(monkeypatch):
    adapter = TelegramBridgeAdapter(token="faketoken", chat_ids={"ang": 42})

    class StrangerChat:
        id = 999

    class StrangerMessage:
        def __init__(self):
            self.text = "spam"
            self.message_id = 99
            self.chat = StrangerChat()
            self.date = datetime.now(timezone.utc)

    class FakeUpdate:
        update_id = 1
        message = StrangerMessage()

    class FakeBot:
        def __init__(self, token):
            pass

        def get_updates(self, offset=None, timeout=None):
            return [FakeUpdate()]

    monkeypatch.setitem(sys.modules, "telegram", types.SimpleNamespace(Bot=FakeBot))
    assert adapter.read_incoming() == []


def _msg(*, id_: str = "out1", user_id: str = "ang", content: str = "hi"):
    from bridge_gateway import BridgeMessage

    return BridgeMessage(id=id_, user_id=user_id, source="telegram", content=content)
