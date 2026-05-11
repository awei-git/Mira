#!/usr/bin/env python3
"""Mira Super Agent — orchestrator with soul, memory, and curiosity.

Modes:
    run     — full cycle: check inbox, maybe explore/reflect
    respond — process inbox requests only
    explore — fetch sources and write briefing
    reflect — weekly reflection and memory consolidation
"""
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Unified sys.path setup — see lib/pathsetup.py for the full list of package dirs
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
import pathsetup  # noqa: F401  (side-effect: registers all Mira package dirs)

import config as mira_config
import health_monitor
from logging_util import throttled_warning  # noqa: E402  — used inside _check_invisible_deps
from notes_bridge import detect_vulnerability_disclosure

from config import (
    MIRA_ROOT,
    WORKSPACE_DIR,
    BRIEFINGS_DIR,
    LOGS_DIR,
    STATE_FILE,
    MIRA_DIR,
    ARTIFACTS_DIR,
    CLEANUP_DAYS,
    LOG_RETENTION_DAYS,
    JOURNAL_DIR,
    WRITINGS_OUTPUT_DIR,
    WRITINGS_DIR,
    PERF_STATS_FILE,
    PERF_WARN_THRESHOLD,
    LAST_OUTPUT_FILE,
    FEEDS_DIR,
    STALE_THRESHOLDS,
    IPHONE_BRIDGE_WARN_LATENCY_MS,
    BRIDGE_STALE_THRESHOLD,
    CALIBRATION_INTERVAL_DAYS,
    CALIBRATION_SAMPLE_SIZE,
    BLIND_SPOT_LOOKBACK_DAYS,
    BLIND_SPOT_SILENCE_THRESHOLD_DAYS,
    MAX_TASKS_PER_CYCLE,
    SURVIVAL_CRITICAL_COMPONENTS,
    SENSITIVE_SURVIVAL_TERMS,
    SENSITIVE_FORCE_LOCAL,
    SENSITIVITY_HOURS_START,
    SENSITIVITY_HOURS_END,
    SENSITIVITY_ROUTE_TO_LOCAL,
    validate_config,
    get_known_user_ids,
    get_user_config,
    is_agent_allowed,
    get_model_restriction,
    should_filter_content,
)

try:
    from bridge import Mira, Message
except (ImportError, ModuleNotFoundError):
    Mira = None
    Message = None
from task_manager import TaskManager, TASKS_DIR, classify_task, get_stuck_tasks
from memory.soul import load_soul, format_soul, append_memory, check_prompt_injection
from llm import claude_think
from sub_agent import append_pipeline_context_to_system_prompt
from writing_workflow import (
    check_writing_responses,
    advance_project,
    start_from_plan,
)
from prompts import respond_prompt

# Extracted workflow modules — business logic for each domain
from workflows.helpers import (
    _append_to_daily_feed,
    _copy_to_briefings,
    _sync_journals_to_briefings,
    _slugify,
    _format_feed_items,
    _extract_deep_dive,
    _extract_comment_suggestions,
    _extract_section,
    _extract_recent_briefing_topics,
    _is_duplicate_topic,
    _extract_recent_published_titles,
    _gather_recent_briefings,
    _gather_recent_episodes,
    _prune_episodes_from_reflect,
    _gather_today_tasks,
    _gather_today_skills,
    _gather_usage_summary,
    _gather_today_comments,
    _mine_za_ideas,
    _mine_za_one,
    _days_since_last_publish,
    PUBLISH_COOLDOWN_DAYS,
    harvest_observations,
    _maybe_create_spontaneous_idea,
)
from workflows.explore import do_explore
from workflows.reflect import do_reflect
from workflows.journal import do_journal
from workflows.research_log import do_research_log
from workflows.research_cycle import do_research_cycle
from workflows.daily import (
    do_daily_report,
    do_daily_photo,
    handle_photo_feedback,
    do_zhesi,
    do_soul_question,
    do_research,
    do_book_review,
    do_analyst,
    do_skill_study,
    run_podcast_episode,
    do_assess,
    _run_self_improve,
    do_idle_think,
    log_cleanup,
)
from workflows.social import (
    do_check_comments,
    do_growth_cycle,
    do_notes_cycle,
    do_spark_check,
)
from workflows.writing import do_autowrite_check, run_autowrite_pipeline
from soul.joint_focus import generate_joint_observation

# Extracted modules — triggers decide "should we run X?", dispatcher spawns bg tasks
from runtime.triggers import (
    _should_health_weekly_report,
)
from runtime.dispatcher import (
    _dispatch_background,
    _is_bg_running,
    _reap_stale_pids,
    _count_bg_running,
    MAX_CONCURRENT_BG,
)
from runtime.jobs import (
    build_job_dispatch,
    build_job_session_record,
    evaluate_job_payload,
    get_jobs,
)
from execution.runtime_contract import normalize_task_status
from soul_manager import (
    log_authorization_event,
    check_audit_coverage,
    check_rules_integrity,
    get_skill_provenance,
    validate_soul_files,
)

# ---------------------------------------------------------------------------
# Extracted sub-modules (pure structural refactor)
# ---------------------------------------------------------------------------
from state import (
    should_shutdown,
    load_state,
    save_state,
    load_session_context,
    save_session_context,
    session_record,
    session_has_recent,
)
import talk as talk_module
from talk import (
    do_talk as _do_talk,
    _format_elapsed,
    _format_status,
    _status_footer,
    _talk_slug,
    _dispatch_or_requeue,
    _quarantine_inbound_command,
    _check_inbound_command_safety,
    _is_meta_command,
    _handle_meta_command,
    _is_writing_request,
    _find_outline,
    _sweep_stuck_items,
)
from publishing import (
    _check_pending_publish,
    _check_pending_podcast,
    _sweep_publish_pipeline,
)
from writing import (
    _log_writer_selection,
    _run_canonical_writing_pipeline,
)
from health import (
    _has_pending_health_exports,
    _run_health_check,
    _write_health_feed,
    _run_health_weekly_report,
)
from jobs import (
    _run_inline_scheduled_job,
    _dispatch_pipeline_followups,
    _dispatch_scheduled_jobs,
    _record_scheduled_job_dispatch,
)
from daily_tasks import (
    _verify_state_key,
    _verify_analyst,
    _verify_journal,
    _verify_reflect,
    _verify_self_evolve,
    _DAILY_TASK_CONTRACTS,
    _self_repair_daily_tasks,
    _daily_task_status_report,
)

log = logging.getLogger("mira")
BRIDGE_STALENESS_THRESHOLD_MINUTES = BRIDGE_STALE_THRESHOLD / 60
BACKGROUND_HEALTH_LOG = LOGS_DIR / "background_health.jsonl"
TASK_DISTRIBUTION_FILE = LOGS_DIR / "task_distribution.json"
THIRD_THING_FILE = Path(__file__).resolve().parent / "notes_outbox" / "third_thing.md"
BRIDGE_THIRD_THING_FILE = MIRA_DIR / "outbox" / "third_thing.md"
JOINT_GARDEN_FILE = _AGENTS_DIR / "shared" / "soul" / "joint_garden.md"
JOINT_GARDEN_STALE_DAYS = 21

_INTENT_CLARIFICATION_REPLY = "What do you want to achieve with this?"
_SENSITIVE_REDACTED_CONTENT = "[sensitive survival exposure routed local]"
_TIME_SENSITIVE_REDACTED_CONTENT = "[time-sensitive message routed local]"
_TIME_SENSITIVE_MIN_MESSAGE_CHARS = 20
VERIFICATION_INDEPENDENCE = True
MIN_COMPLETED_TASK_OUTPUT_BYTES = 50
STUCK_TASK_THRESHOLD_MINUTES = int(getattr(mira_config, "STUCK_TASK_THRESHOLD_MINUTES", 60))
MAX_STUCK_TASKS_BEFORE_ALERT = int(getattr(mira_config, "MAX_STUCK_TASKS_BEFORE_ALERT", 3))
_ORIGINAL_TASK_MANAGER_DISPATCH = TaskManager.dispatch
_ORIGINAL_TASK_MANAGER_COLLECT_RESULT = TaskManager._collect_result
_ORIGINAL_DISPATCH_OR_REQUEUE = _dispatch_or_requeue
_ORIGINAL_PROJECT_RECORD_TO_BRIDGE = getattr(talk_module, "_project_record_to_bridge", None)
_EVALUATION_ACTION_RE = re.compile(
    r"\b(evaluate|evaluation|assess|assessment|score|scoring|review|audit|verify|confirm)\b", re.IGNORECASE
)
_EVALUATOR_TARGET_RE = re.compile(
    r"\b(?:evaluate|assess|score|review|audit|verify|confirm)\s+(?:the\s+)?(?:evaluator|evaluation agent|evaluator agent)\b"
    r"|\b(?:evaluator|evaluation agent|evaluator agent)(?:'s)?\s+(?:output|outputs|performance|score|scores|assessment|evaluation)\b"
    r"|\bscore\s+evaluator\.",
    re.IGNORECASE,
)
_AMBIGUOUS_INTENT_PATTERNS = (
    re.compile(r"\bdo something (?:about|with|for)\b", re.IGNORECASE),
    re.compile(r"\bsomething (?:about|with|for)\b", re.IGNORECASE),
    re.compile(r"\bdeal with (?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\bhandle (?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\btake care of (?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\bmake (?:this|it|that) better\b", re.IGNORECASE),
    re.compile(r"\bfix (?:this|it|that)\b", re.IGNORECASE),
    re.compile(r"\bwhatever\b", re.IGNORECASE),
    re.compile(r"\banything\b", re.IGNORECASE),
)
_ACTION_VERBS = frozenset(
    {
        "analyze",
        "add",
        "assess",
        "build",
        "calculate",
        "check",
        "classify",
        "compare",
        "convert",
        "create",
        "debug",
        "design",
        "draft",
        "edit",
        "evaluate",
        "explain",
        "extract",
        "find",
        "fix",
        "generate",
        "help",
        "implement",
        "make",
        "open",
        "organize",
        "plan",
        "prepare",
        "publish",
        "read",
        "recommend",
        "refactor",
        "remove",
        "research",
        "review",
        "revise",
        "run",
        "schedule",
        "search",
        "send",
        "summarize",
        "test",
        "translate",
        "update",
        "write",
    }
)
_CJK_ACTION_VERBS = (
    "写",
    "做",
    "查",
    "找",
    "改",
    "修",
    "总结",
    "分析",
    "研究",
    "对比",
    "翻译",
    "发布",
    "运行",
    "测试",
    "解释",
    "帮",
    "生成",
    "创建",
    "添加",
    "删除",
    "更新",
    "读",
    "看",
    "搜索",
    "整理",
    "计划",
    "比较",
    "评估",
)
_ORIGINAL_TALK_INTENT_CHECK = getattr(talk_module, "check_intent_clarity", None)

_DOMAIN_TASK_COMMANDS = {
    "run",
    "talk",
    "explore",
    "reflect",
    "journal",
    "research-log",
    "research-cycle",
    "analyst",
    "research",
    "zhesi",
    "soul-question",
    "autowrite-run",
    "writing-pipeline",
    "check-comments",
    "growth-cycle",
    "notes-cycle",
    "spark-check",
    "idle-think",
    "daily-report",
    "assess",
    "podcast",
    "book-review",
    "daily-photo",
    "skill-study",
}

_HEAVY_AGENT_PIPELINE_CONTEXTS = {
    "writer": {
        "upstream_output": "super selected or advanced a writing project from the user request, Mira idea state, project files, and publish cooldown context",
        "downstream_expects": "a draft or final article artifact with writer-gate metadata that publishing, podcast, and social agents can consume",
        "shared_goal": "move the writing project through review, approval, publishing, and follow-up without bypassing system constraints",
    },
    "analyst": {
        "upstream_output": "super scheduled the market-analysis slot and gathered Tetra data, recent briefings, skills, and memory context",
        "downstream_expects": "a dated market briefing saved to artifacts and pushed to the feed with concrete source numbers and portfolio limits respected",
        "shared_goal": "give WA decision-useful market context while preserving the Tetra data contract and downstream feed reliability",
    },
    "researcher": {
        "upstream_output": "super selected the research topic or queue item and supplied prior research state, source material, and memory context",
        "downstream_expects": "a reusable research artifact or queue advancement that research-log, memory, writing, and future research steps can build on",
        "shared_goal": "advance Mira's research-build loop coherently instead of optimizing for a standalone answer",
    },
}

_PIPELINE_PROMPT_FUNCTIONS = ("claude_think", "model_think", "claude_act")


def _pipeline_context_for_agent(agent: str, **overrides) -> dict:
    context = dict(_HEAVY_AGENT_PIPELINE_CONTEXTS.get(str(agent or ""), {}))
    for key in ("upstream_output", "downstream_expects", "shared_goal"):
        value = overrides.get(key)
        if value:
            context[key] = str(value)
    return context


@contextmanager
def _sub_agent_pipeline_context(agent: str, pipeline_context: dict | None = None):
    context = pipeline_context or _pipeline_context_for_agent(agent)
    if not context:
        yield
        return

    modules = []
    try:
        import llm as _llm_module

        modules.append(_llm_module)
    except ImportError:
        pass
    for module_name in ("workflows.daily", "writing_workflow"):
        module = sys.modules.get(module_name)
        if module is not None:
            modules.append(module)

    originals = []

    def _wrap_prompt_call(fn):
        def _wrapped(prompt, *args, **kwargs):
            if kwargs.get("system"):
                next_kwargs = dict(kwargs)
                next_kwargs["system"] = append_pipeline_context_to_system_prompt(next_kwargs["system"], context)
                return fn(prompt, *args, **next_kwargs)
            return fn(append_pipeline_context_to_system_prompt(prompt, context), *args, **kwargs)

        return _wrapped

    for module in modules:
        for name in _PIPELINE_PROMPT_FUNCTIONS:
            fn = getattr(module, name, None)
            if callable(fn):
                originals.append((module, name, fn))
                setattr(module, name, _wrap_prompt_call(fn))

    try:
        yield
    finally:
        for module, name, fn in reversed(originals):
            setattr(module, name, fn)


def _update_coattention(focus_description, invitation):
    path = Path("~/Sandbox/Mira/coattention.md").expanduser()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    focus = str(focus_description or "").strip()
    invite = str(invitation or "").strip()
    entry = f"\n## {timestamp}\n\nFocus: {focus}\n\nInvitation: {invite}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError as exc:
        log.debug("Coattention update failed: %s", exc)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _read_task_distribution() -> dict:
    try:
        data = json.loads(TASK_DISTRIBUTION_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"daily_counts": {}, "rolling_14_day": {}, "blind_spot_flag": 0}
    return data if isinstance(data, dict) else {"daily_counts": {}, "rolling_14_day": {}, "blind_spot_flag": 0}


