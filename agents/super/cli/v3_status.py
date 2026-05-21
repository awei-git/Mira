"""Print V3 memory-first runtime status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mira.configuration import default_v3_config
from mira.engine.effect_log import EffectLog
from mira.kernel.commit import MemoryCommitLog
from mira.kernel.store import JsonKernelStore
from mira.runtime import default_causal_evidence_log, default_ledger, default_v3_paths, run_communication
from mira.web.dashboard import build_dashboard_snapshot


def render_status() -> str:
    paths = default_v3_paths(ROOT)
    kernel = JsonKernelStore(paths.kernel).load()
    snapshot = build_dashboard_snapshot(
        kernel,
        default_ledger(ROOT),
        MemoryCommitLog(paths.commits),
        EffectLog(paths.effect_log),
        causal_evidence_log=default_causal_evidence_log(ROOT),
    )
    lines = [
        "Mira V3 Status",
        "==============",
        "",
        f"Kernel: {paths.kernel}",
        f"Ledger: {paths.ledger}",
        f"Commits: {paths.commits}",
        f"Effect log: {paths.effect_log}",
        f"Pipelines: {len(snapshot.active_pipelines)}",
        f"Recent experiences: {len(snapshot.recent_experience_ids)}",
        f"Scars: {len(snapshot.scars)}",
        f"Active hypotheses: {len(snapshot.active_hypotheses)}",
        f"Skill traces: {len(snapshot.skill_traces)}",
        f"Policies: {snapshot.hard_policy_count} hard, {snapshot.soft_policy_count} soft",
        f"Review queues: {sum(len(v) for v in snapshot.review_queues.values())}",
        f"Causal evidence: {sum(snapshot.causal_evidence_counts.values())}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show Mira V3 status.")
    parser.add_argument("--json", action="store_true", help="Print dashboard/config JSON.")
    parser.add_argument("--run-communication", metavar="MESSAGE", help="Run the migrated communication pipeline.")
    args = parser.parse_args()

    if args.run_communication:
        print(run_communication(args.run_communication, root=ROOT))
        return 0
    if args.json:
        paths = default_v3_paths(ROOT)
        kernel = JsonKernelStore(paths.kernel).load()
        dashboard = build_dashboard_snapshot(
            kernel,
            default_ledger(ROOT),
            MemoryCommitLog(paths.commits),
            EffectLog(paths.effect_log),
            causal_evidence_log=default_causal_evidence_log(ROOT),
        )
        print(json.dumps({"dashboard": dashboard.__dict__, "config": default_v3_config().to_dict()}, indent=2))
        return 0
    print(render_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
