#!/usr/bin/env python3
"""Mira Super Agent — orchestrator with soul, memory, and curiosity.

Modes:
    run     — full cycle: check inbox, maybe explore/reflect
    respond — process inbox requests only
    explore — fetch sources and write briefing
    reflect — weekly reflection and memory consolidation
"""
import json
import hashlib
import importlib
import importlib.util
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import choice, random

# Unified sys.path setup — see lib/pathsetup.py for the full list of package dirs
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
import pathsetup  # noqa: F401  (side-effect: registers all Mira package dirs)
import guard_integrity

logger = logging.getLogger("mira")
log = logger
AVAILABLE_MODULES = {}
DEGRADED_MODULES = {}
STARTUP_IMPORT_FAILED = False
STARTUP_IMPORT_ERROR_LOG = Path("/tmp/mira-startup-errors.log")
publish_blocked = False


class SystemAlert(RuntimeError):
    pass


def _resilient_import(module_path, name):
    try:
        mod = importlib.import_module(module_path)
        AVAILABLE_MODULES[name] = mod
        return mod
    except Exception as e:
        AVAILABLE_MODULES[name] = None
        DEGRADED_MODULES[name] = {
            "module_path": module_path,
            "error": f"{type(e).__name__}: {e}",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.error(f"[DEGRADED] Failed to import {name} from {module_path}: {e}")
        return None


def _degraded_module_payload() -> dict:
    return {
        name: {
            "module_path": detail["module_path"],
            "error": detail["error"],
            "recorded_at": detail["recorded_at"],
        }
        for name, detail in sorted(DEGRADED_MODULES.items())
    }


def _log_degraded_modules() -> None:
    for name, detail in sorted(DEGRADED_MODULES.items()):
        logger.error(
            "[DEGRADED] Failed to import %s from %s: %s",
            name,
            detail["module_path"],
            detail["error"],
        )


def _record_degraded_modules_in_heartbeat() -> None:
    if not DEGRADED_MODULES:
        return
    heartbeat = Path(MIRA_DIR) / "heartbeat.json"
    try:
        data = json.loads(heartbeat.read_text(encoding="utf-8")) if heartbeat.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["degraded_modules"] = _degraded_module_payload()
    try:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(heartbeat)
    except OSError as exc:
        logger.error("Failed to record degraded modules in heartbeat: %s", exc)


def _write_startup_import_heartbeat(message: str, status: str = "ok") -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    heartbeat = Path(MIRA_DIR) / "heartbeat.json"
    try:
        data = json.loads(heartbeat.read_text(encoding="utf-8")) if heartbeat.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["startup_import_status"] = {
        "status": status,
        "message": message,
        "timestamp": timestamp,
    }
    try:
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(heartbeat)
    except OSError as exc:
        logger.error("Failed to record startup import status in heartbeat: %s", exc)


def _record_startup_import_failure(module_name: str, traceback_text: str) -> bool:
    global STARTUP_IMPORT_FAILED

    STARTUP_IMPORT_FAILED = True
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with STARTUP_IMPORT_ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(
                f"\n{'=' * 60}\n" f"{timestamp}\n" f"startup import failure module={module_name}\n" f"{traceback_text}"
            )
            if not traceback_text.endswith("\n"):
                f.write("\n")
    except OSError as exc:
        logger.error("Failed to write startup import failure log: %s", exc)
    print(
        f"STARTUP IMPORT FAILURE: critical module '{module_name}' failed to import. " f"See {STARTUP_IMPORT_ERROR_LOG}",
        file=sys.stderr,
    )
    return False


def _validate_critical_imports() -> bool:
    global STARTUP_IMPORT_FAILED

    import traceback

    STARTUP_IMPORT_FAILED = False
    try:
        importlib.import_module("sub_agent")
    except ImportError:
        return _record_startup_import_failure("sub_agent", traceback.format_exc())
    try:
        importlib.import_module("soul_manager")
    except ImportError:
        return _record_startup_import_failure("soul_manager", traceback.format_exc())
    try:
        importlib.import_module("notes_bridge")
    except ImportError:
        return _record_startup_import_failure("notes_bridge", traceback.format_exc())
    try:
        importlib.import_module("config")
    except ImportError:
        return _record_startup_import_failure("config", traceback.format_exc())
    try:
        importlib.import_module("prompts")
    except ImportError:
        return _record_startup_import_failure("prompts", traceback.format_exc())
    try:
        importlib.import_module("mira")
    except ImportError:
        return _record_startup_import_failure("mira", traceback.format_exc())
    _write_startup_import_heartbeat("shared_modules_validated: all imports OK")
    return True


class _UnavailableFrictionMonitor:
    @staticmethod
    def track_friction(category: str, label: str | None = None):
        def decorator(fn):
            return fn

        return decorator


class _UnavailableSharedConfig:
    AGENT_REGISTRY = {}
    CROSS_VALIDATION_ENABLED = False
    CROSS_VALIDATION_SAMPLE_RATE = 0.2
    EVAL_BENCHMARK_ROTATION_DAYS = 30
    MAX_HARD_RULES = 7
    TIER_MODEL_MAP = {}
    TIMING_LOG_ENABLED = True


def _degraded_health_cascade() -> dict:
    return {
        "status": "degraded",
        "cascade_trace": [
            {
                "step": "health_cascade_import",
                "status": "degraded",
                "detail": "agents.shared.health_cascade unavailable",
            }
        ],
        "root_cause": "agents.shared.health_cascade unavailable",
    }


import config as mira_config
from config import (
    MIRA_ROOT,
    WORKSPACE_DIR,
    BRIEFINGS_DIR,
    LOGS_DIR,
    STATE_FILE,
    MIRA_DIR,
    ARTIFACTS_DIR,
    SKILLS_DIR,
    CLEANUP_DAYS,
    LOG_RETENTION_DAYS,
    JOURNAL_DIR,
    WRITINGS_OUTPUT_DIR,
    WRITINGS_DIR,
    PERF_STATS_FILE,
    PERF_WARN_THRESHOLD,
    FEEDS_DIR,
    IPHONE_BRIDGE_WARN_LATENCY_MS,
    BRIDGE_STALE_THRESHOLD,
    CALIBRATION_INTERVAL_DAYS,
    CALIBRATION_SAMPLE_SIZE,
    BLIND_SPOT_LOOKBACK_DAYS,
    BLIND_SPOT_SILENCE_THRESHOLD_DAYS,
    MAX_TASKS_PER_CYCLE,
    MAX_CONCURRENT_TASKS,
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

health_monitor = _resilient_import("health_monitor", "health_monitor")
soul_manager = _resilient_import("soul_manager", "soul_manager")
shared_config = _resilient_import("agents.shared.config", "shared_config") or _UnavailableSharedConfig
friction_monitor = (
    _resilient_import("agents.shared.friction_monitor", "friction_monitor") or _UnavailableFrictionMonitor()
)
_health_cascade_module = _resilient_import("agents.shared.health_cascade", "health_cascade")
health_cascade = (
    getattr(_health_cascade_module, "health_cascade", _degraded_health_cascade)
    if _health_cascade_module is not None
    else _degraded_health_cascade
)
_logging_util_module = _resilient_import("logging_util", "logging_util")
_notes_bridge_module = _resilient_import("notes_bridge", "notes_bridge")
_sub_agent_module = _resilient_import("sub_agent", "sub_agent")
EXPLORE_MAX_PENDING_TASKS = int(getattr(shared_config, "EXPLORE_MAX_PENDING_TASKS", 4))
LAST_OUTPUT_FILE = getattr(
    shared_config, "LAST_OUTPUT_FILE", getattr(mira_config, "LAST_OUTPUT_FILE", LOGS_DIR / "last_output.json")
)
MAX_UNDELIVERED_OUTPUTS = int(getattr(shared_config, "MAX_UNDELIVERED_OUTPUTS", 5))
STALE_THRESHOLDS = dict(getattr(shared_config, "STALE_THRESHOLDS", getattr(mira_config, "STALE_THRESHOLDS", {})))


def _soul_archive_sqlite_path() -> Path:
    archive_path = None
    for source in (shared_config, mira_config):
        for attr in ("archive_sqlite_path", "ARCHIVE_SQLITE_PATH"):
            archive_path = getattr(source, attr, None)
            if archive_path:
                break
        if archive_path:
            break
    if not archive_path:
        cfg = getattr(mira_config, "_cfg", {})
        if isinstance(cfg, dict):
            archive_path = cfg.get("archive_sqlite_path")
    if archive_path:
        path = Path(str(archive_path)).expanduser()
        if not path.is_absolute():
            path = MIRA_ROOT / path
        return path
    return MIRA_ROOT / "logs" / "soul_archive" / f"soul_archive_{datetime.now().strftime('%Y-%m-%d')}.sqlite"


def throttled_warning(target_log, *args, **kwargs):
    if _logging_util_module is not None and hasattr(_logging_util_module, "throttled_warning"):
        return _logging_util_module.throttled_warning(target_log, *args, **kwargs)
    kwargs.pop("key", None)
    return target_log.warning(*args, **kwargs)


def detect_vulnerability_disclosure(content):
    if _notes_bridge_module is not None and hasattr(_notes_bridge_module, "detect_vulnerability_disclosure"):
        return _notes_bridge_module.detect_vulnerability_disclosure(content)
    return False


DISPATCH_RECEIPT_NAME = getattr(_sub_agent_module, "DISPATCH_RECEIPT_NAME", "dispatch_receipt.json")


def append_pipeline_context_to_system_prompt(system_prompt: str, pipeline_context: dict | None = None) -> str:
    if _sub_agent_module is not None and hasattr(_sub_agent_module, "append_pipeline_context_to_system_prompt"):
        return _sub_agent_module.append_pipeline_context_to_system_prompt(system_prompt, pipeline_context)
    return system_prompt


def validate_local_model_native_tools(logger=None) -> None:
    if _sub_agent_module is not None and hasattr(_sub_agent_module, "validate_local_model_native_tools"):
        return _sub_agent_module.validate_local_model_native_tools(logger=logger)
    return None


def write_dispatch_receipt(*args, **kwargs) -> None:
    if _sub_agent_module is not None and hasattr(_sub_agent_module, "write_dispatch_receipt"):
        return _sub_agent_module.write_dispatch_receipt(*args, **kwargs)
    return None


try:
    from bridge import Mira, Message
except (ImportError, ModuleNotFoundError):
    Mira = None
    Message = None
from task_manager import TaskManager, TaskRecord, TASKS_DIR, classify_task, get_stuck_tasks, _resolve_workspace_dir
from memory.soul import load_soul, format_soul, append_memory, check_prompt_injection
from llm import claude_think
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
    do_daily_collab,
    do_daily_collab_review,
    do_daily_collab_operator_brief,
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
from soul.memory_exporter import export_memory_to_sqlite

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
import jobs as jobs_module

get_jobs = jobs_module.get_jobs
evaluate_job_payload = jobs_module.evaluate_job_payload
from execution.runtime_contract import derive_workflow_id, normalize_task_status


def _soul_manager_noop(*args, **kwargs):
    return None


def _soul_manager_empty_list(*args, **kwargs):
    return []


def _soul_manager_empty_dict(*args, **kwargs):
    return {}


def _soul_manager_false(*args, **kwargs):
    return False


def _soul_manager_unavailable_failures(*args, **kwargs):
    return [("soul_manager", "soul_manager import unavailable")]


def _soul_manager_unverified_provenance(*args, **kwargs):
    return ("unavailable", "unverified")


def _soul_manager_empty_friction_audit(*args, **kwargs):
    return {
        "passed": False,
        "registered": 0,
        "cognitive": [],
        "infrastructure": [],
        "elimination_candidates": [],
        "missing": [],
        "invalid": [],
    }


def _soul_manager_callable(name: str, fallback):
    if soul_manager is None:
        return fallback
    candidate = getattr(soul_manager, name, None)
    return candidate if callable(candidate) else fallback


log_authorization_event = _soul_manager_callable("log_authorization_event", _soul_manager_noop)
check_audit_coverage = _soul_manager_callable("check_audit_coverage", _soul_manager_empty_list)
check_rules_integrity = _soul_manager_callable("check_rules_integrity", _soul_manager_noop)
get_skill_provenance = _soul_manager_callable("get_skill_provenance", _soul_manager_unverified_provenance)
reaudit_all_skills = _soul_manager_callable("reaudit_all_skills", _soul_manager_empty_dict)
reaudit_all_enabled_skills = _soul_manager_callable("reaudit_all_enabled_skills", _soul_manager_noop)
validate_soul_files = _soul_manager_callable("validate_soul_files", _soul_manager_unavailable_failures)
verify_audit_integrity = _soul_manager_callable("verify_audit_integrity", _soul_manager_false)
audit_friction = _soul_manager_callable("audit_friction", _soul_manager_empty_friction_audit)
audit_all_skill_dependencies = _soul_manager_callable("audit_all_skill_dependencies", _soul_manager_empty_dict)

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
    triage_stalled_writing_projects,
)
from health import (
    _has_pending_health_exports,
    _run_health_check,
    _write_health_feed,
    _run_health_weekly_report,
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
AGENT_DEPS_FILE = _AGENTS_DIR / "shared" / "agent_deps.yaml"
SERVICE_DEPENDENCIES_FILE = _AGENTS_DIR / "shared" / "config" / "service_dependencies.yaml"
SERVICE_DEPENDENCY_LOG = LOGS_DIR / "service_dependencies.jsonl"
SECURITY_DRIFT_LOG = LOGS_DIR / "security_drift.log"
AUDIT_TRANSPARENCY_LOG = LOGS_DIR / "audit_transparency.jsonl"
SKILLS_META_FILE = _AGENTS_DIR / "shared" / "soul" / "skills_meta.json"
CANARY_SKILLS_DIR = _AGENTS_DIR / "shared" / "canary_skills"
CANARY_SKILL_AUDIT_ALERT_ID = "canary_skill_audit_failure"
CANARY_SELF_AUDIT_ALERT_ID = "canary_self_audit_failure"
SKILL_REAUDIT_ALERT_ID = "skill_reaudit_failure"
OPERATIONAL_AUDIT_FRICTION_STEPS: tuple[str, ...] = (
    "operational_audit.config_values",
    "operational_audit.soul_files",
    "operational_audit.notes_paths",
    "operational_audit.shared_imports",
    "operational_audit.content_integrity",
    "operational_audit.stuck_tasks",
    "operational_audit.network_connectivity",
    "operational_audit.dependency_health",
    "operational_audit.survival_components",
)
SERVICE_DEPENDENCY_TIMEOUT_SECONDS = 8
SERVICE_DEPENDENCY_MAX_WORKERS = 4
HEALTH_CASCADE_LOG = LOGS_DIR / "health_cascade.jsonl"
HEALTH_CASCADE_ALERT_ID = "mira_health_cascade_dead"
SKILL_DIGESTION_MINUTES = 60  # Minimum time between content ingestion and skill extraction.
MAX_AI_GENERATED_LINES_PER_SESSION = 500
MAX_AGENT_CODE_CHANGES_PER_DAY = 5
TASK_DISTRIBUTION_FILE = LOGS_DIR / "task_distribution.json"
CONTENT_QUALITY_LOG_PATH = MIRA_ROOT / "data" / "content_quality_log.jsonl"
DRIFT_ALERTS_LOG_PATH = MIRA_ROOT / "data" / "drift_alerts.log"
EVALUATOR_SCHEDULE_HOUR = 22
EVALUATOR_LAST_RUN_FILE = MIRA_ROOT / "data" / "evaluator_last_run"
EVALUATOR_LOG_DIR = LOGS_DIR / "evaluator"
WRITER_PROXY_REVIEW_LAST_FILE = MIRA_ROOT / "data" / "last_proxy_review.txt"
WRITER_ANTI_AI_CHECKLIST_FILE = MIRA_ROOT / "agents" / "writer" / "checklists" / "anti-ai.md"
ANTI_AI_PROXY_DRIFT_LOG_PATH = LOGS_DIR / "proxy_drift.json"
ANTI_AI_QUALITY_GUARD_ALERT_MESSAGE = (
    "Anti-AI quality guard has passed all outputs for 7 days and may need recalibration. "
    "Reply with a sample of recent articles you find suboptimal, or 'ok' to continue."
)
AGENT_AUDIT_LOG = getattr(mira_config, "AGENT_AUDIT_LOG", MIRA_ROOT / "logs" / "agent_audit.jsonl")


def _config_flag_enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


CANARY_AUDIT_ENABLED = _config_flag_enabled(
    getattr(shared_config, "CANARY_AUDIT_ENABLED", getattr(mira_config, "CANARY_AUDIT_ENABLED", True))
)
_AGENT_CODE_CHANGE_COUNTER_DATE: str | None = None
_AGENT_CODE_CHANGE_COUNTER = 0
_AGENT_CODE_CHANGE_ACTION_RE = re.compile(
    r"\b(add|apply|change|create|delete|edit|fix|implement|modify|patch|refactor|remove|rename|rewrite|update|write)\b"
    r"|修改|改动|修复|新增|删除|重构|实现"
    r"|更改"
    r"|编辑"
)
_AGENT_CODE_FILE_RE = re.compile(r"\.(py|swift|md)\b", re.IGNORECASE)
AUDIT_TRANSPARENCY_REVIEW_REPEAT_THRESHOLD = 3
AUDIT_TRANSPARENCY_CONCRETE_DANGER_TERMS = frozenset(
    {
        "account_access",
        "base64",
        "code_execution",
        "cookie",
        "credential",
        "deferred_execution",
        "eval",
        "exec",
        "exfiltration",
        "file_write",
        "network",
        "obfuscation",
        "os.system",
        "privilege",
        "prompt_injection",
        "secret",
        "side_channel",
        "subprocess",
        "token",
        "unauthorized",
    }
)
AUDIT_TRANSPARENCY_PATTERN_ONLY_TERMS = frozenset(
    {
        "boundary",
        "infrastructure",
        "label",
        "metadata",
        "pattern",
        "reference",
        "self-referential",
        "trigger",
    }
)
THIRD_THING_FILE = Path(__file__).resolve().parent / "notes_outbox" / "third_thing.md"
BRIDGE_THIRD_THING_FILE = MIRA_DIR / "outbox" / "third_thing.md"
JOINT_GARDEN_FILE = _AGENTS_DIR / "shared" / "soul" / "joint_garden.md"
SOUL_IDENTITY_CONFIG_FILE = _AGENTS_DIR / "shared" / "soul" / "identity.json"
JOINT_GARDEN_STALE_DAYS = 21
CLAUDE_API_PING_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_PING_TIMEOUT_SECONDS = 3
OFFLINE_FALLBACK_PROMPT = _AGENTS_DIR / "shared" / "prompts" / "offline_fallback.txt"
_LAST_NETWORK_STATUS: dict | None = None
_ATTENTION_DRIFT_STOPWORDS = {"a", "an", "and", "as", "for", "in", "of", "or", "the", "to", "with"}
OFF_INTEREST_AGENT_TASK_THRESHOLD = 0.3
_INTEREST_DRIFT_STOPWORDS = _ATTENTION_DRIFT_STOPWORDS | {
    "about",
    "after",
    "against",
    "agent",
    "article",
    "before",
    "briefing",
    "check",
    "completed",
    "current",
    "design",
    "from",
    "generic",
    "have",
    "human",
    "into",
    "more",
    "must",
    "output",
    "publish",
    "recent",
    "report",
    "review",
    "should",
    "summary",
    "task",
    "that",
    "this",
    "topic",
    "under",
    "week",
    "what",
    "when",
}
_INTEREST_DRIFT_SHORT_KEYWORDS = {"ai", "ml", "llm", "hbm", "kpi", "cot"}
FAST_DISPATCH_PATTERNS = {
    r"(?i)^publish\s+article\b": "socialmedia",
    r"(?i)^publish\s+(?:to\s+)?substack\b": "socialmedia",
    r"(?i)^post\s+(?:a\s+)?note\b": "socialmedia",
    r"(?i)^check\s+comments\b": "socialmedia",
    r"(?i)^run\s+explorer\b": "explorer",
    r"(?i)^run\s+(?:daily\s+)?briefing\b": "explorer",
    r"(?i)^read\s+note\b": "reader",
    r"(?i)^run\s+reader\b": "reader",
    r"(?i)^edit\s+photo\b": "photo",
    r"(?i)^run\s+podcast\b": "podcast",
    r"(?i)^run\s+analyst\b": "analyst",
    r"(?i)^run\s+research(?:er)?\b": "researcher",
}
SUBSTACK_PUBLISH_JOB_NAMES = {"substack-growth", "substack-notes"}
SUBSTACK_PUBLISH_REQUEST_RE = re.compile(
    r"\b(?:publish|post|send)\b.{0,80}\bsubstack\b|"
    r"\bsubstack\b.{0,80}\b(?:publish|post|send)\b|"
    r"\bpost\s+(?:a\s+)?(?:substack\s+)?note\b|"
    r"\bpublish\s+(?:article|essay|newsletter)\b",
    re.IGNORECASE,
)


def try_fast_dispatch(task_text):
    text = str(task_text or "").strip()
    if not text:
        return None
    matches = {agent for pattern, agent in FAST_DISPATCH_PATTERNS.items() if re.search(pattern, text)}
    if len(matches) == 1:
        return next(iter(matches))
    return None


def _verify_guard_integrity_at_startup() -> bool:
    global publish_blocked

    try:
        ok = guard_integrity.verify()
    except Exception as exc:
        publish_blocked = True
        log.critical("Substack guard integrity verification failed: %s", exc)
        return False
    if ok:
        publish_blocked = False
        return True
    publish_blocked = True
    log.critical("Substack guard integrity mismatch; Substack publishing is blocked until manually reset and verified")
    return False


def _is_substack_publish_request(task_name: str, content: str) -> bool:
    if task_name not in {"publish", "socialmedia"}:
        return False
    return bool(SUBSTACK_PUBLISH_REQUEST_RE.search(str(content or "")))


def _is_substack_publish_job(job_name: str) -> bool:
    return str(job_name or "") in SUBSTACK_PUBLISH_JOB_NAMES


def _log_substack_publish_block(reason: str) -> None:
    log.critical(
        "Substack publish blocked: %s; guard integrity must be manually reset and verified",
        reason,
    )


def check_context_violation(desc) -> bool:
    text = str(desc or "").casefold()
    if not text:
        return False
    for pattern in getattr(shared_config, "FORBIDDEN_CONTEXT_PATTERNS", []):
        pattern_text = str(pattern or "").strip()
        if pattern_text and pattern_text.casefold() in text:
            return True
    return False


def _log_agent_dep_failure(agent_name: str, module_name: str, traceback_text: str) -> None:
    try:
        crash_path = Path("/tmp/mira-crash.log")
        with open(crash_path, "a", encoding="utf-8") as crash_log:
            crash_log.write(
                f"\n{'=' * 60}\n"
                f"{datetime.now().isoformat()}\n"
                f"agent_deps import failure agent={agent_name} import={module_name}\n"
                f"{traceback_text}\n"
            )
    except OSError:
        pass
    log.critical(
        "Agent dependency import failed agent=%s import=%s\n%s",
        agent_name,
        module_name,
        traceback_text,
    )


def verify_agent_deps() -> None:
    import traceback
    import yaml

    data = yaml.safe_load(AGENT_DEPS_FILE.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid agent dependency manifest: {AGENT_DEPS_FILE}")

    for agent_name, imports in data.items():
        if imports is None:
            imports = []
        if not isinstance(imports, list) or not all(isinstance(item, str) for item in imports):
            raise RuntimeError(f"Invalid dependency list for agent '{agent_name}' in {AGENT_DEPS_FILE}")

        agent_dir = _AGENTS_DIR / str(agent_name)
        if agent_dir.is_dir():
            agent_path = str(agent_dir)
            if agent_path not in sys.path:
                sys.path.insert(0, agent_path)

        for module_name in imports:
            try:
                importlib.import_module(module_name)
            except Exception:
                tb = traceback.format_exc()
                _log_agent_dep_failure(str(agent_name), module_name, tb)
                raise SystemExit(78)


_TIMING_PHASE_STACK: list[dict[str, float]] = []
_TIMING_CYCLE_STACK: list[dict[str, float]] = []
_TIMING_STAGE_STACK: list[dict[str, float]] = []
_TIMING_PROMPT_MODULES = ("llm",)


def _write_timing_phase(phase: str, duration_s: float, category: str) -> None:
    if not getattr(shared_config, "TIMING_LOG_ENABLED", True):
        return
    try:
        timing_path = Path(getattr(shared_config, "TIMING_LOG_PATH", LOGS_DIR / "timing.jsonl"))
        timing_path.parent.mkdir(parents=True, exist_ok=True)
        with timing_path.open("a", encoding="utf-8") as timing_file:
            timing_file.write(
                json.dumps(
                    {
                        "phase": phase,
                        "duration_s": round(duration_s, 6),
                        "category": category,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception as exc:
        log.debug("Timing phase log write failed: %s", exc)


def _start_cycle_timing() -> dict[str, float]:
    cycle = {"started": time.perf_counter(), "inference_s": 0.0}
    _TIMING_CYCLE_STACK.append(cycle)
    return cycle


def _finish_cycle_timing(cycle: dict[str, float]) -> dict[str, float]:
    cycle_total_s = max(0.0, time.perf_counter() - cycle["started"])
    inference_s = min(cycle_total_s, max(0.0, cycle.get("inference_s", 0.0)))
    orchestration_s = max(0.0, cycle_total_s - inference_s)
    if _TIMING_CYCLE_STACK and _TIMING_CYCLE_STACK[-1] is cycle:
        _TIMING_CYCLE_STACK.pop()
    elif cycle in _TIMING_CYCLE_STACK:
        _TIMING_CYCLE_STACK.remove(cycle)
    return {
        "cycle_total_s": round(cycle_total_s, 6),
        "inference_s": round(inference_s, 6),
        "orchestration_s": round(orchestration_s, 6),
        "orchestration_pct": round((orchestration_s / cycle_total_s) * 100, 2) if cycle_total_s else 0.0,
    }


def _write_stage_timing(stage: str, llm_ms: int, total_ms: int) -> None:
    llm_ms = max(0, min(int(llm_ms), int(total_ms)))
    orchestration_ms = max(0, int(total_ms) - llm_ms)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "llm_ms": llm_ms,
        "orchestration_ms": orchestration_ms,
        "total_ms": int(total_ms),
        "llm_ratio": round(llm_ms / total_ms, 4) if total_ms else 0.0,
        "orchestration_ratio": round(orchestration_ms / total_ms, 4) if total_ms else 0.0,
    }
    log.info("STAGE_TIMING %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "llm_timing.jsonl", "a", encoding="utf-8") as timing_file:
            timing_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("stage timing log write failed: %s", exc)


def _inference_ms_from_result(result) -> int:
    if isinstance(result, tuple) and len(result) >= 2:
        return _inference_ms_from_result(result[1])
    if isinstance(result, dict):
        value = result.get("inference_ms")
    else:
        value = getattr(result, "inference_ms", 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _task_result_value(result):
    if isinstance(result, tuple) and result:
        return result[0]
    return result


def _write_task_timing(task_name: str, total_ms: int, inference_ms: int) -> None:
    total_ms = max(0, int(total_ms))
    inference_ms = max(0, min(int(inference_ms), total_ms))
    overhead_ms = max(0, total_ms - inference_ms)
    overhead_pct = (overhead_ms / total_ms * 100) if total_ms else 0.0
    line = (
        f"TIMING task={_normalize_task_distribution_category(task_name)} "
        f"total_ms={total_ms} inference_ms={inference_ms} "
        f"overhead_ms={overhead_ms} overhead_pct={overhead_pct:.2f}"
    )
    log.info(line)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOGS_DIR / "task_timing.log", "a", encoding="utf-8") as timing_file:
            timing_file.write(line + "\n")
    except OSError as exc:
        log.debug("task timing log write failed: %s", exc)


@contextmanager
def _timed_stage(stage: str):
    started = time.perf_counter()
    timing = {"llm_s": 0.0}
    _TIMING_STAGE_STACK.append(timing)
    try:
        yield
    finally:
        total_ms = round((time.perf_counter() - started) * 1000)
        if _TIMING_STAGE_STACK and _TIMING_STAGE_STACK[-1] is timing:
            _TIMING_STAGE_STACK.pop()
        elif timing in _TIMING_STAGE_STACK:
            _TIMING_STAGE_STACK.remove(timing)
        _write_stage_timing(stage, round(timing["llm_s"] * 1000), total_ms)


@contextmanager
def _timed_phase(phase: str, category: str):
    started = time.perf_counter()
    current = {"llm_s": 0.0}
    _TIMING_PHASE_STACK.append(current)
    try:
        yield
    finally:
        elapsed = time.perf_counter() - started
        _TIMING_PHASE_STACK.pop()
        if category == "llm":
            duration_s = elapsed
            nested_llm_s = elapsed
            if _TIMING_CYCLE_STACK:
                _TIMING_CYCLE_STACK[-1]["inference_s"] += elapsed
            for stage_timing in _TIMING_STAGE_STACK:
                stage_timing["llm_s"] += elapsed
        else:
            duration_s = max(0.0, elapsed - current["llm_s"])
            nested_llm_s = current["llm_s"]
        _write_timing_phase(phase, duration_s, category)
        if _TIMING_PHASE_STACK:
            _TIMING_PHASE_STACK[-1]["llm_s"] += nested_llm_s


@contextmanager
def _timed_llm_calls(phase: str, *module_names: str):
    modules = []
    seen_modules: set[int] = set()
    for module_name in (*_TIMING_PROMPT_MODULES, *module_names):
        module = sys.modules.get(module_name)
        if module is None or id(module) in seen_modules:
            continue
        modules.append(module)
        seen_modules.add(id(module))

    originals = []

    def _wrap_prompt_call(name, fn):
        def _wrapped(*args, **kwargs):
            with _timed_phase(f"{phase}.{name}", "llm"):
                return fn(*args, **kwargs)

        return _wrapped

    for module in modules:
        for name in _PIPELINE_PROMPT_FUNCTIONS:
            fn = getattr(module, name, None)
            if callable(fn):
                originals.append((module, name, fn))
                setattr(module, name, _wrap_prompt_call(name, fn))

    try:
        yield
    finally:
        for module, name, fn in reversed(originals):
            setattr(module, name, fn)


_INTENT_CLARIFICATION_REPLY = "What do you want to achieve with this?"
_SENSITIVE_REDACTED_CONTENT = "[sensitive survival exposure routed local]"
_TIME_SENSITIVE_REDACTED_CONTENT = "[time-sensitive message routed local]"
_TIME_SENSITIVE_MIN_MESSAGE_CHARS = 20
_EXPLORE_QUEUE_DEPTH_STATUSES = {
    "queued",
    "pending",
    "accepted",
    "in-progress",
    "in_progress",
    "dispatched",
    "running",
    "working",
}
_DISPATCH_QUEUED_STATUSES = {
    "queued",
    "pending",
    "accepted",
    "in-progress",
    "in_progress",
}
VERIFICATION_INDEPENDENCE = True
MIN_COMPLETED_TASK_OUTPUT_BYTES = 50
CROSS_VALIDATION_PROMPT = (
    "Does this output contain any factual errors, unsupported claims, or quality issues? "
    "Reply CONFIRMED or FLAGGED with specifics."
)
_ORIGINAL_INTENT_SCORING_GUIDANCE = (
    "If original_intent is present, check whether the agent output actually advances that intent, "
    "not only whether the sub-task completed. Discount the score if the sub-task completed but the "
    "original intent is clearly unmet."
)
STUCK_TASK_THRESHOLD_MINUTES = int(getattr(mira_config, "STUCK_TASK_THRESHOLD_MINUTES", 60))
MAX_STUCK_TASKS_BEFORE_ALERT = int(getattr(mira_config, "MAX_STUCK_TASKS_BEFORE_ALERT", 3))
ZOMBIE_THRESHOLD_HOURS = float(getattr(mira_config, "ZOMBIE_THRESHOLD_HOURS", 2))
_ORIGINAL_TASK_MANAGER_DISPATCH = TaskManager.dispatch
_ORIGINAL_TASK_MANAGER_COLLECT_RESULT = TaskManager._collect_result
_ORIGINAL_DISPATCH_OR_REQUEUE = _dispatch_or_requeue
_ORIGINAL_MIRA_POLL_COMMANDS = getattr(Mira, "poll_commands", None) if Mira is not None else None
_ORIGINAL_PROJECT_RECORD_TO_BRIDGE = getattr(talk_module, "_project_record_to_bridge", None)
_SKILL_AUDIT_INTEGRITY_OK = True
_INTERFACE_MESSAGE_RECEIVED_AT = "message_received_at"
_INTERFACE_MESSAGE_RECEIVED_MONOTONIC = "_message_received_monotonic"
_INTERFACE_TASK_DISPATCHED_AT = "task_dispatched_at"
_HEAVY_ROUTE_LIGHT_PATH_TERMS = (
    "general",
    "coder",
    "wiki",
    "memory",
    "local file",
    "local files",
    "deterministic",
    "validation",
    "lookup",
)
_HEAVY_ROUTE_INABILITY_TERMS = (
    "cannot",
    "can't",
    "can not",
    "unable",
    "insufficient",
    "not enough",
    "not suitable",
    "not appropriate",
    "inadequate",
    "blocked",
)
_HEAVY_ROUTE_EMPTY_TARGETS = {"", "none", "null", "n/a", "na", "unknown", "tbd", "task", "result", "output"}


def _count_pending_active_tasks() -> int:
    task_ids = {
        rec.task_id
        for rec in TaskManager()._records
        if normalize_task_status(rec.status) in _EXPLORE_QUEUE_DEPTH_STATUSES
    }

    if getattr(mira_config, "CONTROL_RUNTIME_DB_ENABLED", False):
        try:
            from control.db import schema_name, transaction
            from db.connection import dict_cursor

            with transaction() as conn:
                with dict_cursor(conn) as cur:
                    cur.execute(
                        f"""
                        SELECT id
                        FROM {schema_name()}.tasks
                        WHERE status = ANY(%s)
                        """,
                        (list(_EXPLORE_QUEUE_DEPTH_STATUSES),),
                    )
                    task_ids.update(str(row["id"]) for row in cur.fetchall() if row.get("id"))
        except Exception as exc:
            log.debug("Control DB queue depth check failed: %s", exc)

    return len(task_ids)


def _count_undelivered_outputs() -> int:
    cutoff = time.time() - 3600
    outbox = Path(__file__).resolve().parent / "notes_outbox"
    try:
        return sum(1 for path in outbox.iterdir() if path.is_file() and path.stat().st_mtime < cutoff)
    except OSError:
        return 0


def _dispatch_scheduled_jobs(session_new: list[dict]):
    for job in sorted(jobs_module.get_jobs(), key=lambda item: item.priority):
        target_user_ids = jobs_module.get_known_user_ids() if getattr(job, "per_user", False) else [None]
        for target_user_id in target_user_ids:
            payload = jobs_module.evaluate_job_payload(job, user_id=target_user_id)
            if not payload:
                continue
            if publish_blocked and _is_substack_publish_job(job.name):
                _log_substack_publish_block(f"scheduled job '{job.name}'")
                continue

            if job.name == "explore":
                backlog_count = _count_undelivered_outputs()
                if backlog_count >= MAX_UNDELIVERED_OUTPUTS:
                    log.info("explore skipped: delivery backlog %d items", backlog_count)
                    continue

                queue_depth = _count_pending_active_tasks()
                if queue_depth >= EXPLORE_MAX_PENDING_TASKS:
                    log.info("explore skipped: queue depth %d >= threshold", queue_depth)
                    continue

            if job.inline:
                try:
                    with _timed_phase(f"agent_dispatch.{job.name}.inline", "tool"):
                        jobs_module._run_inline_scheduled_job(job, payload)
                    jobs_module._record_scheduled_job_dispatch(job, payload, user_id=target_user_id)
                except Exception as e:
                    log.error("%s failed: %s", job.name, e)
                continue

            bg_name, cmd = jobs_module.build_job_dispatch(
                job,
                payload,
                python_executable=sys.executable,
                core_path=str(Path(__file__).resolve().parent / "core.py"),
                user_id=target_user_id,
            )
            with _timed_phase(f"agent_dispatch.{job.name}", "tool"):
                dispatched = jobs_module._dispatch_background(bg_name, cmd, group=job.blocking_group)
            if dispatched is False:
                continue
            jobs_module._record_scheduled_job_dispatch(job, payload, user_id=target_user_id)

            session_meta = jobs_module.build_job_session_record(job, payload)
            if session_meta:
                detail = session_meta.get("detail", "")
                if target_user_id:
                    detail = f"{target_user_id}:{detail}" if detail else target_user_id
                session_new.append(session_record(session_meta["action"], detail))


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
_AUTHORIZATION_SOURCES = frozenset({"iphone_bridge", "api_key", "cron", "internal"})
_HIGH_PERMISSION_ROLES = frozenset({"admin", "owner", "root", "system", "high"})
_LOW_PERMISSION_ROLES = frozenset({"guest", "read_only", "readonly", "low"})
_INTERNAL_SENDERS = frozenset({"agent", "mira", "system"})
_CRON_SENDERS = frozenset({"cron", "schedule", "scheduler", "auto"})
_BYPASSED_CONFIRMATION_KEYS = (
    "bypassed_check",
    "confirmation_skipped",
    "skipped_confirmation",
    "skip_confirmation",
)
_CONFIRMATION_COMPLETE_KEYS = (
    "confirmation_completed",
    "confirmation_done",
    "confirmation_passed",
    "confirmed",
)
_LOG_PRUNE_STATE_KEY = "last_log_prune"
_LOG_PRUNE_STATE_DIR_PREFIXES = (".", "_")
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


def _is_prunable_log_file(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    rel = path.relative_to(LOGS_DIR)
    return not any(part.startswith(_LOG_PRUNE_STATE_DIR_PREFIXES) for part in rel.parts[:-1])


def _prune_old_logs_if_due() -> None:
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get(_LOG_PRUNE_STATE_KEY) == today:
        return

    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    deleted = 0
    for path in LOGS_DIR.rglob("*"):
        try:
            if _is_prunable_log_file(path) and path.stat().st_mtime < cutoff:
                os.remove(path)
                deleted += 1
        except OSError as exc:
            log.debug("log prune skipped %s: %s", path, exc)

    state[_LOG_PRUNE_STATE_KEY] = today
    save_state(state)
    log.info("Pruned %d log file(s) older than %d days", deleted, LOG_RETENTION_DAYS)


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
    "daily-collab",
    "autowrite-run",
    "writing-pipeline",
    "writing-triage",
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


def _read_stable_attention_axes() -> list[str]:
    try:
        data = json.loads(SOUL_IDENTITY_CONFIG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    axes = data.get("stable_attention_axes")
    if not isinstance(axes, list):
        return []
    return [str(axis).strip() for axis in axes if str(axis or "").strip()]


def _attention_axis_keywords(axis: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", axis.lower())
        if len(token) > 2 and token not in _ATTENTION_DRIFT_STOPWORDS
    }


def _completed_task_topic_text(result_data: dict, workspace: Path) -> str:
    parts: list[str] = []
    for key in ("summary", "task_type", "agent", "declared_agent", "execution_agent"):
        value = result_data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    tags = result_data.get("tags")
    if isinstance(tags, list):
        parts.extend(str(tag) for tag in tags if str(tag or "").strip())

    message_file = workspace / "message.json"
    try:
        message = (
            json.loads(message_file.read_text(encoding="utf-8")) if workspace.is_dir() and message_file.exists() else {}
        )
    except (json.JSONDecodeError, OSError):
        message = {}
    if isinstance(message, dict):
        for key in ("content", "title", "type"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        message_tags = message.get("tags")
        if isinstance(message_tags, list):
            parts.extend(str(tag) for tag in message_tags if str(tag or "").strip())

    return " ".join(parts).lower()


def _interest_texts_from_value(value) -> list[str]:
    if isinstance(value, dict):
        if "interests" in value:
            return _interest_texts_from_value(value.get("interests"))
        texts: list[str] = []
        for key in ("topic", "title", "name", "description", "text", "content"):
            if value.get(key):
                texts.append(str(value[key]))
        if texts:
            return texts
        return [str(item) for item in value.values() if isinstance(item, str) and item.strip()]
    if isinstance(value, (list, tuple, set)):
        texts: list[str] = []
        for item in value:
            texts.extend(_interest_texts_from_value(item))
        return texts
    text = str(value or "")
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if cleaned and not cleaned.startswith("#"):
            lines.append(cleaned)
    return lines or ([text.strip()] if text.strip() else [])


def _current_human_interests() -> list[str]:
    get_interests = getattr(soul_manager, "get_interests", None)
    if callable(get_interests):
        try:
            interests = get_interests()
        except Exception as exc:
            log.debug("soul_manager.get_interests failed: %s", exc)
        else:
            texts = _interest_texts_from_value(interests)
            if texts:
                return texts
    try:
        return _interest_texts_from_value(load_soul().get("interests", ""))
    except Exception as exc:
        log.debug("interest drift check could not load soul interests: %s", exc)
        return []


def _interest_keywords(text: str) -> set[str]:
    keywords = set()
    for token in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if token in _INTEREST_DRIFT_STOPWORDS:
            continue
        if len(token) >= 4 or token in _INTEREST_DRIFT_SHORT_KEYWORDS:
            keywords.add(token)
    return keywords


def _task_matches_human_interests(topic_text: str, interests: list[str]) -> bool:
    topic_tokens = set(re.findall(r"[a-z0-9]+", topic_text.lower()))
    for interest in interests:
        keywords = _interest_keywords(interest)
        if not keywords:
            continue
        overlap = topic_tokens & keywords
        if len(overlap) >= min(2, len(keywords)):
            return True
    return False


def _task_record_message(record) -> dict:
    workspace_text = str(getattr(record, "workspace", "") or "").strip()
    if not workspace_text:
        return {}
    workspace = Path(workspace_text)
    message_file = workspace / "message.json"
    try:
        message = json.loads(message_file.read_text(encoding="utf-8")) if message_file.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}
    return message if isinstance(message, dict) else {}


def _is_agent_initiated_task(record, message: dict) -> bool:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    candidates = [
        getattr(record, "sender", ""),
        message.get("sender", ""),
        message.get("origin", ""),
        metadata.get("origin", ""),
        metadata.get("source", ""),
        metadata.get("authorizing_source", ""),
        metadata.get("authorization_source", ""),
    ]
    normalized = {str(value or "").strip().lower() for value in candidates if str(value or "").strip()}
    if normalized & {"agent", "auto", "autonomous", "launchagent", "mira", "scheduler", "system"}:
        return True
    if normalized & {"ang", "human", "iphone", "user", "wa"}:
        return False

    text = " ".join(
        str(value or "")
        for value in (
            getattr(record, "task_id", ""),
            getattr(record, "workflow_id", ""),
            getattr(record, "content_preview", ""),
            getattr(record, "summary", ""),
            getattr(record, "task_type", ""),
            getattr(record, "agent", ""),
            " ".join(getattr(record, "tags", []) or []),
            message.get("content", ""),
            message.get("type", ""),
        )
    ).lower()
    return any(
        marker in text
        for marker in (
            "auto-publish",
            "autopublish",
            "autowrite",
            "autonomous",
            "explore",
            "publish pipeline",
            "scheduled",
        )
    )


def _task_result_data(record, workspace: Path) -> dict:
    data = {
        "summary": getattr(record, "summary", ""),
        "task_type": getattr(record, "task_type", ""),
        "agent": getattr(record, "agent", ""),
        "tags": getattr(record, "tags", []) or [],
    }
    result_file = workspace / "result.json"
    try:
        result_data = (
            json.loads(result_file.read_text(encoding="utf-8")) if workspace.is_dir() and result_file.exists() else {}
        )
    except (json.JSONDecodeError, OSError):
        return data
    if isinstance(result_data, dict):
        data.update(result_data)
    return data


def _recent_agent_initiated_completed_tasks(now: datetime) -> list[dict]:
    cutoff_ts = (now - timedelta(days=7)).timestamp()
    complete_statuses = {"done", "verified", "completed", "complete", "completed_unverified"}
    tasks: list[dict] = []
    try:
        records = TaskManager()._records
    except Exception as exc:
        log.debug("interest drift task scan failed: %s", exc)
        return tasks

    for record in records:
        completed_ts = _parse_recovery_timestamp(getattr(record, "completed_at", ""))
        if completed_ts is None or completed_ts < cutoff_ts or completed_ts > now.timestamp():
            continue
        if normalize_task_status(getattr(record, "status", "")) not in complete_statuses:
            continue
        message = _task_record_message(record)
        if not _is_agent_initiated_task(record, message):
            continue
        workspace_text = str(getattr(record, "workspace", "") or "").strip()
        workspace = Path(workspace_text) if workspace_text else Path("/__mira_missing_task_workspace__")
        topic_text = _completed_task_topic_text(_task_result_data(record, workspace), workspace)
        if not topic_text:
            continue
        tasks.append(
            {
                "task_id": getattr(record, "task_id", ""),
                "completed_at": getattr(record, "completed_at", ""),
                "preview": getattr(record, "content_preview", "") or getattr(record, "summary", ""),
                "topic_text": topic_text,
            }
        )
    return tasks


def _format_interest_drift_note(off_interest: list[dict], total: int, fraction: float, interests: list[str]) -> str:
    lines = [
        "Course check: recent agent-initiated work may be drifting from declared interests.",
        "",
        f"Weekly scan: {len(off_interest)}/{total} agent-initiated completed tasks ({fraction:.0%}) did not match the current soul interests.",
        "",
        "Declared interest anchors:",
    ]
    for interest in interests[:8]:
        lines.append(f"- {interest}")
    lines.extend(["", "Off-interest task samples:"])
    for task in off_interest[:5]:
        preview = re.sub(r"\s+", " ", str(task.get("preview") or task.get("task_id") or "")).strip()
        lines.append(f"- {preview[:160]}")
    lines.extend(
        [
            "",
            "Please confirm whether this is an intentional direction change or whether Mira should steer back toward the declared interests.",
        ]
    )
    return "\n".join(lines)


def _send_interest_drift_note(message: str, summary: dict, user_id: str) -> bool:
    try:
        from notes_bridge import send_to_outbox

        sent_path = send_to_outbox(
            message,
            metadata={
                "kind": "interest_drift_course_check",
                "user_id": user_id,
                "priority": "high",
                **summary,
            },
        )
        if not sent_path:
            return False
        path = Path(sent_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload["priority"] = "high"
                payload["type"] = "alert"
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("interest drift outbox priority update failed: %s", exc)
        return True
    except Exception as exc:
        log.debug("interest drift Notes outbox write failed: %s", exc)

    if Mira is None:
        return False
    try:
        now = datetime.now(timezone.utc)
        item_id = f"interest_drift_course_check_{now.strftime('%G_W%V')}"
        bridge = Mira(MIRA_DIR, user_id=user_id)
        if bridge.item_exists(item_id):
            return True
        item = bridge.create_item(
            item_id,
            "alert",
            "Course check: interest drift",
            message,
            sender="agent",
            tags=["mira", "interests", "drift", "course-check"],
            origin="agent",
        )
        item["status"] = "needs-input"
        item["priority"] = "high"
        item["pinned"] = True
        item["interest_drift"] = summary
        bridge._write_item(item)
        bridge._update_manifest(item)
        return True
    except Exception as exc:
        log.debug("interest drift bridge item failed: %s", exc)
        return False


def _check_agent_initiated_interest_drift(user_id: str = "ang") -> None:
    interests = _current_human_interests()
    if not interests:
        return

    now = datetime.now(timezone.utc)
    tasks = _recent_agent_initiated_completed_tasks(now)
    if not tasks:
        return

    off_interest = [task for task in tasks if not _task_matches_human_interests(task["topic_text"], interests)]
    fraction = len(off_interest) / len(tasks)
    if fraction <= OFF_INTEREST_AGENT_TASK_THRESHOLD:
        return

    summary = {
        "total_agent_initiated_tasks": len(tasks),
        "off_interest_tasks": len(off_interest),
        "fraction": round(fraction, 4),
        "threshold": OFF_INTEREST_AGENT_TASK_THRESHOLD,
        "task_ids": [task["task_id"] for task in off_interest[:10]],
    }
    message = _format_interest_drift_note(off_interest, len(tasks), fraction, interests)
    if _send_interest_drift_note(message, summary, user_id):
        log.warning(
            "INTEREST_DRIFT_COURSE_CHECK off_interest=%d total=%d fraction=%.3f threshold=%.3f",
            len(off_interest),
            len(tasks),
            fraction,
            OFF_INTEREST_AGENT_TASK_THRESHOLD,
        )


def _check_stable_attention_drift(topic_texts: list[str]) -> None:
    axes = _read_stable_attention_axes()
    if not axes or not topic_texts:
        return

    topic_blob = "\n".join(topic_texts)
    touched = []
    for axis in axes:
        keywords = _attention_axis_keywords(axis)
        if keywords and any(re.search(rf"\b{re.escape(keyword)}\b", topic_blob) for keyword in keywords):
            touched.append(axis)

    threshold = 2
    if len(touched) < threshold:
        missing = [axis for axis in axes if axis not in touched]
        log.warning(
            "ATTENTION_DRIFT: stable_attention_axes_touched=%d threshold=%d touched=%s missing=%s",
            len(touched),
            threshold,
            touched,
            missing,
        )


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


def _explicit_task_dispatch_agent(msg) -> str | None:
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
        agent_name = _normalize_task_distribution_category(allowed_agents[0])
        if agent_name != "general":
            return agent_name
    return None


def _agent_config(agent_name: str):
    registry = getattr(shared_config, "AGENT_REGISTRY", {})
    if not isinstance(registry, dict):
        return None
    agent_config = registry.get(agent_name)
    return agent_config if isinstance(agent_config, dict) else None


def _agent_requires_local_llm(agent_name: str) -> bool:
    agent_config = _agent_config(agent_name)
    permissions = agent_config.get("permissions") if agent_config else None
    return isinstance(permissions, dict) and bool(permissions.get("local_llm_only"))


def _routing_decision_value(msg, key: str) -> str:
    metadata = getattr(msg, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    value = metadata.get(key)
    if value in (None, ""):
        value = getattr(msg, key, None)
    return str(value or "").strip()


def _heavy_route_has_required_reason(msg) -> bool:
    reason = _routing_decision_value(msg, "escalation_reason").lower()
    if not reason:
        return False
    mentions_light_path = any(term in reason for term in _HEAVY_ROUTE_LIGHT_PATH_TERMS)
    states_inability = any(term in reason for term in _HEAVY_ROUTE_INABILITY_TERMS)
    return mentions_light_path and states_inability


def _heavy_route_has_verification_target(msg) -> bool:
    target = _routing_decision_value(msg, "verification_target").lower()
    return target not in _HEAVY_ROUTE_EMPTY_TARGETS


def _downgrade_heavy_route_to_general(msg, agent_name: str) -> None:
    metadata = dict(getattr(msg, "metadata", {}) or {})
    for key in ("routing_agent", "target_agent", "agent", "agent_type", "task_category", "category"):
        metadata[key] = "general"
        setattr(msg, key, "general")
    msg.metadata = metadata
    msg.allowed_agents = ["general"]
    log.warning(
        "HEAVY_ROUTE_DOWNGRADED: agent=%s missing_required_escalation_reason_or_verification_target",
        agent_name,
    )


def _enforce_heavy_route_invariant(msg) -> None:
    agent_name = _explicit_task_dispatch_agent(msg)
    if not agent_name:
        return
    try:
        from agent_registry import get_registry

        selected_agent = get_registry().get_manifest(agent_name)
    except Exception:
        selected_agent = None
    if selected_agent is None or selected_agent.tier != "heavy":
        return
    if _heavy_route_has_required_reason(msg) and _heavy_route_has_verification_target(msg):
        return
    _downgrade_heavy_route_to_general(msg, agent_name)


def _apply_tier_model_map_for_dispatch(msg) -> None:
    if getattr(msg, "model_restriction", None):
        return
    agent_name = _explicit_task_dispatch_agent(msg)
    if not agent_name or _agent_requires_local_llm(agent_name):
        return
    try:
        from agent_registry import get_registry

        agent = get_registry().get_manifest(agent_name)
    except Exception:
        agent = None
    if agent is None:
        return
    msg.model_restriction = shared_config.TIER_MODEL_MAP.get(agent.tier, getattr(shared_config, "LLM_MODEL", ""))


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


def _record_agent_invocation_audit(msg, task_id: str, duration_ms: int, outcome: str) -> None:
    try:
        content = str(getattr(msg, "content", "") or "")
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_name": _task_dispatch_category(msg),
            "task_id": task_id or str(getattr(msg, "id", "") or ""),
            "task_summary_truncated_to_100chars": content[:100],
            "duration_ms": duration_ms,
            "outcome": outcome,
        }
        AGENT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_AUDIT_LOG, "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("agent invocation audit write failed: %s", exc)


def _agent_declared_permissions(agent_name: str):
    registry = getattr(mira_config, "AGENT_REGISTRY", {})
    if not isinstance(registry, dict):
        return None
    agent_config = registry.get(agent_name)
    if not isinstance(agent_config, dict):
        return None
    return agent_config.get("permissions")


def _record_agent_permissions_audit(msg) -> None:
    try:
        agent_name = _task_dispatch_category(msg)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "agent_dispatch_permissions",
            "agent_name": agent_name,
            "declared_permissions": _agent_declared_permissions(agent_name),
            "message_id": str(getattr(msg, "id", "") or ""),
        }
        AGENT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_AUDIT_LOG, "a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("agent permissions audit write failed: %s", exc)


def _dispatch_active_task_count(task_mgr) -> int:
    try:
        return int(task_mgr.get_active_count())
    except Exception:
        return 0


def _dispatch_queued_task_count(task_mgr) -> int:
    try:
        records = getattr(task_mgr, "_records", []) or []
    except Exception:
        return 0

    count = 0
    for record in records:
        status = record.get("status", "") if isinstance(record, dict) else getattr(record, "status", "")
        if normalize_task_status(status) in _DISPATCH_QUEUED_STATUSES:
            count += 1
    return count


def _log_dispatch_audit(msg, task_mgr, dispatch_decision: str, active_task_count: int | None = None) -> None:
    try:
        requested_agent = _task_dispatch_category(msg)
        task_id = str(getattr(msg, "id", "") or "")
        if active_task_count is None:
            active_task_count = _dispatch_active_task_count(task_mgr)
        entry = {
            "active_task_count": active_task_count,
            "queued_task_count": _dispatch_queued_task_count(task_mgr),
            "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
            "requested_agent": requested_agent,
            "requested_agent_tier": _agent_tier(requested_agent),
            "task_id": task_id,
            "dispatch_decision": dispatch_decision,
        }
        log.info(
            "dispatch_audit %s",
            json.dumps(entry, ensure_ascii=False, sort_keys=True),
            extra={"agent": requested_agent, "task_id": task_id},
        )
    except Exception as exc:
        log.debug("dispatch audit log failed: %s", exc)


def _authorization_metadata(msg) -> dict:
    metadata = getattr(msg, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _normalize_authorization_source(value: object) -> str | None:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized in _AUTHORIZATION_SOURCES:
        return normalized
    if normalized in {"iphone", "ios", "bridge", "notes_bridge", "icloud_bridge"}:
        return "iphone_bridge"
    if normalized in {"api", "api_token", "bearer", "token", "remote_trigger"}:
        return "api_key"
    if normalized in {"launchd", "scheduled"}:
        return "cron"
    if normalized in {"agent", "orchestrator", "system"}:
        return "internal"
    return None


def _authorization_source_for_dispatch(msg) -> str:
    metadata = _authorization_metadata(msg)
    for key in ("authorizing_source", "authorization_source", "auth_source", "auth_method", "source"):
        source = _normalize_authorization_source(metadata.get(key) or getattr(msg, key, None))
        if source:
            return source

    sender = str(getattr(msg, "sender", "") or "").strip().lower()
    if sender in _CRON_SENDERS:
        return "cron"
    if sender in _INTERNAL_SENDERS:
        return "internal"
    return "iphone_bridge"


def _permission_level_for_dispatch(msg) -> str:
    metadata = _authorization_metadata(msg)
    explicit = str(metadata.get("permission_level") or getattr(msg, "permission_level", "") or "").strip().lower()
    if explicit in {"high", "normal", "low"}:
        return explicit

    role = str(getattr(msg, "user_role", "") or metadata.get("user_role") or "").strip().lower()
    if role in _HIGH_PERMISSION_ROLES:
        return "high"
    if role in _LOW_PERMISSION_ROLES:
        return "low"
    return "normal"


def _confirmation_was_skipped_for_dispatch(msg) -> bool:
    metadata = _authorization_metadata(msg)
    for key in _BYPASSED_CONFIRMATION_KEYS:
        if bool(metadata.get(key) or getattr(msg, key, False)):
            return True

    confirmation_required = bool(
        metadata.get("confirmation_required")
        or metadata.get("requires_confirmation")
        or getattr(msg, "confirmation_required", False)
        or getattr(msg, "requires_confirmation", False)
    )
    if not confirmation_required:
        return False

    confirmation_complete = any(
        bool(metadata.get(key) or getattr(msg, key, False)) for key in _CONFIRMATION_COMPLETE_KEYS
    )
    return not confirmation_complete


def _log_worker_dispatch_authorization(msg) -> None:
    try:
        log_authorization_event(
            f"dispatch:{_task_dispatch_category(msg)}",
            _authorization_source_for_dispatch(msg),
            _permission_level_for_dispatch(msg),
            bypassed_check=_confirmation_was_skipped_for_dispatch(msg),
        )
    except Exception as exc:
        log.debug("worker authorization audit write failed: %s", exc)


def _apply_fast_dispatch_plan(msg, workspace_dir: Path) -> str | None:
    if _explicit_task_dispatch_agent(msg):
        return None

    agent_name = try_fast_dispatch(getattr(msg, "content", ""))
    if not agent_name:
        return None

    task_text = str(getattr(msg, "content", "") or "").strip()
    workspace = _resolve_workspace_dir(Path(workspace_dir), str(getattr(msg, "id", "") or ""))
    workspace.mkdir(parents=True, exist_ok=True)
    tier = _agent_tier(agent_name)
    if tier not in {"light", "heavy"}:
        tier = "light"
    plan = [{"agent": agent_name, "instruction": task_text, "tier": tier}]
    (workspace / "pending_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    metadata = dict(getattr(msg, "metadata", {}) or {})
    metadata["routing_agent"] = agent_name
    metadata["dispatch_method"] = "fast_dispatch"
    msg.metadata = metadata
    msg.routing_agent = agent_name
    log.info("fast_dispatch: %s", agent_name)
    return agent_name


def _dispatch_with_agent_audit(self, msg, workspace_dir, *args, **kwargs):
    skip_audit = False
    try:
        skip_audit = self.is_busy()
    except Exception:
        skip_audit = False

    start = time.monotonic()
    task_start = None
    task_name = _task_dispatch_category(msg)
    inference_ms = 0
    task_id = ""
    outcome = "error"
    try:
        _attach_original_request(msg)
        _attach_original_intent(msg)
        _enforce_heavy_route_invariant(msg)
        _apply_tier_model_map_for_dispatch(msg)
        _apply_fast_dispatch_plan(msg, workspace_dir)
        task_name = _task_dispatch_category(msg)
        if publish_blocked and _is_substack_publish_request(task_name, getattr(msg, "content", "")):
            outcome = "blocked"
            _log_substack_publish_block(f"task dispatch '{task_name}'")
            return None
        if not skip_audit:
            _record_agent_permissions_audit(msg)
            _log_worker_dispatch_authorization(msg)
            receipt_workspace = _resolve_workspace_dir(Path(workspace_dir), str(getattr(msg, "id", "") or ""))
            write_dispatch_receipt(
                str(getattr(msg, "id", "") or ""),
                task_name,
                str(getattr(msg, "content", "") or ""),
                receipt_workspace,
            )
        active_task_count = _dispatch_active_task_count(self)
        dispatch_decision = "blocked_concurrency" if active_task_count >= MAX_CONCURRENT_TASKS else "started"
        _log_dispatch_audit(msg, self, dispatch_decision, active_task_count=active_task_count)
        task_start = time.perf_counter()
        dispatch_result = _ORIGINAL_TASK_MANAGER_DISPATCH(self, msg, workspace_dir, *args, **kwargs)
        task_dispatched_at = time.monotonic()
        inference_ms = _inference_ms_from_result(dispatch_result)
        task_id = _task_result_value(dispatch_result)
        outcome = "success" if task_id else "error"
        if task_id:
            _record_interface_latency(msg, task_dispatched_at)
        return task_id
    except (TimeoutError, subprocess.TimeoutExpired):
        outcome = "timeout"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        if task_start is not None:
            task_end = time.perf_counter()
            total_ms = round((task_end - task_start) * 1000)
            _write_task_timing(task_name, total_ms, inference_ms)
        if not skip_audit:
            duration_ms = int((time.monotonic() - start) * 1000)
            _record_agent_invocation_audit(msg, task_id, duration_ms, outcome)


def _root_intent_for_dispatch(msg, fallback: str = "") -> str:
    metadata = getattr(msg, "metadata", {}) or {}
    if isinstance(metadata, dict):
        value = metadata.get("original_intent")
        if isinstance(value, str) and value.strip():
            return value.strip()

    for value in (
        getattr(msg, "original_intent", None),
        getattr(getattr(msg, "root_task", None), "description", None),
        fallback,
        getattr(msg, "content", ""),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _raw_request_for_dispatch(msg) -> str:
    metadata = getattr(msg, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    for value in (
        getattr(msg, "raw_input", None),
        getattr(msg, "original_request", None),
        metadata.get("raw_input"),
        metadata.get("original_request"),
        getattr(msg, "content", ""),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _attach_original_request(msg) -> None:
    original_request = _raw_request_for_dispatch(msg)
    if not original_request or getattr(msg, "_original_request_attached", False):
        return

    original_to_dict = getattr(msg, "to_dict", None)
    if not callable(original_to_dict):
        return

    def to_dict_with_original_request():
        payload = dict(original_to_dict())
        payload.setdefault("original_request", original_request)
        return payload

    msg.to_dict = to_dict_with_original_request
    msg._original_request_attached = True


def _attach_original_intent(msg, fallback: str = "") -> None:
    intent = _root_intent_for_dispatch(msg, fallback=fallback)
    if not intent:
        return
    msg.original_intent = intent
    metadata = dict(getattr(msg, "metadata", {}) or {})
    metadata.setdefault("original_intent", intent)
    metadata.setdefault("evaluation_guidance", _ORIGINAL_INTENT_SCORING_GUIDANCE)
    msg.metadata = metadata


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


CONTEXT_GUARD_REFUSAL_LOG = "Context guard: task refused due to forbidden context pattern"
CONTEXT_GUARD_REFUSAL_RESPONSE = "I can't dispatch this task because it matches a forbidden surveillance context."


def _send_context_guard_refusal(bridge, msg) -> None:
    task_id = str(getattr(msg, "id", "") or "")
    user_id = str(getattr(msg, "user_id", "") or getattr(bridge, "user_id", "") or "ang")
    log.warning("%s task_id=%s user_id=%s", CONTEXT_GUARD_REFUSAL_LOG, task_id, user_id)

    error = {
        "code": "forbidden_context",
        "message": CONTEXT_GUARD_REFUSAL_RESPONSE,
        "retryable": False,
    }
    target_bridge = bridge
    if target_bridge is None and Mira is not None and task_id:
        try:
            target_bridge = Mira(MIRA_DIR, user_id=user_id)
        except Exception as exc:
            log.debug("context guard bridge open failed: %s", exc)

    if target_bridge is not None and task_id:
        try:
            target_bridge.update_status(
                task_id,
                "failed",
                agent_message=CONTEXT_GUARD_REFUSAL_RESPONSE,
                error=error,
            )
        except TypeError:
            try:
                target_bridge.update_status(
                    task_id,
                    "failed",
                    agent_message=CONTEXT_GUARD_REFUSAL_RESPONSE,
                )
            except Exception as exc:
                log.debug("context guard bridge status write failed: %s", exc)
        except Exception as exc:
            log.debug("context guard bridge status write failed: %s", exc)

    try:
        from notes_bridge import send_to_outbox

        send_to_outbox(
            CONTEXT_GUARD_REFUSAL_RESPONSE,
            metadata={
                "task_id": task_id,
                "user_id": user_id,
                "reason": "forbidden_context",
            },
        )
    except Exception as exc:
        log.debug("context guard outbox write failed: %s", exc)


def _dispatch_with_survival_guard(self, msg, workspace_dir, *args, **kwargs):
    original_content = getattr(msg, "content", "")
    _attach_original_intent(msg, fallback=original_content)
    if check_context_violation(original_content):
        _send_context_guard_refusal(None, msg)
        return None
    survival_sensitive = _detect_survival_exposure(original_content)
    time_sensitive = _detect_time_sensitive_message(original_content)
    if not survival_sensitive and not time_sensitive:
        task_id = _dispatch_with_agent_audit(self, msg, workspace_dir, *args, **kwargs)
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
        task_id = _dispatch_with_agent_audit(self, msg, workspace_dir, *args, **kwargs)
        if task_id:
            _record_task_distribution_dispatch(msg)
        return task_id
    finally:
        msg.content = original_content


def _send_notes_dispatch_acknowledgment(bridge, msg) -> None:
    try:
        from notes_bridge import send_to_outbox

        task_preview = re.sub(r"\s+", " ", str(getattr(msg, "content", "") or "")).strip()[:40]
        send_to_outbox(
            f"收到 {task_preview}. 处理中…",
            metadata={
                "task_id": str(getattr(msg, "id", "") or ""),
                "user_id": str(getattr(bridge, "user_id", "") or ""),
            },
        )
    except Exception as exc:
        log.debug("dispatch acknowledgment outbox write failed: %s", exc)


def _dispatch_or_requeue_with_survival_guard(task_mgr, bridge, msg, workspace, cmd=None):
    _attach_interface_received_timestamp(msg, cmd)
    if check_context_violation(getattr(msg, "content", "")):
        _send_context_guard_refusal(bridge, msg)
        return "failed"
    if _is_self_referential_evaluator_dispatch(msg, cmd):
        _mark_self_referential_evaluation_exploratory(msg)
        log.warning(
            "SELF_VERIFICATION_DISPATCH_REJECTED task_id=%s target=evaluator status=exploratory",
            getattr(msg, "id", ""),
        )
        return "exploratory"
    if _detect_survival_exposure(getattr(msg, "content", "")):
        _redact_bridge_item_for_survival_exposure(bridge, getattr(msg, "id", ""))
    active_task_count = _dispatch_active_task_count(task_mgr)
    if active_task_count >= MAX_CONCURRENT_TASKS:
        _log_dispatch_audit(msg, task_mgr, "blocked_concurrency", active_task_count=active_task_count)
    result = _ORIGINAL_DISPATCH_OR_REQUEUE(task_mgr, bridge, msg, workspace, cmd)
    if result == "ok":
        _send_notes_dispatch_acknowledgment(bridge, msg)
    return result


def _stamp_interface_received_commands(commands):
    received_at = datetime.now(timezone.utc).isoformat()
    received_monotonic = time.monotonic()
    for cmd in commands:
        if not isinstance(cmd, dict):
            continue
        cmd.setdefault(_INTERFACE_MESSAGE_RECEIVED_AT, received_at)
        cmd.setdefault(_INTERFACE_MESSAGE_RECEIVED_MONOTONIC, received_monotonic)
    return commands


def _poll_commands_with_interface_latency(self, *args, **kwargs):
    commands = _ORIGINAL_MIRA_POLL_COMMANDS(self, *args, **kwargs)
    if isinstance(commands, list):
        return _stamp_interface_received_commands(commands)
    return commands


def _install_interface_latency_instrumentation() -> None:
    if Mira is None or _ORIGINAL_MIRA_POLL_COMMANDS is None:
        return
    if getattr(Mira, "poll_commands", None) is not _poll_commands_with_interface_latency:
        Mira.poll_commands = _poll_commands_with_interface_latency


def _attach_interface_received_timestamp(msg, cmd: dict | None = None) -> None:
    if not isinstance(cmd, dict):
        return
    if _INTERFACE_MESSAGE_RECEIVED_AT not in cmd and _INTERFACE_MESSAGE_RECEIVED_MONOTONIC not in cmd:
        return
    metadata = dict(getattr(msg, "metadata", {}) or {})
    if _INTERFACE_MESSAGE_RECEIVED_AT in cmd:
        metadata.setdefault(_INTERFACE_MESSAGE_RECEIVED_AT, cmd[_INTERFACE_MESSAGE_RECEIVED_AT])
    if _INTERFACE_MESSAGE_RECEIVED_MONOTONIC in cmd:
        metadata.setdefault(_INTERFACE_MESSAGE_RECEIVED_MONOTONIC, cmd[_INTERFACE_MESSAGE_RECEIVED_MONOTONIC])
    msg.metadata = metadata


def _record_interface_latency(msg, task_dispatched_at: float) -> None:
    metadata = dict(getattr(msg, "metadata", {}) or {})
    received_at = metadata.get(_INTERFACE_MESSAGE_RECEIVED_MONOTONIC)
    if received_at is None:
        return
    try:
        latency_ms = max(0, round((task_dispatched_at - float(received_at)) * 1000))
    except (TypeError, ValueError):
        return
    metadata[_INTERFACE_TASK_DISPATCHED_AT] = datetime.now(timezone.utc).isoformat()
    msg.metadata = metadata
    try:
        from mira import update_interface_latency as _update_iface_lat

        latency_avg = _update_iface_lat(latency_ms)
        heartbeat = MIRA_DIR / "heartbeat.json"
        if not heartbeat.exists():
            return
        data = json.loads(heartbeat.read_text(encoding="utf-8"))
        data["interface_latency_ms"] = latency_avg
        tmp = heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(heartbeat)
    except Exception as exc:
        log.debug("interface_latency write failed: %s", exc)


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


def _cross_validation_sampled() -> bool:
    if not getattr(shared_config, "CROSS_VALIDATION_ENABLED", False):
        return False
    try:
        sample_rate = float(getattr(shared_config, "CROSS_VALIDATION_SAMPLE_RATE", 0.2))
    except (TypeError, ValueError):
        sample_rate = 0.0
    sample_rate = max(0.0, min(1.0, sample_rate))
    return random() < sample_rate


def _result_source_agent(result: dict, rec) -> str:
    candidates = [
        result.get("agent"),
        result.get("agent_type"),
        result.get("declared_agent"),
        result.get("execution_agent"),
        getattr(rec, "task_type", ""),
    ]
    tags = getattr(rec, "tags", None)
    if isinstance(tags, list):
        candidates.extend(tags)
    registry = getattr(shared_config, "AGENT_REGISTRY", {})
    for candidate in candidates:
        agent = _normalize_task_distribution_category(candidate)
        if isinstance(registry, dict) and agent in registry:
            return agent
    return "general"


def _agent_tier(agent_name: str) -> str:
    agent_config = _agent_config(agent_name)
    if agent_config:
        return str(agent_config.get("tier") or "light").strip().lower() or "light"
    return "light"


def _select_cross_validation_peer(agent_name: str) -> str:
    registry = getattr(shared_config, "AGENT_REGISTRY", {})
    if not isinstance(registry, dict):
        return ""
    tier = _agent_tier(agent_name)
    source_local_only = _agent_requires_local_llm(agent_name)
    candidates = []
    for name, config in registry.items():
        candidate = _normalize_task_distribution_category(name)
        if not candidate or candidate in {agent_name, "super"} or not isinstance(config, dict):
            continue
        if str(config.get("tier") or "light").strip().lower() != tier:
            continue
        permissions = config.get("permissions") if isinstance(config.get("permissions"), dict) else {}
        if source_local_only and not bool(permissions.get("local_llm_only")):
            continue
        candidates.append(candidate)
    return choice(sorted(candidates)) if candidates else ""


def _cross_validation_output_text(rec, result: dict) -> str:
    workspace = getattr(rec, "workspace", "")
    if workspace:
        output_file = Path(workspace) / "output.md"
        try:
            if output_file.exists():
                text = output_file.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    return text
        except OSError:
            pass
    return str(result.get("summary") or getattr(rec, "summary", "") or "").strip()


def _cross_validation_status(rec, result: dict) -> str:
    return normalize_task_status(getattr(rec, "status", result.get("status", "")))


def _cross_validation_prompt(peer_agent: str, source_agent: str, rec, result: dict, output: str) -> str:
    clipped_output = output[:12000]
    if len(output) > len(clipped_output):
        clipped_output += "\n\n[Output truncated for lightweight peer validation.]"
    return (
        f"{CROSS_VALIDATION_PROMPT}\n\n"
        f"Peer agent: {peer_agent}\n"
        f"Original agent: {source_agent}\n"
        f"Task ID: {getattr(rec, 'task_id', '')}\n"
        f"Status: {_cross_validation_status(rec, result)}\n"
        f"Summary: {result.get('summary') or getattr(rec, 'summary', '')}\n\n"
        f"Output:\n{clipped_output}"
    )


def _cross_validation_verification(result: dict, rec) -> dict:
    if isinstance(result.get("verification"), dict):
        return dict(result["verification"])
    if isinstance(getattr(rec, "verification", None), dict):
        return dict(rec.verification)
    return {}


def _mark_cross_validation_flagged(rec, result: dict, result_file: Path, peer_agent: str, response: str) -> None:
    detail = response.strip() or "Peer validation did not return CONFIRMED."
    detail = detail[:1000]
    rec.status = "completed_unverified"
    rec.outcome_verified = False
    rec.verification_method = "cross_validation_flagged"
    rec.summary = f"{rec.summary}\n\nCross-validation flagged by {peer_agent}: {detail}".strip()
    verification = _cross_validation_verification(result, rec)
    raw_checks = verification.get("checks")
    checks = list(raw_checks) if isinstance(raw_checks, list) else []
    checks.append(
        {
            "name": "cross_validation",
            "passed": False,
            "agent": peer_agent,
            "message": detail,
        }
    )
    proxy_checked = str(verification.get("proxy_checked") or "").strip()
    verification.update(
        {
            "status": "failed",
            "verified": False,
            "summary": detail,
            "proxy_checked": f"{proxy_checked} + cross_validation" if proxy_checked else "cross_validation",
            "checks": checks,
        }
    )
    rec.verification = verification
    result["status"] = rec.status
    result["outcome_verified"] = False
    result["verification_method"] = rec.verification_method
    result["summary"] = rec.summary
    result["verification"] = rec.verification
    result["cross_validation"] = {
        "status": "flagged",
        "peer_agent": peer_agent,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "response": detail,
    }
    try:
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        log.debug("cross-validation result rewrite failed for %s: %s", rec.task_id, exc)


def _maybe_cross_validate_task_result(rec, result: dict, result_file: Path) -> None:
    if _cross_validation_status(rec, result) not in {"done", "verified"}:
        return
    if not _cross_validation_sampled():
        return
    source_agent = _result_source_agent(result, rec)
    peer_agent = _select_cross_validation_peer(source_agent)
    if not peer_agent:
        log.info("CROSS_VALIDATION_SKIPPED task_id=%s agent=%s reason=no_compatible_peer", rec.task_id, source_agent)
        return
    output = _cross_validation_output_text(rec, result)
    prompt = _cross_validation_prompt(peer_agent, source_agent, rec, result, output)
    try:
        response = (claude_think(prompt, timeout=60, tier=_agent_tier(peer_agent)) or "").strip()
    except Exception as exc:
        response = f"FLAGGED validation_error: {exc}"
    if response.upper().startswith("CONFIRMED"):
        log.info(
            "CROSS_VALIDATION_CONFIRMED task_id=%s agent=%s peer_agent=%s",
            rec.task_id,
            source_agent,
            peer_agent,
        )
        return
    _mark_cross_validation_flagged(rec, result, result_file, peer_agent, response)
    log.warning(
        "CROSS_VALIDATION_FLAGGED task_id=%s agent=%s peer_agent=%s details=%s",
        rec.task_id,
        source_agent,
        peer_agent,
        response[:500],
    )


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
        if isinstance(result, dict):
            _maybe_cross_validate_task_result(rec, result, result_file)
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


def _claude_api_reachable() -> bool:
    request = urllib.request.Request(
        CLAUDE_API_PING_URL,
        method="HEAD",
        headers={"User-Agent": "Mira/claude-reachability-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=CLAUDE_API_PING_TIMEOUT_SECONDS):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return True
        log.warning("Claude API ping failed with HTTP %s", exc.code)
        return False
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        log.warning("Claude API unreachable: %s", exc)
        return False


def _offline_fallback_message() -> str:
    try:
        return OFFLINE_FALLBACK_PROMPT.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("Offline fallback prompt unavailable: %s", exc)
        return "Received. Claude API connectivity is unavailable right now, so I will resume processing when connectivity is restored."


def _offline_command_item_id(cmd: dict) -> str:
    cmd_type = cmd.get("type", "")
    item_id = str(cmd.get("item_id") or "")
    if item_id:
        return item_id
    if cmd_type == "new_discussion":
        item_id = f"disc_{uuid.uuid4().hex[:8]}"
    elif cmd_type == "recall":
        item_id = f"req_recall_{uuid.uuid4().hex[:8]}"
    else:
        item_id = f"req_{uuid.uuid4().hex[:8]}"
    cmd["item_id"] = item_id
    return item_id


def _inject_offline_command_fallback(bridge, cmd: dict, fallback: str) -> None:
    if cmd.get("_offline_fallback_notified"):
        return
    cmd_type = cmd.get("type", "")
    item_id = _offline_command_item_id(cmd)
    sender = cmd.get("sender", "user")
    content = cmd.get("query") if cmd_type == "recall" else cmd.get("content", "")
    title = cmd.get("title") or (str(content)[:50] if content else "Offline request")
    tags = cmd.get("tags") or []
    try:
        if cmd_type == "reply":
            if bridge.item_exists(item_id):
                bridge.update_status(item_id, "queued", agent_message=fallback)
            else:
                bridge.create_discussion(item_id, title, str(content or ""), sender=sender, tags=tags)
                bridge.update_status(item_id, "queued", agent_message=fallback)
        elif cmd_type == "new_discussion":
            if not bridge.item_exists(item_id):
                bridge.create_discussion(item_id, title, str(content or ""), sender=sender, tags=tags)
            bridge.update_status(item_id, "queued", agent_message=fallback)
        else:
            if not bridge.item_exists(item_id):
                bridge.create_task(item_id, title, str(content or ""), sender=sender, tags=tags, origin="user")
            bridge.update_status(item_id, "queued", agent_message=fallback)
        cmd["_offline_fallback_notified"] = True
    except Exception as exc:
        log.warning("Offline fallback injection failed for command %s: %s", cmd.get("id") or item_id, exc)


def _process_offline_control_tasks(fallback: str) -> int:
    if not getattr(mira_config, "CONTROL_RUNTIME_DB_ENABLED", False) or getattr(
        mira_config, "BRIDGE_COMPAT_EXPORT_ENABLED", False
    ):
        return 0
    try:
        from control.db import transaction
        from control.repository import ControlRepository

        with transaction() as conn:
            repo = ControlRepository(conn)
            items = repo.list_dispatchable_tasks(limit=MAX_TASKS_PER_CYCLE)
            for item in items:
                repo.update_task_status(
                    item.get("user_id") or "ang",
                    item.get("id") or "",
                    "queued",
                    summary="Offline fallback delivered; waiting for Claude API connectivity.",
                    agent_message=fallback,
                )
            return len(items)
    except Exception as exc:
        log.warning("Offline control-plane fallback failed: %s", exc)
        return 0


def _do_talk_offline_fallback() -> None:
    fallback = _offline_fallback_message()
    handled = _process_offline_control_tasks(fallback)
    if Mira is None:
        return
    try:
        bridges = Mira.for_all_users(MIRA_DIR)
    except Exception as exc:
        log.warning("Offline fallback bridge discovery failed: %s", exc)
        return
    for bridge in bridges:
        try:
            for cmd in bridge.poll_commands():
                _inject_offline_command_fallback(bridge, cmd, fallback)
                if hasattr(bridge, "requeue_command"):
                    bridge.requeue_command(cmd)
                handled += 1
        except Exception as exc:
            log.warning("Offline fallback command processing failed for %s: %s", getattr(bridge, "user_id", "?"), exc)
    log.warning("Mira offline fallback active; deferred %d request(s)", handled)


def _health_cascade_summary(result: dict) -> str:
    trace = result.get("cascade_trace") if isinstance(result, dict) else []
    parts = []
    for step in trace if isinstance(trace, list) else []:
        if not isinstance(step, dict):
            continue
        parts.append(f"{step.get('step', '?')}={step.get('status', '?')}")
    root = result.get("root_cause") if isinstance(result, dict) else None
    return f"CASCADE status={result.get('status', 'unknown')} trace={' -> '.join(parts)} root_cause={root or 'none'}"


def _log_health_cascade_result(result: dict) -> None:
    summary = _health_cascade_summary(result)
    log.info(summary)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(HEALTH_CASCADE_LOG, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "summary": summary,
                        "result": result,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError as exc:
        log.debug("health cascade log write failed: %s", exc)


def _write_health_cascade_bridge_diagnostic(result: dict) -> None:
    if Mira is None:
        log.error("Health cascade dead and bridge unavailable: %s", result.get("root_cause"))
        return
    summary = _health_cascade_summary(result)
    message = (
        "Mira task processing is paused because the operational health cascade reports dead.\n\n"
        f"Root cause: {result.get('root_cause') or 'unknown'}\n\n"
        f"{summary}"
    )
    try:
        bridges = Mira.for_all_users(MIRA_DIR)
    except Exception as exc:
        log.error("Health cascade bridge diagnostic discovery failed: %s", exc)
        return
    for bridge in bridges:
        try:
            now = datetime.now(timezone.utc).isoformat()
            if bridge.item_exists(HEALTH_CASCADE_ALERT_ID):
                item = bridge._read_item(HEALTH_CASCADE_ALERT_ID)
                if not item:
                    continue
            else:
                item = bridge.create_item(
                    HEALTH_CASCADE_ALERT_ID,
                    "alert",
                    "Mira Health Cascade Failure",
                    message,
                    sender="agent",
                    tags=["system", "health", "cascade", "error"],
                    origin="agent",
                )
            item["type"] = "alert"
            item["title"] = "Mira Health Cascade Failure"
            item["status"] = "failed"
            item["origin"] = "agent"
            item["pinned"] = True
            item["tags"] = list(dict.fromkeys(["system", "health", "cascade", "error", *item.get("tags", [])]))
            item["error"] = {
                "code": "health_cascade_dead",
                "message": result.get("root_cause") or "Health cascade reported dead",
                "retryable": True,
                "timestamp": now,
            }
            messages = item.setdefault("messages", [])
            diagnostic = next((msg for msg in messages if msg.get("id") == "health_cascade_diagnostic"), None)
            if diagnostic is None:
                messages.append(
                    {
                        "id": "health_cascade_diagnostic",
                        "sender": "agent",
                        "content": message,
                        "timestamp": now,
                        "kind": "text",
                    }
                )
            else:
                diagnostic["sender"] = "agent"
                diagnostic["content"] = message
                diagnostic["timestamp"] = now
                diagnostic["kind"] = "text"
            item["updated_at"] = now
            bridge._write_item(item)
            bridge._update_manifest(item)
        except Exception as exc:
            log.error("Health cascade bridge diagnostic failed for %s: %s", getattr(bridge, "user_id", "?"), exc)


def do_talk():
    cascade_result = health_cascade()
    _log_health_cascade_result(cascade_result)
    if cascade_result.get("status") == "dead":
        _write_health_cascade_bridge_diagnostic(cascade_result)
        return None

    _install_interface_latency_instrumentation()
    _install_clear_intent_gate()
    _install_survival_dispatch_guard()
    talk_module.MAX_TASKS_PER_CYCLE = MAX_TASKS_PER_CYCLE
    legacy_mode = not bool(getattr(talk_module, "CONTROL_RUNTIME_DB_ENABLED", True))
    if not legacy_mode and not _claude_api_reachable():
        return _do_talk_offline_fallback()
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

    receipt_path = Path(record.workspace) / DISPATCH_RECEIPT_NAME
    if not receipt_path.exists():
        log.warning(
            "OPERATIONAL_AUDIT content_integrity task_id=%s status=%s receipt_path=%s "
            "suspect=missing_dispatch_receipt",
            record.task_id,
            record.status,
            receipt_path,
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

    if _is_conversation_content_record(record):
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


def _is_conversation_content_record(record) -> bool:
    """Return True for chat tasks where a short answer can be the correct output."""
    tag_keys = {
        str(tag or "").strip().casefold().replace("_", "-")
        for tag in getattr(record, "tags", []) or []
        if str(tag or "").strip()
    }
    return (
        str(getattr(record, "task_id", "") or "").strip() == "disc_daily_collab"
        or "discussion" in tag_keys
        or "conversation" in tag_keys
        or "daily-collab" in tag_keys
        or "daily collab" in tag_keys
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


def _record_network_status_in_heartbeat(network_status: dict) -> None:
    heartbeat = MIRA_DIR / "heartbeat.json"
    if not heartbeat.exists():
        log.debug("network status heartbeat write skipped: missing heartbeat file")
        return

    data = _load_heartbeat_data(heartbeat)
    if data is None:
        log.debug("network status heartbeat write skipped: unreadable heartbeat file")
        return

    data["network_status"] = network_status
    try:
        tmp = heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(heartbeat)
    except OSError as exc:
        log.debug("network status heartbeat write failed: %s", exc)


def _record_blocked_skill_backlog_in_heartbeat() -> None:
    heartbeat = MIRA_DIR / "heartbeat.json"
    if not heartbeat.exists():
        log.debug("blocked skill backlog heartbeat write skipped: missing heartbeat file")
        return

    data = _load_heartbeat_data(heartbeat)
    if data is None:
        log.debug("blocked skill backlog heartbeat write skipped: unreadable heartbeat file")
        return

    try:
        blocked_count = soul_manager.get_blocked_skill_count()
    except Exception as exc:
        log.debug("blocked skill backlog count failed: %s", exc)
        return

    data["blocked_skills_backlog"] = blocked_count
    if blocked_count > 5:
        data["blocked_skills_alert"] = True
    else:
        data.pop("blocked_skills_alert", None)
    try:
        tmp = heartbeat.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.rename(heartbeat)
    except OSError as exc:
        log.debug("blocked skill backlog heartbeat write failed: %s", exc)


def _check_network_connectivity_audit() -> None:
    import urllib.error
    import urllib.request

    global _LAST_NETWORK_STATUS
    network_status = {
        "endpoint": CLAUDE_API_PING_URL,
        "timeout_seconds": CLAUDE_API_PING_TIMEOUT_SECONDS,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reachable": False,
    }

    try:
        request = urllib.request.Request(CLAUDE_API_PING_URL, method="HEAD")
        with urllib.request.urlopen(request, timeout=CLAUDE_API_PING_TIMEOUT_SECONDS) as response:
            network_status["reachable"] = True
            network_status["http_status"] = response.status
    except urllib.error.HTTPError as exc:
        network_status["reachable"] = True
        network_status["http_status"] = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        network_status["error"] = f"{type(exc).__name__}: {exc}"
        log.warning(
            "Network unreachable. Mira is running offline. If local inference is configured, "
            "tasks may still proceed with degraded capability."
        )

    _LAST_NETWORK_STATUS = network_status
    _record_network_status_in_heartbeat(network_status)


def _log_skill_dependency_health(report: dict) -> None:
    if not isinstance(report, dict):
        return

    timestamp = datetime.now(timezone.utc).isoformat()
    lines: list[str] = []
    for skill_name, dependency_report in sorted(report.items()):
        if not isinstance(dependency_report, dict):
            continue
        for dependency, status in sorted(dependency_report.items()):
            normalized_status = str(status or "").strip().lower()
            if normalized_status not in {"broken", "unknown"}:
                continue
            lines.append(
                f"[DEPENDENCY] {timestamp} skill={skill_name} dependency={dependency} status={normalized_status}"
            )
            log.warning(
                "DEPENDENCY_HEALTH skill=%s dependency=%s status=%s",
                skill_name,
                dependency,
                normalized_status,
            )

    if not lines:
        return

    try:
        with open(Path("/tmp/mira-crash.log"), "a", encoding="utf-8") as crash_log:
            for line in lines:
                crash_log.write(line + "\n")
    except OSError as exc:
        log.debug("Dependency health crash log write failed: %s", exc)


def _check_skill_dependency_health_audit() -> None:
    try:
        _log_skill_dependency_health(audit_all_skill_dependencies())
    except Exception as exc:
        log.warning("OPERATIONAL_AUDIT dependency_health status=failed error=%s", exc)


# ---------------------------------------------------------------------------
# Startup health check — make invisible dependencies visible
# ---------------------------------------------------------------------------


@friction_monitor.track_friction(category="good", label="operational_audit")
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

    # 7. Network connectivity: centralized cloud APIs may be unavailable
    _check_network_connectivity_audit()

    # 8. Survival-critical components: no fallback, separate from strategic degradations
    survival_status = _check_survival_critical_components()
    survival_log = log.info if survival_status["tier"] == "ok" else log.warning
    survival_log(
        "OPERATIONAL_AUDIT survival_status=%s",
        json.dumps(survival_status, ensure_ascii=False, sort_keys=True),
    )

    # 9. Friction classification: tag audit roots as cognitive or infrastructure
    try:
        friction_report = audit_friction(OPERATIONAL_AUDIT_FRICTION_STEPS)
        for entry in friction_report.get("cognitive", []):
            if not isinstance(entry, dict):
                continue
            step = str(entry.get("step") or "")
            if step.startswith("operational_audit."):
                log.info(
                    "OPERATIONAL_AUDIT friction step=%s friction_type=cognitive root_cause=good_friction rationale=%s",
                    step,
                    entry.get("rationale") or "",
                )
        for entry in friction_report.get("elimination_candidates", []):
            if not isinstance(entry, dict):
                continue
            step = str(entry.get("step") or "")
            if step.startswith("operational_audit."):
                log.warning(
                    "OPERATIONAL_AUDIT friction step=%s friction_type=infrastructure "
                    "root_cause=bad_friction candidate=eliminate rationale=%s",
                    step,
                    entry.get("rationale") or "",
                )
        if not friction_report.get("passed", False):
            log.warning(
                "OPERATIONAL_AUDIT friction_registry status=failed missing=%s invalid=%s",
                friction_report.get("missing", []),
                friction_report.get("invalid", []),
            )
    except Exception as exc:
        log.warning("OPERATIONAL_AUDIT friction_registry status=failed error=%s", exc)


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
            if source_name == "icloud_bridge_inbox" and path.is_dir() and not any(path.iterdir()):
                continue
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


def _load_service_dependencies() -> tuple[dict, list[dict]]:
    if not SERVICE_DEPENDENCIES_FILE.exists():
        return {"dependencies": []}, []

    try:
        import yaml

        data = yaml.safe_load(SERVICE_DEPENDENCIES_FILE.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("SERVICE_DEPENDENCY_CONFIG_READ_FAILED path=%s error=%s", SERVICE_DEPENDENCIES_FILE, exc)
        return {"dependencies": []}, []

    if isinstance(data, list):
        data = {"dependencies": data}
    if not isinstance(data, dict):
        log.warning("SERVICE_DEPENDENCY_CONFIG_INVALID path=%s", SERVICE_DEPENDENCIES_FILE)
        return {"dependencies": []}, []

    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list):
        log.warning("SERVICE_DEPENDENCY_CONFIG_INVALID dependencies=non-list path=%s", SERVICE_DEPENDENCIES_FILE)
        return data, []

    return data, [dep for dep in dependencies if isinstance(dep, dict) and not dep.get("disabled")]


def _write_service_dependencies(data: dict) -> None:
    try:
        import yaml

        SERVICE_DEPENDENCIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE_DEPENDENCIES_FILE.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning("SERVICE_DEPENDENCY_CONFIG_WRITE_FAILED path=%s error=%s", SERVICE_DEPENDENCIES_FILE, exc)


def _service_dependency_digest(body: str) -> str:
    normalized = re.sub(r"\s+", " ", body).strip()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _fetch_service_dependency_digest(dep: dict) -> dict:
    url = str(dep.get("url") or "").strip()
    name = str(dep.get("name") or url).strip()
    if not url:
        raise ValueError("missing dependency URL")

    from tools.web_browser import fetch_raw

    body = fetch_raw(url, timeout=SERVICE_DEPENDENCY_TIMEOUT_SECONDS)
    if not body.strip():
        raise ValueError("empty response")

    return {
        "name": name,
        "url": url,
        "sha256": _service_dependency_digest(body),
        "content_length": len(body),
    }


def _append_external_dependency_warnings_to_journal(warnings: list[dict], user_id: str = "ang") -> None:
    if not warnings:
        return

    try:
        from user_paths import user_journal_dir

        journal_path = user_journal_dir(user_id) / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    except Exception:
        journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"

    lines = ["## WARNING: External Service Dependency Change"]
    for warning in warnings:
        lines.append(
            "- WARNING: "
            f"{warning['name']} changed at {warning['url']} "
            f"(baseline {warning['baseline_sha256'][:12]}, current {warning['current_sha256'][:12]}). "
            "Review terms, pricing, and free-tier availability before treating it as trusted infrastructure."
        )

    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as journal_file:
            journal_file.write("\n\n---\n\n" + "\n".join(lines) + "\n")
    except OSError as exc:
        log.debug("External dependency journal warning write failed: %s", exc)


def _log_external_dependency_warnings(warnings: list[dict]) -> None:
    if not warnings:
        return

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(SERVICE_DEPENDENCY_LOG, "a", encoding="utf-8") as f:
            for warning in warnings:
                f.write(json.dumps(warning, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("External dependency warning log write failed: %s", exc)

    for warning in warnings:
        log.warning(
            "SERVICE_DEPENDENCY_CHANGED agent=explorer name=%s url=%s baseline=%s current=%s",
            warning["name"],
            warning["url"],
            warning["baseline_sha256"],
            warning["current_sha256"],
        )


def _check_external_dependencies() -> None:
    state = load_state()
    today_key = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_external_dependency_check") == today_key:
        return

    data, dependencies = _load_service_dependencies()
    if not dependencies:
        state["last_external_dependency_check"] = today_key
        save_state(state)
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed

    checked_at = datetime.now(timezone.utc).isoformat()
    warnings: list[dict] = []
    changed = False
    max_workers = min(SERVICE_DEPENDENCY_MAX_WORKERS, max(1, len(dependencies)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_dep = {executor.submit(_fetch_service_dependency_digest, dep): dep for dep in dependencies}
        for future in as_completed(future_to_dep):
            dep = future_to_dep[future]
            try:
                result = future.result()
            except Exception as exc:
                log.warning(
                    "SERVICE_DEPENDENCY_FETCH_FAILED agent=explorer name=%s url=%s error=%s",
                    dep.get("name", "unknown"),
                    dep.get("url", ""),
                    exc,
                )
                continue

            baseline = str(dep.get("baseline_sha256") or dep.get("baseline_content_hash") or "").strip()
            if not baseline:
                dep["baseline_sha256"] = result["sha256"]
                changed = True
            elif result["sha256"] != baseline:
                warnings.append(
                    {
                        "timestamp": checked_at,
                        "event": "service_dependency_changed",
                        "agent": "explorer",
                        "name": result["name"],
                        "url": result["url"],
                        "baseline_sha256": baseline,
                        "current_sha256": result["sha256"],
                        "content_length": result["content_length"],
                    }
                )

            dep["last_checked"] = checked_at
            changed = True

    if changed:
        _write_service_dependencies(data)

    if warnings:
        _log_external_dependency_warnings(warnings)
        _append_external_dependency_warnings_to_journal(warnings)

    state["last_external_dependency_check"] = today_key
    save_state(state)


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


def _should_run_proxy_recalibration() -> bool:
    now = datetime.now()
    if now.hour < 10 or now.hour >= 18:
        return False

    state = load_state()
    last = state.get("last_proxy_recalibration", "")
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


def _should_skill_reaudit() -> bool:
    state = load_state()
    last = state.get("last_skill_reaudit", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                elapsed = (datetime.now() - last_dt).total_seconds()
            else:
                elapsed = (datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc)).total_seconds()
            if elapsed < 7 * 24 * 3600:
                return False
        except ValueError:
            pass
    return True


def _should_security_reaudit() -> bool:
    state = load_state()
    last = state.get("last_security_reaudit", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                elapsed = (datetime.now() - last_dt).total_seconds()
            else:
                elapsed = (datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc)).total_seconds()
            if elapsed < 24 * 3600:
                return False
        except ValueError:
            pass
    return True


def _should_canary_skill_audit() -> bool:
    state = load_state()
    last = state.get("last_canary_skill_audit", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                elapsed = (datetime.now() - last_dt).total_seconds()
            else:
                elapsed = (datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc)).total_seconds()
            if elapsed < 7 * 24 * 3600:
                return False
        except ValueError:
            pass
    return True


def _writer_proxy_review_interval_days() -> int:
    raw = os.getenv("MIRA_WRITER_PROXY_REVIEW_INTERVAL_DAYS")
    if raw is None:
        raw = getattr(
            shared_config,
            "WRITER_PROXY_REVIEW_INTERVAL_DAYS",
            getattr(mira_config, "WRITER_PROXY_REVIEW_INTERVAL_DAYS", 30),
        )
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 30


def _writer_proxy_review_last_timestamp() -> float | None:
    try:
        content = WRITER_PROXY_REVIEW_LAST_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return _parse_recovery_timestamp(content)


def _should_review_writer_proxy() -> bool:
    last_ts = _writer_proxy_review_last_timestamp()
    if last_ts is None:
        return True
    return time.time() - last_ts >= _writer_proxy_review_interval_days() * 24 * 3600


def _anti_ai_quality_guard_failure_threshold() -> float:
    raw = os.getenv("MIRA_ANTI_AI_QUALITY_GUARD_FAILURE_RATE_THRESHOLD")
    if raw is None:
        raw = getattr(
            shared_config,
            "ANTI_AI_QUALITY_GUARD_FAILURE_RATE_THRESHOLD",
            getattr(mira_config, "ANTI_AI_QUALITY_GUARD_FAILURE_RATE_THRESHOLD", 0.0),
        )
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _should_anti_ai_quality_guard_check() -> bool:
    now = datetime.now()
    if now.weekday() != 6 or now.hour < 9 or now.hour >= 12:
        return False

    last_ts = _parse_recovery_timestamp(load_state().get("last_anti_ai_quality_guard_check"))
    if last_ts is not None and time.time() - last_ts < 7 * 24 * 3600:
        return False
    return True


def _recent_anti_ai_proxy_drift_entries(now: datetime) -> list[dict]:
    if not ANTI_AI_PROXY_DRIFT_LOG_PATH.exists():
        return []
    try:
        data = json.loads(ANTI_AI_PROXY_DRIFT_LOG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Anti-AI proxy drift log read failed: %s", exc)
        return []
    if not isinstance(data, list):
        return []

    cutoff = (now - timedelta(days=7)).timestamp()
    now_ts = now.timestamp()
    entries: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        timestamp = _parse_recovery_timestamp(entry.get("timestamp"))
        if timestamp is None or timestamp < cutoff or timestamp > now_ts:
            continue
        entries.append(entry)
    return entries


def check_anti_ai_quality_guard(user_id: str = "ang") -> dict[str, object]:
    now = datetime.now(timezone.utc)
    entries = _recent_anti_ai_proxy_drift_entries(now)
    total = len(entries)
    failures = sum(1 for entry in entries if entry.get("passed") is False)
    failure_rate = failures / total if total else 0.0
    threshold = _anti_ai_quality_guard_failure_threshold()
    result = {
        "entries": total,
        "failures": failures,
        "failure_rate": round(failure_rate, 4),
        "threshold": threshold,
        "alert_emitted": False,
    }

    if total > 0 and failure_rate <= threshold:
        try:
            from notes_bridge import send_to_outbox

            sent_path = send_to_outbox(
                ANTI_AI_QUALITY_GUARD_ALERT_MESSAGE,
                metadata={
                    "type": "alert",
                    "source": "anti_ai_quality_guard",
                    "user_id": user_id,
                    "entries": total,
                    "failures": failures,
                    "failure_rate": round(failure_rate, 4),
                    "threshold": threshold,
                },
            )
            result["alert_emitted"] = bool(sent_path)
        except Exception as exc:
            log.warning("Anti-AI quality guard alert failed: %s", exc)
            result["alert_error"] = str(exc)

    state = load_state()
    state["last_anti_ai_quality_guard_check"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return result


def _security_drift_failure_keys() -> set[str]:
    if not SECURITY_DRIFT_LOG.exists():
        return set()
    keys: set[str] = set()
    try:
        for line in SECURITY_DRIFT_LOG.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = entry.get("failure_key") if isinstance(entry, dict) else None
            if key:
                keys.add(str(key))
    except OSError as exc:
        log.debug("security drift log read failed: %s", exc)
    return keys


def _append_security_drift_alert(entry: dict) -> None:
    try:
        SECURITY_DRIFT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SECURITY_DRIFT_LOG.open("a", encoding="utf-8") as drift_log:
            drift_log.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("security drift log write failed: %s", exc)


def security_reaudit() -> dict[str, int]:
    counts = {"checked": 0, "passed": 0, "failed": 0, "alerted": 0, "skipped": 0}
    known_failure_keys = _security_drift_failure_keys()

    try:
        skill_files = soul_manager.list_skill_files()
    except Exception as exc:
        log.error("security_reaudit: could not list skill files: %s", exc)
        return counts

    for skill_file in skill_files:
        skill_file = Path(skill_file)
        try:
            content = skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            counts["skipped"] += 1
            log.warning("security_reaudit: cannot read %s: %s", skill_file, exc)
            continue

        skill_name = skill_file.stem
        metadata = soul_manager.skill_metadata_from_frontmatter(content)
        if metadata.get("name"):
            skill_name = str(metadata["name"]).strip() or skill_name
        metadata.setdefault("source_path", str(skill_file))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        counts["checked"] += 1
        try:
            result = soul_manager.audit_skill(
                skill_name,
                content,
                source_url=str(skill_file),
                introduced_by="security_reaudit",
                source=str(metadata.get("source") or "internal"),
                metadata=metadata,
                caller_agent="super",
                invocation_source="security_reaudit",
                source_agent="super",
            )
            if not isinstance(result, dict) or not isinstance(result.get("blocked"), bool):
                raise ValueError(f"unexpected audit result: {result!r}")
        except Exception as exc:
            result = {
                "blocked": True,
                "reason": f"audit_infra_failure: {exc}",
                "categories": ["audit_infra_failure"],
            }
            log.warning("AUDIT_INFRA_FAILURE: security_reaudit audit_skill raised %s", exc)

        if result["blocked"]:
            counts["failed"] += 1
            reason = str(result.get("reason") or result.get("blocked_reason") or "blocked")
            categories = result.get("categories", [])
            failure_key = f"{skill_file}:{content_hash}"
            if failure_key not in known_failure_keys:
                alert = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "security_drift_alert",
                    "alert": "SECURITY_DRIFT_ALERT",
                    "skill_name": skill_name,
                    "path": str(skill_file),
                    "content_hash": content_hash,
                    "failure_key": failure_key,
                    "reason": reason,
                    "categories": categories,
                }
                _append_security_drift_alert(alert)
                known_failure_keys.add(failure_key)
                counts["alerted"] += 1
                log.warning(
                    "SECURITY_DRIFT_ALERT skill=%s path=%s reason=%s categories=%s",
                    skill_name,
                    skill_file,
                    reason,
                    categories,
                )
        else:
            counts["passed"] += 1

    log.info(
        "security_reaudit: checked=%d passed=%d failed=%d alerted=%d skipped=%d",
        counts["checked"],
        counts["passed"],
        counts["failed"],
        counts["alerted"],
        counts["skipped"],
    )
    return counts


def _alert_canary_skill_audit_failure(passed_canaries: list[dict]) -> None:
    lines = "\n".join(f"- {item['skill_name']}: {item['path']}" for item in passed_canaries)
    message = (
        "Canary skill audit failed. A known-dangerous skill passed Mira's mandatory audit.\n\n"
        f"{lines}\n\n"
        "Treat this as a critical audit drift incident before enabling or importing any new skills."
    )
    log.critical("CANARY_SKILL_AUDIT_FAILURE: %s", passed_canaries)

    if Mira is None:
        log.error("Cannot write canary skill audit alert: bridge unavailable")
        return

    try:
        bridge = Mira(MIRA_DIR, user_id="ang")
        title = "Canary Skill Audit Failure"
        if bridge.item_exists(CANARY_SKILL_AUDIT_ALERT_ID):
            bridge.append_message(CANARY_SKILL_AUDIT_ALERT_ID, "agent", message)
            item = bridge._read_item(CANARY_SKILL_AUDIT_ALERT_ID)
        else:
            item = bridge.create_item(
                CANARY_SKILL_AUDIT_ALERT_ID,
                "alert",
                title,
                message,
                sender="agent",
                tags=["security", "skill_audit", "canary", "critical"],
                origin="agent",
            )
        if item:
            item["type"] = "alert"
            item["title"] = title
            item["status"] = "failed"
            item["origin"] = "agent"
            item["pinned"] = True
            item["tags"] = list(dict.fromkeys(["security", "skill_audit", "canary", "critical", *item.get("tags", [])]))
            item["error"] = {
                "code": "canary_skill_audit_failed",
                "message": lines,
                "retryable": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            bridge._write_item(item)
            bridge._update_manifest(item)
    except Exception as exc:
        log.error("Failed to write canary skill audit alert: %s", exc)


def canary_skill_audit() -> dict[str, int]:
    from soul_manager import audit_skill

    counts = {"checked": 0, "blocked": 0, "passed": 0, "alerted": 0, "skipped": 0}
    passed_canaries: list[dict] = []

    for canary_file in sorted(CANARY_SKILLS_DIR.glob("*.skill")):
        try:
            content = canary_file.read_text(encoding="utf-8")
        except OSError as exc:
            counts["skipped"] += 1
            log.warning("canary_skill_audit: cannot read %s: %s", canary_file, exc)
            continue

        skill_name = canary_file.stem
        metadata = {
            "source_path": str(canary_file),
            "source": "canary_skill_audit",
            "canary": True,
        }
        counts["checked"] += 1
        try:
            result = audit_skill(
                skill_name,
                content,
                source_url=str(canary_file),
                introduced_by="canary_skill_audit",
                source="canary",
                metadata=metadata,
                caller_agent="super",
                invocation_source="canary_skill_audit",
                source_agent="super",
            )
            if not isinstance(result, dict) or not isinstance(result.get("blocked"), bool):
                raise ValueError(f"unexpected audit result: {result!r}")
        except Exception as exc:
            counts["blocked"] += 1
            log.warning("AUDIT_INFRA_FAILURE: canary_skill_audit audit_skill raised %s", exc)
            continue

        if result["blocked"]:
            counts["blocked"] += 1
        else:
            counts["passed"] += 1
            passed_canaries.append(
                {
                    "skill_name": skill_name,
                    "path": str(canary_file),
                    "result": result,
                }
            )

    if passed_canaries:
        counts["alerted"] = len(passed_canaries)
        _alert_canary_skill_audit_failure(passed_canaries)

    log.info(
        "canary_skill_audit: checked=%d blocked=%d passed=%d alerted=%d skipped=%d",
        counts["checked"],
        counts["blocked"],
        counts["passed"],
        counts["alerted"],
        counts["skipped"],
    )
    return counts


def _alert_canary_self_audit_failure(claim: str, detail: str) -> None:
    message = (
        "Canary self-audit failed. A synthetic false completion claim was not caught by output verification.\n\n"
        f"Claim: {claim}\n"
        f"Verification result: {detail}\n\n"
        "Mira task processing has been halted until the verification pipeline is inspected."
    )
    log.critical("CANARY_SELF_AUDIT_FAILURE claim=%r detail=%s", claim, detail)

    if Mira is None:
        log.error("Cannot write canary self-audit alert: bridge unavailable")
        return

    try:
        bridge = Mira(MIRA_DIR, user_id="ang")
        title = "Canary Self-Audit Failure"
        if bridge.item_exists(CANARY_SELF_AUDIT_ALERT_ID):
            bridge.append_message(CANARY_SELF_AUDIT_ALERT_ID, "agent", message)
            item = bridge._read_item(CANARY_SELF_AUDIT_ALERT_ID)
        else:
            item = bridge.create_item(
                CANARY_SELF_AUDIT_ALERT_ID,
                "alert",
                title,
                message,
                sender="agent",
                tags=["system", "verification", "canary", "critical"],
                origin="agent",
            )
        if item:
            item["type"] = "alert"
            item["title"] = title
            item["status"] = "failed"
            item["origin"] = "agent"
            item["pinned"] = True
            item["tags"] = list(dict.fromkeys(["system", "verification", "canary", "critical", *item.get("tags", [])]))
            item["error"] = {
                "code": "canary_self_audit_failed",
                "message": detail,
                "retryable": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            bridge._write_item(item)
            bridge._update_manifest(item)
    except Exception as exc:
        log.error("Failed to write canary self-audit alert: %s", exc)


def canary_self_audit() -> dict:
    if not CANARY_AUDIT_ENABLED:
        return {"status": "skipped", "reason": "disabled"}
    if already_run_today("canary_self_audit"):
        return {"status": "skipped", "reason": "already_run_today"}

    missing_path = ARTIFACTS_DIR / "canary_self_audit" / f"never_written_{uuid.uuid4().hex}.md"
    claim = f"created {missing_path}"
    try:
        from task_support import _verify_output

        verification_issue = _verify_output(claim, missing_path.parent)
    except Exception as exc:
        detail = f"verification routine raised {type(exc).__name__}: {exc}"
        _alert_canary_self_audit_failure(claim, detail)
        raise SystemAlert(detail) from exc

    if not verification_issue:
        detail = "verification returned success for a nonexistent file claim"
        _alert_canary_self_audit_failure(claim, detail)
        raise SystemAlert(detail)

    _mark_run_today("canary_self_audit")
    log.info("canary_self_audit: false claim caught: %s", verification_issue)
    return {"status": "passed", "claim": claim, "verification": verification_issue}


def _register_core_scheduled_jobs() -> None:
    from runtime import triggers
    from runtime.jobs import BACKGROUND_JOBS, JobSpec

    triggers.should_recalibrate_proxies = _should_recalibrate_proxies
    triggers.should_guard_calibration_prompt = _should_guard_calibration_prompt
    triggers.should_proxy_drift_check = _should_proxy_drift_check
    triggers.should_run_proxy_recalibration = _should_run_proxy_recalibration
    triggers.should_calibrate_proxies = _should_calibrate_proxies
    triggers.should_security_reaudit = _should_security_reaudit
    triggers.should_canary_skill_audit = _should_canary_skill_audit
    triggers.should_review_writer_proxy = _should_review_writer_proxy
    triggers.should_anti_ai_quality_guard_check = _should_anti_ai_quality_guard_check

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

    if not any(job.name == "proxy_recalibration" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="proxy_recalibration",
                command=["proxy-recalibration"],
                trigger="cooldown",
                trigger_name="should_run_proxy_recalibration",
                cooldown_hours=24 * 7,
                state_key_pattern="last_proxy_recalibration",
                priority=48,
                blocking_group="light",
                description="Weekly anti-AI and content-guard audit of published Substack articles",
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

    if not any(job.name == "review_writer_proxy" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="review_writer_proxy",
                command=["review-writer-proxy"],
                trigger="cooldown",
                trigger_name="should_review_writer_proxy",
                cooldown_hours=24 * _writer_proxy_review_interval_days(),
                state_key_pattern="last_proxy_review",
                priority=49,
                blocking_group="light",
                description="Monthly human review of writer anti-AI proxy calibration",
            )
        )

    if not any(job.name == "anti_ai_quality_guard_check" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="anti_ai_quality_guard_check",
                command=["anti-ai-quality-guard-check"],
                trigger="cooldown",
                trigger_name="should_anti_ai_quality_guard_check",
                cooldown_hours=24 * 7,
                state_key_pattern="last_anti_ai_quality_guard_check",
                priority=50,
                blocking_group="light",
                description="Weekly anti-AI quality guard pass/fail drift check",
            )
        )

    if not any(job.name == "security-reaudit" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="security-reaudit",
                command=["security-reaudit"],
                trigger="cooldown",
                trigger_name="should_security_reaudit",
                cooldown_hours=24,
                state_key_pattern="last_security_reaudit",
                priority=43,
                blocking_group="light",
                description="Daily security drift re-audit for installed skills",
            )
        )

    if not any(job.name == "canary_skill_audit" for job in BACKGROUND_JOBS):
        BACKGROUND_JOBS.append(
            JobSpec(
                name="canary_skill_audit",
                command=["canary-skill-audit"],
                trigger="cooldown",
                trigger_name="should_canary_skill_audit",
                cooldown_hours=24 * 7,
                state_key_pattern="last_canary_skill_audit",
                priority=42,
                blocking_group="light",
                description="Weekly adversarial canary run for the skill security audit",
            )
        )


def _daily_last_run_file(name: str) -> Path:
    if name == "evaluator":
        return EVALUATOR_LAST_RUN_FILE
    return MIRA_ROOT / "data" / f"{name}_last_run"


def already_run_today(name: str) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        return _daily_last_run_file(name).read_text(encoding="utf-8").strip() == today
    except OSError:
        return False


def _mark_run_today(name: str) -> None:
    path = _daily_last_run_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now().strftime("%Y-%m-%d") + "\n", encoding="utf-8")


def _set_append_only(path: Path) -> None:
    try:
        import stat

        append_flag = getattr(stat, "UF_APPEND", None)
        if append_flag is None or not hasattr(os, "chflags"):
            return
        flags = getattr(path.stat(), "st_flags", 0)
        if not flags & append_flag:
            os.chflags(path, flags | append_flag)
    except OSError as exc:
        log.debug("Could not set append-only flag for %s: %s", path, exc)


def run_evaluator_independent() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    task_id = f"independent_evaluator_{today}"
    report_path = EVALUATOR_LOG_DIR / f"{today}.md"
    if report_path.exists():
        _set_append_only(EVALUATOR_LOG_DIR)
        _set_append_only(report_path)
        _mark_run_today("evaluator")
        return report_path

    workspace = ARTIFACTS_DIR / "evaluator" / task_id
    workspace.mkdir(parents=True, exist_ok=True)

    from agent_registry import get_registry

    handler = get_registry().load_handler("evaluator")
    report = handler(
        workspace,
        task_id,
        "Independent scheduled evaluator run days=7",
        "launchagent",
        task_id,
    )
    if not report:
        raise RuntimeError("independent evaluator returned no report")

    EVALUATOR_LOG_DIR.mkdir(parents=True, exist_ok=True)
    _set_append_only(EVALUATOR_LOG_DIR)
    try:
        with open(report_path, "x", encoding="utf-8") as report_file:
            report_file.write(report.rstrip() + "\n")
    except FileExistsError:
        pass
    if report_path.exists():
        _set_append_only(report_path)
    _mark_run_today("evaluator")
    log.info("Independent evaluator report written: %s", report_path)
    return report_path


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


def run_proxy_recalibration() -> dict:
    from growth import audit_recent_posts

    result = audit_recent_posts(sample_size=10)
    state = load_state()
    state["last_proxy_recalibration"] = datetime.now().isoformat()
    save_state(state)
    return result


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


def _format_writer_proxy_review_message(samples: list[dict]) -> str:
    lines = [
        "Monthly writer proxy review.",
        "",
        f"Please review the writer agent's anti-ai.md checklist: {WRITER_ANTI_AI_CHECKLIST_FILE}",
        "",
        "Rate 3-5 recent writer artifacts for authenticity on a 1-5 scale:",
        "1 = does not sound like WA, 5 = authentic WA voice and intent.",
        "",
    ]
    if samples:
        lines.append("Candidate artifacts:")
        for index, sample in enumerate(samples[:5], start=1):
            lines.extend(
                [
                    f"{index}. {sample['title']}",
                    f"Artifact: {sample['path']}",
                    f"Excerpt: {sample['excerpt']}",
                    f"Authenticity rating {index}: _/5",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "I could not find enough recent guarded writer artifacts automatically.",
                "Please choose 3-5 recent writer outputs from the artifacts directory and rate them manually.",
                "",
            ]
        )
    lines.extend(
        [
            "If the ratings show proxy drift, update anti-ai.md with the missing rule, example, or hard ban.",
            "Reply with ratings and any checklist changes needed.",
        ]
    )
    return "\n".join(lines)


def _send_writer_proxy_review_message(user_id: str, message: str, samples: list[dict], now: datetime) -> bool:
    item_id = f"review_writer_proxy_{now.strftime('%Y%m%d')}"
    title = f"Writer proxy review {now.strftime('%Y-%m-%d')}"
    if Mira is not None:
        try:
            bridge = Mira(MIRA_DIR, user_id=user_id)
            if bridge.item_exists(item_id):
                log.info("Writer proxy review prompt already exists for %s", now.strftime("%Y-%m-%d"))
                return True
            item = bridge.create_discussion(
                item_id,
                title,
                message,
                sender="agent",
                tags=["mira", "writer", "anti-ai", "proxy-review", "calibration"],
            )
            item["proxy_review_samples"] = samples
            item["anti_ai_checklist"] = str(WRITER_ANTI_AI_CHECKLIST_FILE)
            bridge._write_item(item)
            bridge._update_manifest(item)
            return True
        except Exception as exc:
            log.debug("Writer proxy review bridge send failed: %s", exc)

    try:
        from notes_bridge import send_to_outbox

        send_to_outbox(
            message,
            metadata={
                "item_id": item_id,
                "user_id": user_id,
                "kind": "writer_proxy_review",
                "anti_ai_checklist": str(WRITER_ANTI_AI_CHECKLIST_FILE),
            },
        )
        return True
    except Exception as exc:
        log.error("Writer proxy review send failed: %s", exc)
        return False


def _mark_writer_proxy_review_sent(now: datetime) -> None:
    WRITER_PROXY_REVIEW_LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WRITER_PROXY_REVIEW_LAST_FILE.write_text(now.astimezone(timezone.utc).isoformat() + "\n", encoding="utf-8")


def review_writer_proxy(user_id: str = "ang") -> bool:
    now = datetime.now(timezone.utc)
    last_ts = _writer_proxy_review_last_timestamp()
    if last_ts is not None and now.timestamp() - last_ts < _writer_proxy_review_interval_days() * 24 * 3600:
        log.info("Writer proxy review not due")
        return False

    samples = _recent_guarded_writing_artifacts(5)
    message = _format_writer_proxy_review_message(samples)
    if not _send_writer_proxy_review_message(user_id, message, samples, now):
        return False
    _mark_writer_proxy_review_sent(now)
    log.info("Writer proxy review prompt sent")
    return True


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
    _cycle_timing = _start_cycle_timing()
    _cycle_wall_start = datetime.now(timezone.utc)
    log.info("=== Mira Agent wake ===")
    _update_coattention(
        "full wake cycle: talk, health checks, pipeline maintenance, scheduled work",
        "Annotate this entry with anything Mira should notice during this cycle.",
    )
    _record_stale_heartbeat_mourning(time.time())
    _prune_old_logs_if_due()

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

    _stale_path = LOGS_DIR / "pipeline_stale.json"
    _stale_components = _check_stale_pipelines()
    if _stale_components:
        try:
            _stale_path.write_text(
                json.dumps({"stale": _stale_components, "checked_at": time.time()}),
                encoding="utf-8",
            )
        except Exception as _se:
            log.debug("pipeline_stale write failed: %s", _se)
    elif _stale_path.exists():
        try:
            _stale_path.unlink()
        except OSError as _se:
            log.debug("pipeline_stale clear failed: %s", _se)

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

    try:
        _check_stale_eval_metrics()
    except Exception as _sme:
        log.debug("stale eval metric check failed: %s", _sme)

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
                    if _latency_ms > IPHONE_BRIDGE_WARN_LATENCY_MS:
                        log.warning(
                            "iphone_msg_pickup_latency_ms=%d file=%s",
                            _latency_ms,
                            _msg_file,
                        )
                    else:
                        log.info(
                            "iphone_msg_pickup_latency_ms=%d file=%s",
                            _latency_ms,
                            _msg_file,
                        )
                except OSError:
                    pass

    # Mira first (lightweight, fast) — CRITICAL PATH
    _t0 = _time.monotonic()
    _talk_llm_s0 = _cycle_timing["inference_s"]
    _talk_ok = True
    log_authorization_event("talk", "iphone_bridge", "high", bypassed_check=False)
    try:
        with _timed_phase("inbox_processing", "orchestration"), _timed_llm_calls("inbox_processing"):
            do_talk()
    except Exception as e:
        log.error("Mira failed: %s", e)
        _talk_ok = False
    _talk_llm_ms = round((_cycle_timing["inference_s"] - _talk_llm_s0) * 1000)
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

    _record_blocked_skill_backlog_in_heartbeat()

    if _LAST_NETWORK_STATUS is not None:
        _record_network_status_in_heartbeat(_LAST_NETWORK_STATUS)

    if should_shutdown():
        _cycle_timing_record = _finish_cycle_timing(_cycle_timing)
        log.info("CYCLE_TIMING %s", json.dumps(_cycle_timing_record, sort_keys=True))
        log.info("Shutdown requested — exiting after talk phase")
        return

    # Timing guard: skip non-critical checks if cycle already > 8s
    _elapsed = _time.monotonic() - _cycle_start
    if _elapsed < 8:
        # Auto-advance writing projects stuck in plan_ready (no more Notes approval)
        _t0 = _time.monotonic()
        _write_ok = True
        _writer_advanced = 0
        _write_llm_s0 = _cycle_timing["inference_s"]
        try:
            with _timed_stage("write"):
                with (
                    _sub_agent_pipeline_context("writer"),
                    _timed_phase("pipeline_step.writing", "orchestration"),
                    _timed_llm_calls("pipeline_step.writing"),
                ):
                    _writer_advanced = _run_canonical_writing_pipeline()
        except Exception as e:
            log.error("Writing response check failed: %s", e)
            _write_ok = False
        _model_wait_ms += round((_cycle_timing["inference_s"] - _write_llm_s0) * 1000)
        _write_dur = _time.monotonic() - _t0
        _phase_times["writing_responses"] = round(_write_dur * 1000)
        _record_perf_stat("writer", "writing_pipeline", _write_dur, _write_ok)
        if _write_ok and _writer_advanced:
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
        with _timed_phase("pipeline_step.followups", "orchestration"):
            jobs_module._dispatch_pipeline_followups(_completed_bg, _session_new)
            update_joint_attention(_joint_attention_topic_from_completed_background(_completed_bg))
        _phase_times["pipeline_chain"] = round((_time.monotonic() - _t0) * 1000)

    # Reap stale PID files (hourly) — prevents stuck tasks
    _t0 = _time.monotonic()
    _reap_stale_pids()
    _phase_times["reap_pids"] = round((_time.monotonic() - _t0) * 1000)

    # --- Publishing pipeline: publish -> podcast -> sweep ---
    _t0 = _time.monotonic()
    with _timed_phase("publish", "tool"):
        log_authorization_event("pending_publish", "internal", "normal", bypassed_check=False)
        if publish_blocked:
            _log_substack_publish_block("pending publish pipeline")
        else:
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
    with _timed_phase("agent_dispatch", "tool"):
        _dispatch_scheduled_jobs(_session_new)
    current_hour = datetime.now().hour
    if current_hour == EVALUATOR_SCHEDULE_HOUR and not already_run_today("evaluator"):
        run_evaluator_independent()
    try:
        _check_external_dependencies()
    except Exception as e:
        log.debug("external dependency check failed: %s", e)

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
            if _SKILL_AUDIT_INTEGRITY_OK:
                try:
                    reaudit_all_enabled_skills()
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
            else:
                log.error("Skill re-audit skipped because audit module integrity check is failing")

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
    _cycle_timing_record = _finish_cycle_timing(_cycle_timing)
    _orch_ms = sum(_phase_times.values())
    log.info("CYCLE_TIMING %s", json.dumps(_cycle_timing_record, sort_keys=True))
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
                        **_cycle_timing_record,
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
        _pagg = {"dispatch_ms": 0, "inference_ms": 0, "tools_ms": 0, "total_ms": 0, "n": 0}
        if _phase_log.exists():
            _phase_lines = _phase_log.read_text(encoding="utf-8").splitlines()[-50:]
            for _pl in _phase_lines:
                try:
                    _pr = json.loads(_pl)
                    _pr_ts = _pr.get("ts")
                    if not _pr_ts:
                        continue
                    _pr_dt = datetime.fromisoformat(str(_pr_ts).replace("Z", "+00:00"))
                    if _pr_dt.tzinfo is None:
                        _pr_dt = _pr_dt.replace(tzinfo=timezone.utc)
                    if _pr_dt < _cycle_wall_start:
                        continue
                    _pagg["dispatch_ms"] += _pr.get("phase_dispatch_ms", 0)
                    _pagg["inference_ms"] += _pr.get("phase_inference_ms", 0)
                    _pagg["tools_ms"] += _pr.get("phase_tools_ms", 0)
                    _pagg["total_ms"] += _pr.get("total_ms", 0)
                    _pagg["n"] += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
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
                scheduler_age = _recent_scheduler_success_age(component, now)
                if scheduler_age is not None and scheduler_age <= 6 * 3600:
                    log.debug(
                        "blind spot suppressed for %s: scheduler succeeded %ds ago",
                        component,
                        int(scheduler_age),
                    )
                    continue
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


def _tail_file(path: Path, *, max_lines: int = 40, max_chars: int = 2000) -> str:
    try:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        text = "\n".join(lines).strip()
        if len(text) > max_chars:
            return text[-max_chars:]
        return text
    except OSError:
        return ""


def _emit_output_stale_probe(state: dict, field: str, title: str, body: str) -> None:
    """Record output-liveness findings for assessment without pushing Home noise."""
    now = time.time()
    last = _parse_recovery_timestamp(state.get(field)) or 0
    if now - last < 6 * 3600:
        return
    state[field] = datetime.now(timezone.utc).isoformat()
    findings = state.setdefault("output_liveness_findings", [])
    findings.append(
        {
            "field": field,
            "title": title,
            "body": body,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    del findings[:-20]
    log.warning("Output liveness finding recorded: %s", title)


def _latest_task_result_mtime() -> float | None:
    if not TASKS_DIR.is_dir():
        return None
    latest = None
    try:
        for path in TASKS_DIR.rglob("result.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
    except OSError:
        return None
    return latest


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

    anomalies = _scheduled_pipeline_blind_spots(now, _read_last_outputs())
    for anomaly in anomalies:
        _blind_spot_warn(
            "scheduled pipeline trigger is active but output is absent or stale",
            **anomaly,
        )

    # Symptom-driven tracing: when "regular output" is missing, push a trace.
    latest_task_result = _latest_task_result_mtime()
    if latest_task_result is not None:
        task_gap = now - latest_task_result
        if task_gap > 24 * 3600:
            hours = int(task_gap // 3600)
            err_tail = _tail_file(Path("/tmp/mira-agent.err"), max_lines=80, max_chars=4000)
            crash_tail = _tail_file(Path("/tmp/mira-crash.log"), max_lines=60, max_chars=4000)
            body = (
                f"No new `result.json` under `{TASKS_DIR}` for ~{hours}h. "
                "This is the earliest symptom; start tracing immediately.\n\n"
                "Last error tail:\n"
                f"{err_tail or '(empty)'}\n\n"
                "Last crash tail:\n"
                f"{crash_tail or '(empty)'}"
            )
            _emit_output_stale_probe(
                state,
                "trace_task_output_stale",
                "Mira output stale: no task results",
                body,
            )

    if anomalies:
        sample = anomalies[:3]
        err_tail = _tail_file(Path("/tmp/mira-agent.err"), max_lines=60, max_chars=3000)
        body = "Scheduled job trigger is active but output is stale (sample):\n"
        for a in sample:
            body += (
                f"- job={a.get('job')} component={a.get('component')} user={a.get('user_id')} "
                f"gap_s={a.get('output_gap_seconds')}\n"
            )
        if err_tail:
            body += "\nLast error tail:\n" + err_tail
        _emit_output_stale_probe(
            state,
            "trace_scheduled_output_stale",
            "Mira output stale: scheduled pipeline",
            body.strip(),
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


def _parse_evaluator_score_timestamp(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _numeric_evaluator_score(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean_score_from_mapping(scores: dict) -> float | None:
    values = [_numeric_evaluator_score(value) for value in scores.values()]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _extract_evaluator_score_records(record: dict) -> list[dict]:
    timestamp = record.get("timestamp") or record.get("ts") or record.get("generated_at") or record.get("date")
    records: list[dict] = []

    agent = record.get("agent")
    score = _numeric_evaluator_score(record.get("score"))
    if agent and score is not None:
        records.append({**record, "timestamp": timestamp, "agent": str(agent), "score": score})
        return records

    agents = record.get("agents")
    if isinstance(agents, dict):
        for agent_name, card in agents.items():
            if not isinstance(card, dict):
                continue
            score = _numeric_evaluator_score(card.get("score"))
            if score is None:
                score = _numeric_evaluator_score(card.get("success_rate"))
            if score is None and isinstance(card.get("scores"), dict):
                score = _mean_score_from_mapping(card["scores"])
            if score is None:
                continue
            records.append(
                {
                    **card,
                    "timestamp": timestamp,
                    "agent": str(agent_name),
                    "score": score,
                    "source_record": record,
                }
            )
        return records

    scores = record.get("scores")
    if agent and isinstance(scores, dict):
        score = _mean_score_from_mapping(scores)
        if score is not None:
            records.append({**record, "timestamp": timestamp, "agent": str(agent), "score": score})

    return records


def _load_recent_evaluator_score_records(now: datetime, weeks: int = 4) -> list[dict]:
    cutoff = now - timedelta(days=weeks * 7)
    paths = [LOGS_DIR / "evaluator_scores.jsonl"]
    evaluation_dir = LOGS_DIR / "evaluations"
    if evaluation_dir.exists():
        paths.extend(sorted(evaluation_dir.glob("*_scores.jsonl")))

    records: list[dict] = []
    seen_paths: set[Path] = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError):
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            for record in _extract_evaluator_score_records(raw):
                timestamp = _parse_evaluator_score_timestamp(record.get("timestamp"))
                if timestamp is None or timestamp < cutoff:
                    continue
                record["timestamp"] = timestamp.isoformat()
                records.append(record)
    return records


def _linear_regression_slope_r2(values: list[float]) -> tuple[float, float]:
    x_values = list(range(len(values)))
    x_mean = sum(x_values) / len(x_values)
    y_mean = sum(values) / len(values)
    ss_xx = sum((x - x_mean) ** 2 for x in x_values)
    ss_yy = sum((y - y_mean) ** 2 for y in values)
    if ss_xx == 0:
        return 0.0, 0.0
    ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, values))
    slope = ss_xy / ss_xx
    if ss_yy == 0:
        return slope, 0.0
    r_squared = (ss_xy * ss_xy) / (ss_xx * ss_yy)
    return slope, r_squared


def _has_external_evaluator_confirmation(value) -> bool:
    confirmation_keys = {
        "external_confirmation",
        "external_confirmed",
        "external_validated",
        "human_confirmed",
        "operator_confirmed",
        "user_confirmed",
        "confirmed_by_user",
    }
    external_evidence_types = {
        "external",
        "external_verified",
        "human_confirmed",
        "operator_confirmed",
        "user_confirmed",
    }

    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if normalized_key in confirmation_keys and bool(nested):
                return True
            if normalized_key == "evidence_type" and str(nested).lower() in external_evidence_types:
                return True
            if _has_external_evaluator_confirmation(nested):
                return True
    elif isinstance(value, list):
        return any(_has_external_evaluator_confirmation(item) for item in value)
    return False


def _ensure_evaluator_drift_review_task(
    *,
    slope: float,
    r_squared: float,
    weekly_scores: list[tuple[str, float]],
    user_id: str = "ang",
) -> None:
    if Mira is None:
        return

    title = "Review evaluator prompt for potential evaluation drift"
    detail = ", ".join(f"{week}={score:.3f}" for week, score in weekly_scores)
    body = (
        "Potential evaluation drift detected.\n\n"
        f"Weekly average evaluator scores: {detail}\n"
        f"Linear slope: {slope:.3f} points/week\n"
        f"R^2: {r_squared:.3f}\n\n"
        "Review the evaluator prompt and rubric for brittle assumptions. Do not block improvement loops automatically."
    )

    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        if hasattr(bridge, "load_todos") and hasattr(bridge, "add_todo"):
            for todo in bridge.load_todos():
                if todo.get("title") == title and todo.get("status") not in {
                    "done",
                    "archived",
                    "cancelled",
                    "canceled",
                }:
                    return
            todo = bridge.add_todo(title, priority="low", tags=["mira", "evaluator", "drift"])
            if todo and hasattr(bridge, "add_followup"):
                bridge.add_followup(todo["id"], body, source="agent")
            return

        task_id = f"evaluator_drift_review_{datetime.now().strftime('%Y%m%d')}"
        if hasattr(bridge, "item_exists") and bridge.item_exists(task_id):
            return
        bridge.create_task(
            task_id,
            title,
            body,
            sender="agent",
            tags=["mira", "evaluator", "drift"],
            origin="auto",
        )
    except Exception as exc:
        log.debug("Evaluator drift review task creation failed: %s", exc)


def _check_evaluator_score_trend_drift(user_id: str = "ang") -> None:
    now = datetime.now(timezone.utc)
    records = _load_recent_evaluator_score_records(now, weeks=4)
    if not records:
        return

    weekly: dict[tuple[int, int], list[float]] = {}
    weekly_records: dict[tuple[int, int], list[dict]] = {}
    for record in records:
        timestamp = _parse_evaluator_score_timestamp(record.get("timestamp"))
        score = _numeric_evaluator_score(record.get("score"))
        if timestamp is None or score is None:
            continue
        iso_year, iso_week, _ = timestamp.isocalendar()
        key = (iso_year, iso_week)
        weekly.setdefault(key, []).append(score)
        weekly_records.setdefault(key, []).append(record)

    weekly_scores = [(key, sum(values) / len(values)) for key, values in sorted(weekly.items()) if values][-4:]
    if len(weekly_scores) < 4:
        return

    averages = [score for _, score in weekly_scores]
    if not all(next_score >= score for score, next_score in zip(averages, averages[1:])):
        return
    if averages[-1] == averages[0]:
        return

    recent_records: list[dict] = []
    for key, _ in weekly_scores:
        recent_records.extend(weekly_records.get(key, []))
    if _has_external_evaluator_confirmation(recent_records):
        return

    slope, r_squared = _linear_regression_slope_r2(averages)
    if slope <= 0.05 or r_squared <= 0.7:
        return

    formatted_weekly_scores = [(f"{year}-W{week:02d}", score) for (year, week), score in weekly_scores]
    log.warning(
        "Potential evaluation drift detected slope=%.3f r_squared=%.3f weekly_scores=%s",
        slope,
        r_squared,
        {week: round(score, 3) for week, score in formatted_weekly_scores},
    )
    _ensure_evaluator_drift_review_task(
        slope=slope,
        r_squared=r_squared,
        weekly_scores=formatted_weekly_scores,
        user_id=user_id,
    )


def _check_stale_eval_metrics() -> None:
    rotation_days = getattr(shared_config, "EVAL_BENCHMARK_ROTATION_DAYS", 30)
    last_rotated = getattr(shared_config, "EVAL_BENCHMARK_LAST_ROTATED", {})
    if not isinstance(last_rotated, dict) or not last_rotated:
        return

    today = datetime.now(timezone.utc).date()
    for metric, date_str in last_rotated.items():
        try:
            since = datetime.fromisoformat(str(date_str)).date()
        except (ValueError, TypeError):
            continue
        age_days = (today - since).days
        if age_days > rotation_days:
            note = f"STALE_METRIC: {metric} in use since {date_str} — consider replacing before next eval cycle."
            log.warning(note)
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                with open(LOGS_DIR / "stale_metric_warnings.log", "a", encoding="utf-8") as wf:
                    wf.write(
                        json.dumps(
                            {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "metric": metric,
                                "in_use_since": date_str,
                                "age_days": age_days,
                                "rotation_days": rotation_days,
                                "note": note,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as exc:
                log.debug("stale metric warning write failed: %s", exc)


def _check_stale_pipelines() -> list[dict]:
    _now = time.time()
    _data = _read_last_outputs()
    _stale: list[dict] = []
    for _component, _threshold in STALE_THRESHOLDS.items():
        _last = _data.get(_component)
        if _last is None:
            continue
        try:
            _gap = _now - float(_last)
        except (TypeError, ValueError):
            continue
        if _gap > _threshold:
            _scheduler_age = _recent_scheduler_success_age(_component, _now)
            _writer_stall = _writer_stall_status() if _component == "writer" else None
            if _scheduler_age is not None and _scheduler_age <= 6 * 3600 and not _writer_stall:
                log.info(
                    "%s content output is stale for %ds, but scheduler succeeded %ds ago",
                    _component,
                    int(_gap),
                    int(_scheduler_age),
                )
                continue
            if _writer_stall:
                log.info(
                    "writer content output is stale for %ds with %d stalled writing project(s)",
                    int(_gap),
                    int(_writer_stall.get("stalled_count", 0) or 0),
                )
                _item = {
                    "component": _component,
                    "gap_seconds": int(_gap),
                    "threshold_seconds": int(_threshold),
                }
                _item.update(_writer_stall)
                _stale.append(_item)
                continue
            log.warning(
                "%s has produced no output in %ds — possible silent marginalization",
                _component,
                int(_gap),
            )
            _item = {
                "component": _component,
                "gap_seconds": int(_gap),
                "threshold_seconds": int(_threshold),
            }
            if _writer_stall:
                _item.update(_writer_stall)
            _stale.append(_item)
    return _stale


def _recent_scheduler_success_age(component: str, now: float) -> float | None:
    process_names = {
        "writer": ("writing-pipeline", "autowrite-check"),
        "reflect": ("reflect",),
    }.get(component)
    if not process_names:
        return None
    try:
        health = json.loads(mira_config.HEALTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    processes = health.get("processes", {}) if isinstance(health, dict) else {}
    ages = []
    for name in process_names:
        proc = processes.get(name, {})
        if not isinstance(proc, dict):
            continue
        ts = _parse_recovery_timestamp(proc.get("last_success"))
        if ts is not None:
            ages.append(max(0.0, now - ts))
    return min(ages) if ages else None


def _writer_stall_status() -> dict | None:
    try:
        status = json.loads((LOGS_DIR / "writing_pipeline_status.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(status, dict):
        return None
    stalled = status.get("stalled")
    if not isinstance(stalled, list) or not stalled:
        return None
    try:
        stalled_count = int(status.get("stalled_count") or len(stalled))
    except (TypeError, ValueError):
        stalled_count = len(stalled)
    projects = []
    for item in stalled[:5]:
        if not isinstance(item, dict):
            continue
        projects.append(
            {
                "title": str(item.get("title") or "untitled")[:120],
                "phase": str(item.get("phase") or ""),
                "age_days": item.get("age_days"),
                "reason": str(item.get("reason") or "")[:200],
            }
        )
    if not projects:
        return None
    return {
        "kind": "writing_stalled",
        "stalled_count": stalled_count,
        "phase_counts": status.get("phase_counts", {}),
        "projects": projects,
        "writing_checked_at": status.get("checked_at", ""),
    }


def _append_stale_pipelines_to_journal(stale_components: list[dict], user_id: str = "ang") -> None:
    if not stale_components:
        return
    try:
        from user_paths import user_journal_dir
    except Exception as exc:
        log.debug("stale pipeline journal append unavailable: %s", exc)
        return

    today = datetime.now().strftime("%Y-%m-%d")
    journal_path = user_journal_dir(user_id) / f"{today}.md"
    marker = "<!-- stale-pipeline-output -->"
    try:
        existing = journal_path.read_text(encoding="utf-8") if journal_path.exists() else ""
    except OSError as exc:
        log.debug("stale pipeline journal read failed: %s", exc)
        return
    if marker in existing:
        return

    lines = [marker, "## Pipeline output warnings"]
    for stale in stale_components:
        component = str(stale.get("component", "")).strip()
        if not component:
            continue
        gap = stale.get("gap_seconds")
        if gap is None:
            lines.append(f"- WARNING: {component} has produced no output — possible silent marginalization")
        else:
            lines.append(
                f"- WARNING: {component} has produced no output in {int(gap)}s — possible silent marginalization"
            )
    if len(lines) == 2:
        return

    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as _jf:
            _jf.write("\n\n" + "\n".join(lines) + "\n")
    except OSError as exc:
        log.debug("stale pipeline journal write failed: %s", exc)


def _count_claude_hard_rules(claude_text: str) -> int:
    hard_rules_match = re.search(r"^##\s+HARD RULES\s*$", claude_text, flags=re.IGNORECASE | re.MULTILINE)
    if not hard_rules_match:
        return 0
    section_start = hard_rules_match.end()
    next_section = re.search(r"^##\s+", claude_text[section_start:], flags=re.MULTILINE)
    section_end = section_start + next_section.start() if next_section else len(claude_text)
    hard_rules_section = claude_text[section_start:section_end]
    return len(re.findall(r"^\d+\.\s+", hard_rules_section, flags=re.MULTILINE))


_ORGANIZATIONAL_DRIFT_AUDIT_STEP = (
    "8. **Organizational drift audit**: examine this week's task patterns, decisions, and outputs. "
    "Ask: Did Mira's behavior this week amplify any harmful incentive structures, information asymmetries, "
    "or power dynamics? If so, how can active resistance be designed? Also ask whether Mira amplified any "
    "pre-existing incentive structures, information asymmetries, or power dynamics in the user's context. "
    "If drift is detected, log the pattern and suggest design countermeasures (e.g., refusal templates, "
    "re-framing prompts, or boundary adjustments)."
)


@contextmanager
def _reflect_prompt_with_organizational_drift_audit():
    base_reflect_prompt = do_reflect.__globals__.get("reflect_prompt")
    if base_reflect_prompt is None:
        yield
        return

    def _wrapped_reflect_prompt(*args, **kwargs):
        prompt = base_reflect_prompt(*args, **kwargs)
        marker = "Output THREE things:"
        if marker in prompt:
            return prompt.replace(marker, f"{_ORGANIZATIONAL_DRIFT_AUDIT_STEP}\n\n{marker}", 1)
        return f"{prompt}\n\n{_ORGANIZATIONAL_DRIFT_AUDIT_STEP}"

    do_reflect.__globals__["reflect_prompt"] = _wrapped_reflect_prompt
    try:
        yield
    finally:
        do_reflect.__globals__["reflect_prompt"] = base_reflect_prompt


def _append_hard_rules_consolidation_task_to_journal(
    *,
    rule_count: int,
    threshold: int,
    user_id: str = "ang",
) -> None:
    try:
        from user_paths import user_journal_dir

        journal_path = user_journal_dir(user_id) / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    except Exception:
        journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"

    marker = "<!-- hard-rules-consolidation-task -->"
    try:
        existing = journal_path.read_text(encoding="utf-8") if journal_path.exists() else ""
    except OSError as exc:
        log.debug("hard rules consolidation journal read failed: %s", exc)
        return
    if marker in existing:
        return

    task = (
        f"{marker}\n"
        "## WARNING: Hard Rules Consolidation Audit\n"
        f"- CLAUDE.md HARD RULES count: {rule_count}\n"
        f"- MAX_HARD_RULES threshold: {threshold}\n"
        "- Task: Audit CLAUDE.md HARD RULES for redundancy, obsolescence, and merge opportunities.\n"
        "- Principle: Apply the reading note's 'cascading config vs middleware layers' warning; "
        "avoid solving every failure by adding another procedural middleware layer.\n"
    )
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as journal_file:
            journal_file.write("\n\n---\n\n" + task)
    except OSError as exc:
        log.debug("hard rules consolidation journal write failed: %s", exc)


def _check_hard_rules_consolidation(user_id: str = "ang") -> None:
    claude_path = MIRA_ROOT / "CLAUDE.md"
    try:
        claude_text = claude_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.debug("CLAUDE.md hard rules check skipped: %s", exc)
        return

    rule_count = _count_claude_hard_rules(claude_text)
    threshold = int(getattr(shared_config, "MAX_HARD_RULES", 7))
    if rule_count <= threshold:
        return

    log.warning(
        "HARD_RULES_CONSOLIDATION: CLAUDE.md has %d HARD RULES, exceeding MAX_HARD_RULES=%d",
        rule_count,
        threshold,
    )
    _append_hard_rules_consolidation_task_to_journal(
        rule_count=rule_count,
        threshold=threshold,
        user_id=user_id,
    )


def _dispatch_log_timestamp(value) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).timestamp()


def _read_weekly_dispatch_counts(now: datetime) -> dict[str, int]:
    cutoff = now.timestamp() - 7 * 86400
    counts: dict[str, int] = {}
    dispatch_log = LOGS_DIR / "routing_audit.jsonl"
    try:
        lines = dispatch_log.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError) as exc:
        log.debug("Dispatch log read failed: %s", exc)
        return counts

    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = _dispatch_log_timestamp(entry.get("ts"))
        if timestamp is None or timestamp < cutoff:
            continue
        agent = _normalize_task_distribution_category(
            entry.get("agent") or entry.get("agent_name") or entry.get("task_type")
        )
        counts[agent] = counts.get(agent, 0) + 1
    return counts


def _load_dispatch_history_counts(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    raw_counts = data.get("counts") if isinstance(data, dict) else {}
    if not isinstance(raw_counts, dict):
        return {}
    return {
        _normalize_task_distribution_category(agent): _blind_spot_int(count)
        for agent, count in raw_counts.items()
        if _blind_spot_int(count) > 0
    }


def _append_dispatch_warnings_to_reflect_output(warnings: list[str], user_id: str) -> None:
    if not warnings or Mira is None:
        return
    marker = "<!-- dispatch-distribution-warning -->"
    section = (
        "\n\n---\n\n"
        + marker
        + "\n## WARNING: Dispatch Distribution Drift\n"
        + "\n".join(f"- WARNING: {warning}" for warning in warnings)
    )
    try:
        bridge = Mira(MIRA_DIR, user_id=user_id)
        item_id = f"feed_reflect_{datetime.now().strftime('%Y%m%d')}"
        if not bridge.item_exists(item_id):
            return
        item = bridge._read_item(item_id)
        if not item:
            return
        messages = item.setdefault("messages", [])
        if not messages:
            return
        content = str(messages[0].get("content", "") or "")
        if marker in content:
            return
        messages[0]["content"] = content + section + "\n"
        messages[0]["timestamp"] = datetime.now(timezone.utc).isoformat()
        item["updated_at"] = datetime.now(timezone.utc).isoformat()
        bridge._write_item(item)
        bridge._update_manifest(item)
    except Exception as exc:
        log.debug("Reflect dispatch warning append failed: %s", exc)


def _append_dispatch_warnings_to_journal(warnings: list[str], user_id: str) -> None:
    if not warnings:
        return
    try:
        from user_paths import user_journal_dir

        journal_path = user_journal_dir(user_id) / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    except Exception:
        journal_path = JOURNAL_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    text = "\n".join(f"- WARNING: {warning}" for warning in warnings)
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "a", encoding="utf-8") as journal_file:
            journal_file.write(f"\n\n---\n\n## WARNING: Dispatch Distribution Drift\n{text}\n")
    except OSError as exc:
        log.debug("Journal dispatch warning write failed: %s", exc)


def _dispatch_distribution_snapshot(user_id: str = "ang") -> None:
    """Count past-7-day task dispatches per agent, compare to prior week, warn on drift."""
    now = datetime.now(timezone.utc)
    history_path = Path(__file__).parent / "dispatch_history.json"
    current = _read_weekly_dispatch_counts(now)
    prior = _load_dispatch_history_counts(history_path)
    current_total = sum(current.values())
    prior_total = sum(prior.values())
    warnings: list[str] = []

    if prior and current_total >= prior_total * 0.5:
        for agent, prior_count in sorted(prior.items()):
            cur_count = current.get(agent, 0)
            prior_share = prior_count / prior_total if prior_total else 0
            cur_share = cur_count / current_total if current_total else 0
            if cur_count == 0:
                warnings.append(
                    f"Agent '{agent}' had {prior_count} dispatches last week but 0 this week while total task volume stayed active."
                )
            elif prior_share > 0 and cur_share < prior_share * 0.5:
                warnings.append(
                    f"Agent '{agent}' dispatch share dropped from {prior_share:.1%} to {cur_share:.1%} "
                    f"({prior_count} to {cur_count} dispatches) while total task volume stayed active."
                )

    for warning in warnings:
        log.warning("DISPATCH_DRIFT: %s", warning)
    _append_dispatch_warnings_to_reflect_output(warnings, user_id)
    _append_dispatch_warnings_to_journal(warnings, user_id)

    try:
        _write_text_atomic(
            history_path,
            json.dumps(
                {
                    "counts": current,
                    "total": current_total,
                    "window_days": 7,
                    "window_started_at": datetime.fromtimestamp(now.timestamp() - 7 * 86400, timezone.utc).isoformat(),
                    "window_ended_at": now.isoformat(),
                    "recorded_at": now.isoformat(),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
        )
    except OSError as exc:
        log.debug("Dispatch history write failed: %s", exc)


def _recent_content_quality_entries(limit: int = 20) -> list[dict]:
    try:
        lines = CONTENT_QUALITY_LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    except (FileNotFoundError, OSError) as exc:
        log.debug("Content quality log read failed: %s", exc)
        return []

    entries: list[dict] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _content_quality_violation_count(entry: dict) -> int:
    try:
        return int(entry.get("violation_count", 0))
    except (TypeError, ValueError):
        return 0


def _write_content_drift_alert(alert: dict, user_id: str) -> None:
    try:
        DRIFT_ALERTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DRIFT_ALERTS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(alert, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("Content drift alert write failed: %s", exc)

    try:
        from notes_bridge import send_to_outbox

        send_to_outbox(
            alert["message"],
            metadata={
                "kind": "content_drift_alert",
                "user_id": user_id,
                "fraction": alert["fraction"],
                "threshold": alert["threshold"],
            },
        )
    except Exception as exc:
        log.debug("Content drift Notes outbox write failed: %s", exc)


def check_content_drift(user_id: str = "ang") -> None:
    now = datetime.now(timezone.utc)
    week_key = now.strftime("%G-W%V")
    state = load_state()
    if state.get("last_content_drift_check") == week_key:
        return
    state["last_content_drift_check"] = week_key
    save_state(state)

    entries = _recent_content_quality_entries(limit=20)
    if not entries:
        return

    elevated = sum(1 for entry in entries if _content_quality_violation_count(entry) >= 3)
    fraction = elevated / len(entries)
    threshold = float(getattr(shared_config, "CONTENT_DRIFT_ALERT_THRESHOLD", 0.3))
    if fraction <= threshold:
        return

    message = (
        "CONTENT_DRIFT_ALERT: "
        f"{elevated}/{len(entries)} recent articles ({fraction:.0%}) had elevated anti-AI violations "
        f"(violation_count >= 3), above threshold {threshold:.0%}. "
        "Metric-chasing degradation risk; manual editorial review recommended."
    )
    alert = {
        "timestamp": now.isoformat(),
        "event": "content_drift_alert",
        "recent_entries": len(entries),
        "elevated_entries": elevated,
        "fraction": round(fraction, 4),
        "threshold": threshold,
        "message": message,
    }
    log.warning(message)
    _write_content_drift_alert(alert, user_id)


def _weekly_orchestration_fraction_snapshot() -> None:
    from statistics import median

    phase_log = LOGS_DIR / "task_phase_timing.jsonl"
    if not phase_log.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    by_task_type: dict[str, list[float]] = {}
    all_values: list[float] = []
    try:
        with open(phase_log, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    ts = record.get("ts")
                    if not ts:
                        continue
                    record_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if record_dt.tzinfo is None:
                        record_dt = record_dt.replace(tzinfo=timezone.utc)
                    if record_dt < cutoff:
                        continue
                    fraction = float(record["orchestration_fraction"])
                    if fraction < 0 or fraction > 1:
                        continue
                    task_type = str(record.get("task_type") or record.get("agent") or "unknown").strip() or "unknown"
                    by_task_type.setdefault(task_type, []).append(fraction)
                    all_values.append(fraction)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    continue
    except OSError as exc:
        log.debug("Orchestration fraction weekly read failed: %s", exc)
        return

    if not all_values:
        return

    task_types = {
        task_type: {
            "median_orchestration_fraction": round(median(values), 4),
            "samples": len(values),
        }
        for task_type, values in sorted(by_task_type.items())
        if values
    }
    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "window_days": 7,
        "median_orchestration_fraction": round(median(all_values), 4),
        "samples": len(all_values),
        "task_types": task_types,
    }
    log.info("ORCHESTRATION_FRACTION_WEEKLY %s", json.dumps(snapshot, ensure_ascii=False))
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        (LOGS_DIR / "orchestration_fraction_weekly.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("Orchestration fraction weekly write failed: %s", exc)


def _append_task_latency_to_journal(user_id: str = "ang") -> None:
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
    try:
        from user_paths import user_journal_dir

        journal_path = user_journal_dir(user_id) / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    except Exception:
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


def _alert_soul_integrity_failures(failures: list[tuple[str, str]], *, fatal: bool = True) -> None:
    lines = "\n".join(f"- {filename}: {error}" for filename, error in failures)
    if fatal:
        message = (
            "Mira startup integrity check failed. No pipelines were dispatched because "
            "background soul infrastructure is broken.\n\n"
            f"{lines}"
        )
        status = "failed"
    else:
        message = (
            "Mira startup integrity check is degraded. Core dispatch will continue, "
            "but skill audit and generated/imported skill activation are blocked until this is fixed.\n\n"
            f"{lines}"
        )
        status = "working"
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
            item["status"] = status
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


def _resolve_soul_integrity_alert() -> None:
    """Close the persistent integrity alert once startup checks are healthy again."""
    if Mira is None:
        return
    try:
        bridge = Mira(MIRA_DIR, user_id="ang")
        item_id = "soul_integrity_failure"
        if not bridge.item_exists(item_id):
            return
        item = bridge._read_item(item_id)
        if not item:
            return
        if item.get("status") == "done" and not item.get("error") and not item.get("pinned"):
            return
        bridge.update_status(
            item_id,
            "done",
            agent_message="Mira startup integrity check is healthy again. Skill audit is enabled and core dispatch is running.",
        )
        item = bridge._read_item(item_id)
        if not item:
            return
        item["type"] = "alert"
        item["status"] = "done"
        item["pinned"] = False
        item["error"] = None
        tags = [tag for tag in item.get("tags", []) if tag != "error"]
        item["tags"] = list(dict.fromkeys([*tags, "resolved"]))
        bridge._write_item(item)
        bridge._update_manifest(item)
        log.info("Resolved soul integrity alert item")
    except Exception as exc:
        log.error("Failed to resolve soul integrity alert: %s", exc)


def _record_skill_audit_integrity_state(ok: bool, detail: str = "") -> None:
    path = LOGS_DIR / "skill_audit_integrity.json"
    if ok:
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            log.debug("Failed to clear skill audit integrity marker: %s", exc)
        return

    payload = {
        "status": "degraded",
        "component": "skill_audit",
        "detail": detail,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.rename(path)
    except OSError as exc:
        log.error("Failed to write skill audit integrity marker: %s", exc)


def _run_kol_digest(max_kols: int | None = None, dry_run: bool = False, user_id: str = "ang") -> Path:
    handler_path = _AGENTS_DIR / "kol" / "handler.py"
    spec = importlib.util.spec_from_file_location("mira_kol_handler", handler_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load KOL handler: {handler_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.run_daily_digest(max_kols=max_kols, dry_run=dry_run, user_id=user_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    global _SKILL_AUDIT_INTEGRITY_OK

    # Set up logging (human-readable console + file, plus JSON file for machine parsing)
    from log_config import setup_logging

    setup_logging(logs_dir=LOGS_DIR, json_logs=True)
    if not _validate_critical_imports():
        _write_startup_import_heartbeat("startup blocked: shared module import chain broken", status="blocked")
        log.critical("startup blocked: shared module import chain broken")
        raise SystemExit(78)
    verify_agent_deps()
    _SKILL_AUDIT_INTEGRITY_OK = verify_audit_integrity()
    if not _SKILL_AUDIT_INTEGRITY_OK:
        log.critical("Skill loading and auditing blocked: audit module integrity check failed")
        _record_skill_audit_integrity_state(False, "audit module integrity check failed")
        _alert_soul_integrity_failures(
            [("skill_audit", "audit module integrity check failed; skill audit is blocked but core remains online")],
            fatal=False,
        )
    else:
        _record_skill_audit_integrity_state(True)

    soul_failures = validate_soul_files()
    if soul_failures:
        _alert_soul_integrity_failures(soul_failures)
        raise SystemExit(78)
    _resolve_soul_integrity_alert()

    check_rules_integrity()

    # Validate configuration — log errors but don't crash
    if not validate_config():
        log.warning("Config validation failed — some features may not work")
    _verify_guard_integrity_at_startup()
    validate_local_model_native_tools(logger=log)
    canary_self_audit()

    try:
        from agent_registry import get_registry

        agents = get_registry()
        soul_manager.audit_model_dependency(agents)
    except Exception as exc:
        log.debug("Model dependency audit failed: %s", exc)

    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    _log_skill_depth_advisories(command)
    _stale_components: list[dict] = []
    if command in {"explore", "reflect", "journal", "autowrite-run", "writing-pipeline"}:
        _stale_components = _check_stale_pipelines()

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
        with _timed_phase("inbox_processing", "orchestration"), _timed_llm_calls("inbox_processing"):
            do_talk()
    elif command == "explore":
        sources = flags.get("sources", "").split(",") if flags.get("sources") else None
        slot = flags.get("slot", "")
        with _timed_stage("explore"):
            with _timed_phase("explore_fetch", "orchestration"), _timed_llm_calls("explore_fetch"):
                explore_produced_output = do_explore(source_names=sources, slot_name=slot)
        if explore_produced_output:
            update_joint_attention(
                f"explore briefing knowledge garden: {slot}" if slot else "explore briefing knowledge garden"
            )
            _write_last_output("explorer")
        else:
            log.warning("Explore command produced no briefing or diagnostic; explorer last_output not refreshed")
    elif command == "kol-digest":
        max_kols = int(flags["max-kols"]) if flags.get("max-kols") else None
        dry_run = "--dry-run" in sys.argv
        with _timed_stage("kol_digest"):
            _run_kol_digest(max_kols=max_kols, dry_run=dry_run, user_id=flags.get("user", "ang"))
        update_joint_attention("known KOL daily intelligence digest")
        _write_last_output("kol")
    elif command == "reflect":
        user_id = flags.get("user", "ang")
        with _timed_stage("reflect"):
            _check_hard_rules_consolidation(user_id=user_id)
            with _timed_phase("reflect", "orchestration"), _timed_llm_calls("reflect"):
                with _reflect_prompt_with_organizational_drift_audit():
                    do_reflect(user_id=user_id)
            try:
                _check_agent_initiated_interest_drift(user_id=user_id)
            except Exception as e:
                log.warning("Agent-initiated interest drift check failed: %s", e)
            try:
                _check_evaluator_score_trend_drift(user_id=user_id)
            except Exception as e:
                log.warning("Evaluator score trend drift check failed: %s", e)
            try:
                from drift import compute_drift_alert

                drift_warning = compute_drift_alert()
                if drift_warning:
                    drift_log = MIRA_ROOT / "logs" / "drift_warnings.log"
                    drift_log.parent.mkdir(parents=True, exist_ok=True)
                    with drift_log.open("a", encoding="utf-8") as f:
                        f.write(f"{datetime.now(timezone.utc).isoformat()} {drift_warning}\n")
            except Exception as e:
                log.warning("Evaluator linguistic drift check failed: %s", e)
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
            try:
                soul_manager.check_skill_coherence()
            except Exception as e:
                log.error("Skill coherence check failed: %s", e)
            try:
                soul_manager.export_memory_to_sqlite(str(MIRA_ROOT / "memory_archive.sqlite"))
            except Exception as e:
                log.warning("Memory SQLite export after reflect failed: %s", e)
            try:
                soul_manager.export_to_sqlite(_soul_archive_sqlite_path())
            except Exception as e:
                log.warning("Soul SQLite archive after reflect failed: %s", e)
            try:
                export_memory_to_sqlite()
            except Exception as e:
                log.warning("Shared memory SQLite export after reflect failed: %s", e)
        _send_joint_observation(user_id=user_id)
        _append_joint_attention_landscape_to_journal(user_id=user_id, create_if_missing=False)
        _dispatch_distribution_snapshot(user_id=user_id)
        check_content_drift(user_id=user_id)
        _weekly_orchestration_fraction_snapshot()
        _write_last_output("reflect")
    elif command == "journal":
        user_id = flags.get("user", "ang")
        with _timed_stage("journal"):
            with _timed_phase("journal", "orchestration"), _timed_llm_calls("journal"):
                do_journal(user_id=user_id)
            try:
                soul_manager.export_memory_to_sqlite(str(MIRA_ROOT / "memory_archive.sqlite"))
            except Exception as e:
                log.warning("Memory SQLite export after journal failed: %s", e)
        update_joint_attention("today's journal as a knowledge-garden page")
        _append_joint_attention_landscape_to_journal(user_id=user_id)
        _append_task_latency_to_journal(user_id=user_id)
        _append_stale_pipelines_to_journal(_stale_components, user_id=user_id)
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
    elif command == "daily-collab":
        do_daily_collab(user_id=flags.get("user", "ang"))
    elif command == "daily-collab-review":
        do_daily_collab_review(user_id=flags.get("user", "ang"))
    elif command == "daily-collab-operator-brief":
        do_daily_collab_operator_brief(user_id=flags.get("user", "ang"))
    elif command == "autowrite-check":
        do_autowrite_check()
    elif command == "autowrite-run":
        task_id = flags.get("task-id", f"autowrite_{datetime.now().strftime('%Y-%m-%d')}")
        title = flags.get("title", "Untitled")
        writing_type = flags.get("type", "essay")
        idea = flags.get("idea", "")
        run_autowrite_pipeline(task_id, title, writing_type, idea)
        update_joint_attention(f"writing project: {title}")
        _write_last_output("writer")
    elif command == "writing-pipeline":
        with _timed_stage("write"):
            with _timed_phase("pipeline_step.writing", "orchestration"), _timed_llm_calls("pipeline_step.writing"):
                advanced = _run_canonical_writing_pipeline()
        log.info("Canonical writing pipeline advanced %d project(s)", advanced)
        if advanced:
            update_joint_attention("the active writing-project knowledge garden")
            _write_last_output("writer")
    elif command == "writing-triage":
        status = triage_stalled_writing_projects(dry_run="--dry-run" in sys.argv)
        print(json.dumps(status, ensure_ascii=False, indent=2))
    elif command == "check-comments":
        do_check_comments()
    elif command == "growth-cycle":
        if publish_blocked:
            _log_substack_publish_block("growth-cycle command")
        else:
            do_growth_cycle()
    elif command == "notes-cycle":
        if publish_blocked:
            _log_substack_publish_block("notes-cycle command")
        else:
            do_notes_cycle()
    elif command == "recalibrate-proxies":
        do_recalibrate_proxies(user_id=flags.get("user", "ang"))
    elif command == "guard-calibration-prompt":
        do_guard_calibration_prompt(user_id=flags.get("user", "ang"))
    elif command == "proxy-drift-check":
        do_proxy_drift_check()
    elif command == "proxy-recalibration":
        run_proxy_recalibration()
    elif command == "review-writer-proxy":
        review_writer_proxy(user_id=flags.get("user", "ang"))
    elif command == "anti-ai-quality-guard-check":
        check_anti_ai_quality_guard(user_id=flags.get("user", "ang"))
    elif command == "calibrate-proxies":
        calibrate_proxies(user_id=flags.get("user", "ang"))
    elif command == "security-reaudit":
        security_reaudit()
    elif command == "canary-skill-audit":
        canary_skill_audit()
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
            f"Usage: {sys.argv[0]} [run|talk|respond|explore|kol-digest|reflect|journal|analyst|zhesi|daily-collab|daily-collab-operator-brief|skill-study|security-reaudit|canary-skill-audit|review-writer-proxy|anti-ai-quality-guard-check|autowrite-check|autowrite-run|writing-pipeline|writing-triage|write-check|write-from-plan|spark-check]"
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
