"""Run V3.1 Eval 4: briefing interest fit and feedback loop."""

from __future__ import annotations

import argparse

from _v31_eval_cli import add_common_eval_args, base_payload, emit_payload, exit_code, load_v31_eval_inputs
from mira.evals import build_briefing_item_reviews, build_weekly_blind_sample, evaluate_briefing_interest_fit


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Mira V3.1 briefing-interest eval.")
    add_common_eval_args(parser)
    args = parser.parse_args()
    inputs = load_v31_eval_inputs(args)
    summary = evaluate_briefing_interest_fit(inputs.records)
    item_reviews = build_briefing_item_reviews(inputs.records)
    blind_sample = build_weekly_blind_sample(inputs.records)
    payload = {
        **base_payload("briefing_interest_eval", inputs, summary.passed),
        "briefing_samples": summary.sample_count,
        "briefing_items_scored": summary.item_count,
        "precision_at_5": summary.precision_at_5,
        "action_rate": summary.action_rate,
        "dismiss_rate": summary.dismiss_rate,
        "interest_coverage": summary.interest_coverage,
        "novel_but_relevant_rate": summary.novel_but_relevant_rate,
        "feedback_items": summary.feedback_item_count,
        "feedback_coverage_rate": summary.feedback_coverage_rate,
        "promoted_items": summary.promoted_item_count,
        "weekly_blind_sample_items": len(blind_sample),
        "review_item_count": len(item_reviews),
    }
    emit_payload(payload, as_json=args.json)
    return exit_code(summary.passed)


if __name__ == "__main__":
    raise SystemExit(main())
