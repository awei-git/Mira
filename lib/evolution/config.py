"""Evolution package paths, constants, and feature flags."""

from pathlib import Path  # noqa: F401

from config import SOUL_DIR

EXPERIENCE_DIR = SOUL_DIR / "experiences"
LESSON_DIR = SOUL_DIR / "lessons"
VARIANT_DIR = SOUL_DIR / "variants"

# Phase 1 (Hermes trajectory loop) — enabled 2026-04-17 after Phase 0
# pillar 1 (supervisor scaffold), pillar 3 (circuit breaker wrapping
# oMLX + Substack), and the Substack publication_stats fetcher
# prerequisite landed. Flip back to False to disable all telemetry:
# trajectory persistence, tool_stats aggregation, FTS5 indexing, and
# reward computation. The other modules keep compiling either way.
ENABLE_TRAJECTORY_V2 = True

# Trajectory aggregation paths (Phase 1).
TRAJECTORY_FILE = SOUL_DIR / "trajectories.jsonl"
TOOL_STATS_FILE = SOUL_DIR / "tool_stats.json"
CRASHES_FILE = SOUL_DIR / "crashes.jsonl"  # populated by Phase 0 supervisor
PROPOSED_CHANGES_FILE = SOUL_DIR / "proposed_changes.jsonl"

# Reward signal weights for composite scoring.
# Positive = good outcome, negative = bad outcome.
# Magnitudes reflect how strongly each signal should influence learning.
REWARD_WEIGHTS = {
    # External engagement (strongest signal — real humans reacted)
    "likes": 2.0,
    "comments": 5.0,  # someone cared enough to reply
    "restacks": 3.0,
    "views": 0.01,  # views alone are weak
    # User (WA) feedback (very strong — direct human judgment)
    "wa_positive": 10.0,
    "wa_negative": -15.0,
    "wa_repeated_failure": -25.0,  # "why is this still broken?"
    # Execution outcome (legacy, Phase 0 level)
    "success": 1.0,
    "failure": -3.0,
    "timeout": -2.0,
    # Phase 1 trajectory-derived signals (normalized to [0, 1] where
    # applicable; magnitudes chosen so one good task can offset one
    # small failure but not dominate explicit user feedback).
    "tool_success_rate": 2.5,  # normalized: fraction of tool calls that returned ok
    "outcome_verified": 3.0,  # 0 or 1: did the claimed artifact actually land
    "substack_new_subs_24h": 2.0,  # new subs in 24h post-publish
    "reader_feedback_positive": 3.0,
    "reader_feedback_negative": -4.0,
    "time_cost_penalty": -1.0,  # normalized to [0, 1]: how far past budget
    "crash_penalty": -5.0,  # 0 or 1: worker crashed mid-task
}
