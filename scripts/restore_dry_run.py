#!/usr/bin/env python3
"""Validate and stage a dry-run restore from a Mira backup."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path

_REQUIRED_PATHS = [
    "config.yml",
    "soul/identity.md",
    "soul/memory.md",
    "soul/worldview.md",
]
_REPORT_LOG = Path(__file__).resolve().parents[1] / "logs" / "restore_drills.jsonl"


def run_restore_dry_run(backup_dir: Path) -> dict:
    backup_dir = backup_dir.expanduser().resolve()
    manifest_path = backup_dir / "backup_manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest_errors = verify_manifest(backup_dir, manifest)
    required_errors = verify_required_paths(backup_dir)

    with tempfile.TemporaryDirectory(prefix="mira-restore-dry-run-") as tmpdir:
        stage_dir = Path(tmpdir) / "restored"
        staged = stage_restore_subset(backup_dir, stage_dir)
        report = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ok": not manifest_errors and not required_errors,
            "backup_dir": str(backup_dir),
            "manifest_present": bool(manifest),
            "manifest_errors": manifest_errors,
            "required_errors": required_errors,
            "staged_paths": staged,
        }
        append_report(report)
        return report


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def verify_manifest(backup_dir: Path, manifest: dict) -> list[str]:
    if not manifest:
        return ["backup_manifest.json missing or unreadable"]
    errors = []

    for entry in manifest.get("files", []):
        path = backup_dir / entry.get("path", "")
        if not path.exists():
            errors.append(f"missing file: {entry.get('path', '')}")
            continue
        if path.stat().st_size != entry.get("size"):
            errors.append(f"size mismatch: {entry.get('path', '')}")
            continue
        if _sha256(path) != entry.get("sha256"):
            errors.append(f"hash mismatch: {entry.get('path', '')}")
    return errors


def verify_required_paths(backup_dir: Path) -> list[str]:
    missing = []
    for rel in _REQUIRED_PATHS:
        if not (backup_dir / rel).exists():
            missing.append(f"required path missing: {rel}")
    return missing


def stage_restore_subset(backup_dir: Path, stage_dir: Path) -> list[str]:
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for rel in _REQUIRED_PATHS:
        src = backup_dir / rel
        if not src.exists():
            continue
        dst = stage_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        staged.append(rel)
    return staged


def append_report(report: dict):
    _REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: restore_dry_run.py <backup_dir>")
        return 2
    report = run_restore_dry_run(Path(argv[1]))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
