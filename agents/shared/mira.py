"""Mira — file-based iPhone <-> Mac messaging over iCloud Drive.

Thin wrapper around MiraBridge library, adding Mira-specific defaults
and backward-compatible aliases.
"""
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Add MiraBridge to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "MiraBridge" / "python"))

from mira_bridge import Bridge, _utc_iso, _msg_id, _normalize_sender, _atomic_write, _ensure_downloaded  # noqa: E402
from config import MIRA_DIR  # noqa: E402

# Re-export for backward compatibility
Message = None  # Legacy — no longer used


# ---------------------------------------------------------------------------
# Legacy Message class (for poll() backward compat during migration)
# ---------------------------------------------------------------------------

import json
import logging

log = logging.getLogger("mira")


@dataclass
class Message:
    id: str
    sender: str
    timestamp: str
    content: str
    msg_type: str = "text"
    thread_id: str = ""
    priority: str = "normal"
    metadata: dict = field(default_factory=dict)
    # User access control (set by super agent before dispatch)
    user_id: str = "ang"
    user_role: str = "admin"
    model_restriction: str | None = None
    content_filter: bool = False
    allowed_agents: list = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "Message | None":
        _ensure_downloaded(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                id=data["id"],
                sender=data["sender"],
                timestamp=data["timestamp"],
                content=data["content"],
                msg_type=data.get("type", "text"),
                thread_id=data.get("thread_id", ""),
                priority=data.get("priority", "normal"),
                metadata=data.get("metadata", {}),
                user_id=data.get("user_id", "ang"),
                user_role=data.get("user_role", "admin"),
                model_restriction=data.get("model_restriction"),
                content_filter=data.get("content_filter", False),
                allowed_agents=data.get("allowed_agents", []),
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            log.error("Failed to read message %s: %s", path.name, e)
            return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = d.pop("msg_type")
        return d


# ---------------------------------------------------------------------------
# Mira — Bridge subclass with Mira-specific defaults
# ---------------------------------------------------------------------------

class Mira(Bridge):
    """Mira agent bridge — wraps MiraBridge with Mira defaults.

    Default bridge_dir = MIRA_DIR from config.
    Default user_id = "ang".
    Adds v1 backward-compatible method aliases.
    """

    def __init__(self, bridge_dir: Path = MIRA_DIR, user_id: str = "ang"):
        super().__init__(bridge_dir, user_id)

    @classmethod
    def for_all_users(cls, bridge_dir: Path = MIRA_DIR) -> list["Mira"]:
        bridge_dir = Path(bridge_dir)
        users_dir = bridge_dir / "users"
        if not users_dir.exists():
            return [cls(bridge_dir)]
        instances = []
        for user_dir in sorted(users_dir.iterdir()):
            if user_dir.is_dir() and not user_dir.name.startswith("."):
                instances.append(cls(bridge_dir, user_id=user_dir.name))
        return instances or [cls(bridge_dir)]

    # --- v1 API compatibility aliases ---

    def update_task_status(self, task_id: str, status: str,
                           agent_message: str = "",
                           result_path: str = ""):
        self.update_status(task_id, status,
                           agent_message=agent_message,
                           result_path=result_path)

    def emit_status(self, task_id: str, text: str, icon: str = "gear"):
        self.emit_status_card(task_id, text, icon)

    def append_task_message(self, task_id: str, sender: str, content: str):
        self.append_message(task_id, sender, content)

    def set_task_tags(self, task_id: str, tags: list[str]):
        self.set_tags(task_id, tags)

    def task_exists(self, task_id: str) -> bool:
        return self.item_exists(task_id)

    def create_task(self, task_id: str, title: str, first_message: str,
                    sender: str = "user", tags: list[str] | None = None,
                    origin: str = "user") -> dict:
        return self.create_item(task_id, "request", title, first_message,
                                sender=sender, tags=tags, origin=origin)

    # --- Legacy no-ops ---

    def poll(self) -> list:
        return []

    def ack(self, msg_id: str, status: str = "received"):
        pass

    def mark_processed(self, msg_path: Path):
        pass

    def post(self, content: str, sender: str = "agent",
             thread_id: str = "", msg_type: str = "text") -> str:
        return _msg_id()

    def reply(self, msg_id: str, recipient: str, content: str,
              thread_id: str = "") -> str:
        item_id = thread_id or msg_id
        self.append_message(item_id, "agent", content)
        return _msg_id()
