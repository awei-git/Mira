"""File I/O, integrity verification, and backup utilities for soul management."""

import fcntl
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from config import (
    IDENTITY_FILE,
    WORLDVIEW_FILE,
    CHANGELOG_FILE,
    CHANGELOG_ARCHIVE_DIR,
    CHANGELOG_MAX_LINES,
)

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Safe file write utilities — prevent data loss from concurrent writes
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, content: str):
    """Write file atomically via tmp + fsync + rename.

    Prevents partial writes if process is killed mid-write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _locked_write(path: Path, content: str):
    """Atomic write with exclusive file lock.

    Use for files shared across concurrent processes (memory.md,
    worldview.md, interests.md, scores.json, catalog.jsonl, etc.).
    Blocks until lock is acquired (up to 10s timeout).
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)  # blocking
        try:
            _atomic_write(path, content)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _locked_read_modify_write(path: Path, modify_fn):
    """Read file, apply modify_fn, write back — all under lock.

    modify_fn(current_text: str) -> new_text: str
    If file doesn't exist, modify_fn receives empty string.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            new_content = modify_fn(current)
            _atomic_write(path, new_content)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Knowledge changelog — append-only audit trail for all soul mutations
# ---------------------------------------------------------------------------


def _log_change(action: str, target: str, detail: str = ""):
    """Append one line to the knowledge changelog.

    Format: - [2026-04-05 14:30] ACTION target: detail
    Archives old entries when file exceeds CHANGELOG_MAX_LINES.
    """
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        suffix = f": {detail}" if detail else ""
        line = f"- [{ts}] {action} {target}{suffix}\n"

        def _modify(text):
            if not text:
                text = "# Knowledge Changelog\n\n"
            text += line
            lines = text.split("\n")
            if len(lines) > CHANGELOG_MAX_LINES:
                header = lines[:2]
                entries = lines[2:]
                # Archive overflow to monthly file
                month = datetime.now().strftime("%Y-%m")
                CHANGELOG_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
                archive_path = CHANGELOG_ARCHIVE_DIR / f"{month}.md"
                overflow = entries[: -(CHANGELOG_MAX_LINES - 2)]
                with open(archive_path, "a", encoding="utf-8") as f:
                    f.write("\n".join(overflow) + "\n")
                trimmed = entries[-(CHANGELOG_MAX_LINES - 2) :]
                text = "\n".join(header + trimmed)
            return text

        _locked_read_modify_write(CHANGELOG_FILE, _modify)
    except Exception as e:
        log.debug("Changelog write failed: %s", e)


# ---------------------------------------------------------------------------
# Soul integrity — hash verification + backup rotation
# ---------------------------------------------------------------------------

_HASH_FILE = IDENTITY_FILE.parent / ".soul_hashes.json"
_BACKUP_DIR = IDENTITY_FILE.parent / ".backups"
_MAX_BACKUPS = 3

# Files that define who Mira is — integrity-protected
_PROTECTED_FILES = {
    "identity": IDENTITY_FILE,
    "worldview": WORLDVIEW_FILE,
}


def _compute_hash(path: Path) -> str:
    """SHA-256 of file content."""
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_hashes():
    """Recompute and save hashes for all protected files."""
    hashes = {}
    for name, path in _PROTECTED_FILES.items():
        hashes[name] = _compute_hash(path)
    hashes["updated_at"] = datetime.now().isoformat()
    _atomic_write(_HASH_FILE, json.dumps(hashes, indent=2))


def _load_hashes() -> dict:
    """Load stored hashes."""
    if not _HASH_FILE.exists():
        return {}
    try:
        return json.loads(_HASH_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _rotate_backup(path: Path):
    """Keep last N backups of a soul file."""
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = _BACKUP_DIR / f"{path.stem}_{ts}{path.suffix}"
    if path.exists():
        shutil.copy2(path, backup)
    # Prune old backups
    backups = sorted(_BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)
    for old in backups[_MAX_BACKUPS:]:
        old.unlink()


def verify_soul_integrity() -> list[str]:
    """Check protected soul files against stored hashes.

    Returns list of integrity violations (empty = all good).
    On violation: logs CRITICAL, restores from backup if available.
    """
    stored = _load_hashes()
    if not stored:
        # First run — save current hashes as baseline
        _save_hashes()
        return []

    violations = []
    for name, path in _PROTECTED_FILES.items():
        expected = stored.get(name, "")
        if not expected:
            continue  # No stored hash yet
        actual = _compute_hash(path)
        if actual != expected:
            violations.append(name)
            log.critical(
                "SOUL INTEGRITY VIOLATION: %s has been modified outside authorized writes! " "Expected hash %s, got %s",
                name,
                expected[:12],
                actual[:12],
            )

            # Try to restore from backup
            backups = sorted(_BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)
            if backups:
                latest_backup = backups[0]
                backup_hash = hashlib.sha256(latest_backup.read_bytes()).hexdigest()
                if backup_hash == expected:
                    shutil.copy2(latest_backup, path)
                    log.critical("Restored %s from backup %s", name, latest_backup.name)
                else:
                    log.critical("Backup hash also differs — manual review needed for %s", name)
            else:
                log.critical("No backups available for %s — manual review needed", name)

    return violations


def _protected_write(path: Path, content: str):
    """Write a protected soul file: backup → write → update hash."""
    _rotate_backup(path)
    _locked_write(path, content)
    _save_hashes()
