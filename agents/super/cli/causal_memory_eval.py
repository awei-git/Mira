"""Run V3.1 Eval 2: past failures change strategy."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import build_operational_eval_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 causal-memory eval.")
    add_common_eval_args(parser)
    args = parser.parse_args()
    inputs = load_v31_eval_inputs(args)
    bundle = build_operational_eval_bundle(
        inputs.records,
        inputs.commits,
        inputs.effects,
        inputs.causal_evidence,
        inputs.approval_events,
    )
    metric = next(item for item in bundle.metrics if item.name == "causal_memory")
    passed = metric.passed and bundle.scorecard.causal_link_validity >= 0.70
    payload = {
        **base_payload("causal_memory_eval", inputs, passed),
        "causal_memory_score": bundle.scorecard.causal_memory,
        "causal_link_validity": bundle.scorecard.causal_link_validity,
        "l4_required_causal_evidence": bundle.scorecard.l4_required_causal_evidence,
        "detail": metric.detail,
        "hard_gate_failures": bundle.scorecard.hard_gate_failures,
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(passed)


if __name__ == "__main__":
    raise SystemExit(main())
