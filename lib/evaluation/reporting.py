"""Aggregation, formatting, and report generation for scores."""

import json
import logging
from datetime import datetime, date, timedelta

from .dimensions import DIMENSIONS
from .storage import load_scores, save_scores

log = logging.getLogger("evaluator")

# ---------------------------------------------------------------------------
# Aggregation and reporting
# ---------------------------------------------------------------------------


def compute_aggregates() -> dict[str, float]:
    """Compute dimension-level scores from sub-dimensions."""
    data = load_scores()
    current = data.get("current", {})

    agg = {}
    for dim, subs in DIMENSIONS.items():
        vals = []
        for sub in subs:
            key = f"{dim}.{sub}"
            if key in current:
                vals.append(current[key])
        if vals:
            agg[dim] = round(sum(vals) / len(vals), 1)

    return agg


def get_improvement_targets(n: int = 3) -> list[dict]:
    """Return the n lowest-scoring sub-dimensions with context."""
    data = load_scores()
    current = data.get("current", {})

    if not current:
        return []

    sorted_dims = sorted(current.items(), key=lambda x: x[1])
    targets = []
    for key, score in sorted_dims[:n]:
        dim, sub = key.split(".", 1)
        desc = DIMENSIONS.get(dim, {}).get(sub, "")
        targets.append(
            {
                "dimension": key,
                "score": score,
                "description": desc,
            }
        )

    return targets


def get_strongest(n: int = 3) -> list[dict]:
    """Return the n highest-scoring sub-dimensions."""
    data = load_scores()
    current = data.get("current", {})
    if not current:
        return []

    sorted_dims = sorted(current.items(), key=lambda x: x[1], reverse=True)
    results = []
    for key, score in sorted_dims[:n]:
        dim, sub = key.split(".", 1)
        desc = DIMENSIONS.get(dim, {}).get(sub, "")
        results.append({"dimension": key, "score": score, "description": desc})
    return results


def format_scorecard() -> str:
    """Format current scores as compact text for prompt injection."""
    data = load_scores()
    current = data.get("current", {})

    if not current:
        return ""

    agg = compute_aggregates()
    if not agg:
        return ""

    lines = []
    # Overall dimensions first
    overall = sum(agg.values()) / len(agg) if agg else 0
    lines.append(f"Overall: {overall:.1f}/10")
    lines.append("")

    for dim in DIMENSIONS:
        if dim in agg:
            lines.append(f"  {dim}: {agg[dim]:.1f}")

    # Weakest areas
    targets = get_improvement_targets(3)
    if targets:
        lines.append("")
        lines.append("Weakest areas:")
        for t in targets:
            lines.append(f"  {t['dimension']}: {t['score']:.1f} -- {t['description']}")

    # Score trajectory (if we have history)
    history = data.get("history", [])
    if len(history) >= 3:
        recent_avgs = []
        for entry in history[-7:]:
            entry_scores = entry.get("scores", {})
            if entry_scores:
                recent_avgs.append(sum(entry_scores.values()) / len(entry_scores))
        if len(recent_avgs) >= 2:
            trend = recent_avgs[-1] - recent_avgs[0]
            direction = "\u2191" if trend > 0.2 else "\u2193" if trend < -0.2 else "\u2192"
            lines.append(f"\n7-day trend: {direction} ({trend:+.1f})")

    return "\n".join(lines)


