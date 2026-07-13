#!/usr/bin/env python3
"""Reset soul-integrity baseline for protected files.

Use when verify_soul_integrity() reports a violation but no backup exists
to restore from. This happens when a protected file was modified through a
path that bypasses _protected_write() (manual editor save, direct
write_text, an agent that imports the file path but not the API).

What this does, per protected file:
  1. If actual hash matches stored hash → skip (file is fine).
  2. If actual hash differs and a backup matching the stored hash exists →
     refuse to reset (real restore is the right fix; not this script).
  3. If actual hash differs and NO backup exists → adopt current content
     as the new baseline: copy current → .backups/, rewrite hash file,
     append a changelog entry. This is the legitimate path out of the
     stranded state caused by a missing first-time backup.

Run with --dry-run first to see what would change.

Usage:
    python scripts/reset_soul_baseline.py --dry-run
    python scripts/reset_soul_baseline.py --apply --reason "explanation"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from config import IDENTITY_FILE, WORLDVIEW_FILE, CHANGELOG_FILE  # noqa: E402

PROTECTED = {
    "identity": IDENTITY_FILE,
    "worldview": WORLDVIEW_FILE,
}

HASH_FILE = IDENTITY_FILE.parent / ".soul_hashes.json"
BACKUP_DIR = IDENTITY_FILE.parent / ".backups"


def sha256(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_hashes() -> dict:
    if not HASH_FILE.exists():
        return {}
    return json.loads(HASH_FILE.read_text(encoding="utf-8"))


def existing_backups(name: str, path: Path) -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob(f"{path.stem}_*{path.suffix}"), reverse=True)


def classify(name: str, path: Path, stored: dict) -> tuple[str, dict]:
    expected = stored.get(name, "")
    actual = sha256(path)
    backups = existing_backups(name, path)
    info = {
        "expected": expected[:12] if expected else "<none>",
        "actual": actual[:12] if actual else "<missing>",
        "backups": [b.name for b in backups],
    }
    if not path.exists():
        return "missing_file", info
    if not expected:
        return "no_baseline", info
    if expected == actual:
        return "ok", info
    matching = [b for b in backups if sha256(b) == expected]
    if matching:
        return "tampered_with_restore_available", info | {"restore_from": matching[0].name}
    return "stranded_no_backup", info


def reset_one(name: str, path: Path, reason: str) -> str:
    """Adopt current content as baseline. Creates backup + updates hash."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"{path.stem}_{ts}{path.suffix}"
    shutil.copy2(path, backup_path)

    hashes = load_hashes()
    hashes[name] = sha256(path)
    hashes["updated_at"] = datetime.now().isoformat()
    HASH_FILE.write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    line = (
        f"- [{datetime.now().strftime('%Y-%m-%d %H:%M')}] RESET_BASELINE "
        f"{path.name}: {reason} (manual reset, new backup: {backup_path.name})\n"
    )
    if CHANGELOG_FILE.exists():
        with CHANGELOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    else:
        CHANGELOG_FILE.write_text(f"# Knowledge Changelog\n\n{line}", encoding="utf-8")

    return backup_path.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Report only.")
    ap.add_argument("--apply", action="store_true", help="Perform the reset.")
    ap.add_argument("--reason", default="", help="Reason recorded in changelog.")
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        ap.error("specify --dry-run or --apply")
    if args.apply and not args.reason:
        ap.error("--apply requires --reason")

    stored = load_hashes()
    print(f"hash file: {HASH_FILE}")
    print(f"backup dir: {BACKUP_DIR}")
    print()

    actions: list[tuple[str, Path]] = []
    for name, path in PROTECTED.items():
        status, info = classify(name, path, stored)
        print(
            f"[{status:38s}] {name:10s}  expected={info['expected']:12s}  actual={info['actual']:12s}  backups={len(info['backups'])}"
        )
        if status == "stranded_no_backup":
            actions.append((name, path))
        elif status == "tampered_with_restore_available":
            print(
                f"  ! refusing to reset — backup {info['restore_from']} matches stored hash. Restore manually instead."
            )

    if not actions:
        print("\nNothing to reset.")
        return 0

    if args.dry_run:
        print(f"\n[dry-run] would reset {len(actions)} file(s) by adopting current content as new baseline.")
        return 0

    print()
    for name, path in actions:
        backup_name = reset_one(name, path, args.reason)
        print(f"reset {name}: backup={backup_name}, new hash={sha256(path)[:12]}")
    print(f"\nDone. {len(actions)} file(s) re-baselined.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
