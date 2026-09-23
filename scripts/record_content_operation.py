#!/usr/bin/env python3
"""Ingest one sanitized host receipt; does not collect, schedule or run work."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from content_worker.operations import record_operation
from obligations import Ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--empty-threshold", type=int, default=4)
    args = parser.parse_args()
    if args.receipt.is_symlink():
        raise ValueError("receipt_symlink_rejected")
    with args.receipt.open("rb") as stream:
        body = stream.read(4097)
    if len(body) > 4096:
        raise ValueError("receipt_too_large")
    result = record_operation(
        Ledger(args.database, args.artifact_root), json.loads(body), empty_threshold=args.empty_threshold
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
