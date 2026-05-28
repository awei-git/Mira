"""Prepare all local no-network packets for current V3.1 north-star gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import prepare_north_star_closure_packets


def main() -> int:
    parser = argparse.ArgumentParser(description="Create local operator packets for current north-star closure gates.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    manifest = prepare_north_star_closure_packets(root=args.root)
    payload = manifest.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        counts = payload["counts"]
        print(f"North-star closure manifest: {manifest.manifest_artifact}")
        print(f"- checklist: {manifest.checklist_artifact}")
        print(f"- publication packets: {counts['publication_packets']}")
        print(f"- public feedback packets: {counts['public_feedback_packets']}")
        print(f"- customer discovery packets: {counts['customer_discovery_packets']}")
        print(f"- briefing feedback packets: {counts['briefing_feedback_packets']}")
        if counts["warnings"]:
            print(f"- warnings: {counts['warnings']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
