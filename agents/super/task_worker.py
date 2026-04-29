#!/usr/bin/env python3
"""Task worker — standalone sub-agent process for Mira.

Spawned by TaskManager.dispatch(). Reads a message, loads context,
calls claude_act(), writes output + result JSON.

Usage:
    python task_worker.py --msg-file <path> --workspace <path> --task-id <id> [--thread-id <id>]
"""
import argparse
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Add shared + sibling agent directories to path
_AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "general"))
# `persona`, `memory`, etc. are under agents/shared/. Worker subprocesses
# need this on path or `from persona.persona_context import ...` (in
# handlers_legacy + downstream) crashes with ModuleNotFoundError.
sys.path.insert(0, str(_AGENTS_DIR / "shared"))

# Register the running script under both its `__main__` identity AND the
# `task_worker` name so the circular import in handlers_legacy.py
# (`from task_worker import _write_result, ...`) finds this in-progress
# module instead of trying to re-import task_worker.py from disk and
# recursively re-entering line 788. Without this alias, the reentry hits
# `from handlers_legacy import handle_discussion` while handlers_legacy is
# only at line 39 (handle_discussion is defined at line 289), so the
# import fails. This pattern is the standard fix for "main script imported
# by a module that also imports back."
if __name__ == "__main__":
    sys.modules.setdefault("task_worker", sys.modules["__main__"])

from config import (
    MIRA_DIR,
    MIRA_ROOT,
    ARTIFACTS_DIR,
    JOURNAL_DIR,
    BRIEFINGS_DIR,
    LOGS_DIR,
    MEMORY_FILE,
    WORLDVIEW_FILE,
    CLAUDE_TIMEOUT_THINK,
    CLAUDE_TIMEOUT_ACT,
    TOKEN_BUDGET_WARN_LIGHT,
    TOKEN_BUDGET_WARN_HEAVY,
    MAX_TASK_HORIZON_STEPS,
    MAX_TASK_HORIZON_STEPS_HEAVY,
    record_phase_duration,
)
from execution.runtime_contract import derive_workflow_id, normalize_task_status
from memory.soul import (
    load_soul,
    format_soul,
    append_memory,
    save_skill,
    save_episode,
    recall_context,
    save_knowledge_note,
)
from llm import claude_act, claude_think, ClaudeTimeoutError, reset_session_tokens, get_session_tokens, _log_efficiency

# Handler functions extracted to handlers_legacy.py (imported after all helpers
# are defined to avoid circular import — see bottom of file)


log = logging.getLogger("task_worker")

# Thread-safe task context — replaces bare globals
import threading as _threading

_ctx = _threading.local()


def _get_active_user_id() -> str:
    return getattr(_ctx, "user_id", "ang")


def _get_active_workflow_id() -> str:
    return getattr(_ctx, "workflow_id", "")


# Legacy module-level names — now properties via __getattr__
_ACTIVE_USER_ID = "ang"  # read by external callers; kept for import compat
_ACTIVE_WORKFLOW_ID = ""


def _set_active_user(user_id: str):
    global _ACTIVE_USER_ID
    _ctx.user_id = user_id or "ang"
    _ACTIVE_USER_ID = _ctx.user_id  # keep module-level in sync for importers
    # Keep task_support in sync
    from task_support import _set_active_user_id_ref

    _set_active_user_id_ref(user_id)


def _set_active_workflow(workflow_id: str):
    global _ACTIVE_WORKFLOW_ID
    _ctx.workflow_id = workflow_id or ""
    _ACTIVE_WORKFLOW_ID = _ctx.workflow_id


def _items_dir(user_id: str | None = None) -> Path:
    return MIRA_DIR / "users" / (user_id or _get_active_user_id()) / "items"


def _item_file(task_id: str, user_id: str | None = None) -> Path:
    return _items_dir(user_id) / f"{task_id}.json"


# Legacy compatibility for modules that still import ITEMS_DIR directly.
ITEMS_DIR = _items_dir()


# Task workspaces stored locally
from config import TASKS_DIR

