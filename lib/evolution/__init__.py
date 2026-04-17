"""Self-evolution package — closed-loop learning from real-world outcomes.

Three layers:
  1. Experience Replay: record outcomes, inject best-matching past experiences
  2. Lesson Extraction: daily, distill experiences into reusable principles
  3. Strategy Mutation: weekly, compare reward trends, propose A/B variants

Reward signals come from REAL outcomes (Substack engagement, user feedback,
execution success/failure), not self-assessment scores.

All LLM calls use oMLX (local) to avoid API cost.

Usage:
    from evolution import record_experience, get_relevant_experiences, extract_lessons
    from .rewards import collect_substack_rewards, record_user_feedback
"""

from .experience import (
    record_experience,
    get_relevant_experiences,
    record_task_outcome,
)
from .lessons import (
    extract_lessons,
    get_recent_lessons,
)
from .strategy import (
    propose_strategy_variant,
    evaluate_variant,
)
from .rewards import (
    collect_substack_rewards,
    record_user_feedback,
)

# Phase 1 — Hermes-inspired trajectory loop (scaffolding, flag-gated).
from .trajectory_compressor import compress
from .trajectory_recorder import (
    TrajectoryRecorder,
    persist_per_task,
    append_to_global,
    load_trajectory_jsonl,
)
from .tool_stats import (
    load_tool_stats,
    save_tool_stats,
    merge_into_global,
    success_rate_snapshot,
)
from .rewards_v2 import (
    compute_trajectory_reward,
    load_recent_trajectories,
)
from .trace import trace_task, workflow_trace, traced, TaskTrace
from .trajectory_reflect import (
    format_reflect_context,
    parse_skill_diff,
    needs_human_review,
    record_proposals,
)

__all__ = [
    # Legacy experience API
    "record_experience",
    "get_relevant_experiences",
    "record_task_outcome",
    "extract_lessons",
    "get_recent_lessons",
    "propose_strategy_variant",
    "evaluate_variant",
    "collect_substack_rewards",
    "record_user_feedback",
    # Phase 1 — trajectory
    "TrajectoryRecorder",
    "compress",
    "persist_per_task",
    "append_to_global",
    "load_trajectory_jsonl",
    "load_tool_stats",
    "save_tool_stats",
    "merge_into_global",
    "success_rate_snapshot",
    "compute_trajectory_reward",
    "load_recent_trajectories",
    "trace_task",
    "workflow_trace",
    "traced",
    "TaskTrace",
    "format_reflect_context",
    "parse_skill_diff",
    "needs_human_review",
    "record_proposals",
]
