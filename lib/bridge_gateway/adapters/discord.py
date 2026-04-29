"""DiscordBridgeAdapter — real discord.py integration (read-first).

Design:
- Read-only by default (matches plan Step 3.3). Inbound messages are
  tagged with `reader_feedback` so Phase 1 rewards recognize them as
  community signal.
- Inbound polling uses the REST API (`get_channel_messages` with
  `after` cursor) rather than the gateway/websocket — simpler, fits
  the 30s agent loop, no persistent connection required.
- `send_outgoing` is permitted but defaults to no-op unless
  `enable_replies=True` was explicitly passed (safety rail).

secrets.yml schema:
    api_keys:
      discord:
        bot_token: "Bot abcd..."
        channel_id: 1234567890123456789
        user_id: "ang"  # BridgeMessage.user_id to stamp inbound with
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..adapter import BridgeAdapter, BridgeMessage

log = logging.getLogger("mira.bridge_gateway.discord")


class DiscordBridgeAdapter(BridgeAdapter):
    name = "discord"

    def __init__(
        self,
        *,
        token: str | None = None,
        channel_id: int | None = None,
        user_id: str = "ang",
        enable_replies: bool = False,
        inbound_tag: str = "reader_feedback",
    ) -> None:
        conf = _load_config() if (token is None or channel_id is None) else {}
        self._token = token or conf.get("bot_token")
        self._channel_id = channel_id or conf.get("channel_id")
        self._user_id = user_id
        self._enable_replies = enable_replies
        self._inbound_tag = inbound_tag
        self._last_message_id: int | None = None
        self._last_heartbeat = datetime.fromtimestamp(0, tz=timezone.utc)
        self._disabled_reason: str | None = None
        self._session: Any = None

        if not self._token:
            self._disable("no bot_token in secrets.yml")
        elif not self._channel_id:
            self._disable("no channel_id in secrets.yml")

    def _disable(self, reason: str) -> None:
        if self._disabled_reason != reason:
            log.info("Discord adapter disabled: %s", reason)
        self._disabled_reason = reason

    def _headers(self) -> dict[str, str]:
        token = self._token or ""
        # Token may or may not already be prefixed with "Bot "; normalise.
        if not token.lower().startswith("bot "):
            token = f"Bot {token}"
        return {"Authorization": token, "User-Agent": "mira-bridge-gateway/1.0"}

    # ---- BridgeAdapter API ----------------------------------------------

    def read_incoming(self) -> list[BridgeMessage]:
        if self._disabled_reason:
            return []
        try:
            import urllib.request
            import urllib.parse
            import json as _json
        except Exception as e:
            self._disable(f"urllib unavailable: {e}")
            return []

        params: dict[str, Any] = {"limit": 20}
        if self._last_message_id is not None:
            params["after"] = self._last_message_id
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.warning("Discord fetch failed: %s", e)
            return []

        out: list[BridgeMessage] = []
        for entry in data:
            try:
                mid = int(entry.get("id", 0))
            except (TypeError, ValueError):
                continue
            if mid and (self._last_message_id is None or mid > self._last_message_id):
                self._last_message_id = mid
            content = entry.get("content", "") or ""
            ts_raw = entry.get("timestamp")
            try:
                ts = (
                    datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else datetime.now(timezone.utc)
                )
            except ValueError:
                ts = datetime.now(timezone.utc)
            out.append(
                BridgeMessage(
                    id=f"dc_{mid}",
                    user_id=self._user_id,
                    source=self.name,
                    content=content,
                    timestamp=ts,
                    tags=[self._inbound_tag] if self._inbound_tag else [],
                )
            )
        self._last_heartbeat = datetime.now(timezone.utc)
        return out

    def send_outgoing(self, message: BridgeMessage) -> bool:
        if self._disabled_reason:
            return False
        if not self._enable_replies:
            log.debug("Discord send skipped: enable_replies=False")
            return False
        try:
            import urllib.request
            import json as _json
        except Exception:
            return False
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages"
        payload = _json.dumps({"content": message.content}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={**self._headers(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 300
        except Exception as e:
            log.warning("Discord send failed: %s", e)
            return False
        self._last_heartbeat = datetime.now(timezone.utc)
        return ok

    def heartbeat(self) -> datetime:
        return self._last_heartbeat

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason


# ---------------------------------------------------------------------------
# Secrets loading
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    try:
        from config import load_secrets  # type: ignore
    except Exception:
        return {}
    try:
        secrets = load_secrets() or {}
    except Exception:
        return {}
    raw = (secrets.get("api_keys", {}) or {}).get("discord", {}) or {}
    out: dict[str, Any] = {}
    if "bot_token" in raw:
        out["bot_token"] = raw["bot_token"]
    if "channel_id" in raw:
        try:
            out["channel_id"] = int(raw["channel_id"])
        except (TypeError, ValueError):
            pass
    return out
