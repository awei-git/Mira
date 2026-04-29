"""Throttled/dedup logging — Phase 0 pillar 4.5.

Baseline showed 43万 WARNING in 7 days dominated by a handful of
repeat messages (same pipeline items re-flagged each 30s tick, same
missing notes dir, same health-ingest deadlock). This module offers
throttled helpers so those callers log once per window instead of
every cycle.

Usage:
    from logging_util import throttled_warning

    throttled_warning(
        log, "PIPELINE STUCK: '%s' for >2h", title,
        key=f"stuck:{slug}", interval_seconds=3600,
    )

Call semantics match logger.warning but suppress duplicate (`key`,
`logger_name`) emissions inside `interval_seconds`. When suppressed,
the helper increments an in-memory count; on the next permitted
emission the message is prefixed with `(×N since last)` so the
suppression is visible.
"""

from .throttle import throttled_warning, throttled_info, throttled_error, suppressed_stats

__all__ = ["throttled_warning", "throttled_info", "throttled_error", "suppressed_stats"]
