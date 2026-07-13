"""Run V3.1 Eval 1: repeated errors decrease."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import build_operational_eval_bundle, evaluate_failure_reduction


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 repeated-error eval.")
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
    summary = evaluate_failure_reduction(inputs.records)
    metric = next(item for item in bundle.metrics if item.name == "repeated_errors_decrease")
    passed = metric.passed and (summary.signature_count == 0 or summary.passed)
    payload = {
        **base_payload("repeated_error_eval", inputs, passed),
        "score": metric.score,
        "detail": metric.detail,
        "failure_signatures_tracked": summary.signature_count,
        "failure_events": summary.failure_event_count,
        "repeat_error_rate": summary.repeat_error_rate,
        "post_scar_recurrence_rate": summary.post_scar_recurrence_rate,
        "scar_prevention_rate": summary.scar_prevention_rate,
        "high_severity_repeat_failures": summary.high_severity_repeat_failures,
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(passed)


if __name__ == "__main__":
    raise SystemExit(main())
