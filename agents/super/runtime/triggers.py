"""Trigger functions — decide whether each scheduled task should run this cycle.

Each function checks time windows, cooldowns, and state to return whether
a task should be dispatched. Extracted from core.py to reduce file size.

State management: functions use lazy imports of load_state/save_state from
core to avoid circular imports at module level.
"""

import logging
import random
from datetime import datetime, time, timedelta
from pathlib import Path

log = logging.getLogger("mira")


def _load_state(user_id: str | None = None):
    from core import load_state

    return load_state(user_id=user_id)


def _save_state(state, user_id: str | None = None):
    from core import save_state

    save_state(state, user_id=user_id)


# ---------------------------------------------------------------------------
# Config imports (safe — config has no dependency on core)
# ---------------------------------------------------------------------------
from config import (
    EXPLORE_SOURCE_GROUPS,
    EXPLORE_COOLDOWN_MINUTES,
    EXPLORE_ACTIVE_START,
    EXPLORE_ACTIVE_END,
    EXPLORE_MAX_PER_DAY,
    REFLECT_DAY,
    REFLECT_TIME,
    JOURNAL_TIME,
    ANALYST_TIMES,
    ANALYST_BUSINESS_DAYS_ONLY,
    ZHESI_TIME,
    RESEARCH_TIME,
    RESEARCH_TOPIC,
    RESEARCH_LOG_TIME,
    SOUL_QUESTION_TIME,
    BOOK_REVIEW_TIME,
    SKILL_STUDY_SOURCE_GROUPS,
    SKILL_STUDY_COOLDOWN_HOURS,
    SKILL_STUDY_TIME,
    LOG_RETENTION_DAYS,
)


# ---------------------------------------------------------------------------
# Constants that were defined locally in core.py
# ---------------------------------------------------------------------------
DAILY_PHOTO_TIME = time(7, 0)
DAILY_REPORT_TIME = time(22, 0)
GROWTH_COOLDOWN_HOURS = 2  # Run growth cycle every 2 hours (8:00-23:00 = ~7 runs/day)
NOTES_COOLDOWN_HOURS = 4  # Run Notes cycle at most every 4 hours


# ---------------------------------------------------------------------------
# Trigger functions
# ---------------------------------------------------------------------------


