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
    """UTC timestamp in iOS-compatible ISO8601 format with milliseconds."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _msg_id() -> str:
    return uuid.uuid4().hex[:8]


def _normalize_sender(sender: str) -> str:
    """Normalize sender to 'user' or 'agent'. Consolidates iphone/ang → user."""
    if sender in ("iphone", "ang", "user"):
        return "user"
    return sender  # "agent" stays as-is


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
        self.ledger_file = self.user_dir / "command_ledger.json"

        # Global paths (only heartbeat + profiles at root)
        self.heartbeat_file = bridge_dir / "heartbeat.json"
        self.profiles_file = bridge_dir / "profiles.json"

        # Ensure directories exist (only per-user, nothing at root)
        for d in [self.items_dir, self.commands_dir, self.archive_dir]:
            d.mkdir(parents=True, exist_ok=True)

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
        sender = _normalize_sender(sender)
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
        self._update_manifest(item)
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
        self._update_manifest(item)
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
        self._update_manifest(item)
        return item

    # --- Item updates ---

    def append_message(self, item_id: str, sender: str, content: str,
                       kind: str = "text") -> dict | None:
        """Append a message to an item. Returns updated item or None."""
        sender = _normalize_sender(sender)
        item = self._read_item(item_id)
        if not item:
            log.warning("append_message: item %s not found", item_id)
            return None

        # Dedup: skip if last message from same sender has identical content
        recent_same = [m for m in item["messages"][-5:] if m["sender"] == sender]
        if recent_same and recent_same[-1]["content"] == content and kind == "text":
            log.debug("Skipping duplicate message from %s in %s", sender, item_id)
            return item

        item["messages"].append({
            "id": _msg_id(), "sender": sender,
            "content": content, "timestamp": _utc_iso(), "kind": kind,
        })
        item["updated_at"] = _utc_iso()
        # Reopen if user replies to done/failed
        if sender != "agent" and item["status"] in ("done", "failed"):
            item["status"] = "queued"
        self._write_item(item)
        self._update_manifest(item)
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
        # Clean up status cards on terminal state
        if status in ("done", "failed", "needs-input"):
            item["messages"] = [m for m in item["messages"]
                               if m.get("kind") != "status_card"]
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
        self._update_manifest(item)

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
        self._update_manifest(item)

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

    def add_todo(self, title: str, priority: str = "medium",
                 tags: list[str] | None = None) -> dict:
        todos = self.load_todos()
        todo = {
            "id": f"todo_{uuid.uuid4().hex[:8]}",
            "title": title,
            "priority": priority,
            "status": "pending",
            "tags": tags or [],
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
            "followups": [],
        }
        todos.append(todo)
        self.save_todos(todos)
        return todo

    def add_followup(self, todo_id: str, content: str,
                     source: str = "agent") -> dict | None:
        """Append a followup to a todo (progress, result, related finding)."""
        todos = self.load_todos()
        for t in todos:
            if t["id"] == todo_id:
                # Migrate legacy 'response' field
                if "followups" not in t:
                    t["followups"] = []
                    if t.get("response"):
                        t["followups"].append({
                            "content": t["response"],
                            "source": "agent",
                            "timestamp": t.get("updated_at", _utc_iso()),
                        })
                t["followups"].append({
                    "content": content,
                    "source": source,
                    "timestamp": _utc_iso(),
                })
                t["updated_at"] = _utc_iso()
                self.save_todos(todos)
                return t
        return None

    def update_todo(self, todo_id: str, status: str = "",
                    priority: str = "", title: str = "") -> dict | None:
        todos = self.load_todos()
        for t in todos:
            if t["id"] == todo_id:
                if status:
                    t["status"] = status
                if priority:
                    t["priority"] = priority
                if title:
                    t["title"] = title
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
        """Write heartbeat so phone knows agent is alive.

        Always updates timestamp (phone uses recency to determine online
        status). Throttle only suppresses redundant iCloud syncs when
        status fields haven't changed AND last write was <25s ago.
        The 25s limit ensures the 180s isRecent window on iOS is never
        exceeded even with iCloud sync delays.
        """
        data = {
            "timestamp": _utc_iso(),
            "status": "online",
        }
        if agent_status:
            data["busy"] = agent_status.get("busy", False)
            data["active_count"] = agent_status.get("active_count", 0)

        _atomic_write(self.heartbeat_file, data)

    # ==================================================================
    # Command polling (iOS → agent)
    # ==================================================================

    # ------------------------------------------------------------------
    # Command ledger — reliable delivery tracking
    # ------------------------------------------------------------------

    def _load_ledger(self) -> dict:
        if self.ledger_file.exists():
            try:
                return json.loads(self.ledger_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"processed": {}}

    def _save_ledger(self, ledger: dict):
        ledger["updated_at"] = _utc_iso()
        _atomic_write(self.ledger_file, ledger)

    def _prune_ledger(self, ledger: dict, max_age_days: int = 7):
        """Remove ledger entries older than max_age_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        processed = ledger.get("processed", {})
        pruned = {}
        for cmd_id, ts in processed.items():
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt > cutoff:
                    pruned[cmd_id] = ts
            except (ValueError, TypeError):
                pruned[cmd_id] = ts  # keep unparseable entries
        ledger["processed"] = pruned

    # ------------------------------------------------------------------
    # Command polling — ledger-based, never lose commands
    # ------------------------------------------------------------------

    def poll_commands(self) -> list[dict]:
        """Read command files from iOS with reliable delivery via ledger.

        Rules:
        1. Never delete a command we can't read — leave for next cycle.
        2. Ledger is the "processed" signal, not file deletion.
        3. Write ledger before deleting command file.
        """
        import shutil, tempfile

        try:
            subprocess.run(["brctl", "download", str(self.commands_dir)],
                           capture_output=True, timeout=10)
        except Exception:
            pass

        ledger = self._load_ledger()
        processed = ledger.get("processed", {})
        commands = []
        files_to_delete = []

        # Pass 1: Read all commands, decide what's new vs already processed
        for path in sorted(self.commands_dir.glob("*.json")):
            if path.name.endswith(".tmp"):
                continue

            parts = path.stem.split("_")
            file_id = parts[-1] if len(parts) >= 4 else path.stem

            if file_id in processed:
                files_to_delete.append(path)
                continue

            data = self._try_read_command(path)
            if data is None:
                continue  # leave for next cycle

            cmd_id = data.get("id", file_id)
            if cmd_id in processed:
                files_to_delete.append(path)
                continue

            commands.append(data)
            processed[cmd_id] = _utc_iso()
            files_to_delete.append(path)

        # Pass 2: Save ledger FIRST (crash-safe: worst case = re-process)
        if commands:
            ledger["processed"] = processed
            self._prune_ledger(ledger)
            self._save_ledger(ledger)

        # Pass 3: Delete files (best-effort, idempotent)
        for path in files_to_delete:
            try:
                path.unlink()
            except OSError:
                pass

        # Drain pending queue (requeued commands from previous cycles)
        pending_path = self.user_dir / ".pending_commands.json"
        if pending_path.exists():
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
                if pending:
                    commands = pending + commands
                    pending_path.unlink()
                    log.info("Drained %d pending commands from queue", len(pending))
            except (json.JSONDecodeError, OSError):
                pass

        # Migrate: clean up legacy .processed/ directory if it exists
        legacy_dir = self.user_dir / ".processed"
        if legacy_dir.exists():
            try:
                import shutil as _sh
                _sh.rmtree(str(legacy_dir), ignore_errors=True)
            except Exception:
                pass

        return commands

    def requeue_command(self, cmd: dict):
        """Re-queue a command for next cycle (when agent was busy).

        Appends to a local pending queue file (NOT iCloud commands/).
        poll_commands() checks this file first.
        """
        pending_path = self.user_dir / ".pending_commands.json"
        pending = []
        if pending_path.exists():
            try:
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pending = []
        # Deduplicate by item_id
        existing_ids = {c.get("item_id") for c in pending}
        if cmd.get("item_id") not in existing_ids:
            pending.append(cmd)
            tmp = pending_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.rename(pending_path)
            log.info("Requeued command %s to pending queue (%d total)", cmd.get("id", "?"), len(pending))

    def _try_read_command(self, path: Path) -> dict | None:
        """Try to read a command file. Returns None if unreadable."""
        import shutil, tempfile
        # Attempt 1: copy to tmp to avoid iCloud lock
        try:
            tmp = Path(tempfile.mktemp(suffix=".json"))
            shutil.copy2(str(path), str(tmp))
            data = json.loads(tmp.read_text(encoding="utf-8"))
            tmp.unlink()
            return data
        except (OSError, shutil.Error, json.JSONDecodeError):
            pass
        # Attempt 2: force download specific file, then direct read
        try:
            subprocess.run(["brctl", "download", str(path)],
                           capture_output=True, timeout=10)
            import time as _t
            _t.sleep(1)
            data = json.loads(path.read_text(encoding="utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            log.debug("poll_commands: unreadable (iCloud placeholder?): %s", path.name)
            return None

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
        """Append agent reply to item. msg_id is the item_id."""
        item_id = thread_id or msg_id
        self.append_message(item_id, "agent", content)
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

    def _update_manifest(self, changed_item: dict | None = None):
        """Update manifest.json incrementally. Only full rebuild if manifest missing/corrupt.

        If changed_item is provided, updates only that entry in the manifest.
        Otherwise does a full rebuild (startup, recovery).
        """
        # Try incremental update first
        if changed_item and self.manifest_file.exists():
            try:
                manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
                items = manifest.get("items", [])
                item_id = changed_item["id"]

                # Find and update existing entry, or append new
                found = False
                for i, entry in enumerate(items):
                    if entry.get("id") == item_id:
                        items[i] = {
                            "id": item_id,
                            "type": changed_item.get("type", "request"),
                            "status": changed_item.get("status", "queued"),
                            "updated_at": changed_item.get("updated_at", ""),
                        }
                        found = True
                        break
                if not found:
                    items.append({
                        "id": item_id,
                        "type": changed_item.get("type", "request"),
                        "status": changed_item.get("status", "queued"),
                        "updated_at": changed_item.get("updated_at", ""),
                    })

                manifest["items"] = items
                manifest["updated_at"] = _utc_iso()
                manifest["generation"] = manifest.get("generation", 0) + 1
                _atomic_write(self.manifest_file, manifest)
                return
            except (json.JSONDecodeError, OSError, KeyError):
                pass  # Fall through to full rebuild

        # Full rebuild (startup, corrupt manifest, no changed_item)
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

        old_gen = 0
        if self.manifest_file.exists():
            try:
                old = json.loads(self.manifest_file.read_text(encoding="utf-8"))
                old_gen = old.get("generation", 0)
            except (json.JSONDecodeError, OSError):
                pass

        manifest = {
            "updated_at": _utc_iso(),
            "generation": old_gen + 1,
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
