"""Phase 1 reward computation — trajectory → signal → weighted score.

Key difference from `rewards.py`: this module never accepts a
self-assessment score. Signals are derived from observed facts:
  - tool_success_rate: from TrajectoryRecord.tool_stats
  - outcome_verified:  from status == "done" AND a provided output_ok flag
  - crash_penalty:     from TrajectoryRecord.crashed
  - time_cost_penalty: normalized task duration vs budget
  - substack_new_subs_24h / reader_feedback_*: supplied by callers that
    have access to the engagement fetchers — kept as optional inputs so
    this module stays pure-function testable.

The weighted sum uses `REWARD_WEIGHTS` from `evolution.config`, so
tuning happens in one place.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Iterable

from schemas.trajectory import TrajectoryRecord

from .config import REWARD_WEIGHTS, TRAJECTORY_FILE

log = logging.getLogger("mira.evolution.rewards_v2")


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _aggregate_tool_success_rate(trajectory: TrajectoryRecord) -> float | None:
    total = sum(s.count for s in trajectory.tool_stats.values())
    if total == 0:
        return None
    success = sum(s.success for s in trajectory.tool_stats.values())
    return success / total


def compute_trajectory_reward(
    trajectory: TrajectoryRecord,
    *,
    outcome_verified: bool | None = None,
    substack_new_subs_24h: int | None = None,
    reader_feedback: int | None = None,  # positive int / negative int / None
    elapsed_seconds: float | None = None,
    budget_seconds: float | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """Compute the composite reward for one trajectory.

    Returns (score, components) where `components` is the per-signal
    contribution (already multiplied by its weight) for transparency /
    debugging / dashboard display.

    All signal inputs are optional; missing signals contribute zero.
    """
    w = weights or REWARD_WEIGHTS
    components: dict[str, float] = {}

    # Tool success rate (0..1). Missing when no tools were called.
    tsr = _aggregate_tool_success_rate(trajectory)
    if tsr is not None:
        components["tool_success_rate"] = _clip(tsr) * w.get("tool_success_rate", 0.0)

    # Outcome verified (boolean). If caller didn't provide a value, skip.
    if outcome_verified is not None:
        components["outcome_verified"] = (1.0 if outcome_verified else 0.0) * w.get("outcome_verified", 0.0)

    # Crash penalty (boolean).
    if trajectory.crashed:
        components["crash_penalty"] = 1.0 * w.get("crash_penalty", 0.0)

    # Time cost penalty — only if both elapsed + budget provided.
    # Normalized: overrun fraction clipped to [0, 1].
    if elapsed_seconds is not None and budget_seconds and budget_seconds > 0:
        overrun = max(0.0, elapsed_seconds - budget_seconds) / budget_seconds
        components["time_cost_penalty"] = _clip(overrun) * w.get("time_cost_penalty", 0.0)

    # Substack subscribers — only applies to writer / publish tasks.
    if substack_new_subs_24h is not None and substack_new_subs_24h > 0:
        # Diminishing returns: sqrt so 9 subs ≈ 3 units, 16 ≈ 4.
        components["substack_new_subs_24h"] = (substack_new_subs_24h**0.5) * w.get("substack_new_subs_24h", 0.0)

    # Reader feedback is a signed integer summary (positive − negative).
    if reader_feedback:
        if reader_feedback > 0:
            components["reader_feedback_positive"] = reader_feedback * w.get("reader_feedback_positive", 0.0)
        else:
            components["reader_feedback_negative"] = abs(reader_feedback) * w.get("reader_feedback_negative", 0.0)

    score = sum(components.values())
    return round(score, 3), components


# ---------------------------------------------------------------------------
# Loader for the global trajectories.jsonl — used by reflect and tests.
# ---------------------------------------------------------------------------


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_recent_trajectories(
    days: int = 7,
    path: Path | None = None,
) -> list[TrajectoryRecord]:
    """Load TrajectoryRecords from the global JSONL within the lookback window.

    Safe on missing/corrupted file (returns whatever parses).
    """
    target = path or TRAJECTORY_FILE
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    out: list[TrajectoryRecord] = []
    for row in _iter_jsonl(target):
        try:
            rec = TrajectoryRecord.model_validate(row)
        except Exception:
            continue
        ts = rec.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            out.append(rec)
    return out


# Convenience: summary for dashboards / reflect prompt injection.
def summarize_rewards(trajectories: list[TrajectoryRecord]) -> dict:
    """Lightweight aggregate for reflection context."""
    if not trajectories:
        return {"count": 0}
    scores: list[float] = []
    per_agent: dict[str, list[float]] = {}
    crash_count = 0
    for t in trajectories:
        score, _ = compute_trajectory_reward(t)
        scores.append(score)
        per_agent.setdefault(t.agent, []).append(score)
        if t.crashed:
            crash_count += 1

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "count": len(scores),
        "mean_score": round(_mean(scores), 3),
        "min_score": round(min(scores), 3),
        "max_score": round(max(scores), 3),
        "crash_count": crash_count,
        "per_agent_mean": {a: round(_mean(s), 3) for a, s in per_agent.items()},
        "window_days": (
            datetime.now(timezone.utc)
            - min(
                (t.timestamp.replace(tzinfo=timezone.utc) if t.timestamp.tzinfo is None else t.timestamp)
                for t in trajectories
            )
        ).days,
    }
