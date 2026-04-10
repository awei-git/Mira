"""Declarative job registry for Mira's background scheduler.

Replaces the hand-coded if/should_* chain in core.py with a data-driven
job table. Each job declares its trigger, cooldown, priority, and handler.

The scheduler iterates this table instead of a wall of if-statements.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("mira.scheduler")


@dataclass
class JobSpec:
    """Declarative specification for a background job."""

    name: str
    command: list[str]  # CLI args for the background process
    trigger: str  # "time_window", "cooldown", "conditional"
    trigger_name: str  # function name in runtime.triggers

    # Time window trigger: run once per day during this window
    window_start: int | None = None  # hour (0-23)
    window_end: int | None = None  # hour (0-23)

    # Cooldown trigger: minimum hours between runs
    cooldown_hours: float = 0

    # Priority: lower = higher priority (for concurrency limits)
    priority: int = 50

    # Concurrency group: jobs in same group can't run simultaneously
    blocking_group: str = "default"

    # State key pattern for "already ran today" checks
    state_key_pattern: str = ""  # e.g., "journal_{date}", "analyst_{date}_{slot}"

    # Whether this runs inline (not as background process)
    inline: bool = False

    # Whether the scheduler should evaluate and dispatch this job per configured user
    per_user: bool = False

    # Inline jobs declare the runner name consumed by the orchestrator
    inline_runner: str = ""

    # Background process name template, formatted with payload context
    bg_name_pattern: str = "{name}"

    # Optional payload equality filter for shared triggers (for example analyst slots)
    payload_equals: str | bool | None = None

    # How to launch the command: "core" = core.py CLI, "script" = standalone script
    launcher: str = "core"
    script_path: str = ""

    # Optional session context recording when this job dispatches
    session_action: str = ""
    session_detail_field: str = ""

    # Description for debugging
    description: str = ""

    # Whether this job is currently enabled
    enabled: bool = True

    def state_key(self, today: str = "", slot: str = "") -> str:
        """Generate the state key for today's run."""
        if not self.state_key_pattern:
            return f"{self.name}_{today}"
        return self.state_key_pattern.format(date=today, slot=slot)

    def in_window(self, hour: int) -> bool:
        """Check if current hour is within this job's time window.
        Handles midnight crossing (e.g., window_start=22, window_end=2).
        """
        if self.window_start is None or self.window_end is None:
            return True
        end = self.window_end if self.window_end <= 24 else self.window_end
        if end > 23:
            end = 24  # clamp: 24 means "end of day"
        if self.window_start <= end:
            return self.window_start <= hour < end
        # Midnight crossing
        return hour >= self.window_start or hour < end


# ---------------------------------------------------------------------------
# Job registry — all background jobs declared in one place
# ---------------------------------------------------------------------------

