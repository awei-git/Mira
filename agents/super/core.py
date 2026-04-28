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
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Unified sys.path setup — see lib/pathsetup.py for the full list of package dirs
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
import pathsetup  # noqa: F401  (side-effect: registers all Mira package dirs)

import health_monitor
from logging_util import throttled_warning  # noqa: E402  — used inside _check_invisible_deps

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
    STALE_THRESHOLDS,
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
from task_manager import TaskManager, TASKS_DIR
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
from soul_manager import log_authorization_event, check_audit_coverage

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
from talk import (
    do_talk,
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


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

    _check_invisible_dependencies()

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

        if check_bridge_staleness():
            from config import MIRA_DIR as _MIRA_DIR

            _hb_age = -1
            for _hb_name in ("heartbeat.json", "heartbeat"):
                _hb_path = Path(_MIRA_DIR) / _hb_name
                if _hb_path.exists():
                    try:
                        _hb_age = round(time.time() - _hb_path.stat().st_mtime)
                    except OSError:
                        pass
                    break
            log.warning(
                "component=notes_bridge event=staleness_detected age_seconds=%d",
                _hb_age,
            )
    except Exception as _bse:
        log.debug("Bridge staleness check failed: %s", _bse)

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
        agent = str(data.get("agent", "")).strip()
        if agent not in _TRACKED_AGENTS:
            continue
        if "outcome_verified" not in data:
            continue
        results_by_agent.setdefault(agent, []).append(bool(data["outcome_verified"]))
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    # Set up logging (human-readable console + file, plus JSON file for machine parsing)
    from log_config import setup_logging

    setup_logging(logs_dir=LOGS_DIR, json_logs=True)

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

    if command == "run":
        cmd_run()
    elif command == "talk":
        do_talk()
    elif command == "explore":
        sources = flags.get("sources", "").split(",") if flags.get("sources") else None
        slot = flags.get("slot", "")
        do_explore(source_names=sources, slot_name=slot)
        _write_last_output("explorer")
    elif command == "reflect":
        do_reflect(user_id=flags.get("user", "ang"))
        _write_last_output("reflect")
    elif command == "journal":
        do_journal(user_id=flags.get("user", "ang"))
        _write_last_output("journal")
    elif command == "research-log":
        do_research_log(user_id=flags.get("user", "ang"))
    elif command == "research-cycle":
        do_research_cycle(user_id=flags.get("user", "ang"))
    elif command == "analyst":
        do_analyst(slot=flags.get("slot", ""))
    elif command == "research":
        do_research()
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
    elif command == "writing-pipeline":
        advanced = _run_canonical_writing_pipeline()
        log.info("Canonical writing pipeline advanced %d project(s)", advanced)
    elif command == "check-comments":
        do_check_comments()
    elif command == "growth-cycle":
        do_growth_cycle()
    elif command == "notes-cycle":
        do_notes_cycle()
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
