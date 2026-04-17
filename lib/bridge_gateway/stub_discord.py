"""Placeholder Discord adapter — structure only, no guild connection.

Real adapter will use discord.py, read-only by default (per plan),
tagging inbound messages with `reader_feedback` so Phase 1 rewards
pick them up. Stub keeps the same contract so tests and registry
can exercise the gateway before the real connector lands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .adapter import BridgeAdapter, BridgeMessage


class DiscordStubAdapter(BridgeAdapter):
    """In-memory stub with the same semantics as TelegramStubAdapter.

    Tags every inbound message with `reader_feedback` by default so
    Phase 1 rewards treat them as community signal.
    """

    name = "discord"

    def __init__(
        self,
        *,
        tag_inbound: str = "reader_feedback",
        send_sink: Callable[[BridgeMessage], bool] | None = None,
    ) -> None:
        self._queue: list[BridgeMessage] = []
        self._sent: list[BridgeMessage] = []
        self._tag_inbound = tag_inbound
        self._send_sink = send_sink
        self._last_heartbeat = datetime.now(timezone.utc)

    def inject(self, message: BridgeMessage) -> None:
        if self._tag_inbound and self._tag_inbound not in message.tags:
            message.tags.append(self._tag_inbound)
        self._queue.append(message)

    def read_incoming(self) -> list[BridgeMessage]:
        msgs, self._queue = self._queue, []
        self._last_heartbeat = datetime.now(timezone.utc)
        return msgs

    def send_outgoing(self, message: BridgeMessage) -> bool:
        self._sent.append(message)
        self._last_heartbeat = datetime.now(timezone.utc)
        if self._send_sink is not None:
            return self._send_sink(message)
        return True

    def heartbeat(self) -> datetime:
        return self._last_heartbeat

    @property
    def sent(self) -> list[BridgeMessage]:
        return list(self._sent)