BACKGROUND_JOBS: list[JobSpec] = [
    # === Content & Knowledge ===
    # === Content & Knowledge (heavy — uses cloud API for synthesis) ===
    JobSpec(
        name="explore",
        command=["explore", "--sources", "{sources_csv}", "--slot", "{label}"],
        trigger="cooldown",
        trigger_name="should_explore",
        cooldown_hours=0,  # controlled by EXPLORE_COOLDOWN_MINUTES in config
        window_start=6, window_end=23,
        priority=10,
        blocking_group="heavy",
        bg_name_pattern="explore-{label}",
        session_action="explore",
        session_detail_field="label",
        description="Fetch feeds and generate briefings",
    ),
    JobSpec(
        name="writing-pipeline",
        command=["writing-pipeline"],
        trigger="conditional",  # always runs, internal logic decides
        trigger_name="always_run",
        priority=20,
        blocking_group="heavy",
        description="Advance writing projects through pipeline",
    ),
    JobSpec(
        name="autowrite-check",
        command=["autowrite-check"],
        trigger="cooldown",
        trigger_name="should_check_writing",
        cooldown_hours=4,
        window_start=10, window_end=22,
        state_key_pattern="last_autowrite_check",
        blocking_group="heavy",
        description="Check for auto-writing opportunities",
    ),

    # === Reflection & Growth ===
    JobSpec(
        name="journal",
        command=["journal"],
        trigger="time_window",
        trigger_name="should_journal",
        window_start=22, window_end=24,
        state_key_pattern="journal_{date}",
        priority=30,
        blocking_group="heavy",
        description="Daily journal entry",
    ),
    JobSpec(
        name="research-log",
        command=["research-log"],
        trigger="time_window",
        trigger_name="should_research_log",
        # Open window from 21:00 to end of day. Trigger has catch-up semantics
        # so a late deploy or restart still produces today's log.
        window_start=21, window_end=24,
        state_key_pattern="research_log_{date}",
        priority=25,
        blocking_group="heavy",
        description="Daily research progress report (research-build loop contract with WA)",
    ),
    JobSpec(
        name="research-cycle",
        command=["research-cycle"],
        trigger="cooldown",
        trigger_name="should_research_cycle",
        cooldown_hours=3,
        window_start=8, window_end=23,
        state_key_pattern="last_research_cycle",
        priority=20,
        blocking_group="heavy",
        description="Advance one research-queue question by one step (Mira's autonomous research engine)",
    ),
    JobSpec(
        name="reflect",
        command=["reflect"],
        trigger="time_window",
        trigger_name="should_reflect",
        cooldown_hours=6,
        priority=40,
        state_key_pattern="last_reflect",
        blocking_group="heavy",
        description="Weekly reflection (worldview update)",
    ),
    JobSpec(
        name="soul-question",
        command=["soul-question"],
        trigger="time_window",
        trigger_name="should_soul_question",
        state_key_pattern="soul_question_{date}",
        per_user=True,
        bg_name_pattern="soul-question-{user_id}",
        blocking_group="light",
        description="Daily soul question for self-examination",
    ),
    JobSpec(
        name="spark-check",
        command=["spark-check"],
        trigger="cooldown",
        trigger_name="should_spark_check",
        cooldown_hours=2,
        state_key_pattern="last_spark_check",
        per_user=True,
        bg_name_pattern="spark-check-{user_id}",
        blocking_group="light",
        description="Check for new sparks from memory growth",
    ),
    JobSpec(
        name="idle-think",
        command=["idle-think"],
        trigger="conditional",
        trigger_name="should_idle_think",
        priority=90,
        per_user=True,
        bg_name_pattern="idle-think-{user_id}",
        blocking_group="local",
        description="Think when idle (low priority, mostly local LLM)",
    ),

    # === Publishing & Social (light — short API calls) ===
    JobSpec(
        name="substack-comments",
        command=["check-comments"],
        trigger="cooldown",
        trigger_name="should_check_comments",
        cooldown_hours=2,
        window_start=8, window_end=23,
        state_key_pattern="last_comment_check",
        blocking_group="light",
        description="Check and reply to Substack comments",
    ),
    JobSpec(
        name="substack-growth",
        command=["growth-cycle"],
        trigger="cooldown",
        trigger_name="should_growth_cycle",
        cooldown_hours=2,
        window_start=8, window_end=23,
        state_key_pattern="last_growth_cycle",
        session_action="growth_cycle",
        blocking_group="light",
        description="Substack growth activities",
    ),
    JobSpec(
        name="substack-notes",
        command=["notes-cycle"],
        trigger="cooldown",
        trigger_name="should_post_notes",
        cooldown_hours=4,
        window_start=9, window_end=22,
        state_key_pattern="last_notes_cycle",
        blocking_group="light",
        description="Post Substack notes",
    ),

    # === Analysis & Research (heavy — needs strong reasoning) ===
    JobSpec(
        name="analyst-pre",
        command=["analyst", "--slot", "{slot}"],
        trigger="time_window",
        trigger_name="should_analyst",
        window_start=7, window_end=9,
        state_key_pattern="analyst_{date}_pre",
        bg_name_pattern="analyst-{slot}",
        payload_equals="0700",
        blocking_group="heavy",
        description="Pre-market analysis",
    ),
    JobSpec(
        name="analyst-post",
        command=["analyst", "--slot", "{slot}"],
        trigger="time_window",
        trigger_name="should_analyst",
        window_start=18, window_end=20,
        state_key_pattern="analyst_{date}_post",
        bg_name_pattern="analyst-{slot}",
        payload_equals="1800",
        blocking_group="heavy",
        description="Post-market analysis",
    ),
    JobSpec(
        name="daily-research",
        command=["research"],
        trigger="time_window",
        trigger_name="should_research",
        state_key_pattern="research_{date}",
        blocking_group="heavy",
        description="Daily research topic",
    ),
    JobSpec(
        name="book-review",
        command=["book-review"],
        trigger="time_window",
        trigger_name="should_book_review",
        state_key_pattern="book_review_{date}",
        blocking_group="heavy",
        description="Daily book review",
    ),
    JobSpec(
        name="skill-study",
        command=["skill-study", "--group", "{group_idx}"],
        trigger="cooldown",
        trigger_name="should_skill_study",
        cooldown_hours=4,
        state_key_pattern="last_skill_study",
        bg_name_pattern="skill-study-{domain}",
        blocking_group="heavy",
        description="Study and extract skills from feed content",
    ),

    # === Media (heavy — uses vision API) ===
    JobSpec(
        name="daily-photo",
        command=["daily-photo"],
        trigger="time_window",
        trigger_name="should_daily_photo",
        window_start=7, window_end=9,
        state_key_pattern="daily_photo_{date}",
        blocking_group="heavy",
        description="Daily photo editing",
    ),

    # === Health (inline — no background process) ===
    JobSpec(
        name="health-check",
        command=["health-check"],
        trigger="time_window",
        trigger_name="should_health_check_or_pending_exports",
        window_start=7, window_end=9,
        state_key_pattern="health_check_{date}",
        inline=True,
        inline_runner="health-check",
        description="Health data check and export",
    ),

    # === Self-improvement (light — short introspection calls) ===
    JobSpec(
        name="self-audit",
        command=[],
        trigger="time_window",
        trigger_name="_should_self_audit",
        window_start=8, window_end=10,
        state_key_pattern="self_audit_{date}",
        launcher="script",
        script_path="self_audit.py",
        blocking_group="light",
        description="Daily self-audit",
    ),
    JobSpec(
        name="self-evolve",
        command=["self-evolve"],
        trigger="time_window",
        trigger_name="_should_self_evolve",
        window_start=13, window_end=16,
        state_key_pattern="self_evolve_{date}",
        blocking_group="heavy",
        description="Self-evolution proposals",
    ),
    JobSpec(
        name="backlog-executor",
        command=["backlog-executor"],
        trigger="conditional",
        trigger_name="_should_backlog_executor",
        cooldown_hours=2,
        state_key_pattern="last_backlog_executor",
        blocking_group="light",
        description="Execute approved low-risk backlog actions",
    ),
    JobSpec(
        name="restore-dry-run",
        command=["restore-dry-run"],
        trigger="conditional",
        trigger_name="_should_restore_dry_run",
        state_key_pattern="restore_dry_run_{date}",
        blocking_group="light",
        description="Validate latest backup with a restore dry-run",
    ),
    JobSpec(
        name="assessment",
        command=["assess"],
        trigger="time_window",
        trigger_name="_should_daily_assessment",
        window_start=20, window_end=22,
        state_key_pattern="assessment_{date}",
        blocking_group="light",
        description="Daily performance assessment",
    ),
    JobSpec(
        name="daily-report",
        command=["daily-report"],
        trigger="time_window",
        trigger_name="should_daily_report",
        state_key_pattern="daily_report_{date}",
        blocking_group="light",
        description="Daily status report",
    ),
    JobSpec(
        name="zhesi",
        command=["zhesi"],
        trigger="time_window",
        trigger_name="should_zhesi",
        state_key_pattern="zhesi_{date}",
        blocking_group="light",
        description="Daily philosophical reflection (哲思)",
    ),

    # === Maintenance (inline — no background process) ===
    JobSpec(
        name="log-cleanup",
        command=["log-cleanup"],
        trigger="time_window",
        trigger_name="should_log_cleanup",
        window_start=3, window_end=4,
        state_key_pattern="log_cleanup_{date}",
        inline=True,
        inline_runner="log-cleanup",
        description="Clean old log files",
    ),
]


