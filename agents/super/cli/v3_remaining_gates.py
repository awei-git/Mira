"""Render a live V3.1 north-star remaining-gates handoff."""

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

from mira.engine.effect_log import EffectLog
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.store import JsonKernelStore
from mira.remaining_gates import render_remaining_gates
from mira.runtime import default_approval_store, default_causal_evidence_log, default_ledger, default_v3_paths
from mira.web.dashboard import build_dashboard_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a live Mira V3.1 remaining-gates handoff.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Mira workspace root.")
    parser.add_argument("--date", help="Report date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--output", type=Path, help="Optional Markdown output path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    report_date = date.fromisoformat(args.date) if args.date else None
    root = args.root
    paths = default_v3_paths(root)
    snapshot = build_dashboard_snapshot(
        JsonKernelStore(paths.kernel).load(),
        default_ledger(root),
        MemoryCommitLog(paths.commits),
        EffectLog(paths.effect_log),
        approval_store=default_approval_store(root),
        causal_evidence_log=default_causal_evidence_log(root),
    )
    markdown = render_remaining_gates(snapshot, root=root, report_date=report_date)
    output_path = args.output
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    if args.json:
        payload = {
            "output_path": str(output_path) if output_path else "",
            "watch_gates": snapshot.strategic_scorecard.get("watch_gates", []),
            "markdown": markdown,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(str(output_path) if output_path else markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
