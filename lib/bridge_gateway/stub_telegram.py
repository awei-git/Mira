"""Placeholder Telegram adapter — structure only, no bot connection.

Real connector (using python-telegram-bot and a long-poll loop) will
replace this in a dedicated Step 3.2 PR with token read from
`.env.secret`. Keeping the stub here means other layers (registry,
supervisor, Phase 1 reward hookup) can be unit-tested against the
contract today.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .adapter import BridgeAdapter, BridgeMessage


class TelegramStubAdapter(BridgeAdapter):
    """In-memory stub. Tests enqueue messages via `inject(...)`.

    Constructor accepts optional `send_sink` callable so tests can
    assert what would have been transmitted upstream.
    """

    name = "telegram"

    def __init__(self, *, send_sink: Callable[[BridgeMessage], bool] | None = None) -> None:
        self._queue: list[BridgeMessage] = []
        self._sent: list[BridgeMessage] = []
        self._send_sink = send_sink
        self._last_heartbeat = datetime.now(timezone.utc)

    def inject(self, message: BridgeMessage) -> None:
        """Test seam — simulate an incoming Telegram message."""
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
        """Test seam — inspect delivered messages."""
        return list(self._sent)
