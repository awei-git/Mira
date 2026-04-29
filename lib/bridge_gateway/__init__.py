"""Phase 3 — unified messaging gateway (scaffolding).

Provides a `BridgeAdapter` ABC that future adapters (Telegram, Discord,
Slack, iMessage, ...) implement in parallel. All adapters write items
into the same `bridge_dir/users/<uid>/items/` layout the existing
Notes-based flow uses, so `core.py::do_talk` keeps working unchanged.

The existing iCloud/Notes path in `lib/bridge.py` is NOT moved here —
it continues to work as-is. Once enough adapters are stable, the
registry fan-out can switch over (Step 3.1 of the plan).

See docs/plans/hermes-integration/phase-3-messaging-gateway.md for the
full plan.
"""

from .adapter import BridgeAdapter, BridgeMessage
from .registry import AdapterRegistry
from .stub_telegram import TelegramStubAdapter
from .stub_discord import DiscordStubAdapter
from .adapters.notes import NotesBridgeAdapter
from .adapters.telegram import TelegramBridgeAdapter
from .adapters.discord import DiscordBridgeAdapter

__all__ = [
    "BridgeAdapter",
    "BridgeMessage",
    "AdapterRegistry",
    "TelegramStubAdapter",
    "DiscordStubAdapter",
    "NotesBridgeAdapter",
    "TelegramBridgeAdapter",
    "DiscordBridgeAdapter",
]
