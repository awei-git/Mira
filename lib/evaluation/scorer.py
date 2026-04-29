"""Self-evaluation engine for Mira.

Scores 14 dimensions x ~40 sub-dimensions. Each sub-dimension is 0-10.
Scores update via EMA (alpha=0.3) so they reflect trajectory, not snapshots.

Scoring philosophy: automated metrics where possible, LLM self-reflection
where judgment matters. Never mechanical -- the LLM evaluations ask Mira to
*think* about her work, not count keywords.

This module re-exports the full public API from sub-modules for backward
compatibility. All imports like ``from evaluation.scorer import X`` continue
to work unchanged.
"""

# --- Dimensions and constants ---
from .dimensions import (  # noqa: F401
    DIMENSIONS,
    ALL_SUBDIMS,
    EMA_ALPHA,
    HISTORY_KEEP_DAYS,
)

# --- Storage (load / save / record / prune) ---
from .storage import (  # noqa: F401
    SCORES_FILE,
    SELF_ASSESSED_WEIGHT,
    load_scores,
    save_scores,
    record_event,
    update_weakness_score,
    prune_history,
)

# --- Metrics (automated + LLM evaluations, predictions, reliability) ---
from .metrics import (  # noqa: F401
    evaluate_task_outcome,
    evaluate_explore_auto,
    evaluate_reflect_auto,
    evaluate_writing_auto,
    compute_skill_scores,
    record_skill_usage,
    evaluate_journal,
    evaluate_explore,
    evaluate_reflect,
    evaluate_writing,
    evaluate_note,
    evaluate_comment,
    record_prediction,
    resolve_prediction,
    evaluate_reliability,
)

# --- Reporting (aggregates, scorecard, weekly/monthly reports) ---
from .reporting import (  # noqa: F401
    compute_aggregates,
    get_improvement_targets,
    get_strongest,
    format_scorecard,
    format_improvement_context,
    compute_growth_velocity,
    generate_weekly_report,
    should_publish_monthly_report,
    generate_monthly_report_article,
)

# --- Improvement (diagnosis + plans) ---
from .improvement import (  # noqa: F401
    diagnose_scores,
    generate_improvement_plan,
    get_active_improvements,
)
