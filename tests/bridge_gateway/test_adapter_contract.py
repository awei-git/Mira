"""BridgeMessage roundtrip + stub-adapter contract."""

from __future__ import annotations

from datetime import datetime, timezone

from bridge_gateway import BridgeMessage, DiscordStubAdapter, TelegramStubAdapter
from bridge_gateway.adapter import bridge_message_from_dict


def test_bridge_message_from_dict_parses_iso_timestamp():
    data = {
        "id": "m1",
        "user_id": "ang",
        "source": "telegram",
        "content": "hello",
        "timestamp": "2026-04-16T22:00:00+00:00",
        "tags": ["test"],
    }
    msg = bridge_message_from_dict(data)
    assert msg.id == "m1"
    assert msg.source == "telegram"
    assert msg.timestamp.tzinfo is not None


def test_bridge_message_from_dict_defaults_missing_fields():
    msg = bridge_message_from_dict({"id": "m2", "user_id": "ang"})
    assert msg.source == "unknown"
    assert msg.content == ""
    assert msg.tags == []


def test_telegram_stub_roundtrip():
    adapter = TelegramStubAdapter()
    assert adapter.read_incoming() == []

    adapter.inject(BridgeMessage(id="in1", user_id="ang", source="telegram", content="ping"))
    inbound = adapter.read_incoming()
    assert len(inbound) == 1
    assert inbound[0].content == "ping"
    # Second read drains the queue
    assert adapter.read_incoming() == []

    outgoing = BridgeMessage(id="out1", user_id="ang", source="telegram", content="pong")
    assert adapter.send_outgoing(outgoing) is True
    assert adapter.sent[0].id == "out1"

    assert adapter.heartbeat().tzinfo is not None


def test_discord_stub_auto_tags_inbound():
    adapter = DiscordStubAdapter()
    adapter.inject(BridgeMessage(id="in1", user_id="ang", source="discord", content="great post!"))
    inbound = adapter.read_incoming()
    assert "reader_feedback" in inbound[0].tags


def test_discord_stub_respects_custom_tag():
    adapter = DiscordStubAdapter(tag_inbound="discord_chat")
    adapter.inject(BridgeMessage(id="in1", user_id="ang", source="discord", content="hi"))
    inbound = adapter.read_incoming()
    assert "discord_chat" in inbound[0].tags
    assert "reader_feedback" not in inbound[0].tags


def test_send_sink_allows_test_to_intercept_outbound():
    delivered = []

    def sink(m):
        delivered.append(m)
        return True

    adapter = TelegramStubAdapter(send_sink=sink)
    adapter.send_outgoing(
        BridgeMessage(
            id="o1", user_id="ang", source="telegram", content="through sink", timestamp=datetime.now(timezone.utc)
        )
    )
    assert delivered and delivered[0].id == "o1"
