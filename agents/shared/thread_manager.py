"""ThreadManager — manage conversation threads for Mira.

Threads group messages by topic. Each thread has:
- An entry in threads/index.json
- Optional per-thread memory in threads/{id}/memory.md
"""
import fcntl
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR

log = logging.getLogger("mira")

THREADS_DIR = MIRA_DIR / "threads"
INDEX_FILE = THREADS_DIR / "index.json"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ThreadManager:
    """Manages conversation threads."""

    def __init__(self):
        THREADS_DIR.mkdir(parents=True, exist_ok=True)
        self._threads = self._load_index()

    def _load_index(self) -> list[dict]:
        if not INDEX_FILE.exists():
            return []
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self):
        tmp = INDEX_FILE.with_suffix(".tmp")
        lock = INDEX_FILE.with_suffix(".lock")
        data = json.dumps(self._threads, indent=2, ensure_ascii=False)
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            tmp.write_text(data, encoding="utf-8")
            tmp.rename(INDEX_FILE)
            fcntl.flock(lf, fcntl.LOCK_UN)

    def create_thread(self, title: str) -> str:
        """Create a new thread, return its ID."""
        thread_id = uuid.uuid4().hex[:8]
        entry = {
            "id": thread_id,
            "title": title,
            "created_at": _utc_iso(),
            "last_active": _utc_iso(),
            "archived": False,
        }
        self._threads.append(entry)
        self._save_index()

        # Create thread directory
        (THREADS_DIR / thread_id).mkdir(exist_ok=True)
        log.info("Created thread %s: %s", thread_id, title)
        return thread_id

    def get_or_create_thread(self, thread_id: str, default_title: str) -> str:
        """Get existing thread or create one if thread_id is empty/unknown."""
        if thread_id:
            # Check if exists
            for t in self._threads:
                if t["id"] == thread_id:
                    t["last_active"] = _utc_iso()
                    self._save_index()
                    return thread_id

        # Create new thread
        return self.create_thread(default_title)

    def update_last_active(self, thread_id: str):
        """Update the last_active timestamp for a thread."""
        for t in self._threads:
            if t["id"] == thread_id:
                t["last_active"] = _utc_iso()
                self._save_index()
                return

    def get_thread_history(self, thread_id: str, limit: int = 20) -> list[dict]:
        """Load recent messages from a thread."""
        if not thread_id:
            return []

        messages = []
        for folder_name in ["inbox", "outbox"]:
            folder = MIRA_DIR / folder_name
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if data.get("thread_id") == thread_id:
                        messages.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages[-limit:]

    def get_thread_memory(self, thread_id: str) -> str:
        """Load per-thread memory."""
        if not thread_id:
            return ""
        mem_file = THREADS_DIR / thread_id / "memory.md"
        if mem_file.exists():
            return mem_file.read_text(encoding="utf-8")
        return ""

    def append_thread_memory(self, thread_id: str, entry: str):
        """Append to per-thread memory (fcntl-locked)."""
        if not thread_id:
            return
        thread_dir = THREADS_DIR / thread_id
        thread_dir.mkdir(parents=True, exist_ok=True)
        mem_file = thread_dir / "memory.md"

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"- [{ts}] {entry}\n"

        with open(mem_file, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            if mem_file.stat().st_size == 0:
                f.write("# Thread Memory\n\n")
            f.write(line)
            f.flush()
            fcntl.flock(f, fcntl.LOCK_UN)

    def list_threads(self, include_archived: bool = False) -> list[dict]:
        """Return all threads, optionally including archived ones."""
        if include_archived:
            return self._threads
        return [t for t in self._threads if not t.get("archived")]