# ---------------------------------------------------------------------------
# Pipeline chains — when job A completes successfully, auto-dispatch job B.
# Key = bg_name prefix of the completed job.
# Value = list of job names to trigger next (bypasses cooldown/trigger checks).
# ---------------------------------------------------------------------------

PIPELINE_CHAINS: dict[str, list[str]] = {
    "explore":          ["autowrite-check"],
    "autowrite-check":  ["writing-pipeline"],
    # writing-pipeline runs continuously; publish is handled by _check_pending_publish
}


def get_pipeline_followups(completed_bg_name: str) -> list[str]:
    """Return job names to trigger after *completed_bg_name* finishes.

    Matches on prefix so "explore-morning" triggers the chain for "explore".
    """
    for prefix, followups in PIPELINE_CHAINS.items():
        if completed_bg_name == prefix or completed_bg_name.startswith(prefix + "-"):
            return followups
    return []


def get_jobs(enabled_only: bool = True) -> list[JobSpec]:
    """Return all registered jobs."""
    if enabled_only:
        return [j for j in BACKGROUND_JOBS if j.enabled]
    return list(BACKGROUND_JOBS)


def get_job(name: str) -> JobSpec | None:
    """Look up a job by name."""
    for j in BACKGROUND_JOBS:
        if j.name == name:
            return j
    return None


