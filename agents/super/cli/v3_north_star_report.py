"""Write the V3.1 weekly north-star report artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.engine.effect_log import EffectLog
from mira.evals import build_weekly_north_star_report, write_weekly_north_star_report
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.store import JsonKernelStore
from mira.runtime import default_approval_store, default_causal_evidence_log, default_ledger, default_v3_paths
from mira.web.dashboard import build_dashboard_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Mira V3.1 weekly north-star report.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--week", help="Week label to use in the report filename and heading.")
    parser.add_argument("--window-days", type=int, default=7, help="Number of days in the report window.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where the markdown report artifact should be written. Defaults to data/v3/artifacts/north_star_reports.",
    )
    parser.add_argument(
        "--first-stage-scope",
        action="store_true",
        help="Limit run/effect evidence to the V3.1 first-stage workflow scope.",
    )
    parser.add_argument("--json", action="store_true", help="Print the created path as JSON.")
    parser.add_argument("--stdout", action="store_true", help="Print the report instead of writing an artifact.")
    args = parser.parse_args()

    paths = default_v3_paths(args.root)
    ledger = default_ledger(args.root)
    records = ledger.list()
    causal_evidence = default_causal_evidence_log(args.root).list()
    approval_store = default_approval_store(args.root)
    approval_events = approval_store.list_events()
    commit_log = MemoryCommitLog(paths.commits)
    effect_log = EffectLog(paths.effect_log)
    review_queues = build_dashboard_snapshot(
        JsonKernelStore(paths.kernel).load(),
        ledger,
        commit_log,
        effect_log,
        approval_store=approval_store,
        causal_evidence_log=default_causal_evidence_log(args.root),
        include_implementation_status=False,
    ).review_queues

    if args.stdout:
        print(
            build_weekly_north_star_report(
                records,
                commit_log.list(),
                effect_log.list(),
                causal_evidence,
                approval_events=approval_events,
                week_label=args.week,
                window_days=args.window_days,
                first_stage_scope=args.first_stage_scope,
                review_queues=review_queues,
            )
        )
        return 0

    output_dir = args.output_dir if args.output_dir else paths.artifacts / "north_star_reports"
    output_path = write_weekly_north_star_report(
        output_dir,
        records,
        commit_log.list(),
        effect_log.list(),
        causal_evidence,
        approval_events=approval_events,
        week_label=args.week,
        window_days=args.window_days,
        first_stage_scope=args.first_stage_scope,
        review_queues=review_queues,
    )
    if args.json:
        print(json.dumps({"report_path": str(output_path)}, indent=2, sort_keys=True))
    else:
        print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
