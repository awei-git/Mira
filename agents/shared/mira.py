"""Mira — file-based iPhone ↔ Mac messaging over iCloud Drive.

Protocol:
    inbox/   — phone writes messages here, Mac reads
    outbox/  — Mac writes replies here, phone reads
    ack/     — Mac writes ack files here, phone polls for status
    .processed/ — Mac tracks which inbox messages are already handled
    .heartbeat  — Mac updates this every poll so phone knows agent is alive
"""
import json
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR

log = logging.getLogger("mira")


def _utc_iso() -> str:
    """UTC timestamp in iOS-compatible ISO8601 format (no microseconds, Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# iCloud Drive helper — force-download cloud-only files
# ---------------------------------------------------------------------------

def _ensure_downloaded(path: Path):
    """Force iCloud Drive to download a file if it's a cloud placeholder."""
    try:
        # brctl download triggers iCloud to fetch the file
        subprocess.run(
            ["brctl", "download", str(path)],
            capture_output=True, timeout=10,
        )
        # Wait briefly for download to complete
        for _ in range(5):
            try:
                path.read_bytes()
                return  # readable, done
            except OSError:
                time.sleep(0.5)
    except Exception as e:
        log.warning("brctl download failed for %s: %s", path.name, e)


# ---------------------------------------------------------------------------
# Message model
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
        # Force iCloud download if file is a cloud placeholder
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
    """File-based message queue over iCloud Drive."""

    def __init__(self, bridge_dir: Path = MIRA_DIR):
        self.bridge_dir = bridge_dir
        self.inbox = bridge_dir / "inbox"
        self.outbox = bridge_dir / "outbox"
        self.ack_dir = bridge_dir / "ack"
        self.processed_dir = bridge_dir / ".processed"
        self.heartbeat_file = bridge_dir / "heartbeat.json"
        self.tasks_dir = bridge_dir / "tasks"
        self.threads_dir = bridge_dir / "threads"
        self.archive_dir = bridge_dir / "archive"

        # Ensure directories exist
        for d in [self.inbox, self.outbox, self.ack_dir, self.processed_dir,
                  self.tasks_dir, self.threads_dir, self.archive_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Task state (visible to iOS app)
    # ------------------------------------------------------------------

    def create_task(self, task_id: str, title: str, first_message: str,
                    sender: str = "user", tags: list[str] | None = None,
                    origin: str = "user") -> dict:
        """Create a task file in tasks/ for iOS to display."""
        now = _utc_iso()
        task = {
            "id": task_id,
            "title": title,
            "status": "queued",
            "tags": tags or [],
            "origin": origin,
            "created_at": now,
            "updated_at": now,
            "messages": [
                {"sender": sender, "content": first_message, "timestamp": now},
            ],
            "result_path": None,
        }
        self._write_task(task)
        return task

    def emit_status(self, task_id: str, text: str, icon: str = "gear"):
        """Emit a lightweight status card to a task's message stream.

        Status cards show task progress inline (e.g. "Fetching feeds...",
        "Writing outline...", "Waiting for approval"). iOS renders these
        as compact cards instead of regular chat bubbles.

        Args:
            task_id: The task to emit status to.
            text: Short status text (e.g. "正在写大纲").
            icon: SF Symbol name (gear, doc.text, checkmark, clock, etc.)
        """
        import json as _json
        status_content = _json.dumps(
            {"type": "status", "text": text, "icon": icon},
            ensure_ascii=False,
        )
        msg = {
            "sender": "agent",
            "content": status_content,
            "timestamp": _utc_iso(),
        }
        task = self._read_task(task_id)
        if task:
            task["messages"].append(msg)
            task["updated_at"] = _utc_iso()
            self._write_task(task)
        # Always write sidecar so iOS gets it even on sync race
        self._append_reply(task_id, msg)

    def update_task_status(self, task_id: str, status: str,
                           agent_message: str = "",
                           result_path: str = ""):
        """Update a task's status and optionally append an agent message.

        Comment threads (task_id starts with "comment_") use a simplified
        write path to avoid duplicate messages:
        - Agent replies are in .reply.json ONLY (written by task_worker)
        - This method only updates status, never appends messages

        Regular tasks write to:
        1. Main task JSON (may be overwritten by iOS)
        2. Reply sidecar ({task_id}.reply.json) — agent messages
        3. Status sidecar ({task_id}.status.json) — authoritative agent status
        """
        is_comment = task_id.startswith("comment_")

        task = self._read_task(task_id)
        if not task:
            log.warning("update_task_status: task %s not found, writing sidecars directly", task_id)
            if agent_message and not is_comment:
                msg = {"sender": "agent", "content": agent_message, "timestamp": _utc_iso()}
                self._append_reply(task_id, msg)
            self._write_status_sidecar(task_id, status, agent_message)
            return

        task["status"] = status
        task["updated_at"] = _utc_iso()

        if is_comment:
            # Comment threads: NEVER write agent messages to task JSON.
            # Agent replies live exclusively in .reply.json (written by task_worker).
            # This prevents the duplicate message bug from multiple write paths.
            pass
        elif agent_message:
            msg = {
                "sender": "agent",
                "content": agent_message,
                "timestamp": _utc_iso(),
            }
            task["messages"].append(msg)
            self._append_reply(task_id, msg)

        if result_path:
            task["result_path"] = result_path
        self._write_task(task)
        self._write_status_sidecar(task_id, status, agent_message)

    def append_task_message(self, task_id: str, sender: str, content: str):
        """Append a message to an existing task (for follow-ups)."""
        task = self._read_task(task_id)
        if not task:
            return
        task["messages"].append({
            "sender": sender,
            "content": content,
            "timestamp": _utc_iso(),
        })
        task["updated_at"] = _utc_iso()
        # If user sends a follow-up to a done task, reopen it
        if sender != "agent" and task["status"] in ("done", "failed"):
            task["status"] = "queued"
        self._write_task(task)

    def set_task_tags(self, task_id: str, tags: list[str]):
        """Update task tags (called after smart classification)."""
        task = self._read_task(task_id)
        if not task:
            return
        task["tags"] = tags
        self._write_task(task)

    def _read_task(self, task_id: str) -> dict | None:
        path = self.tasks_dir / f"{task_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _append_reply(self, task_id: str, msg: dict):
        """Append an agent reply to a sidecar file immune to iCloud sync races."""
        path = self.tasks_dir / f"{task_id}.reply.json"
        replies = []
        if path.exists():
            try:
                replies = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        replies.append(msg)
        path.write_text(
            json.dumps(replies, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_status_sidecar(self, task_id: str, status: str,
                               agent_message: str = ""):
        """Write authoritative agent-side status to a separate file.

        This file is ONLY written by the Mac agent. iOS should read it
        on app launch / refresh to reconcile with the task JSON.
        Format: {status, updated_at, last_message (preview)}.
        """
        path = self.tasks_dir / f"{task_id}.status.json"
        data = {
            "status": status,
            "updated_at": _utc_iso(),
        }
        if agent_message:
            data["last_message"] = agent_message[:300]
        path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

    def _write_task(self, task: dict):
        path = self.tasks_dir / f"{task['id']}.json"
        path.write_text(
            json.dumps(task, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def heartbeat(self, agent_status: dict | None = None):
        """Update heartbeat so phone can check agent is alive.

        Args:
            agent_status: optional dict from TaskManager.get_status_summary()
                          with keys: busy, active_count, active_tasks, last_completed
        """
        data = {
            "timestamp": _utc_iso(),
            "status": "online",
        }
        if agent_status is not None:
            data["busy"] = agent_status.get("busy", False)
            data["active_count"] = agent_status.get("active_count", 0)
            data["active_tasks"] = agent_status.get("active_tasks", [])
            data["last_completed"] = agent_status.get("last_completed", "")
        self.heartbeat_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def poll(self) -> list[tuple["Message", Path]]:
        """Scan inbox for new (unprocessed) messages.

        Returns list of (Message, file_path) tuples, sorted by filename (time order).
        """
        # Force iCloud to download any new inbox files
        try:
            subprocess.run(
                ["brctl", "download", str(self.inbox)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

        results = []

        for path in sorted(self.inbox.glob("*.json")):
            # Skip if already processed
            if (self.processed_dir / path.name).exists():
                continue

            msg = Message.from_file(path)
            if msg:
                results.append((msg, path))

        return results

    def ack(self, msg_id: str, status: str = "received"):
        """Write ack so phone knows message status.

        Statuses: received → processing → done | error
        """
        ack_data = {
            "message_id": msg_id,
            "status": status,
            "timestamp": _utc_iso(),
        }
        path = self.ack_dir / f"{msg_id}.json"
        path.write_text(
            json.dumps(ack_data, ensure_ascii=False), encoding="utf-8"
        )

    def mark_processed(self, msg_path: Path):
        """Record that a message file has been processed (prevents re-processing)."""
        marker = self.processed_dir / msg_path.name
        marker.write_text(
            json.dumps({"processed_at": _utc_iso()}),
            encoding="utf-8",
        )

    def reply(self, msg_id: str, recipient: str, content: str,
              thread_id: str = "") -> str:
        """Write a reply to outbox for phone to pick up. Returns reply ID."""
        reply_id = uuid.uuid4().hex[:8]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{recipient}_{ts}_{reply_id}.json"

        reply_data = {
            "id": reply_id,
            "in_reply_to": msg_id,
            "sender": "agent",
            "recipient": recipient,
            "timestamp": _utc_iso(),
            "type": "text",
            "content": content,
            "thread_id": thread_id,
        }

        path = self.outbox / filename
        path.write_text(
            json.dumps(reply_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Reply written: %s → %s", msg_id, filename)
        return reply_id

    def post(self, content: str, sender: str = "agent",
             thread_id: str = "", msg_type: str = "text") -> str:
        """Post an agent-initiated message (not a reply). Returns message ID.

        Used for proactive messages like daily journals and briefings.
        """
        msg_id = uuid.uuid4().hex[:8]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"agent_{ts}_{msg_id}.json"

        data = {
            "id": msg_id,
            "sender": sender,
            "timestamp": _utc_iso(),
            "type": msg_type,
            "content": content,
            "thread_id": thread_id,
        }

        path = self.outbox / filename
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Posted message: %s → %s", msg_id, filename)
        return msg_id

    def cleanup_old(self, days: int = 3):
        """Remove old messages, processed markers, and acks older than N days.

        Skips messages belonging to archived threads.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

        # Get archived thread IDs to skip
        archived_threads = self._get_archived_thread_ids()

        # Clean processed markers and acks
        for d in [self.processed_dir, self.ack_dir]:
            for path in d.glob("*.json"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    pass

        # Clean old inbox and outbox messages (unless archived)
        for d in [self.inbox, self.outbox]:
            for path in d.glob("*.json"):
                try:
                    if path.stat().st_mtime < cutoff:
                        # Check if this message belongs to an archived thread
                        data = json.loads(path.read_text(encoding="utf-8"))
                        if data.get("thread_id") in archived_threads:
                            continue
                        path.unlink()
                except (OSError, json.JSONDecodeError):
                    pass

        count = 0
        log.info("Cleanup done (cutoff: %d days)", days)

    def _get_archived_thread_ids(self) -> set:
        """Get set of archived thread IDs."""
        index_file = self.threads_dir / "index.json"
        if not index_file.exists():
            return set()
        try:
            threads = json.loads(index_file.read_text(encoding="utf-8"))
            return {t["id"] for t in threads if t.get("archived")}
        except (json.JSONDecodeError, OSError):
            return set()

    def archive_thread(self, thread_id: str):
        """Archive a thread: collect all messages → save as markdown, mark archived."""
        # Collect all messages for this thread
        messages = []
        for folder in [self.inbox, self.outbox]:
            for path in sorted(folder.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("thread_id") == thread_id:
                        messages.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        messages.sort(key=lambda m: m.get("timestamp", ""))

        if not messages:
            log.warning("No messages found for thread %s", thread_id)
            return

        # Generate markdown
        lines = [f"# Thread Archive: {thread_id}\n"]
        lines.append(f"Archived at: {_utc_iso()}\n")
        lines.append(f"Messages: {len(messages)}\n\n---\n")

        for msg in messages:
            sender = msg.get("sender", "?")
            ts = msg.get("timestamp", "")[:19]
            content = msg.get("content", "")
            lines.append(f"## [{ts}] {sender}\n")
            lines.append(f"{content}\n\n---\n")

        # Get thread title from index
        title = thread_id
        index_file = self.threads_dir / "index.json"
        if index_file.exists():
            try:
                threads = json.loads(index_file.read_text(encoding="utf-8"))
                for t in threads:
                    if t["id"] == thread_id:
                        title = t.get("title", thread_id)
                        t["archived"] = True
                        break
                index_file.write_text(
                    json.dumps(threads, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except (json.JSONDecodeError, OSError):
                pass

        # Write archive markdown
        safe_title = "".join(c for c in title if c.isalnum() or c in " -_").strip()[:50]
        archive_file = self.archive_dir / f"{thread_id}_{safe_title}.md"
        archive_file.write_text("\n".join(lines), encoding="utf-8")
        log.info("Archived thread %s → %s", thread_id, archive_file.name)
