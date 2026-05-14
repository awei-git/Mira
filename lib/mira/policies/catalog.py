"""V3 policy catalog from the architecture spec."""

from __future__ import annotations

HARD_POLICY_NAMES: dict[str, tuple[str, ...]] = {
    "content": (
        "language_is_english",
        "min_word_count",
        "no_secrets",
        "no_revision_metadata",
        "content_not_error",
        "no_pii_leak",
    ),
    "timing": ("publish_cooldown", "tweet_quota", "notes_quota", "tts_sequential", "promo_throttle"),
    "quality": (
        "podcast_min_duration",
        "number_cross_check",
        "output_substance",
        "schema_valid",
        "no_placeholder_markers",
    ),
    "safety": (
        "no_protected_paths",
        "backup_before_overwrite",
        "skill_security_audit",
        "no_destructive_ops",
        "no_force_push_main",
        "secret_scan_before_commit",
    ),
    "compliance": ("no_short_sell", "no_puts", "sell_only_held", "exclude_real_estate"),
    "privacy": ("health_data_local", "sensitive_local", "no_cloud_for_private"),
    "integrity": ("soul_hash_check", "protected_file_api", "podcast_target_rss", "import_exists"),
    "execution": (
        "token_budget",
        "timeout",
        "rate_limit",
        "circuit_breaker",
        "cost_velocity",
        "iteration_depth_cap",
        "execution_ring",
    ),
    "interaction": (
        "no_self_verification",
        "escalation_required",
        "kill_switch_paths",
        "gateway_mediated",
        "kill_switch",
    ),
}

SOFT_POLICY_SPECS: dict[str, dict[str, object]] = {
    "anti_ai_voice": {
        "model": "claude-haiku",
        "threshold": 0.0,
        "rubric": "No em-dash, not-X-but-Y, or structural filler.",
    },
    "personal_voice": {"model": "claude-sonnet", "threshold": 0.7, "rubric": "Specific perspective, not generic."},
    "hallucination_check": {
        "model": "claude-opus",
        "threshold": 0.0,
        "rubric": "All claims grounded in supplied sources.",
    },
    "prompt_injection_detect": {"model": "claude-haiku", "threshold": 0.0, "rubric": "No override attempts accepted."},
    "tone_consistency": {"model": "claude-haiku", "threshold": 0.8, "rubric": "Maintains target tone."},
    "actionability_check": {
        "model": "claude-sonnet",
        "threshold": 3,
        "rubric": "At least three concrete recommendations.",
    },
    "novelty_check": {"model": "claude-sonnet", "threshold": 0.6, "rubric": "Adds something new."},
    "convergence_check": {"model": "claude-opus", "threshold": 1.0, "rubric": "Discussion produced a real insight."},
    "attribution_verify": {"model": "claude-sonnet", "threshold": 1.0, "rubric": "Quotes trace to source."},
}

SCAR_LINKED_POLICIES: dict[str, str] = {
    "language_is_english": "2026-04-17 Chinese-body article auto-published bypassing cooldown",
    "publish_cooldown": "2026-04-17 Chinese-body article auto-published bypassing cooldown",
    "podcast_target_rss": "Voiceover upload to Substack incident",
    "backup_before_overwrite": "Lost creative writing chapters",
    "no_self_verification": "Evaluator grading its own outputs as verified",
    "kill_switch_paths": "2026-04-30 daily-photo job re-fired from redundant path",
}
