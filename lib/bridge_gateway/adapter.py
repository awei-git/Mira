"""BridgeAdapter ABC — contract every platform adapter must satisfy.

Concrete implementations live in `lib/bridge_gateway/stub_*.py` (stubs)
or future `lib/bridge_gateway/adapters/{telegram,discord,...}.py` once
real connectors exist.

Design note: all adapters produce/consume the same `BridgeMessage`
shape and write into `bridge_dir/users/<uid>/items/`. The super agent's
`do_talk` doesn't know which adapter an item came from — it just
processes items. This keeps the fan-in simple.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class BridgeMessage:
    """One inbound or outbound message at the adapter boundary."""

    id: str
    user_id: str
    source: str  # "notes" | "telegram" | "discord" | ...
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = field(default_factory=list)
    reply_to: str | None = None  # thread / message id on the source platform


class BridgeAdapter(ABC):
    """Contract for every messaging adapter.

    Adapters run under the supervisor (Phase 0 pillar 1) — each one
    a separate process so a crash in one platform can't bring down
    the others.
    """

    #: Short identifier used in logs and BridgeMessage.source.
    name: str = ""

    @abstractmethod
    def read_incoming(self) -> list[BridgeMessage]:
        """Return messages that arrived since the last call.

        Implementations handle their own pagination / cursor state and
        ensure at-least-once delivery (dedup happens downstream via the
        items/ file-naming convention).
        """
        raise NotImplementedError

    @abstractmethod
    def send_outgoing(self, message: BridgeMessage) -> bool:
        """Deliver an outbound message. Returns True on success."""
        raise NotImplementedError

    @abstractmethod
    def heartbeat(self) -> datetime:
        """Return the last time the adapter confirmed connectivity.

        The supervisor uses this to detect hung adapters.
        """
        raise NotImplementedError


def bridge_message_from_dict(data: dict) -> BridgeMessage:
    """Reconstruct a BridgeMessage from its serialized form.

    Kept here so registry + tests share one decoder.
    """
    ts_raw = data.get("timestamp")
    if isinstance(ts_raw, datetime):
        ts = ts_raw
    elif isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)
    return BridgeMessage(
        id=str(data["id"]),
        user_id=str(data["user_id"]),
        source=str(data.get("source", "unknown")),
        content=str(data.get("content", "")),
        timestamp=ts,
        tags=list(data.get("tags") or []),
        reply_to=data.get("reply_to"),
    )
