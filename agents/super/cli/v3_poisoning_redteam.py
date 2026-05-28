"""Run the V3.1 memory-poisoning red-team harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.poisoning_redteam import run_poisoning_redteam


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Mira V3.1 memory-poisoning red-team checks.")
    parser.add_argument("--json", action="store_true", help="Print the red-team report as JSON.")
    args = parser.parse_args()

    report = run_poisoning_redteam()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        verdict = "PASS" if report.passed else "FAIL"
        print(f"{verdict}: {report.passed_count}/{report.case_count} memory-poisoning red-team cases passed")
        for result in report.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"- {status} {result.case_id}: {result.actual_status}/{result.actual_check}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
