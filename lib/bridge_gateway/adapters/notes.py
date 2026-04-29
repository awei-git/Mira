"""NotesBridgeAdapter — wraps the existing `lib/bridge.py::Mira` via composition.

Purpose: expose the Notes/iCloud path through the Phase 3
`BridgeAdapter` contract **without modifying or replacing** the
existing bridge.py. Other subsystems (super.do_talk, etc.) continue
reading items directly from disk; this adapter's role is to let the
new `AdapterRegistry` fan-out outbound messages to Notes alongside
Telegram/Discord/etc.

Semantics:
- `read_incoming()` returns an empty list by default. Items arriving
  via Notes are already surfaced to the super agent through the
  existing filesystem-based flow; re-polling them here would double-
  deliver. When the registry fully owns ingestion (Step 3.1 complete),
  this method can switch to scanning items/ and marking them seen.
- `send_outgoing(msg)` appends to the target item (creating it if
  needed) via the wrapped Mira instance — so replies land where the
  user expects.
- `heartbeat()` reads `bridge_dir/heartbeat.json` (written by the
  agent loop) to report last connectivity.

This keeps `NotesBridgeAdapter` strictly **additive** — zero behaviour
change when the registry isn't calling it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR

# Lazy import: `bridge` pulls in the `mira_bridge` sibling package
# (lives in the MiraBridge/ repo, not on PyPI). CI doesn't have that
# repo cloned, so importing it at module load fails the
# `from bridge_gateway import ...` chain. The class instance is the
# only thing that needs Mira, so defer the import until __init__.
from ..adapter import BridgeAdapter, BridgeMessage

log = logging.getLogger("mira.bridge_gateway.notes")


class NotesBridgeAdapter(BridgeAdapter):
    name = "notes"

    def __init__(self, bridge_dir: Path | str | None = None, user_id: str = "ang"):
        self._bridge_dir = Path(bridge_dir) if bridge_dir else MIRA_DIR
        self._user_id = user_id
        from bridge import Mira  # lazy: see top-of-file note

        self._mira = Mira(bridge_dir=self._bridge_dir, user_id=user_id)

    # ---- read_incoming: intentionally passive ---------------------------

    def read_incoming(self) -> list[BridgeMessage]:
        """Notes items are surfaced via the existing filesystem flow;
        the adapter does not re-poll to avoid double-delivery."""
        return []

    # ---- send_outgoing: delegate to Mira --------------------------------

    def send_outgoing(self, message: BridgeMessage) -> bool:
        """Reply to an existing item or create one if missing.

        We use `message.reply_to` as the target item id when present;
        otherwise `message.id` is both the new item's id and target.
        """
        target_id = message.reply_to or message.id
        try:
            if self._mira.item_exists(target_id):
                self._mira.append_message(target_id, "agent", message.content)
            else:
                # Create a fresh request-style item if nothing to reply to.
                self._mira.create_item(
                    target_id,
                    item_type="request",
                    title=(message.content.splitlines()[0] if message.content else target_id)[:80],
                    first_message=message.content,
                    sender="agent",
                    tags=list(message.tags or []),
                    origin="agent",
                )
            return True
        except Exception as e:
            log.warning("NotesBridgeAdapter send failed for %s: %s", target_id, e)
            return False

    # ---- heartbeat: read shared heartbeat.json --------------------------

    def heartbeat(self) -> datetime:
        hb_file = self._bridge_dir / "heartbeat.json"
        if not hb_file.exists():
            return datetime.fromtimestamp(0, tz=timezone.utc)
        try:
            data = json.loads(hb_file.read_text(encoding="utf-8"))
            ts = data.get("timestamp") or data.get("last_updated")
            if ts:
                return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            log.debug("NotesBridgeAdapter heartbeat parse failed: %s", e)
        return datetime.fromtimestamp(0, tz=timezone.utc)
