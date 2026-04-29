"""Score diagnosis and improvement plan generation."""

import json
import logging
from datetime import datetime

from .dimensions import DIMENSIONS
from .storage import load_scores

log = logging.getLogger("evaluator")

# ---------------------------------------------------------------------------
# Paths and thresholds
# ---------------------------------------------------------------------------
from config import SOUL_DIR as _SOUL_DIR

_SOUL_DIR
_LOW_SCORE = 4.0  # dimensions below this get diagnosed
_DECLINING_DAYS = 3  # consecutive decline triggers alert
_IMPROVEMENT_FILE = _SOUL_DIR / "improvement_plan.json"


def diagnose_scores() -> dict:
    """Analyze scores for weak dimensions and declining trends.

    Returns {
        "low_scores": [{dim, score, category}],
        "declining": [{dim, scores_over_days, delta}],
        "calibration_insights": str,
        "needs_action": bool,
    }
    """
    data = load_scores()
    current = data.get("current", {})
    history = data.get("history", [])

    # 1. Find low scores
    low = []
    for dim, score in current.items():
        if score < _LOW_SCORE:
            category = dim.split(".")[0]
            low.append({"dim": dim, "score": round(score, 2), "category": category})
    low.sort(key=lambda x: x["score"])

    # 2. Find declining trends (last N days)
    declining = []
    if len(history) >= _DECLINING_DAYS:
        recent = history[-_DECLINING_DAYS:]
        for dim in current:
            values = []
            for day in recent:
                day_scores = day.get("scores", {})
                if dim in day_scores:
                    values.append(day_scores[dim])
            if len(values) >= _DECLINING_DAYS:
                # Check if monotonically declining
                is_declining = all(values[i] > values[i + 1] for i in range(len(values) - 1))
                if is_declining:
                    delta = values[0] - values[-1]
                    declining.append(
                        {
                            "dim": dim,
                            "scores": [round(v, 2) for v in values],
                            "delta": round(delta, 2),
                        }
                    )
    declining.sort(key=lambda x: x["delta"], reverse=True)

    # 3. Read calibration insights
    cal_insights = _summarize_calibration()

    return {
        "low_scores": low,
        "declining": declining,
        "calibration_insights": cal_insights,
        "needs_action": bool(low) or bool(declining),
    }


def _summarize_calibration() -> str:
    """Read calibration.jsonl and extract patterns."""
    cal_file = _SOUL_DIR / "calibration.jsonl"
    if not cal_file.exists():
        return ""

    records = []
    for line in cal_file.read_text(encoding="utf-8").strip().splitlines()[-50:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return ""

    # Count calibration outcomes
    outcomes = {}
    for r in records:
        note = r.get("calibration_note", "")
        if note:
            outcomes[note] = outcomes.get(note, 0) + 1

    if not outcomes:
        return ""

    parts = [f"{k}: {v}x" for k, v in sorted(outcomes.items(), key=lambda x: -x[1])]
    return "Calibration: " + ", ".join(parts)


def generate_improvement_plan(diagnosis: dict) -> str | None:
    """Use LLM to generate concrete improvement actions for weak dimensions.

    Returns improvement plan text, or None if no action needed.
    """
    if not diagnosis["needs_action"]:
        return None

    # Build the diagnosis summary for LLM
    parts = []
    if diagnosis["low_scores"]:
        parts.append("## Low Scores (consistently below 4.0)")
        for item in diagnosis["low_scores"][:5]:
            # Look up description from DIMENSIONS
            dim_parts = item["dim"].split(".")
            desc = (
                DIMENSIONS.get(dim_parts[0], {}).get(dim_parts[1], item["dim"]) if len(dim_parts) == 2 else item["dim"]
            )
            parts.append(f"- **{item['dim']}** = {item['score']}: {desc}")

    if diagnosis["declining"]:
        parts.append("\n## Declining Trends (getting worse)")
        for item in diagnosis["declining"][:3]:
            parts.append(
                f"- **{item['dim']}**: {' -> '.join(str(s) for s in item['scores'])} (dropped {item['delta']})"
            )

    if diagnosis["calibration_insights"]:
        parts.append(f"\n## Task Calibration\n{diagnosis['calibration_insights']}")

    diagnosis_text = "\n".join(parts)

    try:
        from llm import claude_think

        prompt = (
            "You are Mira's self-improvement system. Analyze these weak areas "
            "and generate 3-5 concrete, actionable improvements.\n\n"
            f"{diagnosis_text}\n\n"
            "For each improvement:\n"
            "1. What to change (be specific -- which prompt, behavior, or process)\n"
            "2. Expected impact on which score dimension\n"
            "3. How to measure success\n\n"
            "Rules:\n"
            "- Only suggest things Mira can actually do autonomously\n"
            "- Focus on behavioral changes, not infrastructure\n"
            '- Be concrete: "add X to the journal prompt" not "improve journaling"\n'
            "- Prioritize by expected impact\n\n"
            "Return as a numbered list. Be concise."
        )

        plan = claude_think(prompt, timeout=60, tier="light")
        if plan:
            # Save the plan
            plan_data = {
                "generated_at": datetime.now().isoformat(),
                "diagnosis": diagnosis,
                "plan": plan,
                "status": "pending",
            }
            _IMPROVEMENT_FILE.write_text(
                json.dumps(plan_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info(
                "Improvement plan generated: %d low scores, %d declining",
                len(diagnosis["low_scores"]),
                len(diagnosis["declining"]),
            )
            return plan
    except (ImportError, OSError) as e:
        log.warning("Improvement plan generation failed: %s", e)

    return None


def get_active_improvements() -> str:
    """Load current improvement plan for injection into prompts."""
    if not _IMPROVEMENT_FILE.exists():
        return ""
    try:
        data = json.loads(_IMPROVEMENT_FILE.read_text(encoding="utf-8"))
        if data.get("status") == "pending":
            return data.get("plan", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""
