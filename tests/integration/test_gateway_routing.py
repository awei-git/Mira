"""Gateway registry end-to-end: fan-in → item writer → source-routed send."""

from __future__ import annotations

from bridge_gateway import (
    AdapterRegistry,
    BridgeMessage,
    DiscordStubAdapter,
    TelegramStubAdapter,
)


def test_multi_adapter_fanin_and_routed_send():
    telegram = TelegramStubAdapter()
    discord = DiscordStubAdapter()
    reg = AdapterRegistry([telegram, discord])

    telegram.inject(BridgeMessage(id="tg1", user_id="ang", source="telegram", content="hey"))
    discord.inject(BridgeMessage(id="dc1", user_id="ang", source="discord", content="great post"))

    collected: list[BridgeMessage] = []
    reg.poll_all(lambda m: (collected.append(m) or True) and True)

    # Both messages ingested; Discord auto-tagged as reader_feedback.
    assert {m.id for m in collected} == {"tg1", "dc1"}
    dc_msg = next(m for m in collected if m.id == "dc1")
    assert "reader_feedback" in dc_msg.tags

    # Reply back through the correct adapter based on BridgeMessage.source.
    assert reg.send(BridgeMessage(id="reply-tg", user_id="ang", source="telegram", content="thanks")) is True
    assert reg.send(BridgeMessage(id="reply-dc", user_id="ang", source="discord", content="thanks")) is True
    assert len(telegram.sent) == 1
    assert len(discord.sent) == 1
    assert telegram.sent[0].content == "thanks"


def test_failing_adapter_does_not_break_others(monkeypatch):
    telegram = TelegramStubAdapter()
    discord = DiscordStubAdapter()
    reg = AdapterRegistry([telegram, discord])

    monkeypatch.setattr(telegram, "read_incoming", lambda: (_ for _ in ()).throw(RuntimeError("telegram down")))

    discord.inject(BridgeMessage(id="dc2", user_id="ang", source="discord", content="still up"))
    written = []
    reg.poll_all(lambda m: (written.append(m) or True))
    assert [m.id for m in written] == ["dc2"]
