"""Prepare a no-network publication packet for a V3.1 public writeup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import prepare_public_writeup_publication_packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local public-writeup publication packet.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--slug", required=True, help="Stable publication/evidence slug.")
    parser.add_argument("--draft-artifact", type=Path, required=True, help="Local draft artifact to package.")
    parser.add_argument("--expected-preview-hash", help="Optional sha256 hash expected for --draft-artifact.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        packet = prepare_public_writeup_publication_packet(
            root=args.root,
            slug=args.slug,
            draft_artifact=args.draft_artifact,
            expected_preview_hash=args.expected_preview_hash,
        )
    except ValueError as exc:
        parser.error(str(exc))
    payload = packet.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Publication packet: {packet.packet_dir}")
        print(f"- submission: {packet.submission_artifact}")
        print(f"- metadata: {packet.metadata_artifact}")
        print(f"- checklist: {packet.checklist_artifact}")
        print(f"- preview_hash: {packet.preview_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
