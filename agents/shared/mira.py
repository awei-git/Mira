"""Mira — file-based iPhone ↔ Mac messaging over iCloud Drive.

Protocol (unified MiraItem model):
    heartbeat.json    — agent liveness
    manifest.json     — index of all items + timestamps
    items/            — one file per MiraItem, AGENT-OWNED
    commands/         — user → agent commands (iOS writes, agent consumes)
    archive/          — old items moved here

Item types: request, discussion, feed
Statuses: queued, working, needs-input, done, failed, archived
"""
import json
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import MIRA_DIR

log = logging.getLogger("mira")


def _utc_iso() -> str:
    """UTC timestamp in iOS-compatible ISO8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg_id() -> str:
    return uuid.uuid4().hex[:8]


def _atomic_write(path: Path, data: dict):
    """Write JSON atomically via tmp+rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.rename(path)


# ---------------------------------------------------------------------------
# iCloud Drive helper
# ---------------------------------------------------------------------------

def _ensure_downloaded(path: Path):
    """Force iCloud Drive to download a file if it's a cloud placeholder."""
    try:
        subprocess.run(["brctl", "download", str(path)],
                       capture_output=True, timeout=10)
        for _ in range(5):
            try:
                path.read_bytes()
                return
            except OSError:
                time.sleep(0.5)
    except Exception as e:
        log.warning("brctl download failed for %s: %s", path.name, e)


# ---------------------------------------------------------------------------
# Legacy inbox message (for poll() backward compat during migration)
# ---------------------------------------------------------------------------

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
            )
        except (json.JSONDecodeError, KeyError, OSError) as e:
            log.error("Failed to read message %s: %s", path.name, e)
            return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = d.pop("msg_type")
        return d


# ---------------------------------------------------------------------------
# Mira
# ---------------------------------------------------------------------------

