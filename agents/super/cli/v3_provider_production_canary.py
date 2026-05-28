"""Run a readiness-gated V3 production provider canary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import run_provider_production_canary


def render_report(report: dict) -> str:
    lines = [
        "Mira V3 Provider Production Canary",
        "==================================",
        "",
        f"Ready: {'yes' if report['ready'] else 'no'}",
        f"Providers: {', '.join(report['providers']) or '(none)'}",
        f"Dry run: {'yes' if report.get('dry_run') else 'no'}",
    ]
    readiness = report.get("readiness") or {}
    if readiness and not readiness.get("ready"):
        lines.append("Readiness: failed")
    for item in report.get("canaries") or []:
        lines.extend(
            [
                "",
                f"{item['provider']}:",
                f"- workflow: {item['workflow']}",
                f"- target: {item['target']}",
                f"- effect_status: {item.get('effect_status', 'not_run')}",
                f"- external_ref: {item.get('external_ref', '')}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3 readiness-gated production provider canary.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root to mutate.")
    parser.add_argument("--resolver-config", type=Path, help="Provider resolver config path.")
    parser.add_argument("--adapter-config", type=Path, help="Provider adapter config path.")
    parser.add_argument(
        "--provider",
        action="append",
        choices=("substack", "rss", "tts", "social", "market", "health"),
        help="Provider to canary. Defaults to social, market, and health.",
    )
    parser.add_argument("--granted-by", default="v3-provider-production-canary", help="Approval grant actor label.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Check readiness and show the planned canary without mutating state."
    )
    args = parser.parse_args()

    report = run_provider_production_canary(
        root=args.root,
        providers=tuple(args.provider) if args.provider else None,
        granted_by=args.granted_by,
        resolver_config_path=args.resolver_config,
        adapter_config_path=args.adapter_config,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
