"""Helpers for governed backup restore dry-runs."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

log = logging.getLogger("mira.restore_drill")

DEFAULT_BACKUP_ROOT = Path("/Volumes/home/backup/mira")
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "restore_dry_run.py"


def latest_backup_dir(root: Path | None = None) -> Path | None:
    """Return the most recent dated backup directory with a manifest."""
    backup_root = (root or DEFAULT_BACKUP_ROOT).expanduser()
    if not backup_root.exists():
        return None
    candidates = [
        path
        for path in backup_root.iterdir()
        if path.is_dir() and path.name[:4].isdigit() and (path / "backup_manifest.json").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates)[-1]


def run_latest_restore_dry_run(backup_root: Path | None = None) -> dict:
    """Execute restore_dry_run.py against the latest backup directory."""
    backup_dir = latest_backup_dir(backup_root)
    if not backup_dir:
        return {
            "ok": False,
            "reason": "backup_not_found",
            "backup_root": str((backup_root or DEFAULT_BACKUP_ROOT).expanduser()),
        }

    spec = importlib.util.spec_from_file_location("mira_restore_dry_run", _SCRIPT_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("restore_dry_run.py unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_restore_dry_run(backup_dir)
    log.info("RESTORE_DRY_RUN: backup=%s ok=%s", backup_dir, report.get("ok"))
    return report
