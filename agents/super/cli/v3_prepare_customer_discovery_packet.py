"""Prepare a no-network customer-discovery feedback packet for V3.1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import prepare_customer_discovery_feedback_packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local customer-discovery feedback packet.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--topic", default="a2a_trust_manifest", help="Stable topic slug for the feedback request.")
    parser.add_argument("--question", help="Concrete primary question to ask external reviewers.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        packet = prepare_customer_discovery_feedback_packet(root=args.root, topic=args.topic, question=args.question)
    except ValueError as exc:
        parser.error(str(exc))
    payload = packet.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Customer discovery packet: {packet.packet_dir}")
        print(f"- request: {packet.request_artifact}")
        print(f"- metadata: {packet.metadata_artifact}")
        print(f"- checklist: {packet.checklist_artifact}")
        print(f"- record_feedback_command: {packet.record_feedback_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
