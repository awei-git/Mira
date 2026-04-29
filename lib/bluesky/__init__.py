"""Bluesky (AT Protocol) client for Mira.

Thin urllib-based client — no third-party atproto lib dependency. Covers:

- Session management (create + auto-refresh)
- Posting (text, with optional reply + embed)
- Profile read/update
- Feed/post search
- Follow

Public API is in client.py; __init__ re-exports the thin surface Mira uses.
"""

from .client import (
    BlueskyClient,
    BlueskyError,
    get_client,
)

__all__ = ["BlueskyClient", "BlueskyError", "get_client"]