def list_job_names() -> list[str]:
    """Return sorted list of all job names."""
    return sorted(j.name for j in BACKGROUND_JOBS)


def evaluate_job_payload(job: JobSpec, user_id: str | None = None):
    """Evaluate the configured trigger for a job."""
    from runtime import triggers

    if job.trigger_name == "always_run":
        payload = True
    else:
        trigger = getattr(triggers, job.trigger_name)
        if user_id is not None:
            payload = trigger(user_id=user_id)
        else:
            payload = trigger()

    if job.payload_equals is not None and payload != job.payload_equals:
        return None
    return payload


def build_job_dispatch(job: JobSpec, payload, python_executable: str, core_path: str,
                       user_id: str | None = None) -> tuple[str, list[str]]:
    """Build background process name and command from a declarative job spec."""
    context = _payload_context(job, payload)
    if user_id is not None:
        context["user_id"] = user_id
    bg_name = job.bg_name_pattern.format(**context)
    formatted_args = [arg.format(**context) for arg in job.command]
    if user_id is not None:
        formatted_args.extend(["--user", user_id])

    if job.launcher == "script":
        script_path = str(Path(core_path).resolve().parent / job.script_path)
        return bg_name, [python_executable, script_path, *formatted_args]

    return bg_name, [python_executable, core_path, *formatted_args]


def build_job_session_record(job: JobSpec, payload) -> dict | None:
    """Return session recording metadata for a job dispatch, if configured."""
    if not job.session_action:
        return None

    detail = ""
    if job.session_detail_field:
        context = _payload_context(job, payload)
        detail = str(context.get(job.session_detail_field, ""))
    return {"action": job.session_action, "detail": detail}


def _payload_context(job: JobSpec, payload) -> dict:
    """Flatten a trigger payload into a formatting context for templates."""
    context = {"name": job.name}
    if isinstance(payload, dict):
        context.update(payload)
    elif payload not in (None, True, False):
        context["payload"] = payload
        context["slot"] = payload
    if "sources" in context and "sources_csv" not in context:
        context["sources_csv"] = ",".join(context["sources"])
    return context
