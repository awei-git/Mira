"""TelegramBridgeAdapter — real python-telegram-bot integration.

Runtime behaviour:
- Lazy-imports `telegram` only on first poll/send. Missing module →
  permanently disabled (logs once), all operations become no-ops.
- Reads token from `secrets.yml` at `api_keys.telegram.bot_token`.
  Missing token → same disabled state.
- `read_incoming()` uses long-polling via `get_updates(offset=...)`
  with a short timeout so each agent tick can drain the queue without
  blocking its loop.
- `send_outgoing()` posts to the target chat (derived from
  `BridgeMessage.user_id` → Telegram chat_id lookup in the same
  secrets.yml block: `api_keys.telegram.users`).
- `heartbeat()` returns the last time a network call completed.

The adapter is configured to be SAFE in a default Mira install: with
no token or no library installed, it silently reports no activity and
the rest of the gateway keeps working.

Subscription structure expected in secrets.yml:
    api_keys:
      telegram:
        bot_token: "123456:ABC..."
        users:
          ang: 12345678      # user_id -> chat_id mapping
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..adapter import BridgeAdapter, BridgeMessage

log = logging.getLogger("mira.bridge_gateway.telegram")


# Sentinel returned when the adapter can't function.
_DISABLED_REASON_LIBRARY = "python-telegram-bot not installed"
_DISABLED_REASON_NO_TOKEN = "no bot_token in secrets.yml"


class TelegramBridgeAdapter(BridgeAdapter):
    name = "telegram"

    def __init__(
        self,
        *,
        token: str | None = None,
        chat_ids: dict[str, int] | None = None,
        poll_timeout: int = 1,
    ) -> None:
        self._token = token or _load_token()
        self._chat_ids = chat_ids or _load_chat_ids()
        self._poll_timeout = poll_timeout
        self._offset: int | None = None
        self._last_heartbeat = datetime.fromtimestamp(0, tz=timezone.utc)
        self._disabled_reason: str | None = None
        self._bot: Any = None  # telegram.Bot — lazy

        if not self._token:
            self._disable(_DISABLED_REASON_NO_TOKEN)

    # ---- enable/disable helpers -----------------------------------------

    def _disable(self, reason: str) -> None:
        if self._disabled_reason != reason:
            log.info("Telegram adapter disabled: %s", reason)
        self._disabled_reason = reason

    def _lazy_bot(self):
        if self._disabled_reason:
            return None
        if self._bot is not None:
            return self._bot
        try:
            import telegram  # type: ignore
        except Exception:
            self._disable(_DISABLED_REASON_LIBRARY)
            return None
        try:
            self._bot = telegram.Bot(token=self._token)
        except Exception as e:
            self._disable(f"Bot init failed: {e}")
            return None
        return self._bot

    # ---- BridgeAdapter API ----------------------------------------------

    def read_incoming(self) -> list[BridgeMessage]:
        bot = self._lazy_bot()
        if bot is None:
            return []
        try:
            updates = bot.get_updates(offset=self._offset, timeout=self._poll_timeout)
        except Exception as e:
            log.warning("Telegram get_updates failed: %s", e)
            return []
        messages: list[BridgeMessage] = []
        chat_to_user = {v: k for k, v in self._chat_ids.items()}
        for upd in updates:
            self._offset = (getattr(upd, "update_id", 0) or 0) + 1
            msg = getattr(upd, "message", None)
            if msg is None:
                continue
            chat = getattr(msg, "chat", None)
            chat_id = getattr(chat, "id", None) if chat else None
            user_id = chat_to_user.get(chat_id)
            if not user_id:
                # Unknown chat — ignore to avoid open-relay risk.
                log.info("Telegram ignored message from unregistered chat_id=%s", chat_id)
                continue
            text = getattr(msg, "text", "") or ""
            ts = getattr(msg, "date", None) or datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            messages.append(
                BridgeMessage(
                    id=f"tg_{getattr(msg, 'message_id', '')}",
                    user_id=user_id,
                    source=self.name,
                    content=text,
                    timestamp=ts,
                )
            )
        self._last_heartbeat = datetime.now(timezone.utc)
        return messages

    def send_outgoing(self, message: BridgeMessage) -> bool:
        bot = self._lazy_bot()
        if bot is None:
            return False
        chat_id = self._chat_ids.get(message.user_id)
        if chat_id is None:
            log.warning("Telegram send dropped: no chat_id for user %s", message.user_id)
            return False
        try:
            bot.send_message(chat_id=chat_id, text=message.content)
            self._last_heartbeat = datetime.now(timezone.utc)
            return True
        except Exception as e:
            log.warning("Telegram send_message failed: %s", e)
            return False

    def heartbeat(self) -> datetime:
        return self._last_heartbeat

    # ---- diagnostic -----------------------------------------------------

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled_reason


# ---------------------------------------------------------------------------
# Secrets loading helpers (kept module-level so tests can monkeypatch easily)
# ---------------------------------------------------------------------------


def _load_token() -> str | None:
    try:
        from config import load_secrets  # type: ignore
    except Exception:
        return None
    try:
        secrets = load_secrets() or {}
    except Exception:
        return None
    return ((secrets.get("api_keys", {}) or {}).get("telegram", {}).get("bot_token")) or None


def _load_chat_ids() -> dict[str, int]:
    try:
        from config import load_secrets  # type: ignore
    except Exception:
        return {}
    try:
        secrets = load_secrets() or {}
    except Exception:
        return {}
    raw = (secrets.get("api_keys", {}) or {}).get("telegram", {}).get("users", {}) or {}
    out: dict[str, int] = {}
    for user_id, chat_id in raw.items():
        try:
            out[str(user_id)] = int(chat_id)
        except (TypeError, ValueError):
            continue
    return out
