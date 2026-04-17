"""AdapterRegistry contract + fan-in/out behaviour with stub adapters."""

from __future__ import annotations

import pytest

from bridge_gateway import (
    AdapterRegistry,
    BridgeMessage,
    DiscordStubAdapter,
    TelegramStubAdapter,
)


def _msg(id_: str, source: str, *, user_id: str = "ang", content: str = "hi") -> BridgeMessage:
    return BridgeMessage(id=id_, user_id=user_id, source=source, content=content)


def test_registry_rejects_duplicate_name():
    reg = AdapterRegistry([TelegramStubAdapter()])
    with pytest.raises(ValueError):
        reg.register(TelegramStubAdapter())


def test_registry_rejects_nameless_adapter():
    anon = TelegramStubAdapter()
    anon.name = ""  # type: ignore[assignment]
    with pytest.raises(ValueError):
        AdapterRegistry([anon])


def test_poll_all_writes_messages_via_injected_writer():
    telegram = TelegramStubAdapter()
    discord = DiscordStubAdapter()
    reg = AdapterRegistry([telegram, discord])

    telegram.inject(_msg("tg1", "telegram", content="hello from tg"))
    discord.inject(_msg("dc1", "discord", content="hello from dc"))

    written: list[BridgeMessage] = []

    def writer(m: BridgeMessage) -> bool:
        written.append(m)
        return True

    counts = reg.poll_all(writer)
    assert counts == {"telegram": 1, "discord": 1}
    assert {m.id for m in written} == {"tg1", "dc1"}
    # Discord messages auto-tagged by the stub
    dc_msg = next(m for m in written if m.id == "dc1")
    assert "reader_feedback" in dc_msg.tags


def test_poll_all_isolates_failing_adapter(monkeypatch):
    telegram = TelegramStubAdapter()
    discord = DiscordStubAdapter()
    reg = AdapterRegistry([telegram, discord])

    def boom():
        raise RuntimeError("simulated poll failure")

    monkeypatch.setattr(telegram, "read_incoming", boom)

    discord.inject(_msg("dc1", "discord"))

    counts = reg.poll_all(lambda _m: True)
    assert counts["telegram"] == 0
    assert counts["discord"] == 1


def test_send_routes_by_source_and_returns_bool():
    telegram = TelegramStubAdapter()
    reg = AdapterRegistry([telegram])

    ok = reg.send(_msg("out1", "telegram", content="reply"))
    assert ok is True
    assert telegram.sent and telegram.sent[0].id == "out1"

    # Unknown source is a warning + False, not an exception
    assert reg.send(_msg("out2", "whatsapp")) is False


def test_send_failure_bubbles_as_false(monkeypatch):
    telegram = TelegramStubAdapter()
    reg = AdapterRegistry([telegram])

    def fail(_m):
        raise OSError("net down")

    monkeypatch.setattr(telegram, "send_outgoing", fail)
    assert reg.send(_msg("out1", "telegram")) is False


def test_heartbeats_collects_from_all_adapters():
    reg = AdapterRegistry([TelegramStubAdapter(), DiscordStubAdapter()])
    hb = reg.heartbeats()
    assert set(hb.keys()) == {"telegram", "discord"}
