#!/usr/bin/env python3
"""Run one explicit content batch and exit. Never starts the legacy supervisor."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("seeds")
    scan.add_argument("--scan-only", action="store_true")
    scan.add_argument("--repo", type=Path, default=ROOT)
    daily = commands.add_parser("outbox")
    daily.add_argument("--day", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    daily.add_argument("--records", type=Path)
    daily.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.command == "seeds":
        from content_worker.seeds import eligible, read_seeds, run_batch

        rows = read_seeds(args.repo / "seeds/seeds.jsonl")
        selected = [row for row in rows if eligible(row)]
        if args.scan_only:
            print(
                json.dumps(
                    {
                        "total": len(rows),
                        "eligible": [s["seed_id"] for s in selected],
                        "ignored": [s["seed_id"] for s in rows if not eligible(s)],
                    }
                )
            )
        else:
            # One expensive run per timer invocation; no unbounded draining loop.
            results = run_batch(args.repo, selected)
            print(json.dumps(results))
            if any(row["status"] in {"blocked", "editorial_blocked"} for row in results):
                raise SystemExit(1)
    else:
        from content_worker.outbox import daily_records, write_outbox

        records = json.loads(args.records.read_text()) if args.records else daily_records(args.repo, args.day)
        print(write_outbox(args.repo, args.day, records))


if __name__ == "__main__":
    main()