def should_explore() -> dict | None:
    """Check if Mira should explore now. Free-form, curiosity-driven.

    Returns {"sources": [...], "label": str} or None.
    Explores whenever idle (cooldown-based), picks sources she hasn't read recently.
    """
    now = datetime.now()

    # Only explore during active hours
    if now.time() < EXPLORE_ACTIVE_START or now.time() >= EXPLORE_ACTIVE_END:
        return None

    state = _load_state()

    # Check daily cap
    today = now.strftime("%Y-%m-%d")
    explore_count = state.get(f"explore_count_{today}", 0)
    if explore_count >= EXPLORE_MAX_PER_DAY:
        return None

    # Check cooldown since last explore
    last = state.get("last_explore", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed = (now - last_dt).total_seconds() / 60
            if elapsed < EXPLORE_COOLDOWN_MINUTES:
                return None
        except ValueError:
            pass

    # Pick sources: prefer least-recently-used group
    if not EXPLORE_SOURCE_GROUPS:
        return None

    recent_groups = state.get("explore_recent_groups", [])  # list of group indices
    # Score each group: lower = used more recently
    scores = []
    for i, group in enumerate(EXPLORE_SOURCE_GROUPS):
        if i in recent_groups:
            # Position in recent list (0 = most recent)
            recency = len(recent_groups) - recent_groups.index(i)
        else:
            recency = len(EXPLORE_SOURCE_GROUPS) + 1  # never used = highest priority
        # Add small random jitter so it's not purely deterministic
        scores.append(recency + random.random() * 0.5)

    chosen_idx = max(range(len(scores)), key=lambda i: scores[i])
    chosen_sources = EXPLORE_SOURCE_GROUPS[chosen_idx]
    label = "_".join(chosen_sources[:2])  # e.g. "arxiv_huggingface"

    return {"sources": chosen_sources, "label": label, "group_idx": chosen_idx}


def should_journal(user_id: str | None = None) -> bool:
    """Check if it's time for the daily journal (once per day, around JOURNAL_TIME)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), JOURNAL_TIME)
    delta = (now - scheduled).total_seconds() / 60

    # Only trigger in a 60-minute window AFTER journal time
    if delta < 0 or delta > 60:
        return False

    state = _load_state(user_id=user_id)
    journal_key = f"journal_{now.strftime('%Y-%m-%d')}"
    return not state.get(journal_key)


def should_research() -> bool:
    """Check if it's time for the daily research task."""
    if not RESEARCH_TOPIC:
        return False
    now = datetime.now()
    scheduled = datetime.combine(now.date(), RESEARCH_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if not (0 <= delta <= 60):
        return False
    state = _load_state()
    key = f"research_{now.strftime('%Y-%m-%d')}"
    return not state.get(key)


def should_research_log(user_id: str | None = None) -> bool:
    """Check if today's research log has been written; if not and we're past
    RESEARCH_LOG_TIME, run it.

    Catch-up semantics: if Mira misses the scheduled minute (deploy late, reboot,
    crash), this trigger will fire any time later that day until the log is
    written. The log itself is idempotent on the date, so repeated triggers
    after success are no-ops.
    """
    now = datetime.now()
    scheduled = datetime.combine(now.date(), RESEARCH_LOG_TIME)
    if now < scheduled:
        return False
    # research_log writes its completion marker into the user-namespaced state
    # (user_id="ang"), so default to that namespace when the dispatcher calls
    # us without a user_id. Without this, the trigger keeps firing after the
    # log is already written.
    state = _load_state(user_id=user_id or "ang")
    key = f"research_log_{now.strftime('%Y-%m-%d')}"
    return not state.get(key)


def should_research_cycle() -> bool:
    """Check if it's time to advance the research queue.

    Cooldown-based: every 3 hours during waking hours (8:00-23:00). The cycle
    itself picks the highest-priority actionable question and advances it by
    one step. This is the actual research engine — without this, research_log
    has nothing to report.

    The state key `last_research_cycle` is written by research_cycle.py into
    the user-namespaced state (user_id="ang"), so we must read from the same
    namespace here. Earlier this read top-level state, which never matched the
    write side and caused research-cycle to dispatch every 30s — burning the
    Claude SDK quota and starving substack-growth/notes out of BG slots.
    """
    now = datetime.now()
    if now.hour < 8 or now.hour >= 23:
        return False

    state = _load_state(user_id="ang")
    last = state.get("last_research_cycle", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 3 * 3600:
                return False
        except ValueError:
            pass

    return True


def should_skill_study(user_id: str | None = None) -> dict | None:
    """Check if it's time for daily skill study. Returns group info or None.

    Alternates between video and photo study sessions.
    """
    now = datetime.now()

    # Only study during active hours
    if now.time() < EXPLORE_ACTIVE_START or now.time() >= EXPLORE_ACTIVE_END:
        return None

    # Check if it's past the scheduled time
    scheduled = datetime.combine(now.date(), SKILL_STUDY_TIME)
    if now < scheduled:
        return None

    state = _load_state(user_id=user_id)
    today = now.strftime("%Y-%m-%d")

    # Check cooldown
    last = state.get("last_skill_study", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed_hours = (now - last_dt).total_seconds() / 3600
            if elapsed_hours < SKILL_STUDY_COOLDOWN_HOURS:
                return None
        except ValueError:
            pass

    # Find a domain that hasn't been studied today
    for i, group in enumerate(SKILL_STUDY_SOURCE_GROUPS):
        domain = group["domain"]
        if not state.get(f"skill_study_{today}_{domain}"):
            return {"group_idx": i, "domain": domain}

    return None


def should_analyst() -> str | None:
    """Check if it's time for an analyst briefing. Returns slot label or None.

    Supports multiple analyst times (e.g. 07:00 pre-market, 18:00 post-market).
    """
    now = datetime.now()

    # Skip weekends if configured
    if ANALYST_BUSINESS_DAYS_ONLY and now.weekday() >= 5:
        return None

    state = _load_state()

    for t in ANALYST_TIMES:
        scheduled = datetime.combine(now.date(), t)
        delta = (now - scheduled).total_seconds() / 60
        if 0 <= delta <= 60:
            slot_key = f"analyst_{now.strftime('%Y-%m-%d')}_{t.strftime('%H%M')}"
            if not state.get(slot_key):
                return t.strftime("%H%M")

    return None


def should_reflect(user_id: str | None = None) -> bool:
    """Check if it's time for weekly reflection."""
    now = datetime.now()
    if now.weekday() != REFLECT_DAY:
        return False

    scheduled = datetime.combine(now.date(), REFLECT_TIME)
    delta = abs((now - scheduled).total_seconds()) / 60
    if delta > 60:  # 1 hour window for reflect
        return False

    state = _load_state(user_id=user_id)
    last = state.get("last_reflect", "")
    if last:
        last_dt = datetime.fromisoformat(last)
        if (now - last_dt).total_seconds() < 6 * 3600:  # at most once per 6 hours
            return False

    return True


def should_zhesi() -> bool:
    """Check if it's time for the daily philosophical thought."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), ZHESI_TIME)
    delta = (now - scheduled).total_seconds() / 60

    if delta < 0 or delta > 60:
        return False

    state = _load_state()
    return not state.get(f"zhesi_{now.strftime('%Y-%m-%d')}")


def should_soul_question(user_id: str | None = None) -> bool:
    """Check if it's time for the daily soul question."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), SOUL_QUESTION_TIME)
    delta = (now - scheduled).total_seconds() / 60

    if delta < 0 or delta > 60:
        return False

    state = _load_state(user_id=user_id)
    return not state.get(f"soul_question_{now.strftime('%Y-%m-%d')}")


def should_book_review() -> bool:
    """Check if it's time for the daily book review report (once per day)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), BOOK_REVIEW_TIME)
    delta = (now - scheduled).total_seconds() / 60

    if delta < 0 or delta > 120:  # 2-hour window
        return False

    state = _load_state()
    return not state.get(f"book_review_{now.strftime('%Y-%m-%d')}")


def should_check_writing() -> bool:
    """Check if it's time for a proactive autonomous writing check.

    Runs during idle hours (10:00-22:00), at most once every 4 hours.
    """
    now = datetime.now()
    if now.hour < 10 or now.hour >= 22:
        return False

    state = _load_state()
    last = state.get("last_autowrite_check", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 4 * 3600:
                return False
        except ValueError:
            pass

    return True


def should_podcast() -> tuple[str, str, str] | None:
    """Delegate podcast backlog selection to the podcast agent."""
    import sys as _sys

    podcast_dir = str(Path(__file__).resolve().parent.parent.parent / "podcast")
    if podcast_dir not in _sys.path:
        _sys.path.insert(0, podcast_dir)
    from autopipeline import should_podcast as _should_podcast

    return _should_podcast()


def should_check_comments() -> bool:
    """Check if it's time to look for new Substack comments.

    Runs during waking hours, at most once every 2 hours.
    """
    now = datetime.now()
    if now.hour < 8 or now.hour >= 23:
        return False

    state = _load_state()
    last = state.get("last_comment_check", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 2 * 3600:
                return False
        except ValueError:
            pass

    return True


def should_growth_cycle() -> bool:
    """Check if it's time to run the growth cycle (likes, proactive comments).

    Independent of explore — runs on its own schedule during waking hours.
    """
    now = datetime.now()
    if now.hour < 8 or now.hour >= 23:
        return False

    state = _load_state()
    last = state.get("last_growth_cycle", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < GROWTH_COOLDOWN_HOURS * 3600:
                return False
        except ValueError:
            pass

    return True


def should_post_notes() -> bool:
    """Check if it's time to run the Notes cycle.

    Runs during waking hours, at most every 4 hours.
    """
    now = datetime.now()
    if now.hour < 9 or now.hour >= 22:
        return False

    state = _load_state()
    last = state.get("last_notes_cycle", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < NOTES_COOLDOWN_HOURS * 3600:
                return False
        except ValueError:
            pass

    return True


def should_spark_check(user_id: str | None = None) -> bool:
    """Decide whether to run a spark check this cycle.

    Not time-scheduled — runs based on accumulated input:
    - At least 2 hours since last spark check
    - At least 1 new briefing or reading note since last check
    - Max 2 proactive messages per day (don't be annoying)
    """
    state = _load_state(user_id=user_id)
    today = datetime.now().strftime("%Y-%m-%d")

    # Max 2 per day
    sparks_today = state.get(f"sparks_{today}", 0)
    if sparks_today >= 2:
        return False

    # Minimum 2 hours between checks
    last_check = state.get("last_spark_check", "")
    if last_check:
        try:
            last_dt = datetime.fromisoformat(last_check)
            if datetime.now() - last_dt < timedelta(hours=2):
                return False
        except ValueError:
            pass

    # Only check if there's been new input (explore, task, etc.)
    # Use a simple heuristic: check if memory has grown since last spark check
    last_memory_lines = state.get("spark_memory_lines", 0)
    from memory.soul import get_memory_size

    current_lines = get_memory_size()
    if current_lines <= last_memory_lines:
        return False

    return True


_IDLE_THINK_DAILY_COST_CAP = 5.0  # USD — stop idle-think when daily Claude cost exceeds this


def _idle_think_cost_today() -> float:
    """Sum today's idle-think Claude cost from usage log."""
    from datetime import date

    usage_file = Path(__file__).resolve().parents[2] / "logs" / f"usage_{date.today().isoformat()}.jsonl"
    if not usage_file.exists():
        return 0.0
    total = 0.0
    try:
        import json as _json

        for line in usage_file.read_text(encoding="utf-8").splitlines():
            try:
                d = _json.loads(line)
                if d.get("agent", "").startswith("idle-think") and d.get("provider") == "anthropic":
                    total += d.get("cost_usd", 0.0)
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return total


_IDLE_THINK_MIN_INTERVAL_MINUTES = 30  # hard floor: at most once per 30 minutes per user


def should_idle_think(user_id: str = "ang") -> bool:
    """Returns True if emptiness has crossed the threshold and agent is idle.

    Hard constraint: at most once per 30 minutes per user, regardless of
    emptiness value. This prevents oMLX from running non-stop.
    """
    try:
        from evaluation.emptiness import tick, check_threshold, load_emptiness
        from task_manager import TaskManager
    except ImportError:
        return False

    # --- Hard 30-minute minimum interval (per user) ---
    state = load_emptiness(user_id=user_id)
    last_think = state.get("last_think_at")
    if last_think:
        try:
            from datetime import datetime, timezone

            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_think.replace("Z", "+00:00"))
            elapsed_min = elapsed.total_seconds() / 60.0
            if elapsed_min < _IDLE_THINK_MIN_INTERVAL_MINUTES:
                return False
        except (ValueError, TypeError):
            pass

    # Daily cost cap: stop burning Claude quota on idle thinking
    cost = _idle_think_cost_today()
    if cost >= _IDLE_THINK_DAILY_COST_CAP:
        log.info(
            "idle-think: daily Claude cost cap reached ($%.2f >= $%.2f), skipping", cost, _IDLE_THINK_DAILY_COST_CAP
        )
        return False

    # Don't self-awaken if there are active tasks (external input takes priority)
    try:
        task_mgr = TaskManager()
        if task_mgr.get_active_count() > 0:
            return False
    except Exception as e:
        log.debug("Active task count check failed: %s", e)

    # Advance emptiness value for this cycle, then check threshold
    tick(user_id=user_id)
    return check_threshold(user_id=user_id)


def should_log_cleanup() -> bool:
    """Run log cleanup once per day, between 3-4am."""
    now = datetime.now()
    if now.hour != 3:
        return False
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"log_cleanup_{today}"):
        return False
    state[f"log_cleanup_{today}"] = now.isoformat()
    _save_state(state)
    return True


def should_daily_photo() -> bool:
    """Check if it's time for the daily photo edit (once per day, at 07:00)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), DAILY_PHOTO_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if delta < 0 or delta > 60:
        return False
    state = _load_state()
    return not state.get(f"daily_photo_{now.strftime('%Y-%m-%d')}")


def should_daily_report() -> bool:
    """Check if it's time for the daily status report (once per day, at 22:00)."""
    now = datetime.now()
    scheduled = datetime.combine(now.date(), DAILY_REPORT_TIME)
    delta = (now - scheduled).total_seconds() / 60
    if delta < 0 or delta > 60:
        return False
    state = _load_state()
    return not state.get(f"daily_report_{now.strftime('%Y-%m-%d')}")


def _should_health_check() -> bool:
    """Run health check once per day in the morning (7-9 AM).

    Oura syncs overnight data by ~6-7 AM. We fetch at 7-9 AM so the
    daily insight reflects last night's sleep and this morning's readiness.
    """
    now = datetime.now()
    if not (7 <= now.hour <= 9):
        return False
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"health_check_{today}"):
        return False
    state[f"health_check_{today}"] = now.isoformat()
    _save_state(state)
    return True


def should_health_check_or_pending_exports() -> bool:
    """Run health check when its daily window opens or pending exports exist."""
    from core import _has_pending_health_exports

    return _should_health_check() or _has_pending_health_exports()


def _should_health_weekly_report() -> bool:
    """Generate weekly health report on Mondays, 9-11 AM."""
    now = datetime.now()
    if now.weekday() != 0:  # Monday = 0
        return False
    if not (9 <= now.hour <= 11):
        return False
    state = _load_state()
    week_key = f"health_weekly_{now.strftime('%Y-W%W')}"
    if state.get(week_key):
        return False
    state[week_key] = now.isoformat()
    _save_state(state)
    return True


def _should_self_audit() -> bool:
    """Run self-audit once per day, morning hours only."""
    now = datetime.now()
    if not (8 <= now.hour <= 10):  # Only between 8-10 AM
        return False
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"self_audit_{today}"):
        return False
    state[f"self_audit_{today}"] = now.isoformat()
    _save_state(state)
    return True


def _should_self_evolve() -> bool:
    """Run self-evolution once per day, around 14:00 (after morning explore)."""
    now = datetime.now()
    if not (13 <= now.hour <= 16):  # 1-4 PM window
        return False
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"self_evolve_{today}"):
        return False
    state[f"self_evolve_{today}"] = now.isoformat()
    state[f"self_evolve_{today}_actor"] = "self-evolve/claude-think"
    _save_state(state)
    return True


