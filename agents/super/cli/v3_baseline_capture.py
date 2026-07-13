"""Capture V3.1 North Star baseline artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.runtime import capture_v31_baselines


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Mira V3.1 baseline artifacts.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--date", dest="capture_date", help="Baseline date in YYYY-MM-DD form.")
    parser.add_argument("--window-days", type=int, default=7, help="Number of days included in the baseline window.")
    parser.add_argument("--json", action="store_true", help="Print capture result as JSON.")
    args = parser.parse_args()

    capture_date = date.fromisoformat(args.capture_date) if args.capture_date else None
    result = capture_v31_baselines(args.root, capture_date=capture_date, window_days=args.window_days)
    payload = {"date_key": result.date_key, "paths": result.paths}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Captured V3.1 baselines for {result.date_key}")
        for name, path in sorted(result.paths.items()):
            print(f"- {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
