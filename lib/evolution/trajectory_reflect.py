"""Reflect-side consumer of trajectories.jsonl + tool_stats.json.

Phase 1 Step 1.5: when weekly reflect runs, format the last N days of
trajectories + reward distribution + tool_stats delta into a prompt
block that asks the model for a `skill diff` (create / update / config
change). The diff is emitted to `proposed_changes.jsonl`; anything
touching publish flow is flagged for human review per CLAUDE.md Rule 3.

This module only *renders context* and *parses proposals* — it does NOT
call the LLM itself. The caller (reflect workflow) owns the LLM call
so we keep the module pure-function and testable.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Iterable

from schemas.trajectory import TrajectoryRecord

from . import config as _cfg
from .rewards_v2 import compute_trajectory_reward, load_recent_trajectories, summarize_rewards
from .tool_stats import load_tool_stats, success_rate_snapshot

log = logging.getLogger("mira.evolution.reflect")

# Any proposal whose `affects` field matches one of these substrings
# must NOT be applied automatically — reflect writes it to the bridge
# inbox for you to review per CLAUDE.md hard rule 3.
PUBLISH_SENSITIVE_SUBSTRINGS = ("publish", "substack", "preflight", "content_guard")


def format_reflect_context(
    *,
    days: int = 7,
    max_trajectories: int = 25,
    trajectories: list[TrajectoryRecord] | None = None,
    tool_stats_path: Path | None = None,
) -> str:
    """Produce a markdown block for injection into the weekly reflect prompt.

    Layout:
        ## Trajectory reward summary
        - total / mean / min / max / crashes / per-agent
        ## Tool success rates (global snapshot)
        | tool | count | success_rate |
        ## Top-scoring trajectories
        ...
        ## Bottom-scoring trajectories
        ...

    Returns empty string when no trajectories are available — lets the
    caller skip adding an empty section.
    """
    if trajectories is None:
        trajectories = load_recent_trajectories(days=days)
    if not trajectories:
        return ""

    summary = summarize_rewards(trajectories)
    lines: list[str] = [
        "## Trajectory reward summary",
        f"- window: {days}d, records: **{summary['count']}**",
        f"- score min/mean/max: {summary['min_score']:+.2f} / "
        f"{summary['mean_score']:+.2f} / {summary['max_score']:+.2f}",
        f"- crash count: {summary['crash_count']}",
    ]
    if summary.get("per_agent_mean"):
        per_agent = ", ".join(f"{a}={s:+.2f}" for a, s in summary["per_agent_mean"].items())
        lines.append(f"- per-agent mean: {per_agent}")

    # Tool stats snapshot
    try:
        rates = success_rate_snapshot(load_tool_stats(tool_stats_path))
    except Exception:
        rates = {}
    if rates:
        lines += ["", "## Tool success rates (global)", "", "| tool | success_rate |", "|---|---|"]
        for tool, rate in sorted(rates.items(), key=lambda kv: kv[1]):
            lines.append(f"| `{tool}` | {rate:.2f} |")

    # Top / bottom scorers
    scored: list[tuple[float, TrajectoryRecord]] = []
    for t in trajectories:
        score, _ = compute_trajectory_reward(t)
        scored.append((score, t))
    scored.sort(key=lambda kv: kv[0])
    tail = scored[-min(5, max_trajectories) :][::-1]
    head = scored[: min(5, max_trajectories)]

    lines += ["", "## Top-scoring trajectories"]
    for score, t in tail:
        lines.append(f"- task `{t.task_id}` ({t.agent}): {score:+.2f}")
    lines += ["", "## Bottom-scoring trajectories"]
    for score, t in head:
        lines.append(f"- task `{t.task_id}` ({t.agent}): {score:+.2f}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Proposal parsing + persistence
# ---------------------------------------------------------------------------


def parse_skill_diff(llm_output: str) -> list[dict]:
    """Extract a JSON array of skill-diff proposals from the LLM's reply.

    Each proposal should be a dict with at least:
      - "kind": "create" | "update" | "config_change"
      - "target": skill name or config path
      - "rationale": one-sentence reason
      - "affects": free-form string (searched for publish-sensitive substrings)

    Accepts either a raw JSON array or a markdown ```json block; returns
    an empty list when nothing parses (never raises).
    """
    if not llm_output:
        return []

    # Pull out a JSON array — tolerate `` ```json `` fences.
    text = llm_output.strip()
    if "```" in text:
        for block in text.split("```"):
            stripped = block.strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
            if stripped.startswith("["):
                text = stripped
                break

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.info("parse_skill_diff: no JSON array found")
        return []
    if not isinstance(data, list):
        return []

    proposals: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not entry.get("kind") or not entry.get("target"):
            continue
        proposals.append(
            {
                "kind": entry.get("kind"),
                "target": entry.get("target"),
                "rationale": entry.get("rationale", ""),
                "affects": entry.get("affects", ""),
                "diff": entry.get("diff", ""),
            }
        )
    return proposals


def needs_human_review(proposal: dict) -> bool:
    affects = (proposal.get("affects") or "").lower()
    target = (proposal.get("target") or "").lower()
    hay = f"{affects} {target}"
    return any(needle in hay for needle in PUBLISH_SENSITIVE_SUBSTRINGS)


def record_proposals(
    proposals: Iterable[dict],
    *,
    path: Path | None = None,
) -> tuple[list[dict], list[dict]]:
    """Append proposals to PROPOSED_CHANGES_FILE and split by review need.

    Returns (auto_applicable, needs_review). Both lists are plain dicts
    (the same items echoed for convenience). Never raises — logs and
    returns ([], []) on IO failure.
    """
    target = path or _cfg.PROPOSED_CHANGES_FILE
    auto: list[dict] = []
    review: list[dict] = []
    ts = date.today().isoformat()

    rows: list[str] = []
    for p in proposals:
        row = {"ts": ts, **p, "needs_review": needs_human_review(p)}
        rows.append(json.dumps(row, ensure_ascii=False))
        (review if row["needs_review"] else auto).append(p)

    if not rows:
        return auto, review
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
    except OSError as e:
        log.warning("record_proposals: failed to persist to %s: %s", target, e)
        return [], []
    return auto, review
