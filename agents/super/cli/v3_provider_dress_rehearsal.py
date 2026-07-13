"""Run a local V3 provider dress rehearsal through approval and reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import run_local_provider_dress_rehearsal


def render_report(report: dict) -> str:
    lines = [
        "Mira V3 Provider Dress Rehearsal",
        "=================================",
        "",
        f"Ready: {'yes' if report['ready'] else 'no'}",
        f"Providers: {', '.join(report['providers']) or '(none)'}",
    ]
    for item in report.get("rehearsals") or []:
        lines.extend(
            [
                "",
                f"{item['provider']}:",
                f"- workflow: {item['workflow']}",
                f"- target: {item['target']}",
                f"- effect_status: {item['effect_status']}",
                f"- external_ref: {item['external_ref']}",
                f"- provider_state_manifest: {item['provider_state_manifest']}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3 local provider dress rehearsal.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root to mutate.")
    parser.add_argument(
        "--provider",
        action="append",
        choices=("social", "market", "health"),
        help="Provider to rehearse. Defaults to social, market, and health.",
    )
    parser.add_argument("--granted-by", default="v3-provider-dress-rehearsal", help="Approval grant actor label.")
    args = parser.parse_args()

    report = run_local_provider_dress_rehearsal(
        root=args.root,
        providers=tuple(args.provider) if args.provider else None,
        granted_by=args.granted_by,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