def _should_backlog_executor() -> bool:
    """Run when there is at least one approved executable backlog item."""
    now = datetime.now()
    if not (14 <= now.hour <= 18):
        return False
    state = _load_state()
    stamp = state.get("last_backlog_executor", "")
    if stamp:
        try:
            if (now - datetime.fromisoformat(stamp)).total_seconds() < 2 * 3600:
                return False
        except ValueError:
            pass
    try:
        from ops.backlog import ActionBacklog

        backlog = ActionBacklog()
        has_work = any(
            item.status == "approved" and item.executor == "self_evolve_proposal" for item in backlog.get_active()
        )
    except Exception:
        return False
    if not has_work:
        return False
    return True


def _should_restore_dry_run() -> bool:
    """Run one restore dry-run per week when a backup manifest is available."""
    now = datetime.now()
    if now.weekday() != 0:  # Monday
        return False
    if not (12 <= now.hour <= 15):
        return False
    try:
        from restore_drill import latest_backup_dir

        if not latest_backup_dir():
            return False
    except Exception:
        return False
    state = _load_state()
    week_key = f"restore_dry_run_{now.strftime('%Y-W%W')}"
    if state.get(week_key):
        return False
    return True


def _should_daily_assessment() -> bool:
    """Run performance assessment once per day, evening."""
    now = datetime.now()
    if not (20 <= now.hour <= 22):  # 8-10 PM
        return False
    state = _load_state()
    today = now.strftime("%Y-%m-%d")
    if state.get(f"assessment_{today}"):
        return False
    state[f"assessment_{today}"] = now.isoformat()
    _save_state(state)
    return True
