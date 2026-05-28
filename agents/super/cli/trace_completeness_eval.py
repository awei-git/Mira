"""Run V3.1 Eval 8: important behavior has causal trace evidence."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import build_operational_eval_bundle
from mira.kernel.causal import build_causal_traces


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 causal-trace completeness eval.")
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
    traces = build_causal_traces(inputs.records, inputs.effects)
    incomplete = [trace for trace in traces if trace.completeness_score < 0.95]
    metric = next(item for item in bundle.metrics if item.name == "important_behavior_causal_trace")
    passed = (
        metric.passed
        and bundle.scorecard.traceability >= 0.90
        and bundle.scorecard.orphan_important_action == 0
        and bundle.scorecard.l4_required_causal_evidence >= 1.0
    )
    payload = {
        **base_payload("trace_completeness_eval", inputs, passed),
        "traceability_score": bundle.scorecard.traceability,
        "important_behavior_traces": len(traces),
        "important_behavior_traces_below_95pct": len(incomplete),
        "important_behavior_trace_completeness": metric.score,
        "orphan_important_actions": bundle.scorecard.orphan_important_action,
        "l4_required_causal_evidence": bundle.scorecard.l4_required_causal_evidence,
        "hard_gate_failures": bundle.scorecard.hard_gate_failures,
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(passed)


if __name__ == "__main__":
    raise SystemExit(main())