class Mira:
    """File-based message queue over iCloud Drive — multi-user item protocol.

    Each user has their own namespace: users/{user_id}/items/, commands/, etc.
    Shared items live in shared/items/.
    Default user_id is 'ang' for backward compatibility.
    """

    def __init__(self, bridge_dir: Path = MIRA_DIR, user_id: str = "ang"):
        self.bridge_dir = bridge_dir
        self.user_id = user_id

        # Per-user paths
        self.user_dir = bridge_dir / "users" / user_id
        self.items_dir = self.user_dir / "items"
        self.commands_dir = self.user_dir / "commands"
        self.archive_dir = self.user_dir / "archive"
        self.manifest_file = self.user_dir / "manifest.json"
        self.user_config_file = self.user_dir / "config.json"
        self.processed_dir = self.user_dir / ".processed"

        # Global paths (only heartbeat + profiles at root)
        self.heartbeat_file = bridge_dir / "heartbeat.json"
        self.profiles_file = bridge_dir / "profiles.json"

        # Ensure directories exist (only per-user, nothing at root)
        for d in [self.items_dir, self.commands_dir, self.archive_dir,
                  self.processed_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._last_heartbeat_data: str = ""
        self._last_heartbeat_time: float = 0

        # Load user config (agent name etc.)
        self._user_config = self._load_user_config()

    @property
    def agent_name(self) -> str:
        return self._user_config.get("agent_name", "Mira")

    def _load_user_config(self) -> dict:
        if self.user_config_file.exists():
            try:
                return json.loads(self.user_config_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"agent_name": "Mira", "display_name": self.user_id}

    @classmethod
    def for_all_users(cls, bridge_dir: Path = MIRA_DIR) -> list["Mira"]:
        """Create Mira instances for all registered users."""
        users_dir = bridge_dir / "users"
        if not users_dir.exists():
            return [cls(bridge_dir)]
        instances = []
        for user_dir in sorted(users_dir.iterdir()):
            if user_dir.is_dir() and not user_dir.name.startswith("."):
                instances.append(cls(bridge_dir, user_id=user_dir.name))
        return instances or [cls(bridge_dir)]

    # ==================================================================
    # Item CRUD — agent-owned, atomic writes
    # ==================================================================

    def create_item(self, item_id: str, item_type: str, title: str,
                    first_message: str, sender: str = "user",
                    tags: list[str] | None = None,
                    origin: str = "user",
                    quick: bool = False,
                    parent_id: str = "") -> dict:
        """Create a new item (request, discussion, or feed)."""
        now = _utc_iso()
        item = {
            "id": item_id,
            "type": item_type,
            "title": title,
            "status": "queued",
            "tags": tags or [],
            "origin": origin,
            "pinned": False,
            "quick": quick,
            "parent_id": parent_id,
            "created_at": now,
            "updated_at": now,
            "messages": [
                {"id": _msg_id(), "sender": sender,
                 "content": first_message, "timestamp": now, "kind": "text"},
            ],
            "error": None,
            "result_path": None,
        }
        self._write_item(item)
        self._update_manifest()
        return item

    # --- Convenience creators ---

    def create_task(self, task_id: str, title: str, first_message: str,
                    sender: str = "user", tags: list[str] | None = None,
                    origin: str = "user") -> dict:
        """Create a request item. API-compatible with v1."""
        return self.create_item(task_id, "request", title, first_message,
                                sender=sender, tags=tags, origin=origin)

    def create_feed(self, feed_id: str, title: str, content: str,
                    tags: list[str] | None = None) -> dict:
        """Create a feed item (briefing, journal, reflection)."""
        item = self.create_item(feed_id, "feed", title, content,
                                sender="agent", tags=tags, origin="agent")
        item["status"] = "done"
        self._write_item(item)
        self._update_manifest()
        return item

    def create_discussion(self, disc_id: str, title: str, first_message: str,
                          sender: str = "agent", tags: list[str] | None = None,
                          parent_id: str = "") -> dict:
        """Create a discussion (Mira-initiated or from feed comment)."""
        item = self.create_item(disc_id, "discussion", title, first_message,
                                sender=sender, tags=tags, origin="agent" if sender == "agent" else "user",
                                parent_id=parent_id)
        if sender == "agent":
            item["status"] = "needs-input"
        self._write_item(item)
        self._update_manifest()
        return item

    # --- Item updates ---

    def append_message(self, item_id: str, sender: str, content: str,
                       kind: str = "text") -> dict | None:
        """Append a message to an item. Returns updated item or None."""
        item = self._read_item(item_id)
        if not item:
            log.warning("append_message: item %s not found", item_id)
            return None
        item["messages"].append({
            "id": _msg_id(), "sender": sender,
            "content": content, "timestamp": _utc_iso(), "kind": kind,
        })
        item["updated_at"] = _utc_iso()
        # Reopen if user replies to done/failed
        if sender != "agent" and item["status"] in ("done", "failed"):
            item["status"] = "queued"
        self._write_item(item)
        self._update_manifest()
        return item

    def update_status(self, item_id: str, status: str,
                      agent_message: str = "",
                      result_path: str = "",
                      error: dict | None = None):
        """Update item status with optional message and error."""
        item = self._read_item(item_id)
        if not item:
            log.warning("update_status: item %s not found", item_id)
            return
        item["status"] = status
        item["updated_at"] = _utc_iso()
        if agent_message:
            item["messages"].append({
                "id": _msg_id(), "sender": "agent",
                "content": agent_message, "timestamp": _utc_iso(),
                "kind": "text",
            })
        if error:
            item["error"] = {
                "code": error.get("code", "internal"),
                "message": error.get("message", "未知错误"),
                "retryable": error.get("retryable", False),
                "timestamp": _utc_iso(),
            }
            item["messages"].append({
                "id": _msg_id(), "sender": "agent",
                "content": item["error"]["message"],
                "timestamp": _utc_iso(), "kind": "error",
            })
        if result_path:
            item["result_path"] = result_path
        self._write_item(item)
        self._update_manifest()

    def emit_status_card(self, item_id: str, text: str, icon: str = "gear"):
        """Emit a status card message (progress indicator)."""
        self.append_message(
            item_id, "agent",
            json.dumps({"type": "status", "text": text, "icon": icon},
                       ensure_ascii=False),
            kind="status_card",
        )

    def set_tags(self, item_id: str, tags: list[str]):
        """Update item tags."""
        item = self._read_item(item_id)
        if not item:
            return
        item["tags"] = tags
        item["updated_at"] = _utc_iso()
        self._write_item(item)

    def item_exists(self, item_id: str) -> bool:
        return (self.items_dir / f"{item_id}.json").exists()

    # --- v1 API compatibility wrappers ---

    def update_task_status(self, task_id: str, status: str,
                           agent_message: str = "",
                           result_path: str = ""):
        """v1-compatible: update_task_status → update_status."""
        self.update_status(task_id, status,
                           agent_message=agent_message,
                           result_path=result_path)

    def emit_status(self, task_id: str, text: str, icon: str = "gear"):
        """v1-compatible: emit_status → emit_status_card."""
        self.emit_status_card(task_id, text, icon)

    def append_task_message(self, task_id: str, sender: str, content: str):
        """v1-compatible: append_task_message → append_message."""
        self.append_message(task_id, sender, content)

    def set_task_tags(self, task_id: str, tags: list[str]):
        """v1-compatible: set_task_tags → set_tags."""
        self.set_tags(task_id, tags)

    def task_exists(self, task_id: str) -> bool:
        """v1-compatible."""
        return self.item_exists(task_id)

    # ==================================================================
    # Sharing
    # ==================================================================

    def share_item(self, item_id: str):
        """Copy an item to shared/items/ so all users can see it."""
        item = self._read_item(item_id)
        if not item:
            return
        item["shared_by"] = self.user_id
        shared_items = self.bridge_dir / "shared" / "items"
        shared_items.mkdir(parents=True, exist_ok=True)
        _atomic_write(shared_items / f"{item_id}.json", item)
        # Update shared manifest
        self._update_shared_manifest()
        log.info("Shared item %s from %s", item_id, self.user_id)

    def _update_shared_manifest(self):
        """Rebuild shared/manifest.json."""
        shared_items = self.bridge_dir / "shared" / "items"
        entries = []
        if shared_items.exists():
            for path in shared_items.glob("*.json"):
                if path.suffix == ".tmp":
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entries.append({
                        "id": data["id"],
                        "type": data.get("type", "request"),
                        "status": data.get("status", "done"),
                        "updated_at": data.get("updated_at", ""),
                    })
                except (json.JSONDecodeError, OSError, KeyError):
                    continue
        manifest_file = self.bridge_dir / "shared" / "manifest.json"
        _atomic_write(manifest_file, {"updated_at": _utc_iso(), "items": entries})

    # ==================================================================
    # Todo List
    # ==================================================================

    @property
    def todos_file(self) -> Path:
        return self.user_dir / "todos.json"

    def load_todos(self) -> list[dict]:
        if not self.todos_file.exists():
            return []
        try:
            return json.loads(self.todos_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def save_todos(self, todos: list[dict]):
        _atomic_write(self.todos_file, todos)

    def add_todo(self, title: str, priority: str = "medium") -> dict:
        todos = self.load_todos()
        todo = {
            "id": f"todo_{uuid.uuid4().hex[:8]}",
            "title": title,
            "priority": priority,
            "status": "pending",
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
            "response": None,
        }
        todos.append(todo)
        self.save_todos(todos)
        return todo

    def update_todo(self, todo_id: str, status: str = "",
                    response: str = "") -> dict | None:
        todos = self.load_todos()
        for t in todos:
            if t["id"] == todo_id:
                if status:
                    t["status"] = status
                if response:
                    t["response"] = response
                t["updated_at"] = _utc_iso()
                self.save_todos(todos)
                return t
        return None

    def remove_todo(self, todo_id: str):
        todos = [t for t in self.load_todos() if t["id"] != todo_id]
        self.save_todos(todos)

    def get_next_todo(self) -> dict | None:
        """Get highest priority pending todo for agent to work on."""
        todos = self.load_todos()
        pending = [t for t in todos if t["status"] == "pending"]
        if not pending:
            return None
        priority_order = {"high": 0, "medium": 1, "low": 2}
        pending.sort(key=lambda t: (priority_order.get(t["priority"], 1), t["created_at"]))
        return pending[0]

    # ==================================================================
    # Heartbeat
    # ==================================================================

    def heartbeat(self, agent_status: dict | None = None):
        """Write heartbeat so phone knows agent is alive. Throttled."""
        data = {
            "timestamp": _utc_iso(),
            "status": "online",
        }
        if agent_status:
            data["busy"] = agent_status.get("busy", False)
            data["active_count"] = agent_status.get("active_count", 0)

        status_key = json.dumps({k: v for k, v in data.items()
                                  if k != "timestamp"}, sort_keys=True)
        now = time.time()
        if (status_key == self._last_heartbeat_data
                and now - self._last_heartbeat_time < 60):
            return

        _atomic_write(self.heartbeat_file, data)
        self._last_heartbeat_data = status_key
        self._last_heartbeat_time = now

    # ==================================================================
    # Command polling (iOS → agent)
    # ==================================================================

    def poll_commands(self) -> list[dict]:
        """Read and consume command files from iOS.

        iCloud can hold locks on files (Resource deadlock avoided / EAGAIN).
        Strategy: copy to temp, read from temp, mark as processed.
        """
        import shutil, tempfile

        try:
            subprocess.run(["brctl", "download", str(self.commands_dir)],
                           capture_output=True, timeout=10)
        except Exception:
            pass

        commands = []
        for path in sorted(self.commands_dir.glob("*.json")):
            if path.suffix == ".tmp":
                continue
            # Skip already-processed commands
            marker = self.processed_dir / f"cmd_{path.stem}"
            if marker.exists():
                try:
                    path.unlink()
                    marker.unlink()
                except OSError:
                    pass
                continue
            try:
                # Copy to temp to avoid iCloud lock
                tmp = Path(tempfile.mktemp(suffix=".json"))
                shutil.copy2(str(path), str(tmp))
                data = json.loads(tmp.read_text(encoding="utf-8"))
                tmp.unlink()
                commands.append(data)
                # Mark as processed
                marker.write_text(_utc_iso(), encoding="utf-8")
                try:
                    path.unlink()
                    marker.unlink()
                except OSError:
                    pass
            except (json.JSONDecodeError, OSError, shutil.Error) as e:
                log.warning("poll_commands: retry next cycle %s: %s", path.name, e)
        return commands

    # ==================================================================
    # Legacy stubs (no-op, prevent old code from creating root folders)
    # ==================================================================

    def poll(self) -> list:
        """Legacy: no-op. Commands are now via poll_commands()."""
        return []

    def ack(self, msg_id: str, status: str = "received"):
        """Legacy: no-op."""
        pass

    def mark_processed(self, msg_path: Path):
        """Legacy: no-op."""
        pass

    def reply(self, msg_id: str, recipient: str, content: str,
              thread_id: str = "") -> str:
        """Legacy: no-op. Agent replies go through update_task_status()."""
        return _msg_id()

    def post(self, content: str, sender: str = "agent",
             thread_id: str = "", msg_type: str = "text") -> str:
        """Legacy: no-op (feeds/journals now created via create_feed)."""
        return _msg_id()

    # ==================================================================
    # Maintenance
    # ==================================================================

    def cleanup_old(self, days: int = 3):
        """Archive old done items."""
        self.archive_done_items(days=7)

    def archive_done_items(self, days: int = 7):
        """Move done/failed items older than N days to archive/."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        changed = False
        for path in list(self.items_dir.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item.get("status") not in ("done", "failed"):
                    continue
                # Don't archive pinned items
                if item.get("pinned"):
                    continue
                updated = datetime.fromisoformat(
                    item["updated_at"].replace("Z", "+00:00"))
                if updated < cutoff:
                    item["status"] = "archived"
                    dest = self.archive_dir / path.name
                    _atomic_write(dest, item)
                    path.unlink()
                    changed = True
                    log.info("Archived item %s", item["id"])
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        if changed:
            self._update_manifest()

    def archive_thread(self, thread_id: str):
        """Archive a specific item by ID."""
        item = self._read_item(thread_id)
        if not item:
            return
        item["status"] = "archived"
        dest = self.archive_dir / f"{thread_id}.json"
        _atomic_write(dest, item)
        src = self.items_dir / f"{thread_id}.json"
        if src.exists():
            src.unlink()
        self._update_manifest()
        log.info("Archived item %s", thread_id)

    # ==================================================================
    # Manifest
    # ==================================================================

    def _update_manifest(self):
        """Rebuild manifest.json from items/ directory."""
        entries = []
        for path in self.items_dir.glob("*.json"):
            if path.suffix == ".tmp":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append({
                    "id": data["id"],
                    "type": data.get("type", "request"),
                    "status": data.get("status", "queued"),
                    "updated_at": data.get("updated_at", ""),
                })
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        manifest = {
            "updated_at": _utc_iso(),
            "items": entries,
        }
        _atomic_write(self.manifest_file, manifest)

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _read_item(self, item_id: str) -> dict | None:
        path = self.items_dir / f"{item_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_item(self, item: dict):
        path = self.items_dir / f"{item['id']}.json"
        _atomic_write(path, item)
