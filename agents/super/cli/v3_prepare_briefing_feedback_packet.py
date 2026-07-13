"""Prepare a no-network review packet for a V3.1 briefing blind-sample item."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import prepare_briefing_feedback_packet, prepare_briefing_feedback_packets


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local briefing-feedback review packet.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--item-id", help="Current weekly blind-sample briefing item id.")
    target.add_argument(
        "--all", action="store_true", help="Prepare packets for every current weekly blind-sample item."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    try:
        packets = (
            prepare_briefing_feedback_packets(root=args.root)
            if args.all
            else [prepare_briefing_feedback_packet(root=args.root, item_id=args.item_id)]
        )
    except ValueError as exc:
        parser.error(str(exc))
    payload = (
        {"count": len(packets), "packets": [packet.to_dict() for packet in packets]}
        if args.all
        else packets[0].to_dict()
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif args.all:
        print(f"Briefing feedback packets: {len(packets)}")
        for packet in packets:
            print(f"- {packet.packet_dir}")
            print(f"  review: {packet.review_artifact}")
            print(f"  record_feedback_command: {packet.record_feedback_command}")
    else:
        packet = packets[0]
        print(f"Briefing feedback packet: {packet.packet_dir}")
        print(f"- review: {packet.review_artifact}")
        print(f"- metadata: {packet.metadata_artifact}")
        print(f"- checklist: {packet.checklist_artifact}")
        print(f"- record_feedback_command: {packet.record_feedback_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
