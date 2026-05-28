"""Audit a V3.1 public-writeup draft before publication review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import public_writeup_safety_report


def render_report(report) -> str:
    lines = [
        "Mira V3 Public Writeup Safety",
        "================================",
        "",
        f"Passed: {'yes' if report.passed else 'no'}",
        f"Draft: {report.draft_artifact}",
        f"Preview hash: {report.preview_hash}",
    ]
    if report.findings:
        lines.extend(["", "Findings:"])
        lines.extend(f"- {finding}" for finding in report.findings)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic public-writeup draft safety audit.")
    parser.add_argument("--draft-artifact", type=Path, required=True, help="Local public-writeup draft artifact.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report = public_writeup_safety_report(args.draft_artifact)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
