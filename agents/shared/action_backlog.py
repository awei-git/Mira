"""Action backlog — tracks improvement items from reflections and audits.

Each reflect/audit cycle should produce concrete action items that get
tracked through: proposed → approved → implemented → rejected → expired.
"""
from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mira")

_SOUL_DIR = Path(__file__).resolve().parent / "soul"
_BACKLOG_FILE = _SOUL_DIR / "action_backlog.json"

VALID_STATUSES = {"proposed", "approved", "in_progress", "implemented", "rejected", "expired"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ActionItem:
    """A concrete improvement action from reflection or audit."""

    title: str
    description: str
    source: str  # "reflect", "self_audit", "self_evolve", "manual"
    status: str = "proposed"
    priority: str = "medium"  # "high", "medium", "low"
    target_dimension: str = ""  # evaluator dimension this aims to improve
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    expires_at: str | None = None  # ISO date — None = no expiry
    resolution: str = ""  # how it was resolved

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ActionItem | None:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        try:
            return cls(**filtered)
        except TypeError:
            return None

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) > exp
        except ValueError:
            return False


class ActionBacklog:
    """Manages the action item backlog."""

    def __init__(self, path: Path | None = None):
        self._path = path or _BACKLOG_FILE
        self._items: list[ActionItem] = []
        self.load()

    def load(self):
        if not self._path.exists():
            self._items = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._items = [i for d in data if (i := ActionItem.from_dict(d)) is not None]
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load action backlog: %s", e)
            self._items = []

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        lock = self._path.with_suffix(".lock")
        data = json.dumps([i.to_dict() for i in self._items],
                          indent=2, ensure_ascii=False)
        with open(lock, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            tmp.write_text(data, encoding="utf-8")
            tmp.rename(self._path)
            fcntl.flock(lf, fcntl.LOCK_UN)

    def add(self, item: ActionItem) -> bool:
        """Add an action item. Returns False if duplicate title exists."""
        for existing in self._items:
            if existing.title == item.title and existing.status in ("proposed", "approved", "in_progress"):
                return False
        self._items.append(item)
        self.save()
        log.info("BACKLOG_ADD: %s (source=%s, priority=%s)", item.title, item.source, item.priority)
        return True

    def update_status(self, title: str, new_status: str, resolution: str = "") -> bool:
        """Update the status of an action item."""
        if new_status not in VALID_STATUSES:
            return False
        for item in self._items:
            if item.title == title:
                item.status = new_status
                item.updated_at = _utc_now()
                if resolution:
                    item.resolution = resolution
                self.save()
                log.info("BACKLOG_UPDATE: %s → %s", title, new_status)
                return True
        return False

    def get_active(self) -> list[ActionItem]:
        """Return items that need attention (proposed/approved/in_progress)."""
        return [i for i in self._items
                if i.status in ("proposed", "approved", "in_progress")
                and not i.is_expired()]

    def get_by_status(self, status: str) -> list[ActionItem]:
        return [i for i in self._items if i.status == status]

    def expire_stale(self, max_days: int = 30) -> int:
        """Mark old proposed items as expired. Returns count expired."""
        count = 0
        cutoff = datetime.now(timezone.utc)
        for item in self._items:
            if item.status != "proposed":
                continue
            # Check explicit expiry
            if item.is_expired():
                item.status = "expired"
                item.updated_at = _utc_now()
                count += 1
                continue
            # Check age-based expiry
            try:
                created = datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))
                age_days = (cutoff - created).days
                if age_days > max_days:
                    item.status = "expired"
                    item.updated_at = _utc_now()
                    count += 1
            except (ValueError, TypeError):
                pass
        if count:
            self.save()
            log.info("BACKLOG_EXPIRE: %d items expired", count)
        return count

    def summary(self) -> str:
        """One-line summary for logging."""
        active = self.get_active()
        by_status = {}
        for i in active:
            by_status[i.status] = by_status.get(i.status, 0) + 1
        parts = [f"{s}={c}" for s, c in sorted(by_status.items())]
        return f"Backlog: {len(active)} active ({', '.join(parts) if parts else 'empty'})"

    def __len__(self) -> int:
        return len(self._items)