def _write_task_distribution(data: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = TASK_DISTRIBUTION_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(TASK_DISTRIBUTION_FILE)


def _normalize_task_distribution_category(value) -> str:
    category = re.sub(r"[^a-z0-9_.:-]+", "-", str(value or "").strip().lower()).strip("-")
    return category or "general"


def _task_dispatch_category(msg) -> str:
    metadata = getattr(msg, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    for key in ("routing_agent", "target_agent", "agent", "agent_type", "task_category", "category"):
        value = getattr(msg, key, None) or metadata.get(key)
        if value:
            return _normalize_task_distribution_category(value)

    allowed_agents = getattr(msg, "allowed_agents", None) or metadata.get("allowed_agents")
    if isinstance(allowed_agents, str):
        allowed_agents = [allowed_agents]
    if isinstance(allowed_agents, list) and len(allowed_agents) == 1 and allowed_agents[0]:
        return _normalize_task_distribution_category(allowed_agents[0])

    tags = classify_task(str(getattr(msg, "content", "") or ""))
    if tags:
        return _normalize_task_distribution_category(tags[0])
    return "general"


def _task_distribution_cutoff_dates(today: datetime.date, days: int) -> set[str]:
    return {(today - timedelta(days=offset)).isoformat() for offset in range(days)}


def _refresh_task_distribution_rollups(data: dict, today: datetime.date) -> None:
    daily_counts = data.setdefault("daily_counts", {})
    keep_days = max(14, int(BLIND_SPOT_LOOKBACK_DAYS) + int(BLIND_SPOT_SILENCE_THRESHOLD_DAYS))
    keep_dates = _task_distribution_cutoff_dates(today, keep_days)
    for day in list(daily_counts):
        if day not in keep_dates:
            daily_counts.pop(day, None)

    recent_dates = _task_distribution_cutoff_dates(today, 14)
    rolling: dict[str, int] = {}
    for day, counts in daily_counts.items():
        if day not in recent_dates or not isinstance(counts, dict):
            continue
        for category, count in counts.items():
            if category == "_total":
                continue
            rolling[category] = rolling.get(category, 0) + _blind_spot_int(count)
    data["rolling_14_day"] = rolling
    data["updated_at"] = datetime.now(timezone.utc).isoformat()


def _record_task_distribution_dispatch(msg) -> None:
    try:
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        category = _task_dispatch_category(msg)
        data = _read_task_distribution()
        daily_counts = data.setdefault("daily_counts", {})
        day_counts = daily_counts.setdefault(day, {})
        day_counts[category] = _blind_spot_int(day_counts.get(category)) + 1
        day_counts["_total"] = _blind_spot_int(day_counts.get("_total")) + 1
        data["last_dispatch_at"] = now.isoformat()
        _refresh_task_distribution_rollups(data, now.date())
        _write_task_distribution(data)
    except Exception as exc:
        log.debug("task distribution record failed: %s", exc)


def _task_distribution_recent_total(daily_counts: dict, dates: list[str]) -> int:
    return sum(_blind_spot_int((daily_counts.get(day) or {}).get("_total")) for day in dates)


def _task_distribution_blind_spot_warnings(data: dict, today: datetime.date) -> list[dict]:
    daily_counts = data.get("daily_counts") if isinstance(data.get("daily_counts"), dict) else {}
    lookback_days = max(1, int(BLIND_SPOT_LOOKBACK_DAYS))
    silence_days = max(1, int(BLIND_SPOT_SILENCE_THRESHOLD_DAYS))
    recent_dates = [(today - timedelta(days=offset)).isoformat() for offset in range(1, silence_days + 1)]
    baseline_dates = [(today - timedelta(days=offset)).isoformat() for offset in range(1, lookback_days + 1)]
    baseline_total = _task_distribution_recent_total(daily_counts, baseline_dates)
    recent_total = _task_distribution_recent_total(daily_counts, recent_dates)
    if baseline_total <= 0:
        return []

    expected_recent_total = (baseline_total / lookback_days) * silence_days
    if recent_total < max(1, expected_recent_total * 0.5):
        return []

    categories: set[str] = set()
    for day in baseline_dates:
        counts = daily_counts.get(day) or {}
        if not isinstance(counts, dict):
            continue
        categories.update(
            category for category, count in counts.items() if category != "_total" and _blind_spot_int(count) > 0
        )

    warnings = []
    warned = data.setdefault("last_warning_date_by_category", {})
    today_key = today.isoformat()
    for category in sorted(categories):
        baseline_volume = sum(_blind_spot_int((daily_counts.get(day) or {}).get(category)) for day in baseline_dates)
        recent_volume = sum(_blind_spot_int((daily_counts.get(day) or {}).get(category)) for day in recent_dates)
        if baseline_volume > 0 and recent_volume == 0 and warned.get(category) != today_key:
            warnings.append(
                {
                    "category": category,
                    "silent_days": silence_days,
                    "baseline_days": lookback_days,
                    "baseline_volume": baseline_volume,
                    "recent_total_volume": recent_total,
                    "baseline_total_volume": baseline_total,
                }
            )
            warned[category] = today_key
    return warnings


def _append_task_distribution_warning_to_journal(warning: dict) -> None:
    journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    message = (
        f"Task dispatch category '{warning['category']}' has been silent for "
        f"{warning['silent_days']} completed day(s) after {warning['baseline_volume']} dispatch(es) "
        f"in the trailing {warning['baseline_days']} day baseline, while total task volume remains active."
    )
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as jf:
            jf.write(f"\n\n---\n\n**[WARNING] Blind Spot Variance Monitor**\n\n{message}\n")
    except OSError as exc:
        log.debug("task distribution journal warning failed: %s", exc)
    _blind_spot_warn("task dispatch category went quiet", **warning)


def _check_task_distribution_blind_spots(state: dict) -> None:
    today = datetime.now(timezone.utc).date()
    today_key = today.isoformat()
    if state.get("last_task_distribution_check") == today_key:
        return

    data = _read_task_distribution()
    _refresh_task_distribution_rollups(data, today)
    warnings = _task_distribution_blind_spot_warnings(data, today)
    if warnings:
        data["blind_spot_flag"] = _blind_spot_int(data.get("blind_spot_flag")) + len(warnings)
        state["blind_spot_flag"] = _blind_spot_int(state.get("blind_spot_flag")) + len(warnings)
        for warning in warnings:
            _append_task_distribution_warning_to_journal(warning)
    state["last_task_distribution_check"] = today_key
    _write_task_distribution(data)


def _render_third_thing(topic: str, timestamp: str) -> str:
    return (
        "# Third Thing\n\n"
        f"Updated: {timestamp}\n\n"
        f"Current joint observation object: {topic}\n\n"
        "This is the problem, knowledge garden, or phenomenon Mira and the user are tracking together right now.\n"
    )


def update_joint_attention(topic: str) -> None:
    focus = " ".join(str(topic or "").split())
    if not focus:
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = _render_third_thing(focus, timestamp)
    try:
        _write_text_atomic(THIRD_THING_FILE, content)
        _write_text_atomic(BRIDGE_THIRD_THING_FILE, content)
    except OSError as exc:
        log.debug("Third Thing write failed: %s", exc)

    try:
        from soul.third_thing_tracker import ThirdThingRegistry

        ThirdThingRegistry().touch_mira(
            focus,
            at=datetime.now(timezone.utc),
            description="Current joint observation object",
        )
    except Exception as exc:
        log.debug("Third Thing registry update failed: %s", exc)

    _serve_joint_attention_to_bridge(content, timestamp)


def _serve_joint_attention_to_bridge(content: str, timestamp: str) -> None:
    if Mira is None:
        return
    try:
        bridges = Mira.for_all_users(MIRA_DIR)
    except Exception as exc:
        log.debug("Third Thing bridge discovery failed: %s", exc)
        return

    for bridge in bridges:
        try:
            item_id = "third_thing"
            if bridge.item_exists(item_id):
                item = bridge._read_item(item_id)
                if not item:
                    continue
                item["type"] = "feed"
                item["title"] = "Third Thing"
                item["status"] = "done"
                item["origin"] = "agent"
                item["pinned"] = True
                item["tags"] = list(dict.fromkeys(["mira", "joint-attention", "third-thing", *item.get("tags", [])]))
                item["updated_at"] = timestamp
                messages = item.setdefault("messages", [])
                if messages:
                    messages[0]["content"] = content
                    messages[0]["timestamp"] = timestamp
                    messages[0]["sender"] = "agent"
                else:
                    item = bridge.create_feed(
                        item_id,
                        "Third Thing",
                        content,
                        tags=["mira", "joint-attention", "third-thing"],
                        pinned=True,
                    )
                bridge._write_item(item)
                bridge._update_manifest(item)
            else:
                item = bridge.create_feed(
                    item_id,
                    "Third Thing",
                    content,
                    tags=["mira", "joint-attention", "third-thing"],
                    pinned=True,
                )
                item["pinned"] = True
                bridge._write_item(item)
                bridge._update_manifest(item)
        except Exception as exc:
            log.debug("Third Thing bridge update failed for %s: %s", getattr(bridge, "user_id", "?"), exc)


def _joint_attention_topic_from_completed_background(completed: list[str]) -> str:
    for name in completed:
        if name.startswith("research-cycle") or name.startswith("research-log") or name == "research":
            return "Mira's autonomous research-build loop"
        if name.startswith("reflect"):
            return "the current joint-attention landscape"
        if name.startswith("journal"):
            return "today's journal as a knowledge-garden page"
        if name.startswith("explore"):
            label = name.removeprefix("explore-").replace("-", " ").strip()
            return f"explore briefing knowledge garden: {label}" if label else "explore briefing knowledge garden"
        if name.startswith("writing-pipeline") or name.startswith("autowrite"):
            return "the active writing-project knowledge garden"
    return ""


def _intent_clear(message) -> bool:
    text = getattr(message, "content", message)
    if isinstance(text, dict):
        text = text.get("content") or text.get("title") or ""
    cleaned = " ".join(str(text or "").strip().split())
    if len(cleaned) <= 20:
        return False
    if any(pattern.search(cleaned) for pattern in _AMBIGUOUS_INTENT_PATTERNS):
        return False

    lower = cleaned.lower()
    has_action_verb = any(re.search(rf"\b{re.escape(verb)}\b", lower) for verb in _ACTION_VERBS)
    return has_action_verb or any(verb in cleaned for verb in _CJK_ACTION_VERBS)


def _check_intent_clarity_before_dispatch(text: str) -> dict:
    if not _intent_clear(text):
        return {"is_clear": False, "question": _INTENT_CLARIFICATION_REPLY}
    if callable(_ORIGINAL_TALK_INTENT_CHECK):
        return _ORIGINAL_TALK_INTENT_CHECK(text)
    return {"is_clear": True, "question": ""}


def _detect_survival_exposure(text: str) -> bool:
    """Sensitive survival disclosures are routed only to secret, never cloud APIs."""
    if not SENSITIVE_FORCE_LOCAL:
        return False
    content = str(text or "")
    lower_content = content.lower()
    return detect_vulnerability_disclosure(content) or any(
        term and str(term).lower() in lower_content for term in SENSITIVE_SURVIVAL_TERMS
    )


def _is_sensitive_hour(hour: int | None = None) -> bool:
    if not SENSITIVITY_ROUTE_TO_LOCAL:
        return False
    try:
        start = int(SENSITIVITY_HOURS_START)
        end = int(SENSITIVITY_HOURS_END)
    except (TypeError, ValueError):
        return False
    if start == end:
        return False
    current = datetime.now().hour if hour is None else int(hour)
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _detect_time_sensitive_message(text: str) -> bool:
    content = str(text or "").strip()
    return len(content) > _TIME_SENSITIVE_MIN_MESSAGE_CHARS and _is_sensitive_hour()


def _append_survival_exposure_audit(msg) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "生存性暴露已路由至本地",
        "task_id": getattr(msg, "id", ""),
        "thread_id": getattr(msg, "thread_id", ""),
        "user_id": getattr(msg, "user_id", ""),
        "routing_agent": "secret",
        "sensitive_flag": True,
    }
    try:
        audit_file = LOGS_DIR / "sensitive_routing_audit.jsonl"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("sensitive routing audit write failed: %s", exc)
    log.warning(
        "生存性暴露已路由至本地 task=%s routing_agent=secret sensitive_flag=True",
        getattr(msg, "id", ""),
    )


def _mark_time_sensitive_message(msg, original_content: str) -> None:
    metadata = dict(getattr(msg, "metadata", {}) or {})
    metadata["routing_agent"] = "secret"
    metadata["time_sensitive_window"] = True
    metadata["reduced_logging"] = True
    metadata["sensitive_reason"] = metadata.get("sensitive_reason", "late_night_window")
    privacy_policy = metadata.get("_privacy_policy") if isinstance(metadata.get("_privacy_policy"), dict) else {}
    metadata["_privacy_policy"] = {
        **privacy_policy,
        "local_only": True,
        "no_cloud_apis": True,
        "no_verbatim_logging": True,
        "routing_agent": "secret",
    }
    msg.metadata = metadata
    msg.model_restriction = "omlx"
    msg.routing_agent = "secret"
    msg.reduced_logging = True
    msg.allowed_agents = ["secret"]

    original_to_dict = msg.to_dict

    def _to_dict():
        payload = dict(original_to_dict())
        payload["content"] = original_content
        payload["metadata"] = metadata
        payload["routing_agent"] = "secret"
        payload["model_restriction"] = "omlx"
        payload["reduced_logging"] = True
        tags = list(payload.get("tags") or [])
        for tag in ("secret", "private"):
            if tag not in tags:
                tags.append(tag)
        payload["tags"] = tags
        payload["allowed_agents"] = ["secret"]
        return payload

    msg.to_dict = _to_dict


def _mark_survival_sensitive_message(msg, original_content: str) -> None:
    metadata = dict(getattr(msg, "metadata", {}) or {})
    metadata["routing_agent"] = "secret"
    metadata["sensitive_flag"] = True
    metadata["sensitive_reason"] = "survival_exposure"
    metadata["_privacy_policy"] = {
        "local_only": True,
        "no_cloud_apis": True,
        "routing_agent": "secret",
    }
    msg.metadata = metadata
    msg.model_restriction = "omlx"
    msg.routing_agent = "secret"
    msg.sensitive_flag = True
    msg.allowed_agents = ["secret"]

    original_to_dict = msg.to_dict

    def _to_dict():
        payload = dict(original_to_dict())
        payload["content"] = original_content
        payload["metadata"] = metadata
        payload["routing_agent"] = "secret"
        payload["model_restriction"] = "omlx"
        payload["sensitive_flag"] = True
        tags = list(payload.get("tags") or [])
        for tag in ("secret", "private"):
            if tag not in tags:
                tags.append(tag)
        payload["tags"] = tags
        payload["allowed_agents"] = ["secret"]
        return payload

    msg.to_dict = _to_dict


def _redact_bridge_item_for_survival_exposure(bridge, item_id: str) -> None:
    try:
        item = bridge._read_item(item_id)
        if not item:
            return
        metadata = dict(item.get("metadata") or {})
        metadata["routing_agent"] = "secret"
        metadata["sensitive_flag"] = True
        metadata["sensitive_reason"] = "survival_exposure"
        metadata["_privacy_policy"] = {
            "local_only": True,
            "no_cloud_apis": True,
            "routing_agent": "secret",
        }
        item["metadata"] = metadata
        for message in reversed(item.get("messages", [])):
            if (message.get("sender") or "").lower() not in {"agent", "mira"}:
                message["content"] = _SENSITIVE_REDACTED_CONTENT
                message["sensitive_flag"] = True
                break
        bridge._write_item(item)
        bridge._update_manifest(item)
    except Exception as exc:
        log.debug("sensitive bridge redaction failed: %s", exc)


def _dispatch_with_survival_guard(self, msg, workspace_dir, *args, **kwargs):
    original_content = getattr(msg, "content", "")
    survival_sensitive = _detect_survival_exposure(original_content)
    time_sensitive = _detect_time_sensitive_message(original_content)
    if not survival_sensitive and not time_sensitive:
        task_id = _ORIGINAL_TASK_MANAGER_DISPATCH(self, msg, workspace_dir, *args, **kwargs)
        if task_id:
            _record_task_distribution_dispatch(msg)
        return task_id

    if survival_sensitive:
        _mark_survival_sensitive_message(msg, original_content)
        _append_survival_exposure_audit(msg)
        redacted_content = _SENSITIVE_REDACTED_CONTENT
    else:
        redacted_content = _TIME_SENSITIVE_REDACTED_CONTENT
    if time_sensitive:
        _mark_time_sensitive_message(msg, original_content)
    msg.content = redacted_content
    try:
        task_id = _ORIGINAL_TASK_MANAGER_DISPATCH(self, msg, workspace_dir, *args, **kwargs)
        if task_id:
            _record_task_distribution_dispatch(msg)
        return task_id
    finally:
        msg.content = original_content


def _dispatch_or_requeue_with_survival_guard(task_mgr, bridge, msg, workspace, cmd=None):
    if _is_self_referential_evaluator_dispatch(msg, cmd):
        _mark_self_referential_evaluation_exploratory(msg)
        log.warning(
            "SELF_VERIFICATION_DISPATCH_REJECTED task_id=%s target=evaluator status=exploratory",
            getattr(msg, "id", ""),
        )
        return "exploratory"
    if _detect_survival_exposure(getattr(msg, "content", "")):
        _redact_bridge_item_for_survival_exposure(bridge, getattr(msg, "id", ""))
    return _ORIGINAL_DISPATCH_OR_REQUEUE(task_mgr, bridge, msg, workspace, cmd)


def _evaluation_target_value(value) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, (list, tuple, set)):
        return " ".join(_evaluation_target_value(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_evaluation_target_value(item) for item in value.values())
    return ""


def _evaluation_targets_evaluator(container) -> bool:
    if not isinstance(container, dict):
        return False
    target_keys = {
        "agent",
        "agent_name",
        "evaluate_agent",
        "evaluation_target",
        "score_agent",
        "subject",
        "target",
        "target_agent",
    }
    for key in target_keys:
        value = _evaluation_target_value(container.get(key))
        if value in {"evaluator", "evaluator agent", "evaluation agent"}:
            return True
    return False


def _is_self_referential_evaluator_dispatch(msg, cmd=None) -> bool:
    metadata = getattr(msg, "metadata", {}) or {}
    text_parts = [
        getattr(msg, "content", ""),
        getattr(msg, "routing_agent", ""),
    ]
    if isinstance(metadata, dict):
        text_parts.append(_evaluation_target_value(metadata))
    if isinstance(cmd, dict):
        text_parts.append(_evaluation_target_value(cmd))
    combined = " ".join(part for part in text_parts if part)
    if _EVALUATOR_TARGET_RE.search(combined):
        return True
    if not _EVALUATION_ACTION_RE.search(combined):
        return False
    return _evaluation_targets_evaluator(metadata) or _evaluation_targets_evaluator(cmd)


def _mark_self_referential_evaluation_exploratory(msg) -> None:
    metadata = dict(getattr(msg, "metadata", {}) or {})
    metadata["verification_status"] = "exploratory"
    metadata["verification_method"] = "self_verification_rejected"
    metadata["outcome_verified"] = False
    msg.metadata = metadata


def _completion_party(value) -> str:
    return str(value or "").strip().lower()


def _independence_parties(result: dict) -> tuple[str, str]:
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    completed_by = _completion_party(
        result.get("completed_by") or result.get("agent") or result.get("agent_type") or result.get("completed_agent")
    )
    verified_by = _completion_party(
        result.get("verified_by")
        or verification.get("verified_by")
        or verification.get("verifier")
        or verification.get("agent")
        or verification.get("checked_by")
    )
    return completed_by, verified_by


def _requires_independent_verification(result: dict, rec) -> bool:
    if not VERIFICATION_INDEPENDENCE:
        return False
    completed_by, verified_by = _independence_parties(result)
    if not completed_by or not verified_by or completed_by != verified_by:
        return False
    status = normalize_task_status(getattr(rec, "status", result.get("status", "")))
    return status in {"done", "verified"} or bool(result.get("outcome_verified"))


def _mark_independent_verification_required(rec, result: dict) -> None:
    completed_by, verified_by = _independence_parties(result)
    rec.status = "completed_unverified"
    rec.outcome_verified = False
    rec.verification_method = "independent_verification_required"
    detail = (
        "Completion rejected: completed_by and verified_by are the same agent "
        f"({completed_by or verified_by}). Super or another agent must verify before done."
    )
    rec.summary = f"{rec.summary}\n\n{detail}".strip() if rec.summary else detail
    verification = dict(rec.verification or {})
    verification.update(
        {
            "status": "failed",
            "verified": False,
            "summary": detail,
            "proxy_checked": "independent_verification_required",
        }
    )
    checks = list(verification.get("checks") or [])
    checks.append(
        {
            "name": "verification_independence",
            "passed": False,
            "message": detail,
        }
    )
    verification["checks"] = checks
    rec.verification = verification


def _collect_result_with_verification_independence(self, rec):
    collected = _ORIGINAL_TASK_MANAGER_COLLECT_RESULT(self, rec)
    if not collected:
        return collected
    result_file = Path(rec.workspace) / "result.json" if getattr(rec, "workspace", "") else None
    if not result_file or not result_file.exists():
        return collected
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return collected
    if not isinstance(result, dict) or not _requires_independent_verification(result, rec):
        return collected
    completed_by, _verified_by = _independence_parties(result)
    _mark_independent_verification_required(rec, result)
    result["status"] = rec.status
    result["outcome_verified"] = False
    result["verification_method"] = rec.verification_method
    result["summary"] = rec.summary
    result["verification"] = rec.verification
    try:
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("independent verification result rewrite failed for %s: %s", rec.task_id, exc)
    log.warning("TASK_COMPLETION_REJECTED_SELF_VERIFIED task_id=%s agent=%s", rec.task_id, completed_by)
    return collected


def _sensitive_record_content(rec) -> str:
    workspace = getattr(rec, "workspace", "")
    if not workspace:
        return ""
    try:
        msg_file = Path(workspace) / "message.json"
        if not msg_file.exists():
            return ""
        payload = json.loads(msg_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if payload.get("sensitive_flag") is True or metadata.get("sensitive_flag") is True:
        return str(payload.get("content") or "")
    return ""


def _desensitize_sensitive_text(text: str, sensitive_content: str) -> str:
    content = str(text or "")
    if sensitive_content and sensitive_content in content:
        return content.replace(sensitive_content, _SENSITIVE_REDACTED_CONTENT)
    return content


def _mark_bridge_response_sensitive(bridge, item_id: str) -> None:
    try:
        item = bridge._read_item(item_id)
        if not item:
            return
        metadata = dict(item.get("metadata") or {})
        metadata["routing_agent"] = "secret"
        metadata["sensitive_flag"] = True
        metadata["sensitive_reason"] = "survival_exposure"
        metadata["_privacy_policy"] = {
            "local_only": True,
            "no_cloud_apis": True,
            "routing_agent": "secret",
        }
        item["metadata"] = metadata
        for message in reversed(item.get("messages", [])):
            if (message.get("sender") or "").lower() in {"agent", "mira"}:
                message["sensitive_flag"] = True
                break
        bridge._write_item(item)
        bridge._update_manifest(item)
    except Exception as exc:
        log.debug("sensitive response marker failed: %s", exc)


def _project_record_to_bridge_with_survival_guard(bridge, task_mgr, rec) -> None:
    if not callable(_ORIGINAL_PROJECT_RECORD_TO_BRIDGE):
        return
    sensitive_content = _sensitive_record_content(rec)
    if not sensitive_content:
        _ORIGINAL_PROJECT_RECORD_TO_BRIDGE(bridge, task_mgr, rec)
        return

    original_get_reply_content = task_mgr.get_reply_content

    def _get_reply_content(record):
        return _desensitize_sensitive_text(original_get_reply_content(record), sensitive_content)

    task_mgr.get_reply_content = _get_reply_content
    try:
        _ORIGINAL_PROJECT_RECORD_TO_BRIDGE(bridge, task_mgr, rec)
        _mark_bridge_response_sensitive(bridge, getattr(rec, "task_id", ""))
    finally:
        task_mgr.get_reply_content = original_get_reply_content


def _install_clear_intent_gate() -> None:
    if getattr(talk_module, "check_intent_clarity", None) is _check_intent_clarity_before_dispatch:
        return
    talk_module.check_intent_clarity = _check_intent_clarity_before_dispatch


def _install_survival_dispatch_guard() -> None:
    if TaskManager.dispatch is not _dispatch_with_survival_guard:
        TaskManager.dispatch = _dispatch_with_survival_guard
    if TaskManager._collect_result is not _collect_result_with_verification_independence:
        TaskManager._collect_result = _collect_result_with_verification_independence
    if getattr(talk_module, "_dispatch_or_requeue", None) is not _dispatch_or_requeue_with_survival_guard:
        talk_module._dispatch_or_requeue = _dispatch_or_requeue_with_survival_guard
    if getattr(talk_module, "_project_record_to_bridge", None) is not _project_record_to_bridge_with_survival_guard:
        talk_module._project_record_to_bridge = _project_record_to_bridge_with_survival_guard


def _log_skill_depth_advisories(command: str) -> None:
    if command not in _DOMAIN_TASK_COMMANDS:
        return
    try:
        from config import SKILLS_INDEX

        if not SKILLS_INDEX.exists():
            return
        skills = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("skill depth advisory load failed: %s", exc)
        return

    for skill in skills if isinstance(skills, list) else []:
        if not isinstance(skill, dict) or not skill.get("name"):
            continue
        _source, depth = get_skill_provenance(str(skill["name"]))
        if depth == "unverified":
            log.warning(
                "depth_advisory: skill '%s' has no verified domain grounding; outcomes may be surface-level",
                skill["name"],
            )


def do_talk():
    _install_clear_intent_gate()
    _install_survival_dispatch_guard()
    talk_module.MAX_TASKS_PER_CYCLE = MAX_TASKS_PER_CYCLE
    return _do_talk()


def _survival_component_status(name: str, status: str, detail: str, warnings: list[str] | None = None) -> dict:
    return {
        "component": name,
        "status": status,
        "detail": detail,
        "warnings": warnings or [],
    }


def _survival_heartbeat_status() -> dict:
    heartbeat = MIRA_DIR / "heartbeat.json"
    if not heartbeat.exists():
        return _survival_component_status("heartbeat", "exposed", f"missing heartbeat file: {heartbeat}")

    updated_at = _heartbeat_updated_at(heartbeat)
    if updated_at is None:
        return _survival_component_status("heartbeat", "exposed", f"unreadable heartbeat timestamp: {heartbeat}")

    age_seconds = time.time() - updated_at
    detail = f"age_seconds={round(age_seconds, 1)} path={heartbeat}"
    if age_seconds <= 300:
        return _survival_component_status("heartbeat", "ok", detail)
    if age_seconds <= 600:
        return _survival_component_status("heartbeat", "degraded", detail, ["heartbeat stale"])
    return _survival_component_status("heartbeat", "exposed", detail, ["heartbeat stale beyond recovery threshold"])


def _survival_notes_bridge_status() -> dict:
    required_paths = [MIRA_DIR, MIRA_DIR / "inbox", MIRA_DIR / "outbox"]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        return _survival_component_status("notes_bridge", "exposed", "missing bridge path(s)", missing)

    try:
        from notes_bridge import check_bridge_staleness

        is_stale, age_minutes = check_bridge_staleness(
            MIRA_DIR,
            threshold_minutes=BRIDGE_STALENESS_THRESHOLD_MINUTES,
        )
    except Exception as exc:
        return _survival_component_status("notes_bridge", "exposed", f"bridge staleness check failed: {exc}")

    detail = f"age_minutes={round(age_minutes, 1)} root={MIRA_DIR}"
    if is_stale:
        return _survival_component_status("notes_bridge", "degraded", detail, ["bridge stale"])
    return _survival_component_status("notes_bridge", "ok", detail)


def _process_matches(pid: int, needle: str) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return True
    return result.returncode == 0 and needle in (result.stdout or "")


def _survival_task_worker_status() -> dict:
    worker_script = Path(__file__).resolve().parent / "task_worker.py"
    if not worker_script.exists():
        return _survival_component_status("task_worker", "exposed", f"missing worker script: {worker_script}")

    manager = TaskManager()
    active = [record for record in manager._records if record.status in {"dispatched", "running"}]
    dead = [
        record.task_id
        for record in active
        if not record.pid or not _process_matches(int(record.pid), str(worker_script))
    ]
    if dead:
        return _survival_component_status(
            "task_worker",
            "exposed",
            f"active worker process missing for task(s): {', '.join(dead)}",
        )

    warnings = []
    now = time.time()
    for record in active:
        heartbeat = Path(record.workspace) / "heartbeat.json" if record.workspace else None
        if not heartbeat or not heartbeat.exists():
            warnings.append(f"{record.task_id}: worker heartbeat missing")
            continue
        updated_at = _heartbeat_updated_at(heartbeat)
        if updated_at is None:
            warnings.append(f"{record.task_id}: worker heartbeat unreadable")
        elif now - updated_at > 180:
            warnings.append(f"{record.task_id}: worker heartbeat stale")

    detail = f"active_workers={len(active)} script={worker_script}"
    if warnings:
        return _survival_component_status("task_worker", "degraded", detail, warnings)
    return _survival_component_status("task_worker", "ok", detail)


def _survival_preflight_status() -> dict:
    try:
        from publish.preflight import preflight_check
    except Exception as exc:
        return _survival_component_status("preflight", "exposed", f"preflight import failed: {exc}")

    if not callable(preflight_check):
        return _survival_component_status("preflight", "exposed", "preflight_check is not callable")
    return _survival_component_status("preflight", "ok", "publish.preflight.preflight_check callable")


def _check_survival_critical_components() -> dict:
    checks = {
        "heartbeat": _survival_heartbeat_status,
        "notes_bridge": _survival_notes_bridge_status,
        "task_worker": _survival_task_worker_status,
        "preflight": _survival_preflight_status,
    }
    components = []
    for component in SURVIVAL_CRITICAL_COMPONENTS:
        check = checks.get(component)
        if check is None:
            components.append(_survival_component_status(component, "exposed", "no survival check registered"))
            continue
        try:
            components.append(check())
        except Exception as exc:
            components.append(_survival_component_status(component, "exposed", f"survival check failed: {exc}"))

    if any(component["status"] == "exposed" for component in components):
        tier = "exposed"
    elif any(component["status"] == "degraded" for component in components):
        tier = "degraded"
    else:
        tier = "ok"

    return {
        "tier": tier,
        "exposure_class": "survival",
        "fallback": "none",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": components,
    }


def _check_recent_completed_task_content_integrity() -> None:
    try:
        manager = TaskManager()
    except Exception as exc:
        log.warning("OPERATIONAL_AUDIT content_integrity suspect=task_status_unreadable error=%s", exc)
        return

    complete_statuses = {"done", "verified", "completed", "complete", "completed_unverified"}
    completed = [
        record
        for record in manager._records
        if record.completed_at and normalize_task_status(record.status) in complete_statuses
    ]
    if not completed:
        return

    completed.sort(key=lambda record: _parse_recovery_timestamp(record.completed_at) or 0, reverse=True)
    record = completed[0]
    if not record.workspace:
        log.warning(
            "OPERATIONAL_AUDIT content_integrity task_id=%s status=%s suspect=missing_workspace",
            record.task_id,
            record.status,
        )
        return

    output_path = Path(record.workspace) / "output.md"
    try:
        output_size = os.path.getsize(output_path)
    except OSError:
        log.warning(
            "OPERATIONAL_AUDIT content_integrity task_id=%s status=%s output_path=%s suspect=missing_output",
            record.task_id,
            record.status,
            output_path,
        )
        return

    if output_size <= MIN_COMPLETED_TASK_OUTPUT_BYTES:
        log.warning(
            "OPERATIONAL_AUDIT content_integrity task_id=%s status=%s output_path=%s output_bytes=%d "
            "minimum_bytes=%d suspect=truncated_output",
            record.task_id,
            record.status,
            output_path,
            output_size,
            MIN_COMPLETED_TASK_OUTPUT_BYTES,
        )


def _check_stuck_tasks_audit() -> None:
    try:
        stuck = get_stuck_tasks(threshold_minutes=STUCK_TASK_THRESHOLD_MINUTES)
    except Exception as exc:
        log.warning("OPERATIONAL_AUDIT stuck_tasks suspect=task_state_unreadable error=%s", exc)
        return

    count = int(stuck.get("count", 0) or 0)
    if count > MAX_STUCK_TASKS_BEFORE_ALERT:
        log.warning(
            "OPERATIONAL_AUDIT stuck_tasks count=%d threshold=%d threshold_minutes=%d agent_types=%s task_ids=%s",
            count,
            MAX_STUCK_TASKS_BEFORE_ALERT,
            STUCK_TASK_THRESHOLD_MINUTES,
            ",".join(stuck.get("agent_types", [])),
            ",".join(stuck.get("task_ids", [])),
        )


# ---------------------------------------------------------------------------
# Startup health check — make invisible dependencies visible
# ---------------------------------------------------------------------------


def _check_invisible_dependencies():
    """Enumerate and validate shared modules and path dependencies that every
    agent silently relies on but that are never directly monitored.

    Logs a structured WARNING for each node that fails. Does not raise.
    """
    from config import (
        MIRA_ROOT,
        SOUL_DIR,
        SECRETS_FILE,
        MIRA_DIR,
        TASK_TIMEOUT,
        TASK_TIMEOUT_LONG,
    )

    # 1. Spot-check config values
    for node, value, expected in [
        ("config.MIRA_ROOT", MIRA_ROOT, "non-empty path"),
        ("config.TASK_TIMEOUT", TASK_TIMEOUT, "positive int"),
        ("config.TASK_TIMEOUT_LONG", TASK_TIMEOUT_LONG, "positive int"),
    ]:
        if not value:
            throttled_warning(
                log,
                "INVISIBLE_DEP node=%s expected=%s actual=missing/falsy",
                node,
                expected,
                key=f"invis:{node}",
            )
    if not SECRETS_FILE.exists():
        throttled_warning(
            log,
            "INVISIBLE_DEP node=config.SECRETS_FILE expected=exists actual=missing path=%s",
            SECRETS_FILE,
            key="invis:secrets_file",
        )

    # 2. Soul directory and core identity/memory files
    if not SOUL_DIR.exists():
        throttled_warning(
            log,
            "INVISIBLE_DEP node=soul_dir expected=exists actual=missing path=%s",
            SOUL_DIR,
            key="invis:soul_dir",
        )
    else:
        try:
            from memory.soul import health_check as _soul_health

            result = _soul_health()
            if not result.get("ok"):
                for fname in result.get("missing", []):
                    throttled_warning(
                        log,
                        "INVISIBLE_DEP node=soul/%s expected=non-empty-file actual=missing-or-empty",
                        fname,
                        key=f"invis:soul_file:{fname}",
                    )
        except Exception as _exc:
            throttled_warning(
                log,
                "INVISIBLE_DEP node=memory.soul expected=importable actual=%s",
                _exc,
                key="invis:memory_soul_import",
            )

    # 3. Notes inbox / outbox paths
    for label, path in [
        ("notes_inbox", MIRA_DIR / "inbox"),
        ("notes_outbox", MIRA_DIR / "outbox"),
    ]:
        if not path.exists():
            throttled_warning(
                log,
                "INVISIBLE_DEP node=%s expected=exists actual=missing path=%s",
                label,
                path,
                key=f"invis:{label}",
            )

    # 4. Import checks for shared modules that everything depends on
    for mod_name in ("bridge", "memory.soul"):
        try:
            import importlib

            importlib.import_module(mod_name)
        except Exception as _exc:
            throttled_warning(
                log,
                "INVISIBLE_DEP node=%s expected=importable actual=%s",
                mod_name,
                _exc,
                key=f"invis:import:{mod_name}",
            )

    # 5. Content integrity: the newest completed task must have non-trivial output
    _check_recent_completed_task_content_integrity()

    # 6. Stuck task detection: active states past transition threshold
    _check_stuck_tasks_audit()

    # 7. Survival-critical components: no fallback, separate from strategic degradations
    survival_status = _check_survival_critical_components()
    survival_log = log.info if survival_status["tier"] == "ok" else log.warning
    survival_log(
        "OPERATIONAL_AUDIT survival_status=%s",
        json.dumps(survival_status, ensure_ascii=False, sort_keys=True),
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _run_auth_health_if_due(interval_s: int = 300) -> None:
    state = load_state()
    now = time.time()
    last = float(state.get("last_auth_health_check", 0) or 0)
    if now - last < interval_s:
        return
    try:
        from auth_health import run_auth_health_checks

        results = run_auth_health_checks()
        for result in results:
            if result.severity in {"warning", "critical"}:
                log.warning(
                    "AUTH_HEALTH provider=%s status=%s detail=%s", result.provider, result.status, result.detail
                )
            else:
                log.info("AUTH_HEALTH provider=%s status=%s", result.provider, result.status)
        state["last_auth_health_check"] = now
        save_state(state)
    except Exception as exc:
        log.warning("Auth health check failed: %s", exc)


def _auto_recover_enabled() -> bool:
    raw = os.environ.get("AUTO_RECOVER")
    if raw is None:
        try:
            from config import _cfg

            recovery_cfg = _cfg.get("recovery", {}) if isinstance(_cfg.get("recovery", {}), dict) else {}
            raw = str(
                _cfg.get(
                    "AUTO_RECOVER",
                    _cfg.get("auto_recover", recovery_cfg.get("AUTO_RECOVER", False)),
                )
            )
        except Exception:
            raw = "false"
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _log_recovery(symptom: str, action: str) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symptom": symptom,
        "action": action,
    }
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "recovery.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("recovery log write failed: %s", exc)


def _parse_recovery_timestamp(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _load_heartbeat_data(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _heartbeat_updated_at(path: Path) -> float | None:
    data = _load_heartbeat_data(path)
    if data:
        for key in ("last_heartbeat", "timestamp", "updated_at", "last_updated", "ts"):
            ts = _parse_recovery_timestamp(data.get(key))
            if ts is not None:
                return ts
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _mourning_context(data: dict) -> dict:
    context = {}
    agent_status = data.get("agent_status") if isinstance(data.get("agent_status"), dict) else {}
    active_tasks = agent_status.get("active_tasks") or data.get("active_tasks") or []
    if active_tasks:
        last_task = active_tasks[-1]
        if isinstance(last_task, dict):
            task_id = last_task.get("task_id") or last_task.get("id")
            if task_id:
                context["last_task_id"] = task_id
        elif isinstance(last_task, str):
            context["last_task_id"] = last_task
    for key in ("status", "busy", "active_count"):
        if key in data:
            context[key] = data[key]
    return context


def _append_mourning_record(record: dict) -> None:
    path = LOGS_DIR / "mourning.json"
    records = []
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                records = existing
            elif isinstance(existing, dict):
                records = [existing]
        records.append(record)
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("mourning log write failed: %s", exc)


def _record_stale_heartbeat_mourning(now: float) -> None:
    heartbeat = MIRA_DIR / "heartbeat.json"
    if not heartbeat.exists():
        return

    data = _load_heartbeat_data(heartbeat) or {}
    updated_at = _heartbeat_updated_at(heartbeat)
    if updated_at is None:
        return

    age_seconds = now - updated_at
    if age_seconds <= 300:
        return

    _append_mourning_record(
        {
            "detected_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "last_heartbeat": datetime.fromtimestamp(updated_at, timezone.utc).isoformat(),
            "downtime_seconds": round(age_seconds, 3),
            "context": _mourning_context(data),
        }
    )


def _recover_stale_heartbeat(now: float) -> None:
    heartbeat = MIRA_DIR / "heartbeat.json"
    if not heartbeat.exists():
        return

    updated_at = _heartbeat_updated_at(heartbeat)
    if updated_at is None:
        return

    age_seconds = now - updated_at
    if age_seconds <= 600:
        return

    label = "com.angwei.mira-agent"
    target = f"gui/{os.getuid()}/{label}"
    symptom = f"heartbeat stale age_seconds={int(age_seconds)} path={heartbeat}"
    action = f"launchctl kickstart -k {target}; sigterm pid={os.getpid()}"
    _log_recovery(symptom, action)
    try:
        subprocess.Popen(
            ["launchctl", "kickstart", "-k", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        os.kill(os.getpid(), signal.SIGTERM)
    except OSError as exc:
        _log_recovery(symptom, f"restart failed: {exc}")


def _recent_tracebacks(crash_log: Path, now: float) -> list[str]:
    if not crash_log.exists():
        return []
    try:
        size = crash_log.stat().st_size
        with open(crash_log, "r", encoding="utf-8", errors="replace") as f:
            if size > 512 * 1024:
                f.seek(size - 512 * 1024)
                f.readline()
            text = f.read()
    except OSError:
        return []

    tracebacks: list[str] = []
    for chunk in re.split(r"\n={20,}\n", text):
        lines = chunk.strip().splitlines()
        if len(lines) < 2:
            continue
        ts = _parse_recovery_timestamp(lines[0].strip())
        if ts is None or now - ts > 120:
            continue
        body = "\n".join(lines[1:]).strip()
        if "Traceback (most recent call last):" in body:
            tracebacks.append(body)
    return tracebacks


def _task_workspace_from_path(path: Path) -> Path | None:
    try:
        rel = path.resolve().relative_to(TASKS_DIR.resolve())
    except (OSError, ValueError):
        return None
    if not rel.parts or rel.parts[0] == ".quarantine":
        return None
    return TASKS_DIR / rel.parts[0]


def _find_traceback_workspace(traceback_text: str) -> Path | None:
    workspace_patterns = (
        r"--workspace(?:=|\s+)([^\s]+)",
        r"workspace(?:=|:)\s*['\"]?([^'\"\s,)]+)",
    )
    for pattern in workspace_patterns:
        match = re.search(pattern, traceback_text)
        if match:
            workspace = _task_workspace_from_path(Path(match.group(1)))
            if workspace and workspace.exists():
                return workspace

    task_root = re.escape(str(TASKS_DIR))
    for match in re.finditer(task_root + r"/[^'\"\s:)]+", traceback_text):
        workspace = _task_workspace_from_path(Path(match.group(0)))
        if workspace and workspace.exists():
            return workspace

    match = re.search(r"--task-id(?:=|\s+)([^\s]+)", traceback_text)
    if match:
        workspace = TASKS_DIR / match.group(1)
        if workspace.exists():
            return workspace
    return None


def _quarantine_task_workspace(workspace: Path, symptom: str) -> None:
    try:
        resolved = workspace.resolve()
        resolved.relative_to(TASKS_DIR.resolve())
    except (OSError, ValueError):
        return
    if resolved == TASKS_DIR.resolve() or ".quarantine" in resolved.parts:
        return

    quarantine_dir = TASKS_DIR / ".quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = quarantine_dir / f"{workspace.name}-{suffix}"
    while dest.exists():
        dest = quarantine_dir / f"{workspace.name}-{suffix}-{uuid.uuid4().hex[:6]}"
    try:
        shutil.move(str(workspace), str(dest))
        _log_recovery(symptom, f"quarantined workspace {workspace} -> {dest}")
    except OSError as exc:
        _log_recovery(symptom, f"quarantine failed for {workspace}: {exc}")


def _recover_crash_loop(now: float) -> None:
    counts: dict[str, int] = {}
    for traceback_text in _recent_tracebacks(Path("/tmp/mira-crash.log"), now):
        counts[traceback_text] = counts.get(traceback_text, 0) + 1

    for traceback_text, count in counts.items():
        if count <= 3:
            continue
        symptom = f"repeated identical traceback count={count} window_seconds=120"
        workspace = _find_traceback_workspace(traceback_text)
        if workspace is None:
            _log_recovery(symptom, "no task workspace identified for quarantine")
            continue
        _quarantine_task_workspace(workspace, symptom)


def _remove_orphaned_task_locks(now: float) -> None:
    if not TASKS_DIR.exists():
        return
    try:
        import fcntl
    except ImportError:
        return

    cutoff_seconds = 30 * 60
    for workspace in TASKS_DIR.iterdir():
        if not workspace.is_dir() or workspace.name == ".quarantine":
            continue
        try:
            lock_files = list(workspace.rglob("*.lock"))
        except OSError:
            continue
        for lock_file in lock_files:
            try:
                if now - lock_file.stat().st_mtime <= cutoff_seconds:
                    continue
                with open(lock_file, "a", encoding="utf-8") as lf:
                    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lf, fcntl.LOCK_UN)
                lock_file.unlink()
                _log_recovery(
                    f"orphaned task lock age_seconds>{cutoff_seconds} path={lock_file}",
                    f"removed lock {lock_file}",
                )
            except BlockingIOError:
                continue
            except OSError as exc:
                _log_recovery(f"orphaned task lock path={lock_file}", f"remove failed: {exc}")


def self_heal() -> None:
    if not _auto_recover_enabled():
        return

    now = time.time()
    try:
        _recover_stale_heartbeat(now)
        _recover_crash_loop(now)
        _remove_orphaned_task_locks(now)
    except Exception as exc:
        _log_recovery("self_heal exception", f"failed: {exc}")


def _background_dependency_dirs() -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = [("feeds", FEEDS_DIR)]
    try:
        if FEEDS_DIR.exists():
            for path in sorted(FEEDS_DIR.rglob("*")):
                if path.is_dir():
                    sources.append((f"feeds/{path.relative_to(FEEDS_DIR)}", path))
    except OSError as exc:
        log.debug("Background dependency feed scan failed: %s", exc)

    sources.extend(
        [
            ("icloud_bridge_inbox", MIRA_DIR / "inbox"),
            ("notes_bridge_outbox", MIRA_DIR / "outbox"),
        ]
    )
    return sources


def check_background_dependencies() -> list[dict]:
    now = datetime.now()
    if now.hour >= 23 or now.hour < 7:
        return []

    try:
        from mira import BACKGROUND_STALENESS_THRESHOLD_HOURS
    except Exception:
        BACKGROUND_STALENESS_THRESHOLD_HOURS = 4

    try:
        threshold_hours = float(BACKGROUND_STALENESS_THRESHOLD_HOURS)
    except (TypeError, ValueError):
        threshold_hours = 4

    threshold_seconds = threshold_hours * 3600
    now_ts = time.time()
    stale_entries: list[dict] = []

    for source_name, path in _background_dependency_dirs():
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            log.debug("Background dependency stat failed for %s: %s", path, exc)
            continue

        age_seconds = now_ts - mtime
        if age_seconds <= threshold_seconds:
            continue

        stale_entries.append(
            {
                "source_name": source_name,
                "last_seen": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
                "hours_stale": round(age_seconds / 3600, 2),
                "severity": "warning",
            }
        )

    if stale_entries:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(BACKGROUND_HEALTH_LOG, "a", encoding="utf-8") as f:
                for entry in stale_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.debug("Background health log write failed: %s", exc)

    return stale_entries


def _should_recalibrate_proxies() -> bool:
    now = datetime.now()
    if now.hour < 10 or now.hour >= 18:
        return False

    state = load_state()
    last = state.get("last_recalibrate_proxies", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.year == now.year and last_dt.month == now.month:
                return False
        except ValueError:
            pass

    return True


def _should_guard_calibration_prompt() -> bool:
    now = datetime.now()
    if now.hour < 10 or now.hour >= 18:
        return False

    state = load_state()
    last = state.get("last_guard_calibration_prompt", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 7 * 24 * 3600:
                return False
        except ValueError:
            pass

    return True


def _should_proxy_drift_check() -> bool:
    now = datetime.now()
    if now.hour < 10 or now.hour >= 18:
        return False

    state = load_state()
    last = state.get("last_proxy_drift_check", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < 7 * 24 * 3600:
                return False
        except ValueError:
            pass

    return True


def _should_calibrate_proxies() -> bool:
    now = datetime.now()
    if now.weekday() != 6 or now.hour < 10 or now.hour >= 18:
        return False

    state = load_state()
    last = state.get("last_calibrate_proxies", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (now - last_dt).total_seconds() < CALIBRATION_INTERVAL_DAYS * 24 * 3600:
                return False
        except ValueError:
            pass

    return True


def _register_core_scheduled_jobs() -> None:
    from runtime import triggers
    from runtime.jobs import BACKGROUND_JOBS, JobSpec

    triggers.should_recalibrate_proxies = _should_recalibrate_proxies
    triggers.should_guard_calibration_prompt = _should_guard_calibration_prompt
    triggers.should_proxy_drift_check = _should_proxy_drift_check
    triggers.should_calibrate_proxies = _should_calibrate_proxies

    if not any(job.name == "recalibrate_proxies" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="recalibrate_proxies",
                command=["recalibrate-proxies"],
                trigger="cooldown",
                trigger_name="should_recalibrate_proxies",
                cooldown_hours=24 * 30,
                state_key_pattern="last_recalibrate_proxies",
                priority=45,
                blocking_group="light",
                description="Monthly human voice-authenticity recalibration for published articles",
            )
        )

    if not any(job.name == "guard_calibration_prompt" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="guard_calibration_prompt",
                command=["guard-calibration-prompt"],
                trigger="cooldown",
                trigger_name="should_guard_calibration_prompt",
                cooldown_hours=24 * 7,
                state_key_pattern="last_guard_calibration_prompt",
                priority=46,
                blocking_group="light",
                description="Weekly human calibration prompt for Substack guard decisions",
            )
        )

    if not any(job.name == "proxy_drift_check" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="proxy_drift_check",
                command=["proxy-drift-check"],
                trigger="cooldown",
                trigger_name="should_proxy_drift_check",
                cooldown_hours=24 * 7,
                state_key_pattern="last_proxy_drift_check",
                priority=47,
                blocking_group="light",
                description="Weekly evaluator proxy-drift check for published articles",
            )
        )

    if not any(job.name == "calibrate_proxies" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="calibrate_proxies",
                command=["calibrate-proxies"],
                trigger="cooldown",
                trigger_name="should_calibrate_proxies",
                cooldown_hours=24 * CALIBRATION_INTERVAL_DAYS,
                state_key_pattern="last_calibrate_proxies",
                priority=44,
                blocking_group="light",
                description="Weekly human quality calibration for guarded writing outputs",
            )
        )


def _parse_substack_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _fetch_last_month_substack_articles() -> list[dict]:
    from substack import _get_substack_config, get_recent_posts

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=31)
    articles = []

    for post in get_recent_posts(limit=50):
        post_date = _parse_substack_date(post.get("post_date") or post.get("published_at"))
        if not post_date or post_date < cutoff:
            continue
        slug = post.get("slug", "")
        url = post.get("url") or post.get("canonical_url") or ""
        if not url and subdomain and slug:
            url = f"https://{subdomain}.substack.com/p/{slug}"
        articles.append(
            {
                "id": post.get("id"),
                "title": post.get("title", "Untitled"),
                "slug": slug,
                "url": url,
                "post_date": post_date.isoformat(),
            }
        )

    return articles


def _append_recalibration_log(record: dict) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        for filename in ("recalibrate_proxies.jsonl", "guard_vigilance.jsonl"):
            with open(LOGS_DIR / filename, "a", encoding="utf-8") as _rf:
                _rf.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("Recalibration log write failed: %s", exc)


def _extract_recalibration_rating(text: str) -> int | None:
    import re

    match = re.search(r"\b([1-5])(?:\s*/\s*5)?\b", text)
    return int(match.group(1)) if match else None


def _log_recalibration_responses(user_id: str = "ang") -> None:
    items_dir = MIRA_DIR / "users" / user_id / "items"
    if not items_dir.exists():
        return

    seen_path = LOGS_DIR / ".recalibrate_proxies_seen.json"
    try:
        seen = set(json.loads(seen_path.read_text(encoding="utf-8"))) if seen_path.exists() else set()
    except (json.JSONDecodeError, OSError):
        seen = set()

    changed = False
    for path in sorted(items_dir.glob("recalibrate_proxies_*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        article = item.get("recalibration_article", {})
        for msg in item.get("messages", []):
            sender = str(msg.get("sender") or "")
            content = str(msg.get("content") or "").strip()
            if not content or sender == "agent":
                continue
            msg_id = str(msg.get("id") or f"{item.get('id')}:{msg.get('timestamp')}")
            key = f"{item.get('id')}:{msg_id}"
            if key in seen:
                continue
            _append_recalibration_log(
                {
                    "event": "recalibration_response",
                    "item_id": item.get("id"),
                    "article": article,
                    "sender": sender,
                    "rating": _extract_recalibration_rating(content),
                    "response": content,
                }
            )
            seen.add(key)
            changed = True

    if changed:
        try:
            seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.debug("Recalibration seen write failed: %s", exc)


def do_recalibrate_proxies(user_id: str = "ang") -> bool:
    import random

    _log_recalibration_responses(user_id=user_id)
    articles = _fetch_last_month_substack_articles()
    if not articles:
        log.info("Recalibrate proxies: no Substack articles published in the last month")
        return False

    article = random.choice(articles)
    prompt = (
        "Monthly voice recalibration.\n\n"
        f"Article: {article['title']}\n"
        f"Published: {article['post_date'][:10]}\n"
        f"URL: {article.get('url') or '(no URL found)'}\n\n"
        "Rate this article 1-5 on how well it captures your voice and intent. Any drift?"
    )

    bridge = Mira(MIRA_DIR, user_id=user_id)
    now = datetime.now()
    item_id = f"recalibrate_proxies_{now.strftime('%Y%m')}"
    if bridge.item_exists(item_id):
        log.info("Recalibrate proxies: prompt already exists for %s", now.strftime("%Y-%m"))
        return False

    item = bridge.create_discussion(
        item_id,
        f"Monthly voice recalibration {now.strftime('%Y-%m')}",
        prompt,
        sender="agent",
        tags=["mira", "guard", "recalibration", "substack", "voice"],
    )
    item["recalibration_article"] = article
    bridge._write_item(item)
    bridge._update_manifest(item)

    _append_recalibration_log(
        {
            "event": "recalibration_prompt_posted",
            "item_id": item_id,
            "article": article,
            "prompt": prompt,
        }
    )
    log.info("Recalibrate proxies prompt posted for article: %s", article["title"])
    return True


def do_guard_calibration_prompt(user_id: str = "ang") -> bool:
    from calibration import CALIBRATION_PROMPT_SAMPLE_SIZE, send_guard_calibration_prompt

    posted = send_guard_calibration_prompt(user_id=user_id, sample_size=CALIBRATION_PROMPT_SAMPLE_SIZE)
    state = load_state()
    state["last_guard_calibration_prompt"] = datetime.now().isoformat()
    save_state(state)
    return posted


def do_proxy_drift_check() -> int:
    import evaluator

    flagged = evaluator.detect_proxy_drift()
    return len(flagged)


def _load_anti_ai_scanner():
    import importlib.util

    scanner_path = MIRA_ROOT / "agents" / "writer" / "handler.py"
    spec = importlib.util.spec_from_file_location("_mira_writer_handler_calibration", scanner_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        log.debug("Anti-AI scanner load failed: %s", exc)
        return None
    return getattr(module, "scan_anti_ai_patterns", None)


def _anti_ai_passed(text: str, scanner) -> bool:
    if scanner is None:
        return False
    try:
        scan = scanner(text)
        return float(scan.get("score", 0) or 0) <= float(scan.get("threshold", 0) or 0)
    except Exception as exc:
        log.debug("Anti-AI scan failed: %s", exc)
        return False


def _verification_checks_passed(result: dict) -> bool:
    if not result.get("outcome_verified"):
        return False
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    if verification.get("passed") is False:
        return False
    checks = verification.get("checks") or result.get("checks") or []
    for check in checks:
        if isinstance(check, dict) and check.get("passed") is False:
            return False
    return True


def _is_writing_calibration_candidate(result: dict, path: Path) -> bool:
    tags = " ".join(str(tag).lower() for tag in result.get("tags", []) if isinstance(tag, str))
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    haystack = " ".join(
        [
            tags,
            str(result.get("agent") or "").lower(),
            str(result.get("task_type") or "").lower(),
            str(verification.get("artifact_type") or "").lower(),
            str(path).lower(),
        ]
    )
    if any(token in haystack for token in ("writer", "writing", "substack", "publish", "article", "essay")):
        return True
    for root in (WRITINGS_OUTPUT_DIR, WRITINGS_DIR):
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _read_calibration_artifact(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _recent_guarded_writing_artifacts(sample_size: int) -> list[dict]:
    scanner = _load_anti_ai_scanner()
    result_paths: list[tuple[float, Path]] = []
    try:
        for path in TASKS_DIR.rglob("result.json"):
            try:
                result_paths.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError as exc:
        log.debug("Calibration result scan failed: %s", exc)
        return []

    samples: list[dict] = []
    seen_paths: set[str] = set()
    for _, result_path in sorted(result_paths, key=lambda item: item[0], reverse=True):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(result, dict) or not _verification_checks_passed(result):
            continue
        for artifact in result.get("artifacts_produced", []) or []:
            if not isinstance(artifact, dict) or artifact.get("type") != "file":
                continue
            artifact_path = Path(str(artifact.get("path") or ""))
            if not artifact_path.is_absolute():
                artifact_path = result_path.parent / artifact_path
            if artifact_path.suffix.lower() not in {"", ".md", ".txt"}:
                continue
            key = str(artifact_path)
            if key in seen_paths or not artifact_path.exists():
                continue
            text = _read_calibration_artifact(artifact_path)
            if len(text.strip()) < 200:
                continue
            if not _is_writing_calibration_candidate(result, artifact_path):
                continue
            if not _anti_ai_passed(text, scanner):
                continue
            seen_paths.add(key)
            samples.append(
                {
                    "task_id": result.get("task_id") or result_path.parent.name,
                    "title": result.get("title") or artifact_path.stem,
                    "path": str(artifact_path),
                    "modified": datetime.fromtimestamp(artifact_path.stat().st_mtime).isoformat(),
                    "excerpt": " ".join(text.split())[:700],
                }
            )
            break
        if len(samples) >= sample_size:
            break
    return samples


def _format_proxy_calibration_message(samples: list[dict]) -> str:
    lines = [
        "Weekly proxy calibration.",
        "",
        "These recent writing outputs passed the automated guards and anti-AI scan. Please rate each 1-5 for actual quality.",
        "",
    ]
    for index, sample in enumerate(samples, start=1):
        lines.extend(
            [
                f"{index}. {sample['title']}",
                f"Artifact: {sample['path']}",
                f"Excerpt: {sample['excerpt']}",
                f"Rating {index}: _/5",
                "",
            ]
        )
    lines.append("Reply inline, for example: 1: 4, 2: 3, 3: 5, 4: 2.")
    return "\n".join(lines)


def _append_proxy_calibration_log(record: dict) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "calibrate_proxies.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("Proxy calibration log write failed: %s", exc)


def _extract_proxy_calibration_ratings(text: str, sample_count: int) -> list[int]:
    import re

    ratings: list[int] = []
    pattern = r"(?:^|[\s,;])(?:[1-9]\d*)\s*[:.)-]\s*([1-5])(?:\s*/\s*5)?\b"
    for match in re.finditer(pattern, text):
        ratings.append(int(match.group(1)))
    if not ratings and sample_count == 1:
        match = re.search(r"\b([1-5])(?:\s*/\s*5)?\b", text)
        if match:
            ratings.append(int(match.group(1)))
    return ratings


def _ensure_guard_review_todo(bridge, average: float, item_id: str) -> None:
    title = "Review guard rules after low proxy calibration ratings"
    for todo in bridge.load_todos():
        if todo.get("title") == title and todo.get("status") in {"pending", "working"}:
            return
    todo = bridge.add_todo(title, priority="high", tags=["mira", "guard", "calibration"])
    bridge.add_followup(
        todo["id"],
        f"Average human quality rating was {average:.2f}/5 on {item_id}. Review content guard rules and anti-AI checks for proxy drift.",
        source="agent",
    )


def _record_proxy_calibration_responses(user_id: str = "ang") -> list[int]:
    items_dir = MIRA_DIR / "users" / user_id / "items"
    if not items_dir.exists():
        return []

    seen_path = LOGS_DIR / ".calibrate_proxies_seen.json"
    try:
        seen = set(json.loads(seen_path.read_text(encoding="utf-8"))) if seen_path.exists() else set()
    except (json.JSONDecodeError, OSError):
        seen = set()

    ratings: list[int] = []
    changed = False
    bridge = Mira(MIRA_DIR, user_id=user_id)
    for path in sorted(items_dir.glob("calibrate_proxies_*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sample_count = len(item.get("calibration_samples") or [])
        for msg in item.get("messages", []):
            sender = str(msg.get("sender") or "")
            content = str(msg.get("content") or "").strip()
            if not content or sender == "agent":
                continue
            msg_id = str(msg.get("id") or f"{item.get('id')}:{msg.get('timestamp')}")
            key = f"{item.get('id')}:{msg_id}"
            if key in seen:
                continue
            parsed = _extract_proxy_calibration_ratings(content, sample_count)
            _append_proxy_calibration_log(
                {
                    "event": "calibration_response",
                    "item_id": item.get("id"),
                    "sender": sender,
                    "ratings": parsed,
                    "response": content,
                }
            )
            ratings.extend(parsed)
            seen.add(key)
            changed = True

    if changed:
        try:
            seen_path.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.debug("Proxy calibration seen write failed: %s", exc)
    if ratings:
        average = sum(ratings) / len(ratings)
        if average < 3.0:
            _ensure_guard_review_todo(bridge, average, "calibrate_proxies")
    return ratings


def calibrate_proxies(user_id: str = "ang") -> bool:
    _record_proxy_calibration_responses(user_id=user_id)
    samples = _recent_guarded_writing_artifacts(max(1, int(CALIBRATION_SAMPLE_SIZE)))
    if not samples:
        log.info("Proxy calibration: no guarded writing artifacts found")
        return False

    now = datetime.now()
    item_id = f"calibrate_proxies_{now.strftime('%G_W%V')}"
    bridge = Mira(MIRA_DIR, user_id=user_id)
    if bridge.item_exists(item_id):
        log.info("Proxy calibration prompt already exists for %s", now.strftime("%G-W%V"))
        return False

    item = bridge.create_discussion(
        item_id,
        f"Weekly proxy calibration {now.strftime('%G-W%V')}",
        _format_proxy_calibration_message(samples),
        sender="agent",
        tags=["mira", "guard", "calibration", "proxy-drift", "writing"],
    )
    item["calibration_samples"] = samples
    bridge._write_item(item)
    bridge._update_manifest(item)

    _append_proxy_calibration_log(
        {
            "event": "calibration_prompt_posted",
            "item_id": item_id,
            "sample_count": len(samples),
            "samples": samples,
        }
    )
    state = load_state()
    state["last_calibrate_proxies"] = now.isoformat()
    save_state(state)
    log.info("Proxy calibration prompt posted with %d sample(s)", len(samples))
    return True


def cmd_run():
    """Full cycle: talk -> respond -> dispatch background work.

    The super agent MUST stay fast (<10s). All long-running work
    (writing pipeline, explore, reflect) runs in background processes
    so heartbeat and Mira polling stay responsive.
    """
    import time as _time

    _cycle_start = _time.monotonic()
    _cycle_wall_start = datetime.now(timezone.utc)
    log.info("=== Mira Agent wake ===")
    _update_coattention(
        "full wake cycle: talk, health checks, pipeline maintenance, scheduled work",
        "Annotate this entry with anything Mira should notice during this cycle.",
    )
    _record_stale_heartbeat_mourning(time.time())

    self_heal()
    _check_invisible_dependencies()
    _run_auth_health_if_due()
    check_background_dependencies()

    try:
        _sf_path = LOGS_DIR / "security_flags.jsonl"
        if _sf_path.exists():
            _sf_lines = _sf_path.read_text(encoding="utf-8").splitlines()[-20:]
            _flagged_sources: set[str] = set()
            for _sf_line in _sf_lines:
                try:
                    _sf_rec = json.loads(_sf_line)
                    _sf_ts = datetime.fromisoformat(_sf_rec["timestamp"])
                    if _sf_ts >= _cycle_wall_start:
                        _flagged_sources.add(_sf_rec.get("skill_name", "unknown"))
                        log.warning(
                            "SECURITY_FLAG agent=%s skill=%s reason=%s",
                            _sf_rec.get("agent_id", "unknown"),
                            _sf_rec.get("skill_name", "unknown"),
                            _sf_rec.get("block_reason"),
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            if _flagged_sources:
                log.warning(
                    "SECURITY_FLAG_SUMMARY %d new block event(s) this cycle: %s",
                    len(_flagged_sources),
                    sorted(_flagged_sources),
                )
    except OSError as _sfe:
        log.debug("security_flags read failed: %s", _sfe)

    _stale_components = _check_stale_pipelines()
    if _stale_components:
        try:
            _stale_path = LOGS_DIR / "pipeline_stale.json"
            _stale_path.write_text(
                json.dumps({"stale": _stale_components, "checked_at": time.time()}),
                encoding="utf-8",
            )
        except Exception as _se:
            log.debug("pipeline_stale write failed: %s", _se)

    try:
        from notes_bridge import check_bridge_staleness

        is_stale, age_minutes = check_bridge_staleness(
            MIRA_DIR,
            threshold_minutes=BRIDGE_STALENESS_THRESHOLD_MINUTES,
        )
        if is_stale:
            log.warning(
                f"INFRA_BLIND_SPOT: iCloud bridge last updated {age_minutes:.1f}m ago — iPhone sync may be stalled"
            )
    except Exception as _bse:
        log.debug("Bridge staleness check failed: %s", _bse)

    try:
        _check_review_trust_inflation()
    except Exception as _tie:
        log.debug("review trust inflation check failed: %s", _tie)

    # Load session context from previous cycles
    _session_ctx = load_session_context()
    _session_new = []  # entries from this cycle
    _phase_times: dict[str, int] = {}
    _model_wait_ms = 0  # blocking model calls in this cycle (bg dispatches don't block)

    # Safety net: ensure today's journal/zhesi are visible to iOS
    _t0 = _time.monotonic()
    try:
        _sync_journals_to_briefings()
    except Exception as e:
        log.error("Journal sync check failed: %s", e)
    _phase_times["sync_journals"] = round((_time.monotonic() - _t0) * 1000)

    # Log pickup latency for pending iPhone command files before processing
    _bridge_commands_root = MIRA_DIR / "users"
    if _bridge_commands_root.is_dir():
        _pickup_now = datetime.now()
        for _user_cmd_dir in _bridge_commands_root.iterdir():
            _cmds_dir = _user_cmd_dir / "commands"
            if not _cmds_dir.is_dir():
                continue
            for _msg_file in _cmds_dir.glob("*.json"):
                if _msg_file.name.endswith(".tmp"):
                    continue
                try:
                    _mtime = _msg_file.stat().st_mtime
                    _latency_ms = round((_pickup_now - datetime.fromtimestamp(_mtime)).total_seconds() * 1000)
                    if _latency_ms >= IPHONE_BRIDGE_WARN_LATENCY_MS:
                        log.warning(
                            "iphone_msg_pickup_latency_ms=%d file=%s",
                            _latency_ms,
                            _msg_file.name,
                        )
                    else:
                        log.info(
                            "iphone_msg_pickup_latency_ms=%d file=%s",
                            _latency_ms,
                            _msg_file.name,
                        )
                except OSError:
                    pass

    # Mira first (lightweight, fast) — CRITICAL PATH
    _t0 = _time.monotonic()
    _llm_t0 = time.perf_counter()
    _talk_ok = True
    log_authorization_event("talk", "iphone_bridge", "high", bypassed_check=False)
    try:
        do_talk()
    except Exception as e:
        log.error("Mira failed: %s", e)
        _talk_ok = False
    _talk_llm_ms = round((time.perf_counter() - _llm_t0) * 1000)
    _model_wait_ms += _talk_llm_ms
    _talk_dur = _time.monotonic() - _t0
    _phase_times["talk"] = round(_talk_dur * 1000)
    _record_perf_stat("talk", "talk", _talk_dur, _talk_ok)

    try:
        _log_recalibration_responses()
    except Exception as e:
        log.debug("Recalibration response logging failed: %s", e)

    try:
        _record_proxy_calibration_responses()
    except Exception as e:
        log.debug("Proxy calibration response logging failed: %s", e)

    try:
        from mira import update_interface_latency as _update_iface_lat

        _iface_lat_avg = _update_iface_lat(_phase_times["talk"])
        _hb_path = MIRA_DIR / "heartbeat.json"
        if _hb_path.exists():
            _hb_data = json.loads(_hb_path.read_text(encoding="utf-8"))
            _hb_data["interface_latency_ms"] = _iface_lat_avg
            _hb_tmp = _hb_path.with_suffix(".tmp")
            _hb_tmp.write_text(json.dumps(_hb_data), encoding="utf-8")
            _hb_tmp.rename(_hb_path)
    except Exception as _ile:
        log.debug("interface_latency write failed: %s", _ile)

    if should_shutdown():
        log.info("Shutdown requested — exiting after talk phase")
        return

    # Timing guard: skip non-critical checks if cycle already > 8s
    _elapsed = _time.monotonic() - _cycle_start
    if _elapsed < 8:
        # Auto-advance writing projects stuck in plan_ready (no more Notes approval)
        _t0 = _time.monotonic()
        _write_ok = True
        try:
            with _sub_agent_pipeline_context("writer"):
                _run_canonical_writing_pipeline()
        except Exception as e:
            log.error("Writing response check failed: %s", e)
            _write_ok = False
        _write_dur = _time.monotonic() - _t0
        _phase_times["writing_responses"] = round(_write_dur * 1000)
        _record_perf_stat("writer", "writing_pipeline", _write_dur, _write_ok)
        _write_last_output("writer")

        # Sync Mira's own status + read all app feeds
        _t0 = _time.monotonic()
        try:
            from tools.app_feeds import read_app_feeds, sync_mira_status

            sync_mira_status()
            feeds = read_app_feeds()
            if feeds:
                log.info("App feeds: %s", ", ".join(f["app"] for f in feeds))
        except Exception as e:
            log.warning("App feed sync/read failed: %s", e)
        _phase_times["app_feeds"] = round((_time.monotonic() - _t0) * 1000)
    else:
        log.info("Cycle > 8s (%.1fs), deferring non-critical checks", _elapsed)

    # --- Harvest background process outcomes & check health ---
    _t0 = _time.monotonic()
    _completed_bg: list[str] = []
    try:
        _completed_bg = health_monitor.harvest_all() or []
        health_monitor.check_anomalies()
    except Exception as e:
        log.error("Health monitor failed: %s", e)
    _phase_times["health"] = round((_time.monotonic() - _t0) * 1000)

    # --- Pipeline chaining: trigger follow-up jobs for completed ones ---
    if _completed_bg:
        _t0 = _time.monotonic()
        _dispatch_pipeline_followups(_completed_bg, _session_new)
        update_joint_attention(_joint_attention_topic_from_completed_background(_completed_bg))
        _phase_times["pipeline_chain"] = round((_time.monotonic() - _t0) * 1000)

    # Reap stale PID files (hourly) — prevents stuck tasks
    _t0 = _time.monotonic()
    _reap_stale_pids()
    _phase_times["reap_pids"] = round((_time.monotonic() - _t0) * 1000)

    # --- Publishing pipeline: publish -> podcast -> sweep ---
    _t0 = _time.monotonic()
    log_authorization_event("pending_publish", "internal", "normal", bypassed_check=False)
    _check_pending_publish()
    _check_pending_podcast()
    _sweep_publish_pipeline()
    _phase_times["pending_publish"] = round((_time.monotonic() - _t0) * 1000)

    # --- All heavy work below runs through the declarative scheduler ---
    _t0 = _time.monotonic()
    log_authorization_event("scheduled_jobs", "cron", "normal", bypassed_check=False)
    _register_core_scheduled_jobs()
    try:
        periodic_blind_spot_check()
    except Exception as e:
        log.debug("blind spot check failed: %s", e)
    _dispatch_scheduled_jobs(_session_new)

    # Weekly health report — Monday morning
    if _should_health_weekly_report():
        try:
            _run_health_weekly_report()
        except Exception as e:
            log.error("Health weekly report failed: %s", e)

    # Weekly skill security re-audit — slotted alongside reflect/memory consolidation
    _reaudit_now = datetime.now()
    if _reaudit_now.weekday() == 6:
        _reaudit_key = f"skill_reaudit_{_reaudit_now.strftime('%Y-W%W')}"
        _reaudit_state = load_state()
        if not _reaudit_state.get(_reaudit_key):
            _reaudit_state[_reaudit_key] = _reaudit_now.isoformat()
            save_state(_reaudit_state)
            try:
                from memory.soul_skills import reaudit_stale_skills

                reaudit_stale_skills()
            except Exception as e:
                log.error("Skill re-audit failed: %s", e)
            try:
                _unaudited = check_audit_coverage()
                if _unaudited:
                    log.warning(
                        "SKILL_AUDIT_COVERAGE: %d skill file(s) have no audit record: %s",
                        len(_unaudited),
                        ", ".join(_unaudited),
                    )
            except Exception as e:
                log.error("Skill audit coverage check failed: %s", e)

    _phase_times["dispatch"] = round((_time.monotonic() - _t0) * 1000)

    # -----------------------------------------------------------------------
    # Self-repair: retry critical daily tasks that failed or never completed
    # -----------------------------------------------------------------------
    _t0 = _time.monotonic()
    _self_repair_daily_tasks()
    _daily_task_status_report()
    _phase_times["self_repair"] = round((_time.monotonic() - _t0) * 1000)

    _t0 = _time.monotonic()
    _refresh_operator_dashboards()
    _phase_times["operator_dashboard"] = round((_time.monotonic() - _t0) * 1000)

    _t0 = _time.monotonic()
    try:
        _log_outcome_success_rates()
    except Exception as _oe:
        log.debug("Outcome rate logging failed: %s", _oe)
    _phase_times["outcome_rates"] = round((_time.monotonic() - _t0) * 1000)

    # Save session context for next cycle
    if _session_new:
        save_session_context(_session_ctx + _session_new)

    _cycle_ms = round((_time.monotonic() - _cycle_start) * 1000)
    _orch_ms = sum(_phase_times.values())
    log.info(
        "TIMING cycle=%ds orchestration=%dms model_wait=%dms phases=%s",
        round(_cycle_ms / 1000),
        _orch_ms,
        _model_wait_ms,
        json.dumps(_phase_times),
    )
    try:
        from config import TIMING_LOG

        with open(TIMING_LOG, "a", encoding="utf-8") as _tf:
            _tf.write(
                json.dumps(
                    {
                        "ts": datetime.now().isoformat(),
                        "cycle_ms": _cycle_ms,
                        "orchestration_ms": _orch_ms,
                        "model_wait_ms": _model_wait_ms,
                        "phases": _phase_times,
                    }
                )
                + "\n"
            )
    except Exception as _te:
        log.debug("Timing log write failed: %s", _te)

    try:
        _lt_log = LOGS_DIR / "llm_timing.jsonl"
        _lt_ts = datetime.now().isoformat()
        with open(_lt_log, "a", encoding="utf-8") as _ltf:
            _ltf.write(
                json.dumps(
                    {
                        "ts": _lt_ts,
                        "stage": "talk",
                        "llm_ms": _talk_llm_ms,
                        "orchestration_ms": _phase_times["talk"] - _talk_llm_ms,
                        "total_ms": _phase_times["talk"],
                    }
                )
                + "\n"
            )
            _ltf.write(
                json.dumps(
                    {
                        "ts": _lt_ts,
                        "stage": "cycle",
                        "llm_ms": _model_wait_ms,
                        "orchestration_ms": _orch_ms - _model_wait_ms,
                        "total_ms": _cycle_ms,
                    }
                )
                + "\n"
            )
    except Exception as _lte:
        log.debug("llm_timing log write failed: %s", _lte)

    try:
        _phase_log = LOGS_DIR / "task_phase_timing.jsonl"
        if _phase_log.exists():
            _phase_lines = _phase_log.read_text(encoding="utf-8").splitlines()[-50:]
            _pagg = {"dispatch_ms": 0, "inference_ms": 0, "tools_ms": 0, "total_ms": 0, "n": 0}
            for _pl in _phase_lines:
                try:
                    _pr = json.loads(_pl)
                    _pagg["dispatch_ms"] += _pr.get("phase_dispatch_ms", 0)
                    _pagg["inference_ms"] += _pr.get("phase_inference_ms", 0)
                    _pagg["tools_ms"] += _pr.get("phase_tools_ms", 0)
                    _pagg["total_ms"] += _pr.get("total_ms", 0)
                    _pagg["n"] += 1
                except (json.JSONDecodeError, KeyError):
                    continue
            if _pagg["n"]:
                log.info(
                    "PHASE_TOTALS tasks=%d dispatch_ms=%d inference_ms=%d tools_ms=%d total_ms=%d",
                    _pagg["n"],
                    _pagg["dispatch_ms"],
                    _pagg["inference_ms"],
                    _pagg["tools_ms"],
                    _pagg["total_ms"],
                )
    except Exception as _pae:
        log.debug("Phase totals logging failed: %s", _pae)
    log.info("=== Mira Agent sleep ===")


def _record_perf_stat(agent: str, task_type: str, duration_s: float, success: bool) -> None:
    """Append one perf entry to perf_stats.jsonl and warn if p90 exceeds threshold."""
    from config import CLAUDE_TIMEOUT_THINK, CLAUDE_TIMEOUT_ACT

    _AGENT_TIMEOUTS = {
        "talk": CLAUDE_TIMEOUT_THINK,
        "writer": CLAUDE_TIMEOUT_ACT,
    }
    configured_timeout = _AGENT_TIMEOUTS.get(agent, CLAUDE_TIMEOUT_ACT)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "task_type": task_type,
        "duration_s": round(duration_s, 2),
        "success": success,
    }
    try:
        PERF_STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PERF_STATS_FILE, "a", encoding="utf-8") as _pf:
            _pf.write(json.dumps(entry) + "\n")
    except OSError as _e:
        log.debug("perf_stats write failed: %s", _e)
        return

    try:
        lines = PERF_STATS_FILE.read_text(encoding="utf-8").splitlines()
        agent_durations: list[float] = []
        for _line in reversed(lines):
            if not _line.strip():
                continue
            try:
                _rec = json.loads(_line)
            except json.JSONDecodeError:
                continue
            if _rec.get("agent") == agent:
                agent_durations.append(float(_rec["duration_s"]))
                if len(agent_durations) >= 50:
                    break
        if len(agent_durations) >= 10:
            _sorted = sorted(agent_durations)
            p90 = _sorted[int(len(_sorted) * 0.9)]
            if p90 > configured_timeout * PERF_WARN_THRESHOLD:
                log.warning(
                    "PERF_DRIFT: %s p90=%.1fs threshold=%ds — consider raising timeout or reducing task scope",
                    agent,
                    p90,
                    configured_timeout,
                )
    except OSError as _e:
        log.debug("perf_stats read failed: %s", _e)


def _result_agent_type(result: dict) -> str:
    return str(result.get("agent_type") or result.get("agent") or "").strip()


def _result_outcome_verified(result: dict) -> bool | None:
    if "outcome_verified" in result:
        return bool(result.get("outcome_verified"))
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    if "verified" in verification:
        return bool(verification.get("verified"))
    return None


def _log_outcome_success_rates():
    """Compute and log per-agent-type outcome_success_rate over last 50 tasks."""
    _TRACKED_AGENTS = {"surfer", "socialmedia", "explorer"}
    results_by_agent: dict[str, list[bool]] = {}
    try:
        from task_manager import TASKS_DIR

        result_files = sorted(
            TASKS_DIR.rglob("result.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:300]
    except Exception as _e:
        log.debug("Outcome rate scan failed: %s", _e)
        return
    for rf in result_files:
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        agent = _result_agent_type(data)
        if agent not in _TRACKED_AGENTS:
            continue
        outcome_verified = _result_outcome_verified(data)
        if outcome_verified is None:
            continue
        results_by_agent.setdefault(agent, []).append(outcome_verified)
    for agent, outcomes in results_by_agent.items():
        window = outcomes[:50]
        if not window:
            continue
        rate = sum(window) / len(window)
        log.info(
            "OUTCOME_SUCCESS_RATE agent=%s rate=%.2f verified=%d total=%d",
            agent,
            rate,
            sum(window),
            len(window),
        )


def _read_last_outputs() -> dict:
    try:
        if LAST_OUTPUT_FILE.exists():
            return json.loads(LAST_OUTPUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_last_output(component: str) -> None:
    try:
        data = _read_last_outputs()
        data[component] = time.time()
        LAST_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _tmp = LAST_OUTPUT_FILE.with_suffix(".tmp")
        _tmp.write_text(json.dumps(data), encoding="utf-8")
        _tmp.rename(LAST_OUTPUT_FILE)
    except Exception as _e:
        log.debug("last_output write failed: %s", _e)


def _count_task_workspaces() -> int:
    if not TASKS_DIR.is_dir():
        return 0
    try:
        return sum(1 for path in TASKS_DIR.iterdir() if path.is_dir())
    except OSError:
        return 0


def _feed_fetch_snapshot() -> dict:
    path = FEEDS_DIR / "feed_stats.json"
    if not path.exists():
        return {"total_items": 0, "sample_count": 0, "latest_fetch": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"total_items": 0, "sample_count": 0, "latest_fetch": None}

    total_items = 0
    sample_count = 0
    latest_fetch = None
    for entries in data.values() if isinstance(data, dict) else []:
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                total_items += max(0, int(entry.get("item_count", 0)))
            except (TypeError, ValueError):
                pass
            sample_count += 1
            ts = _parse_recovery_timestamp(entry.get("timestamp"))
            if ts is not None:
                latest_fetch = ts if latest_fetch is None else max(latest_fetch, ts)
    return {"total_items": total_items, "sample_count": sample_count, "latest_fetch": latest_fetch}


def _latest_briefing_mtime() -> float | None:
    if not BRIEFINGS_DIR.is_dir():
        return None
    latest = None
    try:
        for path in BRIEFINGS_DIR.glob("*.md"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
    except OSError:
        return None
    return latest


def _blind_spot_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _blind_spot_warn(message: str, **details) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "WARNING",
        "message": message,
        **details,
    }
    try:
        with open(Path("/tmp/mira-blindspot.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        log.debug("blind spot warning write failed: %s", exc)
    log.warning("BLIND_SPOT %s", json.dumps(record, ensure_ascii=False, sort_keys=True))


def _scheduled_pipeline_blind_spots(now: float, outputs: dict) -> list[dict]:
    job_outputs = {
        "explore": "explorer",
        "writing-pipeline": "writer",
        "reflect": "reflect",
        "journal": "journal",
    }
    anomalies = []
    for job in get_jobs():
        component = job_outputs.get(job.name)
        if not component:
            continue
        user_ids = get_known_user_ids() if getattr(job, "per_user", False) else [None]
        for user_id in user_ids:
            try:
                payload = evaluate_job_payload(job, user_id=user_id)
            except Exception as exc:
                log.debug("blind spot trigger evaluation failed for %s: %s", job.name, exc)
                continue
            if not payload:
                continue
            try:
                last_output = float(outputs.get(component, 0) or 0)
            except (TypeError, ValueError):
                last_output = 0
            output_gap = now - last_output if last_output > 0 else None
            if output_gap is None or output_gap > 2 * 3600:
                anomalies.append(
                    {
                        "job": job.name,
                        "component": component,
                        "user_id": user_id,
                        "last_output": last_output or None,
                        "output_gap_seconds": int(output_gap) if output_gap is not None else None,
                    }
                )
    return anomalies


def periodic_blind_spot_check() -> None:
    state = load_state()
    now = time.time()
    last = _parse_recovery_timestamp(state.get("last_blind_spot_check")) or 0
    if now - last < 30 * 60:
        return

    state["last_blind_spot_check"] = datetime.now(timezone.utc).isoformat()
    task_workspace_count = _count_task_workspaces()
    previous_task_workspace_count = state.get("blind_spot_task_workspace_count")
    previous_task_count = _blind_spot_int(previous_task_workspace_count)
    heartbeat = MIRA_DIR / "heartbeat.json"
    heartbeat_updated_at = _heartbeat_updated_at(heartbeat) if heartbeat.exists() else None
    heartbeat_age = now - heartbeat_updated_at if heartbeat_updated_at is not None else None
    if (
        previous_task_workspace_count is not None
        and task_workspace_count > previous_task_count
        and (heartbeat_age is None or heartbeat_age > 300)
    ):
        _blind_spot_warn(
            "task workspaces increased while heartbeat was stale",
            task_workspace_count=task_workspace_count,
            previous_task_workspace_count=previous_task_count,
            heartbeat_age_seconds=int(heartbeat_age) if heartbeat_age is not None else None,
        )
    state["blind_spot_task_workspace_count"] = task_workspace_count

    fetch_snapshot = _feed_fetch_snapshot()
    previous_fetch_total = state.get("blind_spot_feed_total_items")
    previous_feed_total = _blind_spot_int(previous_fetch_total)
    latest_briefing = _latest_briefing_mtime()
    briefing_gap = now - latest_briefing if latest_briefing is not None else None
    if (
        previous_fetch_total is not None
        and fetch_snapshot["total_items"] > previous_feed_total
        and (briefing_gap is None or briefing_gap > 2 * 3600)
    ):
        _blind_spot_warn(
            "explorer fetch counts increased without a recent briefing",
            feed_total_items=fetch_snapshot["total_items"],
            previous_feed_total_items=previous_feed_total,
            feed_sample_count=fetch_snapshot["sample_count"],
            latest_fetch=fetch_snapshot["latest_fetch"],
            briefing_gap_seconds=int(briefing_gap) if briefing_gap is not None else None,
        )
    state["blind_spot_feed_total_items"] = fetch_snapshot["total_items"]

    for anomaly in _scheduled_pipeline_blind_spots(now, _read_last_outputs()):
        _blind_spot_warn(
            "scheduled pipeline trigger is active but output is absent or stale",
            **anomaly,
        )

    _check_task_distribution_blind_spots(state)

    save_state(state)


def _review_trust_inflation_threshold() -> int:
    try:
        from config import REVIEW_TRUST_INFLATION_THRESHOLD

        return max(1, int(REVIEW_TRUST_INFLATION_THRESHOLD))
    except (ImportError, AttributeError, TypeError, ValueError):
        return 8


def _recent_evaluator_reports(limit: int = 10) -> list[Path]:
    candidates: list[Path] = []
    for report_dir in (
        LOGS_DIR,
        MIRA_ROOT / "data" / "soul" / "scorecards",
        MIRA_ROOT / "lib" / "soul" / "scorecards",
    ):
        if not report_dir.exists():
            continue
        try:
            candidates.extend(report_dir.glob("*evaluator*.json"))
            candidates.extend(report_dir.glob("*scorecard*.json"))
            if report_dir.name == "scorecards":
                candidates.extend(report_dir.glob("*.json"))
        except OSError:
            continue
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def _has_review_issue_flag(value) -> bool:
    if isinstance(value, dict):
        return any(_has_review_issue_flag(v) for k, v in value.items() if str(k).endswith(("warning", "warnings")))
    if isinstance(value, list):
        return bool(value)
    return bool(value)


def _is_clean_evaluator_report(report: dict) -> bool:
    agents = report.get("agents", {})
    if not isinstance(agents, dict):
        return False

    scored_agents = [card for card in agents.values() if isinstance(card, dict) and card.get("task_count", 0) > 0]
    if not scored_agents:
        return False

    for card in scored_agents:
        if float(card.get("success_rate", 0) or 0) < 1.0:
            return False
        if int(card.get("failed", 0) or 0) > 0:
            return False
        if int(card.get("guard_fires", 0) or 0) > 0 or int(card.get("guard_fired_count", 0) or 0) > 0:
            return False
        scores = card.get("scores", {})
        if not isinstance(scores, dict) or float(scores.get("task_success", 0) or 0) < 1.0:
            return False
        if float(scores.get("guard_fire_rate", 0) or 0) > 0:
            return False

    super_scores = report.get("super", {}).get("scores", {})
    if isinstance(super_scores, dict):
        for key in ("crash_rate", "timeout_rate", "error_rate"):
            if float(super_scores.get(key, 0) or 0) > 0:
                return False
        for key in ("timeout_count", "error_count", "stuck_tasks"):
            if int(super_scores.get(key, 0) or 0) > 0:
                return False
        if super_scores.get("heartbeat_ok") is False:
            return False

    aggregate = report.get("aggregate", {})
    if isinstance(aggregate, dict):
        if int(aggregate.get("stale_score_count", 0) or 0) > 0:
            return False
        if int(aggregate.get("scaffolding_rejection_count", 0) or 0) > 0:
            return False
        if aggregate.get("low_confidence_agents"):
            return False

    if report.get("stale_skills") or report.get("marginalized_skills"):
        return False

    return not _has_review_issue_flag(report)


def _check_review_trust_inflation() -> None:
    reports = _recent_evaluator_reports(limit=10)
    if len(reports) < 10:
        return

    streak = 0
    for path in reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            break
        if not _is_clean_evaluator_report(report):
            break
        streak += 1

    threshold = _review_trust_inflation_threshold()
    if streak < threshold:
        return

    message = (
        f"Possible trust inflation detected — {streak} consecutive clean reviews. "
        "Review loops may be degrading into superficial coherence checks. Manual spot-check recommended."
    )
    warning = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "streak": streak,
        "threshold": threshold,
        "message": message,
    }
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "trust_inflation_warnings.log", "a", encoding="utf-8") as wf:
            wf.write(json.dumps(warning, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("trust inflation warning write failed: %s", exc)
    log.warning("REVIEW_TRUST_INFLATION streak=%d threshold=%d — %s", streak, threshold, message)


def _check_stale_pipelines() -> list[str]:
    _now = time.time()
    _data = _read_last_outputs()
    _stale: list[str] = []
    for _component, _threshold in STALE_THRESHOLDS.items():
        _last = _data.get(_component)
        if _last is None:
            continue
        _gap = _now - float(_last)
        if _gap > _threshold:
            log.warning(
                "%s has produced no output in %ds — possible silent marginalization",
                _component,
                int(_gap),
            )
            _stale.append(_component)
    return _stale


def _dispatch_distribution_snapshot() -> None:
    """Count past-7-day task dispatches per agent, compare to prior week, warn on drift."""
    from datetime import timedelta

    _DISPATCH_HISTORY = Path(__file__).parent / "dispatch_history.json"
    cutoff = (datetime.now() - timedelta(days=7)).timestamp()

    current: dict[str, int] = {}
    try:
        result_files = [p for p in TASKS_DIR.rglob("result.json") if p.stat().st_mtime >= cutoff]
        for rf in result_files:
            try:
                data = json.loads(rf.read_text(encoding="utf-8"))
                agent = str(data.get("agent", "")).strip()
                if agent:
                    current[agent] = current.get(agent, 0) + 1
            except (json.JSONDecodeError, OSError):
                continue
    except Exception as _e:
        log.debug("Dispatch snapshot scan failed: %s", _e)
        return

    if not current:
        return

    prior: dict[str, int] = {}
    try:
        if _DISPATCH_HISTORY.exists():
            prior = json.loads(_DISPATCH_HISTORY.read_text(encoding="utf-8")).get("counts", {})
    except (json.JSONDecodeError, OSError):
        pass

    if prior:
        prior_total = sum(prior.values())
        current_total = sum(current.values())
        drift_warnings: list[str] = []

        for agent, prior_count in prior.items():
            cur_count = current.get(agent, 0)
            prior_share = prior_count / prior_total if prior_total else 0
            cur_share = cur_count / current_total if current_total else 0

            if cur_count == 0:
                drift_warnings.append(f"Agent '{agent}' had {prior_count} dispatches last week but 0 this week.")
            elif prior_share > 0 and (prior_share - cur_share) / prior_share > 0.5:
                drift_warnings.append(
                    f"Agent '{agent}' share dropped from {prior_share:.1%} to {cur_share:.1%} "
                    f"({prior_count} → {cur_count} dispatches)."
                )

        for msg in drift_warnings:
            log.warning("DISPATCH_DRIFT: %s", msg)
            try:
                journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
                with open(journal_path, "a", encoding="utf-8") as _jf:
                    _jf.write(f"\n\n---\n\n**[WARNING] Dispatch Drift Detected**\n\n{msg}\n")
            except OSError as _je:
                log.debug("Journal dispatch warning write failed: %s", _je)

    try:
        _DISPATCH_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        _DISPATCH_HISTORY.write_text(
            json.dumps(
                {"counts": current, "recorded_at": datetime.now().isoformat()},
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as _e:
        log.debug("Dispatch history write failed: %s", _e)


def _append_task_latency_to_journal() -> None:
    cutoff = time.time() - 86400
    latencies: list[float] = []
    try:
        for meta_file in TASKS_DIR.rglob("metadata.json"):
            try:
                if meta_file.stat().st_mtime < cutoff:
                    continue
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                queued_at = data.get("queued_at")
                lat = data.get("latency_s")
                if lat is not None and queued_at is not None and float(queued_at) >= cutoff:
                    latencies.append(float(lat))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
    except Exception:
        return
    if not latencies:
        return
    s = sorted(latencies)
    n = len(s)

    def _pct(p):
        k = (n - 1) * p / 100
        lo, hi = int(k), min(int(k) + 1, n - 1)
        return s[lo] + (k - lo) * (s[hi] - s[lo])

    p50 = round(_pct(50))
    p95 = round(_pct(95))
    journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    try:
        with open(journal_path, "a", encoding="utf-8") as _jf:
            _jf.write(f"\n⏱ task latency p50/p95: {p50}s / {p95}s\n")
    except OSError:
        pass


def _append_joint_attention_landscape_to_journal(user_id: str = "ang", *, create_if_missing: bool = True) -> None:
    try:
        from soul.third_thing_tracker import ThirdThingRegistry
        from user_paths import user_journal_dir
    except Exception as e:
        log.debug("Third-thing tracker unavailable: %s", e)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    journal_path = user_journal_dir(user_id) / f"{today}.md"
    if not create_if_missing and not journal_path.exists():
        return

    marker = "<!-- joint-attention-landscape -->"
    try:
        existing = journal_path.read_text(encoding="utf-8") if journal_path.exists() else ""
    except OSError as e:
        log.debug("Joint attention journal read failed: %s", e)
        return
    if marker in existing:
        return

    try:
        registry = ThirdThingRegistry()
        living = registry.get_living_third_things()
        all_things = list(registry.things.values())
        for thing in all_things:
            registry.compute_convergence(thing.name)
    except Exception as e:
        log.debug("Joint attention landscape build failed: %s", e)
        return

    dormant = [thing for thing in all_things if thing.status == "dormant"]
    living_names = {thing.name for thing in living}
    other = [thing for thing in all_things if thing.name not in living_names and thing.status != "dormant"]

    lines = [marker, "## Joint attention landscape"]
    if living:
        lines.append("Alive:")
        lines.extend(_format_third_thing_line(thing) for thing in living[:8])
    else:
        lines.append("Alive: none currently registered.")

    if other:
        lines.append("Diverging:")
        lines.extend(_format_third_thing_line(thing) for thing in other[:5])

    if dormant:
        lines.append("Dormant:")
        lines.extend(_format_third_thing_line(thing) for thing in dormant[:5])

    if not all_things:
        lines.append("No registered third-things yet.")

    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as _jf:
            _jf.write("\n\n" + "\n".join(lines) + "\n")
    except OSError as e:
        log.debug("Joint attention journal write failed: %s", e)


def _format_third_thing_line(thing) -> str:
    return f"- {thing.name}: {thing.status}, convergence {thing.convergence_score:.2f}"


def _extract_joint_observation_focus(note: str) -> str:
    match = re.search(r"looking at together:\s*(.+?)\.", note, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return "the current joint observation from reflection"


def _send_joint_observation(user_id: str = "ang") -> None:
    try:
        note = generate_joint_observation(user_id=user_id)
        if not note:
            return
        update_joint_attention(_extract_joint_observation_focus(note))
        bridge = Mira(MIRA_DIR, user_id=user_id)
        item_id = f"joint_observation_{datetime.now().strftime('%Y%m%d')}"
        bridge.create_feed(
            item_id,
            "Joint Observation",
            note,
            tags=["reflection", "joint-attention", "co-reflection"],
        )
        log.info("Joint observation sent")
    except Exception as e:
        log.warning("Joint observation generation failed: %s", e)


def _refresh_operator_dashboards():
    """Persist operator dashboard snapshots for each configured user."""
    try:
        from operator_dashboard import write_operator_summary
    except Exception as _exc:
        log.warning("Operator dashboard unavailable: %s", _exc)
        return

    for user_id in get_known_user_ids():
        try:
            write_operator_summary(user_id=user_id)
        except Exception as _exc:
            log.warning("Operator dashboard refresh failed for %s: %s", user_id, _exc)


def _alert_soul_integrity_failures(failures: list[tuple[str, str]]) -> None:
    lines = "\n".join(f"- {filename}: {error}" for filename, error in failures)
    message = (
        "Mira startup integrity check failed. No pipelines were dispatched because "
        "background soul infrastructure is broken.\n\n"
        f"{lines}"
    )
    log.critical("Soul startup integrity check failed: %s", failures)

    if Mira is None:
        log.error("Cannot write soul integrity alert: bridge unavailable")
        return

    try:
        bridge = Mira(MIRA_DIR, user_id="ang")
        item_id = "soul_integrity_failure"
        title = "Mira Soul Integrity Failure"
        if bridge.item_exists(item_id):
            bridge.append_message(item_id, "agent", message)
            item = bridge._read_item(item_id)
        else:
            item = bridge.create_item(
                item_id,
                "alert",
                title,
                message,
                sender="agent",
                tags=["system", "soul", "integrity", "error"],
                origin="agent",
            )
        if item:
            item["type"] = "alert"
            item["title"] = title
            item["status"] = "failed"
            item["origin"] = "agent"
            item["pinned"] = True
            item["tags"] = list(dict.fromkeys(["system", "soul", "integrity", "error", *item.get("tags", [])]))
            item["error"] = {
                "code": "soul_integrity_failed",
                "message": lines,
                "retryable": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            bridge._write_item(item)
            bridge._update_manifest(item)
    except Exception as exc:
        log.error("Failed to write soul integrity alert: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    # Set up logging (human-readable console + file, plus JSON file for machine parsing)
    from log_config import setup_logging

    setup_logging(logs_dir=LOGS_DIR, json_logs=True)
    soul_failures = validate_soul_files()
    if soul_failures:
        _alert_soul_integrity_failures(soul_failures)
        return

    check_rules_integrity()

    # Prune old log files — once daily, gated by marker file
    import os as _prune_os

    _prune_marker = LOGS_DIR / ".last_prune"
    _prune_today = datetime.now().strftime("%Y-%m-%d")
    if not _prune_marker.exists() or _prune_marker.read_text(encoding="utf-8").strip() != _prune_today:
        _cutoff = time.time() - LOG_RETENTION_DAYS * 86400
        for _lf in LOGS_DIR.rglob("*"):
            if not _lf.is_file() or _lf.name.startswith("."):
                continue
            _rel = _lf.relative_to(LOGS_DIR)
            if any(p.startswith("_") for p in _rel.parts[:-1]):
                continue
            try:
                if _lf.stat().st_mtime < _cutoff:
                    _prune_os.remove(_lf)
            except OSError:
                pass
        try:
            _prune_marker.write_text(_prune_today, encoding="utf-8")
        except OSError:
            pass

    # Validate configuration — log errors but don't crash
    if not validate_config():
        log.warning("Config validation failed — some features may not work")

    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    _log_skill_depth_advisories(command)

    # Set usage agent context for token tracking
    from llm import set_usage_agent

    set_usage_agent(command if command != "run" else "super")

    # Parse optional flags
    args = sys.argv[2:]
    flags = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--") and i + 1 < len(args):
            flags[args[i][2:]] = args[i + 1]
            i += 2
        else:
            i += 1

    if command != "run" and command in _DOMAIN_TASK_COMMANDS:
        _update_coattention(
            f"{command} command",
            "Annotate this entry with what Mira should attend to, question, or connect.",
        )

    if command == "run":
        from locks.process import ProcessLockActive, launchagent_lock

        try:
            with launchagent_lock():
                cmd_run()
        except ProcessLockActive as exc:
            log.info("%s", exc)
            return
    elif command == "talk":
        do_talk()
    elif command == "explore":
        sources = flags.get("sources", "").split(",") if flags.get("sources") else None
        slot = flags.get("slot", "")
        do_explore(source_names=sources, slot_name=slot)
        update_joint_attention(
            f"explore briefing knowledge garden: {slot}" if slot else "explore briefing knowledge garden"
        )
        _write_last_output("explorer")
    elif command == "reflect":
        do_reflect(user_id=flags.get("user", "ang"))
        _send_joint_observation(user_id=flags.get("user", "ang"))
        _append_joint_attention_landscape_to_journal(user_id=flags.get("user", "ang"), create_if_missing=False)
        _dispatch_distribution_snapshot()
        _write_last_output("reflect")
    elif command == "journal":
        do_journal(user_id=flags.get("user", "ang"))
        update_joint_attention("today's journal as a knowledge-garden page")
        _append_joint_attention_landscape_to_journal(user_id=flags.get("user", "ang"))
        _append_task_latency_to_journal()
        _write_last_output("journal")
    elif command == "research-log":
        do_research_log(user_id=flags.get("user", "ang"))
        update_joint_attention("Mira's autonomous research-build loop")
    elif command == "research-cycle":
        with _sub_agent_pipeline_context("researcher"):
            do_research_cycle(user_id=flags.get("user", "ang"))
        update_joint_attention("Mira's autonomous research-build loop")
    elif command == "analyst":
        with _sub_agent_pipeline_context("analyst"):
            do_analyst(slot=flags.get("slot", ""))
    elif command == "research":
        with _sub_agent_pipeline_context("researcher"):
            do_research()
        update_joint_attention("Mira's autonomous research-build loop")
    elif command == "zhesi":
        do_zhesi(user_id=flags.get("user", "ang"))
    elif command == "soul-question":
        do_soul_question(user_id=flags.get("user", "ang"))
    elif command == "autowrite-check":
        do_autowrite_check()
    elif command == "autowrite-run":
        task_id = flags.get("task-id", f"autowrite_{datetime.now().strftime('%Y-%m-%d')}")
        title = flags.get("title", "Untitled")
        writing_type = flags.get("type", "essay")
        idea = flags.get("idea", "")
        run_autowrite_pipeline(task_id, title, writing_type, idea)
        update_joint_attention(f"writing project: {title}")
    elif command == "writing-pipeline":
        advanced = _run_canonical_writing_pipeline()
        log.info("Canonical writing pipeline advanced %d project(s)", advanced)
        if advanced:
            update_joint_attention("the active writing-project knowledge garden")
    elif command == "check-comments":
        do_check_comments()
    elif command == "growth-cycle":
        do_growth_cycle()
    elif command == "notes-cycle":
        do_notes_cycle()
    elif command == "recalibrate-proxies":
        do_recalibrate_proxies(user_id=flags.get("user", "ang"))
    elif command == "guard-calibration-prompt":
        do_guard_calibration_prompt(user_id=flags.get("user", "ang"))
    elif command == "proxy-drift-check":
        do_proxy_drift_check()
    elif command == "calibrate-proxies":
        calibrate_proxies(user_id=flags.get("user", "ang"))
    elif command == "spark-check":
        do_spark_check(user_id=flags.get("user", "ang"))
    elif command == "idle-think":
        do_idle_think(user_id=flags.get("user", "ang"))
    elif command == "daily-report":
        do_daily_report()
    elif command == "assess":
        do_assess()
    elif command == "self-improve":
        _run_self_improve()
    elif command == "self-evolve":
        from self_evolve import run_evolve

        run_evolve(dry_run="--dry-run" in sys.argv)
    elif command == "backlog-executor":
        from backlog_executor import run_once

        run_once(dry_run="--dry-run" in sys.argv)
    elif command == "restore-dry-run":
        from restore_drill import run_latest_restore_dry_run

        report = run_latest_restore_dry_run()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report.get("ok"):
            sys.exit(1)
    elif command == "podcast":
        lang = flags.get("lang", "zh")
        slug = flags.get("slug", "")
        title = flags.get("title", slug.replace("-", " ").title())
        run_podcast_episode(lang, slug, title)
    elif command == "book-review":
        do_book_review()
    elif command == "daily-photo":
        do_daily_photo()
    elif command == "growth-snapshot":
        from growth_snapshot import run_snapshot

        run_snapshot()
        _write_last_output("growth_snapshot")
    elif command == "skill-study":
        group_idx = int(flags.get("group", "0"))
        do_skill_study(group_idx=group_idx, user_id=flags.get("user", "ang"))
    elif command == "write-check":
        # List active writing projects
        responses = check_writing_responses()
        if responses:
            for r in responses:
                print(f"Active: {r['project']['title']} ({r['project']['phase']})")
        else:
            print("No active writing projects")
    elif command == "write-from-plan":
        if len(sys.argv) < 3:
            print(
                "Usage: core.py write-from-plan <path-to-plan.md> [--title title] [--type novel|essay|blog|technical|poetry]"
            )
            sys.exit(1)
        plan_path = sys.argv[2]
        title = flags.get("title", "")
        writing_type = flags.get("type", "novel")
        start_from_plan(title, plan_path, writing_type)
    else:
        print(
            f"Usage: {sys.argv[0]} [run|talk|respond|explore|reflect|journal|analyst|zhesi|skill-study|autowrite-check|autowrite-run|writing-pipeline|write-check|write-from-plan|spark-check]"
        )
        sys.exit(1)


def _send_crash_notification(error: str):
    """Record a crash to the user's archive (NOT the main inbox).

    Pre-2026-04-27: crashes wrote to items/, surfacing every timeout as a
    red 'Agent Crash' in the iOS home feed — 60+ accumulated. Crashes are
    diagnostic signals for me, not actionable items for WA. They live in
    archive/ now so I can still find them, but they don't pollute the feed.
    """
    try:
        import json, uuid
        from pathlib import Path
        from datetime import datetime, timezone as tz
        from config import MIRA_DIR

        archive_dir = MIRA_DIR / "users" / "ang" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        msg_id = uuid.uuid4().hex[:8]
        iso = datetime.now(tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        short_err = error[:500] if len(error) > 500 else error
        item = {
            "id": f"req_crash_{msg_id}",
            "type": "request",
            "title": "Agent Crash",
            "status": "archived",
            "tags": ["system", "crash"],
            "origin": "agent",
            "pinned": False,
            "quick": False,
            "parent_id": None,
            "created_at": iso,
            "updated_at": iso,
            "messages": [
                {
                    "id": msg_id,
                    "sender": "agent",
                    "content": f"Mira crashed.\n\n{short_err}",
                    "timestamp": iso,
                    "kind": "error",
                }
            ],
            "error": {"code": "crash", "message": short_err, "retryable": False, "timestamp": iso},
            "result_path": None,
        }
        path = archive_dir / f"req_crash_{msg_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.rename(path)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # Let sys.exit() propagate normally
    except Exception as _main_exc:
        import traceback

        tb = traceback.format_exc()
        # Log to file even if logging isn't set up
        try:
            crash_path = Path("/tmp/mira-crash.log")
            with open(crash_path, "a") as f:
                f.write(f"\n{'='*60}\n{datetime.now().isoformat()}\n{tb}\n")
        except Exception:
            pass
        # Try logging if available
        try:
            logging.critical("Unhandled exception in main():\n%s", tb)
        except Exception:
            pass
        # Notify user — but rate-limit to avoid notification spam
        # Only send if no crash notification in the last 10 minutes
        try:
            last_crash_file = Path("/tmp/mira-last-crash-notify")
            should_notify = True
            if last_crash_file.exists():
                age = time.time() - last_crash_file.stat().st_mtime
                should_notify = age > 600  # 10 minutes
            if should_notify:
                _send_crash_notification(str(_main_exc))
                last_crash_file.write_text(str(_main_exc))
        except Exception:
            pass
        sys.exit(1)
