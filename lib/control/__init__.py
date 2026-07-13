"""Mira control-plane storage and projections.

The control plane is additive during the API migration. It reads existing
bridge/task files and projects them into Postgres tables under a dedicated
schema; it does not mutate source bridge data.
"""

from .projection import item_from_rows
from .repository import ControlRepository, sync_user_from_legacy

__all__ = ["ControlRepository", "item_from_rows", "sync_user_from_legacy"]
