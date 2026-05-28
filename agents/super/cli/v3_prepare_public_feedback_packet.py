"""Prepare a no-network feedback solicitation packet for a V3.1 public writeup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import prepare_public_feedback_solicitation_packet, prepare_public_feedback_solicitation_packets


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local public-feedback solicitation packet.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--slug", help="Stable publication/evidence slug.")
    parser.add_argument("--published-url", help="Published writeup URL to request feedback on.")
    parser.add_argument("--title", help="Optional title to use in the feedback request.")
    parser.add_argument("--stats-artifact", type=Path, help="Optional publication stats JSON artifact.")
    parser.add_argument(
        "--all", action="store_true", help="Prepare packets for every recorded writeup still missing feedback."
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    if args.all and (args.slug or args.published_url or args.title or args.stats_artifact):
        parser.error("--all cannot be combined with --slug, --published-url, --title, or --stats-artifact")
    if not args.all and (not args.slug or not args.published_url):
        parser.error("--slug and --published-url are required unless --all is used")

    try:
        packets = (
            prepare_public_feedback_solicitation_packets(root=args.root)
            if args.all
            else [
                prepare_public_feedback_solicitation_packet(
                    root=args.root,
                    slug=args.slug,
                    published_url=args.published_url,
                    title=args.title,
                    stats_artifact=args.stats_artifact,
                )
            ]
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
        print(f"Feedback packets: {len(packets)}")
        for packet in packets:
            print(f"- {packet.packet_dir}")
            print(f"  request: {packet.request_artifact}")
            print(f"  record_feedback_command: {packet.record_feedback_command}")
    else:
        packet = packets[0]
        print(f"Feedback packet: {packet.packet_dir}")
        print(f"- request: {packet.request_artifact}")
        print(f"- metadata: {packet.metadata_artifact}")
        print(f"- checklist: {packet.checklist_artifact}")
        print(f"- record_feedback_command: {packet.record_feedback_command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
