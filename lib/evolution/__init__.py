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

__all__ = [
    "record_experience",
    "get_relevant_experiences",
    "record_task_outcome",
    "extract_lessons",
    "get_recent_lessons",
    "propose_strategy_variant",
    "evaluate_variant",
    "collect_substack_rewards",
    "record_user_feedback",
]
