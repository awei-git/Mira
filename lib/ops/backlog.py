"""Action backlog — tracks improvement items from reflections and audits.

Each reflect/audit cycle should produce concrete action items that get
tracked through: proposed → approved → in_progress → verified / rejected / expired.
"""
from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mira")

from config import SOUL_DIR as _SOUL_DIR; _SOUL_DIR  # imported from config
_BACKLOG_FILE = _SOUL_DIR / "action_backlog.json"

VALID_STATUSES = {
    "proposed",
    "approved",
    "in_progress",
    "verified",
    "implemented",  # legacy alias kept for backward compatibility
    "rejected",
    "expired",
}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


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
    executor: str = ""  # registered execution path, if any
    payload: dict = field(default_factory=dict)
    verification_summary: str = ""
    verified_at: str = ""
    last_error: str = ""

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
        self._lock_path = self._path.with_suffix(".lock")
        self._items: list[ActionItem] = []
        self.load()

    def _read_items_unlocked(self) -> list[ActionItem]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [i for d in data if (i := ActionItem.from_dict(d)) is not None]
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load action backlog: %s", e)
            return []

    def _write_items_unlocked(self, items: list[ActionItem]):
        tmp = self._path.with_suffix(".tmp")
        data = json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False)
        tmp.write_text(data, encoding="utf-8")
        tmp.rename(self._path)

    def _with_lock(self, lock_type: int, fn):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf, lock_type)
            try:
                return fn()
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def load(self):
        self._items = self._with_lock(fcntl.LOCK_SH, self._read_items_unlocked)

    def save(self):
        def _save():
            self._write_items_unlocked(self._items)
        self._with_lock(fcntl.LOCK_EX, _save)

    def add(self, item: ActionItem) -> bool:
        """Add an action item. Returns False if duplicate title exists."""
        added = False

        def _add():
            nonlocal added
            items = self._read_items_unlocked()
            for existing in items:
                if existing.title == item.title and existing.status in ("proposed", "approved", "in_progress"):
                    self._items = items
                    return
            items.append(item)
            self._write_items_unlocked(items)
            self._items = items
            added = True

        self._with_lock(fcntl.LOCK_EX, _add)
        if added:
            log.info("BACKLOG_ADD: %s (source=%s, priority=%s)", item.title, item.source, item.priority)
        return added

    def update_status(self, title: str, new_status: str, resolution: str = "") -> bool:
        """Update the status of an action item."""
        if new_status not in VALID_STATUSES:
            return False
        updated = False

        def _update():
            nonlocal updated
            items = self._read_items_unlocked()
            for item in items:
                if item.title == title:
                    item.status = new_status
                    item.updated_at = _utc_now()
                    if resolution:
                        item.resolution = resolution
                    self._write_items_unlocked(items)
                    self._items = items
                    updated = True
                    return
            self._items = items

        self._with_lock(fcntl.LOCK_EX, _update)
        if updated:
            log.info("BACKLOG_UPDATE: %s → %s", title, new_status)
        return updated

    def get_active(self) -> list[ActionItem]:
        """Return items that need attention (proposed/approved/in_progress)."""
        self.load()
        return [i for i in self._items
                if i.status in ("proposed", "approved", "in_progress")
                and not i.is_expired()]

    def get_by_status(self, status: str) -> list[ActionItem]:
        self.load()
        return [i for i in self._items if i.status == status]

    def update_item(self, title: str, **fields) -> bool:
        """Update arbitrary fields for one item."""
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            return False
        updated = False

        def _update():
            nonlocal updated
            items = self._read_items_unlocked()
            for item in items:
                if item.title != title:
                    continue
                for key, value in fields.items():
                    if hasattr(item, key):
                        setattr(item, key, value)
                item.updated_at = _utc_now()
                self._write_items_unlocked(items)
                self._items = items
                updated = True
                return
            self._items = items

        self._with_lock(fcntl.LOCK_EX, _update)
        if updated:
            log.info("BACKLOG_PATCH: %s (%s)", title, ", ".join(sorted(fields)))
        return updated

    def claim_next_approved(self, executors: set[str] | None = None) -> ActionItem | None:
        """Atomically move the next approved item into in_progress."""
        claimed: ActionItem | None = None

        def _claim():
            nonlocal claimed
            items = self._read_items_unlocked()
            candidates = [
                item for item in items
                if item.status == "approved"
                and not item.is_expired()
                and (executors is None or item.executor in executors)
            ]
            candidates.sort(key=lambda item: (_PRIORITY_ORDER.get(item.priority, 99), item.created_at))
            if not candidates:
                self._items = items
                return
            target = candidates[0]
            for item in items:
                if item.title == target.title:
                    item.status = "in_progress"
                    item.updated_at = _utc_now()
                    item.last_error = ""
                    claimed = item
                    break
            self._write_items_unlocked(items)
            self._items = items

        self._with_lock(fcntl.LOCK_EX, _claim)
        if claimed:
            log.info("BACKLOG_CLAIM: %s", claimed.title)
        return claimed

    def finish_execution(
        self,
        title: str,
        *,
        success: bool,
        resolution: str,
        verification_summary: str = "",
        error: str = "",
    ) -> bool:
        """Finalize an execution attempt as verified or rejected."""
        status = "verified" if success else "rejected"
        return self.update_item(
            title,
            status=status,
            resolution=resolution,
            verification_summary=verification_summary,
            verified_at=_utc_now() if success else "",
            last_error=error,
        )

    def expire_stale(self, max_days: int = 30) -> int:
        """Mark old proposed items as expired. Returns count expired."""
        count = 0
        cutoff = datetime.now(timezone.utc)

        def _expire():
            nonlocal count
            items = self._read_items_unlocked()
            for item in items:
                if item.status != "proposed":
                    continue
                if item.is_expired():
                    item.status = "expired"
                    item.updated_at = _utc_now()
                    count += 1
                    continue
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
                self._write_items_unlocked(items)
            self._items = items

        self._with_lock(fcntl.LOCK_EX, _expire)
        if count:
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
        self.load()
        return len(self._items)
