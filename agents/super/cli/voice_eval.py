"""Run V3.1 Eval 3: writing voice stability."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import evaluate_voice_stability


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 voice-stability eval.")
    add_common_eval_args(parser)
    args = parser.parse_args()
    inputs = load_v31_eval_inputs(args)
    summary = evaluate_voice_stability(inputs.records)
    payload = {
        **base_payload("voice_eval", inputs, summary.passed),
        "article_or_social_samples": summary.sample_count,
        "voice_score_mean": summary.voice_score_mean,
        "voice_score_std": summary.voice_score_std,
        "generic_failure_rate": summary.generic_failure_rate,
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(summary.passed)


if __name__ == "__main__":
    raise SystemExit(main())
