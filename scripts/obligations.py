#!/usr/bin/env python3
"""Operator inspection/import/backup of the single local ledger; no paid dispatch."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from obligations import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("events")
    migration = commands.add_parser("import-legacy")
    migration.add_argument("--file", type=Path, action="append", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--destination", type=Path, required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--id", required=True)
    reconcile.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    ledger = Ledger(args.database, args.artifact_root)
    if args.command == "list":
        result = ledger.listing()
    elif args.command == "events":
        result = ledger.events()
    elif args.command == "import-legacy":
        result = [ledger.import_legacy(path) for path in args.file]
    elif args.command == "backup":
        ledger.backup(args.destination)
        result = {"backup": str(args.destination)}
    else:
        result = ledger.reconcile(args.id, "mira-aws", args.revision)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