# ---------------------------------------------------------------------------
# Planning functions extracted to planning/planner.py
# ---------------------------------------------------------------------------
from planning.planner import _load_super_skills, _plan_task, _synthesize_outputs


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit_status(task_id: str, text: str, icon: str = "gear"):
    """Emit a status card to an item's message stream.

    Status cards appear as compact inline cards in the iOS app.
    Writes directly to items/ with atomic write.
    """
    import uuid as _uuid

    status_content = json.dumps(
        {"type": "status", "text": text, "icon": icon},
        ensure_ascii=False,
    )
    msg = {
        "id": _uuid.uuid4().hex[:8],
        "sender": "agent",
        "content": status_content,
        "timestamp": _utc_iso(),
        "kind": "status_card",
    }
    # Write to items/ (new protocol)
    item_file = _item_file(task_id)
    if item_file.exists():
        try:
            item = json.loads(item_file.read_text(encoding="utf-8"))
            item["messages"].append(msg)
            item["updated_at"] = _utc_iso()
            tmp = item_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.rename(item_file)
            return
        except (json.JSONDecodeError, OSError):
            pass
    # Fallback: try legacy tasks/ dir
    task_file = TASKS_DIR / f"{task_id}.json"
    if task_file.exists():
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
            task["messages"].append(msg)
            task["updated_at"] = _utc_iso()
            task_file.write_text(
                json.dumps(task, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (json.JSONDecodeError, OSError):
            pass


# ---------------------------------------------------------------------------
# Privacy detection — local keyword match, NO cloud API calls
# ---------------------------------------------------------------------------

_PRIVATE_KEYWORDS = re.compile(
    r"secret|private|confidential|隐私|私密|保密|机密|"
    r"password|密码|口令|"
    r"tax(?:es)?|报税|税务|税|"
    r"salary|工资|薪资|收入|"
    r"medical|health|病历|体检|就医|诊断|"
    r"legal|lawsuit|律师|官司|"
    r"bank\s*account|银行|账户余额|"
    r"ssn|social\s*security|身份证|护照|"
    r"family\s*(?:issue|problem|matter)|家事|家庭矛盾",
    re.IGNORECASE,
)


def _is_private_task(content: str, task_id: str = "", tags: list[str] | None = None) -> bool:
    """Detect privacy-sensitive content using LOCAL keyword matching only.

    No LLM call, no network request. Pure regex + tag check.
    Conservative: false positives are OK (user can re-route),
    but false negatives leak private data to cloud APIs.

    Triggers:
    1. User put "private" or "secret" or "隐私" in message text
    2. Task ID contains "private" or "secret"
    3. Task tags include "private" or "secret"
    4. Content matches privacy keyword patterns (tax, salary, medical, etc.)

    "private 但记住" / "private but remember" → still private, but thread memory kept.
    """
    # Explicit user override — user said "private" in the message
    lower = content[:500].lower()
    if any(kw in lower for kw in ("private", "secret", "隐私", "私密", "保密")):
        return True

    # Task metadata
    if task_id and ("private" in task_id or "secret" in task_id):
        return True
    if tags and ("private" in tags or "secret" in tags):
        return True

    # Content pattern matching
    return bool(_PRIVATE_KEYWORDS.search(content[:500]))


# ---------------------------------------------------------------------------
# Streaming progress — thread-local task context for intermediate updates
# ---------------------------------------------------------------------------

_tls = threading.local()


def _set_streaming_task_id(task_id: str):
    """Store task_id in thread-local so any agent can emit progress."""
    _tls.task_id = task_id


def emit_progress(text: str, icon: str = "arrow.right.circle"):
    """Emit an intermediate progress update from within any agent handler.

    Agents can call this to surface partial results before the final output.
    Safe to call even if no task_id is set (no-op in that case).
    """
    task_id = getattr(_tls, "task_id", None)
    if task_id:
        _emit_status(task_id, text[:200], icon)


# ---------------------------------------------------------------------------
# Plan step schema validation
# ---------------------------------------------------------------------------

# _VALID_TIERS and _VALID_DIFFICULTIES moved to planning/plan_schema.py


# Import from canonical planning module (single source of truth)
from planning.plan_schema import (
    AGENT_ALIASES as _AGENT_ALIASES,
    normalize_agent_name as _normalize_agent_name,
    validate_plan_step as _validate_plan_step,
)


# ---------------------------------------------------------------------------
# Calibration functions extracted to execution/calibration.py
# ---------------------------------------------------------------------------
from execution.calibration import (
    _CALIBRATION_FILE,
    _QUALITY_LOG,
    _record_premortem,
    _record_postmortem,
    _track_output_quality,
    detect_quality_regression,
)


class _Heartbeat:
    """Background heartbeat for long-running tasks — emits status every 60s."""

    def __init__(self, task_id: str, interval: int = 60):
        self._task_id = task_id
        self._interval = interval
        self._start = time.time()
        self._timer = None
        self._running = False

    def start(self):
        self._running = True
        self._schedule()

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()

    def _schedule(self):
        if not self._running:
            return
        self._timer = threading.Timer(self._interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        if not self._running:
            return
        elapsed = int(time.time() - self._start)
        mins = elapsed // 60
        _emit_status(self._task_id, f"Still working... ({mins}m elapsed)", "hourglass")
        self._schedule()


# ---------------------------------------------------------------------------
# Imports from extracted modules (task_support, task_result, plan_executor)
# ---------------------------------------------------------------------------

from task_support import (
    _load_exec_history,
    _append_exec_log,
    _verify_output,
    _get_round_num,
    smart_classify,
    _enrich_plan_with_runtime_policy,
    _result_metadata,
    _safe_general_fallback,
    try_extract_skill,
    _register_runtime_tools_created,
    _is_approval,
    _is_rejection,
    _execute_pending_publish,
    _invoke_registry_handler,
    _invoke_registry_preflight,
)

from task_result import (
    _write_progress,
    _validate_completion,
    _extract_knowledge_writeback,
    _step_id_from_metadata,
    _serialize_checks,
    _verification_payload_from_verify,
    _verification_not_run,
    _normalize_verification_payload,
    _step_verification_payload,
    _workspace_relative_artifact_paths,
    _collect_result_artifacts,
    _infer_failure_class,
    _infer_next_action,
    _canonicalize_result_payload,
    _write_result,
    _snapshot_file,
    _verify_step_artifact,
    _ensure_step_result,
    _load_step_summary,
    _update_thread_memory,
)

from plan_executor import (
    _execute_plan,
    _execute_plan_steps,
)

# ---------------------------------------------------------------------------
# Context helpers extracted to execution/context.py
# ---------------------------------------------------------------------------
from execution.context import (
    load_task_conversation,
    load_thread_history,
    load_thread_memory,
    compress_conversation,
    _truncate_messages,
    _load_recent_journals,
    _load_recent_briefings,
)
from execution.plan_state import (
    initialize_plan_artifacts,
    mark_step_finished,
    mark_step_running,
)


# ---------------------------------------------------------------------------
# Discussion mode — conversational exchange, not task execution
# ---------------------------------------------------------------------------

# _EDIT_MARKERS, _is_edit_request, _handle_edit_artifact -> handlers_legacy.py

# _load_recent_journals, _load_recent_briefings → imported from execution/context.py above

# handle_discussion -> handlers_legacy.py


def main():
    parser = argparse.ArgumentParser(description="TalkBridge task worker")
    parser.add_argument("--msg-file", required=True, help="Path to message JSON")
    parser.add_argument("--workspace", required=True, help="Workspace directory")
    parser.add_argument("--task-id", required=True, help="Task ID")
    parser.add_argument("--thread-id", default="", help="Thread ID for context")
    args = parser.parse_args()

    # Set up logging to workspace
    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(workspace / "worker.log", encoding="utf-8"),
        ],
    )

    log.info("Worker started: task=%s thread=%s", args.task_id, args.thread_id)
    task_start = time.time()
    _perf_start = time.perf_counter()

    # Read message
    try:
        msg_data = json.loads(Path(args.msg_file).read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read message: %s", e)
        _write_result(workspace, args.task_id, "error", f"Failed to read message: {e}")
        sys.exit(1)

    msg_content = msg_data.get("content", "")
    msg_sender = msg_data.get("sender", "unknown")
    thread_id = args.thread_id or msg_data.get("thread_id", "")
    workflow_id = derive_workflow_id(
        task_id=args.task_id,
        thread_id=thread_id,
        workflow_id=msg_data.get("workflow_id", ""),
    )
    _set_active_workflow(workflow_id)

    # --- User access control context ---
    _user_id = msg_data.get("user_id", "ang")
    _set_active_user(_user_id)
    _user_role = msg_data.get("user_role", "admin")
    _model_restriction = msg_data.get("model_restriction")
    _content_filter = msg_data.get("content_filter", False)
    _allowed_agents = msg_data.get("allowed_agents", [])
    log.info(
        "User context: workflow=%s user=%s role=%s model_restriction=%s content_filter=%s allowed_agents=%s",
        workflow_id,
        _user_id,
        _user_role,
        _model_restriction,
        _content_filter,
        ",".join(_allowed_agents) if _allowed_agents else "all",
    )

    # Load conversation history and execution history for context
    conversation = load_task_conversation(args.task_id, user_id=_user_id)
    conversation = compress_conversation(conversation)
    exec_history = _load_exec_history(workspace)

    # --- Check for pending plan (resume after user confirmation) ---
    pending_plan_file = workspace / "pending_plan.json"
    if pending_plan_file.exists():
        try:
            plan = json.loads(pending_plan_file.read_text(encoding="utf-8"))
            pending_plan_file.unlink()  # consumed
            plan = _enrich_plan_with_runtime_policy(plan)
            log.info("Resuming pending plan (%d steps): %s", len(plan), plan)
            _execute_plan(
                plan,
                workspace,
                args.task_id,
                msg_content,
                msg_sender,
                thread_id,
                user_id=_user_id,
                allowed_agents=_allowed_agents,
                content_filter=_content_filter,
                model_restriction=_model_restriction,
                workflow_id=workflow_id,
            )
            log.info("Worker exiting")
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load pending plan, re-planning: %s", e)

    # --- Check for article comment (comment_YYYY-MM-DD_suffix thread ID) ---
    if thread_id.startswith("comment_"):
        _handle_article_comment(workspace, args.task_id, thread_id, msg_content, msg_sender)
        log.info("Worker exiting (comment)")
        return

    # --- Check for in-progress video session (stateful multi-round) ---
    video_state_file = workspace / "video_state.json"
    if video_state_file.exists():
        log.info("Resuming video session (video_state.json found)")
        _handle_video(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (video)")
        return

    # --- Check for in-progress photo session (stateful multi-round) ---
    photo_state_file = workspace / "photo_state.json"
    if photo_state_file.exists():
        log.info("Resuming photo session (photo_state.json found)")
        _handle_photo(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (photo)")
        return

    # --- Check for approval (user confirms a pending action) ---
    if _is_approval(msg_content):
        # Check for autowrite approval — schedule publish, don't re-preview
        if args.task_id.startswith("autowrite_"):
            _handle_autowrite_approval(workspace, args.task_id)
            log.info("Worker exiting (autowrite approval → pending publish)")
            return

        pending_plan_file = workspace / "pending_plan.json"
        if pending_plan_file.exists():
            log.info("Approval detected, resuming pending plan")
            _emit_status(args.task_id, "Resuming...", "play.circle")
            try:
                plan = json.loads(pending_plan_file.read_text(encoding="utf-8"))
                pending_plan_file.unlink()
                plan = _enrich_plan_with_runtime_policy(plan)
                _execute_plan(
                    plan,
                    workspace,
                    args.task_id,
                    msg_content,
                    msg_sender,
                    thread_id,
                    user_id=_user_id,
                    allowed_agents=_allowed_agents,
                    content_filter=_content_filter,
                    model_restriction=_model_restriction,
                    workflow_id=workflow_id,
                )
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load pending plan on approval: %s", e)
                _write_result(workspace, args.task_id, "error", f"Could not resume: {e}")
            log.info("Worker exiting (approval)")
            return

    # --- Load full task data for thread context ---
    task_data = msg_data  # Contains messages array if available
    # Try items/ first, fallback to legacy tasks/
    item_file = _item_file(args.task_id, _user_id)
    task_file = MIRA_DIR / "tasks" / f"{args.task_id}.json"
    src_file = item_file if item_file.exists() else task_file
    if src_file.exists():
        try:
            task_data = json.loads(src_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # --- Check for edit-artifact request (lightweight edit, skip full planning) ---
    if _is_edit_request(msg_content, task_data):
        log.info("Edit-artifact mode detected for task %s", args.task_id)
        _emit_status(args.task_id, "Editing...", "pencil")
        response = _handle_edit_artifact(task_data, workspace, args.task_id, msg_content, msg_sender, thread_id)
        if response:
            log.info("Worker exiting (edit)")
            return
        log.warning("Edit handler returned empty, falling through to task planning")

    # --- Fast-path for conversational discussions (no planner, no agent
    # routing — just a direct reply via handle_discussion). Soul questions,
    # threads, comment-replies all hit this. Avoids 2+ minutes of planning
    # overhead for what should be a 30s response.
    if task_data.get("type") == "discussion":
        log.info("Discussion fast-path for task %s", args.task_id)
        _emit_status(args.task_id, "Thinking...", "bubble.left.and.text.bubble.right")
        handle_discussion(task_data, workspace, args.task_id, thread_id, tier="light")
        log.info("Worker exiting (discussion fast-path)")
        return

    # --- Fixed startup: read progress from prior runs ---
    progress = ""
    progress_file = workspace / "progress.md"
    if progress_file.exists():
        progress = progress_file.read_text(encoding="utf-8")
        log.info("Loaded progress.md (%d chars) from prior run", len(progress))

    # --- Privacy pre-routing: detect secret tasks LOCALLY before any cloud call ---
    task_tags = msg_data.get("tags", [])
    if _is_private_task(msg_content, task_id=args.task_id, tags=task_tags):
        log.info("Privacy keywords detected — routing to secret agent (local only)")
        _emit_status(args.task_id, "Private mode...", "lock.shield")
        _handle_secret(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (secret — no cloud, no persist)")
        return

    # --- Proactive recall: search memory for relevant prior context ---
    prior_context = ""
    try:
        prior_context = recall_context(msg_content, user_id=_user_id)
        if prior_context:
            log.info("Proactive recall found relevant context (%d chars)", len(prior_context))
    except Exception as e:
        log.warning("Proactive recall failed: %s", e)

    # --- Plan and execute via LLM ---
    _emit_status(args.task_id, "Planning...", "list.bullet.clipboard")

    # Inject progress into context so planner knows what was done before
    planning_context = prior_context
    if progress:
        planning_context = f"## Progress from prior session\n{progress}\n\n{planning_context}"

    _perf_dispatch_ms = round((time.perf_counter() - _perf_start) * 1000)
    _perf_inference_t0 = time.perf_counter()
    _think_start = time.time()
    plan = _plan_task(
        msg_content,
        conversation=conversation,
        exec_history=exec_history,
        prior_context=planning_context,
        allowed_agents=_allowed_agents,
        content_filter=_content_filter,
    )
    _think_duration = time.time() - _think_start
    _perf_inference_ms = round((time.perf_counter() - _perf_inference_t0) * 1000)
    task_agent = plan[0].get("agent", "unknown") if plan else "unknown"
    log.info(
        "PHASE_TIMING task_id=%s agent=%s phase=think configured_timeout_s=%d actual_duration_s=%.2f",
        args.task_id,
        task_agent,
        CLAUDE_TIMEOUT_THINK,
        _think_duration,
    )
    record_phase_duration(task_agent, "think", CLAUDE_TIMEOUT_THINK, _think_duration)

    plan = _enrich_plan_with_runtime_policy(plan)
    log.info("Plan: %s", plan)

    _HEAVY_HORIZON_AGENTS = {"analyst", "researcher", "writer", "podcast"}
    _horizon_agent = plan[0].get("agent", "unknown") if plan else "unknown"
    _horizon_limit = MAX_TASK_HORIZON_STEPS_HEAVY if _horizon_agent in _HEAVY_HORIZON_AGENTS else MAX_TASK_HORIZON_STEPS
    _horizon_exceeded = len(plan) > _horizon_limit
    if _horizon_exceeded:
        log.info(
            "Horizon limit: truncating plan from %d to %d steps for agent %s", len(plan), _horizon_limit, _horizon_agent
        )
        plan = plan[:_horizon_limit]

    reset_session_tokens()
    _act_start = time.time()
    _perf_tools_t0 = time.perf_counter()
    _execute_plan(
        plan,
        workspace,
        args.task_id,
        msg_content,
        msg_sender,
        thread_id,
        user_id=_user_id,
        allowed_agents=_allowed_agents,
        content_filter=_content_filter,
        model_restriction=_model_restriction,
        workflow_id=workflow_id,
    )
    if _horizon_exceeded:
        _checkpoint_msg = f"Task paused: horizon limit of {_horizon_limit} steps reached for {_horizon_agent}. Checkpoint saved — resume to continue."
        _emit_status(args.task_id, _checkpoint_msg, "pause.circle")
        _write_result(workspace, args.task_id, "paused_horizon_limit", _checkpoint_msg)
        log.info("Worker exiting (paused_horizon_limit: %s steps > %d)", len(plan), _horizon_limit)
        return
    _act_duration = time.time() - _act_start
    _perf_tools_ms = round((time.perf_counter() - _perf_tools_t0) * 1000)
    log.info(
        "PHASE_TIMING task_id=%s agent=%s phase=act configured_timeout_s=%d actual_duration_s=%.2f",
        args.task_id,
        task_agent,
        CLAUDE_TIMEOUT_ACT,
        _act_duration,
    )
    record_phase_duration(task_agent, "act", CLAUDE_TIMEOUT_ACT, _act_duration)

    _in_tok, _out_tok, _model_id = get_session_tokens()
    _result_words = 0
    _out_md = workspace / "output.md"
    if _out_md.exists():
        try:
            _result_words = len(_out_md.read_text(encoding="utf-8", errors="ignore").split())
        except OSError:
            pass
    _efficiency = _out_tok / max(1, _result_words)
    _heavy_agents = {"writer", "researcher", "podcast", "socialmedia"}
    _tier = "heavy" if task_agent in _heavy_agents else "light"
    _budget = TOKEN_BUDGET_WARN_HEAVY if _tier == "heavy" else TOKEN_BUDGET_WARN_LIGHT
    _total_tok = _in_tok + _out_tok
    log.info(
        "TOKEN_EFFICIENCY task_id=%s agent=%s model=%s input_tokens=%d output_tokens=%d total_tokens=%d words=%d efficiency=%.2f",
        args.task_id,
        task_agent,
        _model_id,
        _in_tok,
        _out_tok,
        _total_tok,
        _result_words,
        _efficiency,
    )
    _log_efficiency(
        task_id=args.task_id,
        agent=task_agent,
        model=_model_id,
        input_tokens=_in_tok,
        output_tokens=_out_tok,
        words=_result_words,
    )
    if _total_tok > _budget:
        log.warning(
            "TOKEN_BUDGET_WARN task_id=%s agent=%s tier=%s total_tokens=%d budget=%d",
            args.task_id,
            task_agent,
            _tier,
            _total_tok,
            _budget,
        )

    # --- Write progress.md for next session ---
    _write_progress(workspace, args.task_id, msg_content)

    task_type = task_agent
    configured_timeout = CLAUDE_TIMEOUT_ACT
    elapsed = time.time() - task_start
    utilization_pct = elapsed / configured_timeout * 100
    log.info(
        "task_timing task_type=%s configured_timeout_s=%d actual_duration_s=%.2f utilization_pct=%.1f",
        task_type,
        configured_timeout,
        elapsed,
        utilization_pct,
    )
    if utilization_pct > 80:
        log.warning(
            "Timeout pressure: %s used %.0f%% of %ds budget",
            task_type,
            utilization_pct,
            configured_timeout,
        )
    elif utilization_pct < 5:
        log.warning(
            "Timeout over-provisioned: %s used only %.1f%% of %ds budget",
            task_type,
            utilization_pct,
            configured_timeout,
        )
    try:
        _stats_file = TASKS_DIR / "timing_stats.jsonl"
        _stats_entry = json.dumps(
            {
                "ts": _utc_iso(),
                "task_type": task_type,
                "phase": "act",
                "configured_timeout_s": configured_timeout,
                "actual_duration_s": round(elapsed, 2),
                "utilization_pct": round(utilization_pct, 1),
                "token_usage": {"input": _in_tok, "output": _out_tok},
            },
            ensure_ascii=False,
        )
        with open(_stats_file, "a", encoding="utf-8") as _sf:
            _sf.write(_stats_entry + "\n")
    except Exception as _e:
        log.debug("Failed to write timing stats: %s", _e)

    try:
        _result_file = workspace / "result.json"
        if _result_file.exists():
            _result = json.loads(_result_file.read_text(encoding="utf-8"))
            _result["token_usage"] = {"input": _in_tok, "output": _out_tok}
            _tmp = _result_file.with_suffix(".tmp")
            _tmp.write_text(json.dumps(_result, ensure_ascii=False, indent=2), encoding="utf-8")
            _tmp.rename(_result_file)
    except Exception as _e:
        log.debug("Failed to write token_usage to result.json: %s", _e)

    _perf_total_ms = round((time.perf_counter() - _perf_start) * 1000)
    _phase_record = {
        "task_id": args.task_id,
        "agent": task_agent,
        "phase_dispatch_ms": _perf_dispatch_ms,
        "phase_inference_ms": _perf_inference_ms,
        "phase_tools_ms": _perf_tools_ms,
        "total_ms": _perf_total_ms,
    }
    log.info("PHASE_BREAKDOWN %s", json.dumps(_phase_record, ensure_ascii=False))
    try:
        _phase_log = LOGS_DIR / "task_phase_timing.jsonl"
        with open(_phase_log, "a", encoding="utf-8") as _pf:
            _pf.write(json.dumps({**_phase_record, "ts": _utc_iso()}, ensure_ascii=False) + "\n")
    except Exception as _pe:
        log.debug("Phase timing log write failed: %s", _pe)
    log.info("Worker exiting")


# _plan_task → imported from planning/planner.py above

# _synthesize_outputs → imported from planning/planner.py above

# Handler functions (_handle_briefing, _handle_writing, etc.) have been
# extracted to handlers_legacy.py and are imported at the top of this file.


# ---------------------------------------------------------------------------
# Handler imports — deferred to avoid circular import with handlers_legacy.py
# (handlers_legacy imports helpers defined above from this module)
# ---------------------------------------------------------------------------
from handlers_legacy import (  # noqa: E402
    handle_discussion,
    _handle_edit_artifact,
    _handle_briefing,
    _handle_writing,
    _handle_quick_write,
    _handle_full_write,
    _handle_publish,
    _handle_analyst,
    _handle_video,
    _handle_photo,
    _handle_podcast,
    _handle_article_comment,
    _handle_math,
    _handle_secret,
    _handle_discussion_agent,
    _handle_socialmedia,
    _handle_surfer,
    _handle_general,
    _handle_autowrite_approval,
    _is_edit_request,
    _is_quick_write,
    _write_comment_reply_sidecar,
    _EDIT_MARKERS,
)

if __name__ == "__main__":
    main()
