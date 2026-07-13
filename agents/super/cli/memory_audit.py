"""Run V3.1 Eval 7: memory health and pollution audit."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import build_memory_audit_records, evaluate_memory_health


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 memory-health audit.")
    add_common_eval_args(parser)
    args = parser.parse_args()
    inputs = load_v31_eval_inputs(args)
    summary = evaluate_memory_health(inputs.commits, inputs.records)
    audits = build_memory_audit_records(inputs.commits)
    payload = {
        **base_payload("memory_audit", inputs, summary.passed),
        "commit_count": len(inputs.commits),
        "audit_record_count": len(audits),
        "audited_memories": summary.audited_memories,
        "memory_precision": summary.memory_precision,
        "unsupported_claim_rate": summary.unsupported_claim_rate,
        "quarantine_recall": summary.quarantine_recall,
        "snapshot_contamination_rate": summary.snapshot_contamination_rate,
        "contaminated_snapshot_count": summary.contaminated_snapshot_count,
        "critical_pollution_count": summary.critical_pollution_count,
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(summary.passed)


if __name__ == "__main__":
    raise SystemExit(main())
