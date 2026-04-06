#!/usr/bin/env python3
"""Generate a manifest with hashes for one Mira backup directory."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def build_manifest(backup_dir: Path) -> dict:
    files = []
    for path in sorted(backup_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "backup_manifest.json":
            continue
        rel = path.relative_to(backup_dir).as_posix()
        files.append(
            {
                "path": rel,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backup_dir": str(backup_dir),
        "file_count": len(files),
        "files": files,
    }


def write_manifest(backup_dir: Path) -> Path:
    manifest = build_manifest(backup_dir)
    out = backup_dir / "backup_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: backup_integrity.py <backup_dir>")
        return 2
    backup_dir = Path(argv[1]).expanduser().resolve()
    if not backup_dir.exists():
        print(f"Backup dir not found: {backup_dir}")
        return 1
    out = write_manifest(backup_dir)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
