"""AdapterRegistry — fan-in across multiple adapters.

Collects messages from all enabled adapters, writes them into the
existing `bridge_dir/users/<uid>/items/` layout, and forwards outbound
messages back to the right adapter based on `BridgeMessage.source`.

Dependency-free on bridge.py: tests inject their own writer callable.
When actually wired (Step 3.1), the registry will use
`lib/bridge.py::Mira.create_item` under the hood.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Iterable

from .adapter import BridgeAdapter, BridgeMessage

log = logging.getLogger("mira.bridge_gateway.registry")


ItemWriter = Callable[[BridgeMessage], bool]
"""Function that commits an inbound BridgeMessage to the items/ store.
Passed in by caller so registry doesn't depend on bridge.py directly."""


class AdapterRegistry:
    def __init__(self, adapters: Iterable[BridgeAdapter] | None = None) -> None:
        self._adapters: dict[str, BridgeAdapter] = {}
        for a in adapters or []:
            self.register(a)

    def register(self, adapter: BridgeAdapter) -> None:
        if not adapter.name:
            raise ValueError("BridgeAdapter.name must be a non-empty identifier")
        if adapter.name in self._adapters:
            raise ValueError(f"duplicate adapter name: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def names(self) -> list[str]:
        return list(self._adapters.keys())

    def get(self, name: str) -> BridgeAdapter:
        return self._adapters[name]

    # ------------------------------------------------------------------
    # Fan-in / fan-out
    # ------------------------------------------------------------------

    def poll_all(self, item_writer: ItemWriter) -> dict[str, int]:
        """Read every adapter once, persist via item_writer, return counts.

        Failures in one adapter never abort others — logged and skipped.
        """
        counts: dict[str, int] = {}
        for name, adapter in self._adapters.items():
            try:
                messages = adapter.read_incoming()
            except Exception as e:
                log.warning("adapter %s read failed: %s", name, e)
                counts[name] = 0
                continue
            written = 0
            for msg in messages:
                try:
                    if item_writer(msg):
                        written += 1
                except Exception as e:
                    log.warning("adapter %s item write failed for msg %s: %s", name, msg.id, e)
            counts[name] = written
        return counts

    def send(self, message: BridgeMessage) -> bool:
        """Dispatch outbound message to the adapter named by message.source."""
        adapter = self._adapters.get(message.source)
        if adapter is None:
            log.warning("outbound dropped: no adapter for source %s", message.source)
            return False
        try:
            return adapter.send_outgoing(message)
        except Exception as e:
            log.warning("adapter %s send failed: %s", message.source, e)
            return False

    def heartbeats(self) -> dict[str, datetime]:
        out: dict[str, datetime] = {}
        for name, adapter in self._adapters.items():
            try:
                out[name] = adapter.heartbeat()
            except Exception as e:
                log.debug("adapter %s heartbeat unavailable: %s", name, e)
        return out
