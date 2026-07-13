from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from config import CONTROL_DATABASE_URL, CONTROL_DB_SCHEMA


DEFAULT_HOURLY_BACKUP_DIR = Path.home() / "MiraBackup" / "postgres" / "hourly"


@dataclass(frozen=True)
class BackupResult:
    path: Path
    command: tuple[str, ...]
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.path.exists() and self.path.stat().st_size > 0


def _timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def prune_hourly_backups(backup_dir: Path, *, keep: int = 24) -> list[Path]:
    backups = sorted(backup_dir.glob("*.dump"), key=lambda p: p.name, reverse=True)
    removed: list[Path] = []
    valid_backups: list[Path] = []
    for path in backups:
        if path.stat().st_size == 0:
            path.unlink()
            removed.append(path)
        else:
            valid_backups.append(path)
    for path in valid_backups[keep:]:
        path.unlink()
        removed.append(path)
    return removed


def run_hourly_pg_backup(
    *,
    database_url: str = CONTROL_DATABASE_URL,
    schema: str = CONTROL_DB_SCHEMA,
    backup_dir: Path = DEFAULT_HOURLY_BACKUP_DIR,
    now: datetime | None = None,
    keep: int = 24,
    lock_wait_timeout: str = "60s",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> BackupResult:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{_timestamp(now)}.dump"
    command: Sequence[str] = (
        "pg_dump",
        "--format=custom",
        "-Z",
        "9",
        "--lock-wait-timeout",
        lock_wait_timeout,
        "--schema",
        schema,
        "--file",
        str(target),
        database_url,
    )
    completed = runner(command, check=False)
    result = BackupResult(path=target, command=tuple(command), returncode=int(completed.returncode))
    if result.ok:
        prune_hourly_backups(backup_dir, keep=keep)
    elif target.exists() and target.stat().st_size == 0:
        target.unlink()
    return result
