"""DiscordBridgeAdapter — graceful degrade + mocked REST path."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone

from bridge_gateway.adapters.discord import DiscordBridgeAdapter


def test_disabled_without_token():
    adapter = DiscordBridgeAdapter(token=None, channel_id=1)
    assert adapter.disabled_reason == "no bot_token in secrets.yml"
    assert adapter.read_incoming() == []
    assert adapter.send_outgoing(_msg()) is False


def test_disabled_without_channel_id():
    adapter = DiscordBridgeAdapter(token="Bot x", channel_id=None)
    assert adapter.disabled_reason == "no channel_id in secrets.yml"


def test_read_incoming_tags_reader_feedback(monkeypatch):
    adapter = DiscordBridgeAdapter(token="Bot abc", channel_id=111, user_id="ang")

    payload = [
        {
            "id": "1001",
            "content": "great post",
            "timestamp": "2026-04-16T23:00:00+00:00",
        },
        {
            "id": "1002",
            "content": "boring",
            "timestamp": "2026-04-16T23:05:00+00:00",
        },
    ]

    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    msgs = adapter.read_incoming()
    assert [m.content for m in msgs] == ["great post", "boring"]
    assert all("reader_feedback" in m.tags for m in msgs)
    assert all(m.user_id == "ang" for m in msgs)


def test_read_incoming_advances_after_cursor(monkeypatch):
    adapter = DiscordBridgeAdapter(token="Bot abc", channel_id=111, user_id="ang")

    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "after=" in req.full_url:
            return _FakeResp(
                json.dumps([{"id": "2002", "content": "later", "timestamp": "2026-04-16T23:30:00+00:00"}]).encode()
            )
        return _FakeResp(
            json.dumps([{"id": "2001", "content": "first", "timestamp": "2026-04-16T23:00:00+00:00"}]).encode()
        )

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    first = adapter.read_incoming()
    second = adapter.read_incoming()
    assert [m.content for m in first] == ["first"]
    assert [m.content for m in second] == ["later"]
    assert "after=" not in calls[0]
    assert "after=2001" in calls[1]


def test_send_outgoing_disabled_unless_opted_in(monkeypatch):
    adapter = DiscordBridgeAdapter(token="Bot abc", channel_id=111, enable_replies=False)
    # Should never hit network
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("network forbidden"))
    )
    assert adapter.send_outgoing(_msg()) is False


def test_send_outgoing_posts_when_enabled(monkeypatch):
    adapter = DiscordBridgeAdapter(token="Bot abc", channel_id=111, enable_replies=True)

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _FakeResp(b"ok", status=200)

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    ok = adapter.send_outgoing(_msg(content="hello world"))
    assert ok is True
    assert "channels/111/messages" in captured["url"]
    assert b"hello world" in captured["body"]


# ---- helpers --------------------------------------------------------------


class _FakeResp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _msg(*, id_: str = "o1", user_id: str = "ang", content: str = "hi"):
    from bridge_gateway import BridgeMessage

    return BridgeMessage(id=id_, user_id=user_id, source="discord", content=content)