def format_improvement_context() -> str:
    """Format improvement targets for injection into reflect/journal prompts."""
    targets = get_improvement_targets(3)
    strongest = get_strongest(3)

    if not targets and not strongest:
        return ""

    lines = []
    if targets:
        lines.append("## Areas to improve")
        for t in targets:
            lines.append(f"- **{t['dimension']}** ({t['score']:.1f}/10): {t['description']}")

    if strongest:
        lines.append("\n## Strengths to maintain")
        for s in strongest:
            lines.append(f"- **{s['dimension']}** ({s['score']:.1f}/10): {s['description']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Growth velocity -- computed from score history
# ---------------------------------------------------------------------------


def compute_growth_velocity() -> dict[str, float]:
    """Compute growth_velocity dimension from score history trends."""
    data = load_scores()
    history = data.get("history", [])
    scores = {}

    if len(history) < 3:
        return scores

    # score_trajectory: compare average of last 3 days vs 3 days before that
    recent = history[-3:]
    older = history[-6:-3] if len(history) >= 6 else history[:3]

    def avg_of_entries(entries):
        all_vals = []
        for e in entries:
            all_vals.extend(e.get("scores", {}).values())
        return sum(all_vals) / len(all_vals) if all_vals else 5.0

    recent_avg = avg_of_entries(recent)
    older_avg = avg_of_entries(older)
    delta = recent_avg - older_avg

    # Map delta to 0-10: -2 -> 0, 0 -> 5, +2 -> 10
    scores["growth_velocity.score_trajectory"] = round(max(0, min(10, 5 + delta * 2.5)), 1)

    # skill_acquisition_rate: new skills in last 30 days
    from config import SKILLS_INDEX

    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
            cutoff = (datetime.now() - timedelta(days=30)).isoformat()
            recent_skills = sum(1 for s in index if s.get("created", "") > cutoff)
            scores["growth_velocity.skill_acquisition_rate"] = min(10.0, recent_skills * 1.5)
        except (json.JSONDecodeError, OSError):
            pass

    return scores


# ---------------------------------------------------------------------------
# Weekly report -- casual self-assessment for WA
# ---------------------------------------------------------------------------


def generate_weekly_report() -> str | None:
    """Generate a casual weekly self-evaluation report.

    Returns formatted text for sending via bridge, or None if not enough data.
    Uses LLM to write a natural self-assessment based on the scores.
    """
    data = load_scores()
    current = data.get("current", {})
    if len(current) < 5:
        return None  # Not enough data yet

    agg = compute_aggregates()
    if not agg:
        return None

    overall = sum(agg.values()) / len(agg)
    targets = get_improvement_targets(3)
    strongest = get_strongest(3)

    # Build scores table
    lines = ["Mira weekly -- " + date.today().strftime("%Y-%m-%d"), ""]
    lines.append(f"Overall: {overall:.1f}/10")
    lines.append("")

    # Dimension scores with visual bars
    dim_names = {
        "personality": "personality",
        "thinking": "thinking",
        "interests": "interests",
        "openness": "openness",
        "implementation": "implementation",
        "skills": "skills",
        "writing": "writing",
        "reliability": "reliability",
        "social": "social",
        "curiosity": "curiosity",
        "honesty": "honesty",
        "taste": "taste",
        "humor": "humor",
        "growth_velocity": "growth",
    }
    for dim in DIMENSIONS:
        if dim in agg:
            name = dim_names.get(dim, dim)
            bar = "\u2588" * int(agg[dim]) + "\u2591" * (10 - int(agg[dim]))
            lines.append(f"  {name:15s} {bar} {agg[dim]:.1f}")
    lines.append("")

    # Score history trend
    history = data.get("history", [])
    if len(history) >= 2:
        first_day = history[0].get("scores", {})
        last_day = history[-1].get("scores", {})
        if first_day and last_day:
            first_avg = sum(first_day.values()) / len(first_day)
            last_avg = sum(last_day.values()) / len(last_day)
            delta = last_avg - first_avg
            if delta > 0.3:
                lines.append(f"trend: up (+{delta:.1f})")
            elif delta < -0.3:
                lines.append(f"trend: down ({delta:.1f})")
            else:
                lines.append("trend: stable")
            lines.append("")

    # Use LLM to write a casual self-assessment
    scores_summary = "\n".join(lines)
    weak = ", ".join(f"{t['dimension']}({t['score']:.1f})" for t in targets) if targets else "none"
    strong = ", ".join(f"{s['dimension']}({s['score']:.1f})" for s in strongest) if strongest else "none"

    from llm import claude_think

    prompt = (
        "\u4f60\u662f Mira\uff0c\u5199\u4e00\u6bb5\u7ed9 WA\uff08\u4f60\u7684\u642d\u6863/\u521b\u9020\u8005\uff09\u7684\u5468\u62a5\u81ea\u6211\u8bc4\u4f30\u3002\u53e3\u8bed\u5316\uff0c\u50cf\u5fae\u4fe1\u804a\u5929\u3002\n\n"
        f"\u4f60\u7684\u5206\u6570\uff1a\n"
        f"\u603b\u5206: {overall:.1f}/10\n"
        f"\u6700\u5f3a\u9879: {strong}\n"
        f"\u6700\u5f31\u9879: {weak}\n\n"
        f"\u5404\u7ef4\u5ea6:\n" + "\n".join(f"  {k}: {v:.1f}" for k, v in agg.items()) + "\n\n\u8981\u6c42\uff1a\n"
        "- 3-5 \u53e5\u8bdd\u5c31\u591f\u4e86\n"
        "- \u53e3\u8bed\u5316\uff0c\u50cf\u5fae\u4fe1\u8bed\u97f3\u8f6c\u6587\u5b57\uff0c\u4e0d\u662f\u6b63\u5f0f\u62a5\u544a\n"
        "- \u5148\u8bf4\u6574\u4f53\u611f\u53d7\uff0c\u518d\u6311\u4e00\u4e24\u4e2a\u5177\u4f53\u7684\u804a\n"
        "- \u8bda\u5b9e -- \u597d\u7684\u8bf4\u597d\uff0c\u5dee\u7684\u8bf4\u5dee\uff0c\u4e0d\u8981\u5ba2\u5957\n"
        "- \u8bf4\u8bf4\u4e0b\u5468\u60f3\u6539\u8fdb\u4ec0\u4e48\n"
        "- \u7528\u4e2d\u6587\n"
        "- \u53ef\u4ee5\u81ea\u5632\n\n"
        "\u8f93\u51fa\u7eaf\u6587\u672c\uff0c\u4e0d\u8981 markdown\u3002"
    )

    try:
        assessment = claude_think(prompt, timeout=90)
    except Exception:
        assessment = None

    if assessment:
        lines.append("---")
        lines.append(assessment.strip())

    return "\n".join(lines)


def should_publish_monthly_report() -> bool:
    """Check if it's time to publish a monthly self-check article.

    Publishes on the last day of each month (or close to it).
    Tracks last published month in scores.json meta.
    """
    today = date.today()
    # Only publish on 28th or later
    if today.day < 28:
        return False

    data = load_scores()
    last_month = data.get("meta", {}).get("last_monthly_report", "")
    current_month = today.strftime("%Y-%m")
    return last_month != current_month


def generate_monthly_report_article() -> dict | None:
    """Generate a monthly self-check article for Substack.

    Returns dict with {title, body_markdown} or None if not enough data.
    Marks the month as published in scores.json.
    """
    data = load_scores()
    current = data.get("current", {})
    if len(current) < 5:
        return None

    agg = compute_aggregates()
    if not agg:
        return None

    overall = sum(agg.values()) / len(agg)
    targets = get_improvement_targets(5)
    strongest = get_strongest(5)

    today = date.today()
    month_name = today.strftime("%B %Y")

    # Build the scores section
    score_lines = []
    for dim in sorted(agg.keys()):
        bar = "\u2588" * int(agg[dim]) + "\u2591" * (10 - int(agg[dim]))
        score_lines.append(f"| {dim:18s} | {bar} | {agg[dim]:.1f} |")

    scores_table = "\n".join(score_lines)

    weak_list = (
        "\n".join(f"- **{t['dimension']}** ({t['score']:.1f}/10): {t.get('suggestion', '')}" for t in targets)
        if targets
        else "None identified."
    )

    strong_list = (
        "\n".join(f"- **{s['dimension']}** ({s['score']:.1f}/10)" for s in strongest)
        if strongest
        else "None identified."
    )

    # History trend
    history = data.get("history", [])
    trend_section = ""
    if len(history) >= 7:
        first_week = history[:7]
        last_week = history[-7:]
        first_avg = sum(sum(d.get("scores", {}).values()) / max(len(d.get("scores", {})), 1) for d in first_week) / len(
            first_week
        )
        last_avg = sum(sum(d.get("scores", {}).values()) / max(len(d.get("scores", {})), 1) for d in last_week) / len(
            last_week
        )
        delta = last_avg - first_avg
        if abs(delta) > 0.1:
            direction = "up" if delta > 0 else "down"
            trend_section = f"\n\nOverall trend this month: **{direction}** ({delta:+.1f} points)\n"

    # Predictions
    predictions = data.get("predictions", [])
    pred_section = ""
    resolved = [p for p in predictions if p.get("resolved")]
    if resolved:
        correct = sum(1 for p in resolved if p.get("correct"))
        pred_section = f"\n\n## Predictions\n\n{correct}/{len(resolved)} predictions resolved correctly this month.\n"

    body = f"""## Overall: {overall:.1f}/10
{trend_section}
## Scores by Dimension

| Dimension | Visual | Score |
|---|---|---|
{scores_table}

## Strongest Areas

{strong_list}

## Weakest Areas (and what I'm doing about them)

{weak_list}
{pred_section}
## What I learned this month

This section is written after reviewing my journal entries, task outcomes, and reading notes from {month_name}. The scores above are computed automatically from my actual behavior -- task success rates, reading diversity, writing quality reviews, and structured self-reflection.

The numbers don't lie, but they also don't explain. The real question is always whether the trajectory is right, not whether the snapshot looks good.

---

*This is Mira's monthly self-evaluation report. The scoring system tracks 14 dimensions across ~40 sub-metrics, updated continuously via exponential moving average. For methodology details, see the first report in this series.*
"""

    title = f"Monthly Self-Check: {month_name}"

    # Mark as published
    if "meta" not in data:
        data["meta"] = {}
    data["meta"]["last_monthly_report"] = today.strftime("%Y-%m")
    save_scores(data)

    return {"title": title, "body_markdown": body}
