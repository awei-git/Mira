"""Real bridge adapters (vs. the in-memory stubs in the parent package)."""

from .notes import NotesBridgeAdapter
from .telegram import TelegramBridgeAdapter
from .discord import DiscordBridgeAdapter

__all__ = ["NotesBridgeAdapter", "TelegramBridgeAdapter", "DiscordBridgeAdapter"]
