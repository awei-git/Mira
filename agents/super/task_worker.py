#!/usr/bin/env python3
"""Task worker — standalone sub-agent process for Mira.

Spawned by TaskManager.dispatch(). Reads a message, loads context,
calls claude_act(), writes output + result JSON.

Usage:
    python task_worker.py --msg-file <path> --workspace <path> --task-id <id> [--thread-id <id>]
"""
import argparse
import ast
import atexit
import functools
import json
import logging
import random
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
    TASK_TIMEOUT,
    TOKEN_BUDGET_WARN_LIGHT,
    TOKEN_BUDGET_WARN_HEAVY,
    MAX_TASK_HORIZON_STEPS,
    MAX_TASK_HORIZON_STEPS_HEAVY,
    MAX_ATTRIBUTION_DEPTH,
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
from soul_manager import audit_skill_judgment
from llm import (
    claude_act,
    claude_think,
    ClaudeTimeoutError,
    reset_session_tokens,
    get_session_tokens,
    set_model_policy,
    _log_efficiency,
)
import llm as _llm_module
from sub_agent import (
    REASONING_REWRITE_PROMPT,
    SubAgentFormatError,
    extract_reasoning_payload,
    require_reasoning_in_instruction,
    task_log_tokens_from_counts,
    _validate_result,
)

# Handler functions extracted to handlers_legacy.py (imported after all helpers
# are defined to avoid circular import — see bottom of file)


log = logging.getLogger("task_worker")

_llm_timing_state = threading.local()


def _reset_llm_timing():
    _llm_timing_state.llm_time = 0.0


def _get_llm_time() -> float:
    return float(getattr(_llm_timing_state, "llm_time", 0.0) or 0.0)


_receipt_state = threading.local()


def _reset_task_receipt(started_at: str) -> None:
    _receipt_state.started_at = started_at
    _receipt_state.agent_id = ""
    _receipt_state.skills_invoked = []
    _receipt_state.external_actions = []


def _set_receipt_agent(agent_id: str | None) -> None:
    if agent_id:
        _receipt_state.agent_id = str(agent_id)


def _record_skill_invocation(skill_name: str | None) -> None:
    name = str(skill_name or "").strip()
    if not name:
        return
    skills = getattr(_receipt_state, "skills_invoked", None)
    if skills is None:
        return
    if name not in skills:
        skills.append(name)


def _record_external_action(action_type: str, target: str | None) -> None:
    action_type = str(action_type or "").strip()
    target = str(target or "").strip()
    if not action_type or not target:
        return
    actions = getattr(_receipt_state, "external_actions", None)
    if actions is None:
        return
    action = {"type": action_type, "target": target}
    if action not in actions:
        actions.append(action)


def _receipt_status_from_result(workspace: Path) -> tuple[str, str, dict]:
    result_path = workspace / "result.json"
    if not result_path.exists():
        return "unknown", "", {}
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "unknown", "", {}
    status = str(result.get("status") or "unknown")
    agent_id = str(result.get("agent") or result.get("execution_agent") or "")
    return status, agent_id, result


def _receipt_actions_from_result(result: dict) -> list[dict]:
    actions: list[dict] = []
    for artifact in result.get("artifacts_produced", []) if isinstance(result, dict) else []:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("type") == "file" and artifact.get("path"):
            actions.append({"type": "file_write", "target": str(artifact["path"])})

    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    artifact_type = str(verification.get("artifact_type") or "").strip().lower()
    verification_method = str(result.get("verification_method") or "").strip().lower()
    tags = {str(tag).strip().lower() for tag in result.get("tags", []) if tag}
    if artifact_type == "publish" or "publish" in tags or verification_method == "publish_url_confirmed":
        target = str(verification.get("target") or "").strip()
        if not target:
            urls = re.findall(r"https?://[^\s)>\]\"']+", str(result.get("summary") or ""))
            target = urls[0].rstrip(".,") if urls else "Substack"
        actions.append({"type": "publish", "target": target})
    return actions


def _write_task_receipt(
    workspace: Path,
    task_id: str,
    *,
    agent_id: str | None = None,
    started_at: str | None = None,
    exit_status: str | None = None,
) -> None:
    result_status, result_agent, result = _receipt_status_from_result(workspace)
    receipt_agent = str(agent_id or getattr(_receipt_state, "agent_id", "") or result_agent or "unknown")
    completed_at = _utc_iso()
    actions = list(getattr(_receipt_state, "external_actions", []) or [])
    for action in _receipt_actions_from_result(result):
        if action not in actions:
            actions.append(action)
    receipt = {
        "timestamp": completed_at,
        "task_id": str(task_id),
        "agent_id": receipt_agent,
        "started_at": started_at or getattr(_receipt_state, "started_at", "") or completed_at,
        "completed_at": completed_at,
        "exit_status": str(exit_status or result_status),
        "skills_invoked": list(getattr(_receipt_state, "skills_invoked", []) or []),
        "external_actions": actions,
    }
    try:
        path = workspace / "receipt.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.debug("Failed to write receipt for %s: %s", task_id, exc)


def _wrap_llm_api_call(fn):
    if getattr(fn, "_mira_timed_llm_call", False):
        return fn

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        start = time.perf_counter()
        _record_external_action("api_call", fn.__name__)
        try:
            return fn(*args, **kwargs)
        finally:
            _llm_timing_state.llm_time = _get_llm_time() + (time.perf_counter() - start)

    wrapped._mira_timed_llm_call = True
    return wrapped


claude_act = _wrap_llm_api_call(claude_act)
claude_think = _wrap_llm_api_call(claude_think)
_llm_module.claude_act = claude_act
_llm_module.claude_think = claude_think

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


def _get_active_workflow_intent() -> str:
    return getattr(_ctx, "workflow_intent", "")


def _set_active_workflow_intent(intent: str):
    _ctx.workflow_intent = intent or ""


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
from planning import planner as _planner_module
from planning.planner import _plan_task, _synthesize_outputs


def _load_super_skills(task_content: str = "") -> str:
    skills_dir = Path(__file__).resolve().parent / "skills"
    index_path = skills_dir / "index.json"
    if not index_path.exists():
        return ""
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    if task_content:
        lower = task_content.lower()
        selected = []
        for entry in index:
            fname = entry.get("file", "")
            tags = entry.get("tags", [])
            desc = entry.get("description", "").lower()
            tag_match = any(str(tag).lower() in lower for tag in tags)
            desc_words = set(desc.split())
            content_words = set(lower.split())
            desc_match = len(desc_words & content_words) >= 2
            multi_match = "multi-step" in fname and "step" in lower
            synth_match = "synthesis" in fname and "synthesize" in lower
            if tag_match or desc_match or multi_match or synth_match:
                selected.append(entry)
        if not selected:
            selected = index
    else:
        selected = index
    sections = []
    for entry in selected:
        skill_file = skills_dir / entry.get("file", "")
        if not skill_file.exists():
            continue
        try:
            skill_text = skill_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        judgment_audit = audit_skill_judgment(skill_text, entry.get("tags", []))
        if not judgment_audit["passed"]:
            log.warning(
                "SKILL_LOAD blocked: skill=%s reason='judgment template incomplete' missing=%s tags=%s",
                entry.get("name") or entry.get("file", ""),
                judgment_audit["missing"],
                judgment_audit["tags"],
            )
            continue
        skill_name = entry.get("name") or entry.get("file", "")
        _record_skill_invocation(skill_name)
        _record_external_action("skill_execution", skill_name)
        sections.append(skill_text)
    return "\n\n---\n\n".join(sections)


_planner_module._load_super_skills = _load_super_skills


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
            messages = item.setdefault("messages", [])
            while messages and messages[-1].get("kind") == "status_card" and messages[-1].get("sender") == "agent":
                messages.pop()
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
            messages = task.setdefault("messages", [])
            while messages and messages[-1].get("kind") == "status_card" and messages[-1].get("sender") == "agent":
                messages.pop()
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

# Word-boundary match for the explicit-override keywords. The previous plain
# `"private" in lower` triggered on path literals like `/private/tmp/...`
# (macOS standard) and routed market-analysis tasks to the secret/oMLX agent.
# `(?<![\w/])` before the keyword excludes word-prefix matches AND path-prefix
# matches; `\b` after stops `private_key` from also matching.
_PRIVACY_OVERRIDE_EN = re.compile(
    r"(?<![\w/])(?:private|secret)\b",
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
    # Explicit user override — user said "private" / "secret" / privacy
    # words AS A STANDALONE WORD in the message. Plain substring match
    # was too eager: paths like `/private/tmp/` (macOS standard) and any
    # file path containing the literal "private" misrouted to oMLX. Use
    # word-boundary regex on English keywords. Chinese keywords don't
    # have a word-boundary concept so they keep substring match.
    head = content[:500]
    if _PRIVACY_OVERRIDE_EN.search(head):
        return True
    if any(kw in head for kw in ("隐私", "私密", "保密")):
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


def _load_matching_progress(workspace: Path, task_id: str) -> str:
    """Load progress.md only when it belongs to this exact task.

    Workspace names are human-readable and can collide. Progress from a
    different task is worse than no progress because it makes the worker and
    UI report stale state as if it were current.
    """
    progress_file = workspace / "progress.md"
    if not progress_file.exists():
        return ""
    try:
        progress = progress_file.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read progress.md for %s: %s", task_id, exc)
        return ""

    first_line = progress.splitlines()[0] if progress.splitlines() else ""
    match = re.search(r"^#\s*Progress\s+[—-]\s+(\S+)", first_line)
    if not match:
        log.warning("Ignoring progress.md for %s: missing task id header", task_id)
        return ""
    progress_task_id = match.group(1).strip()
    if progress_task_id != task_id:
        log.warning(
            "Ignoring stale progress.md in %s: belongs to %s, current task is %s",
            workspace,
            progress_task_id,
            task_id,
        )
        return ""
    log.info("Loaded progress.md (%d chars) for current task", len(progress))
    return progress


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

    def __init__(self, task_id: str, interval: int = 60, workspace: Path | None = None):
        self._task_id = task_id
        self._interval = interval
        self._start = time.time()
        self._timer = None
        self._running = False
        self._workspace = workspace
        self._count = 0

    def start(self):
        self._running = True
        self._write_heartbeat("running")
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
        self._count += 1
        activity = self._activity_snapshot(elapsed)
        self._write_heartbeat("running", activity)
        log.info("Heartbeat task=%s elapsed=%ds count=%d", self._task_id, elapsed, self._count)
        _emit_status(self._task_id, activity["status_text"], activity["status_icon"])
        self._schedule()

    def _write_heartbeat(self, status: str, activity: dict | None = None):
        if not self._workspace:
            return
        elapsed = int(time.time() - self._start)
        activity = activity or self._activity_snapshot(elapsed)
        payload = {
            "task_id": self._task_id,
            "status": status,
            "started_at": datetime.fromtimestamp(self._start, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": _utc_iso(),
            "elapsed_seconds": elapsed,
            "heartbeat_count": self._count,
            **{k: v for k, v in activity.items() if k != "status_icon"},
        }
        try:
            path = self._workspace / "heartbeat.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.debug("Failed to write heartbeat for %s: %s", self._task_id, exc)

    def _activity_snapshot(self, elapsed: int) -> dict:
        current = self._current_step()
        elapsed_text = _format_elapsed(elapsed)
        remaining = max(0, int(TASK_TIMEOUT) - elapsed)
        if remaining:
            eta_text = f"timeout guard in {_format_elapsed(remaining)}"
        else:
            eta_text = "past timeout guard; supervisor should reap if still active"

        if current:
            prefix = f"Step {current['current_step']}/{current['total_steps']} · {current['agent']}"
            action = current.get("action") or "working"
            text = f"{prefix}: {_clip(action, 82)}. Elapsed {elapsed_text}; {eta_text}."
            return {
                **current,
                "elapsed_text": elapsed_text,
                "eta_text": eta_text,
                "status_text": text,
                "status_icon": "hourglass",
            }

        text = f"Working on request. Elapsed {elapsed_text}; {eta_text}."
        return {
            "current_phase": "running",
            "elapsed_text": elapsed_text,
            "eta_text": eta_text,
            "status_text": text,
            "status_icon": "hourglass",
        }

    def _current_step(self) -> dict | None:
        if not self._workspace:
            return None
        for filename in ("step_states.json", "plan.json"):
            path = self._workspace / filename
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            steps = data.get("steps") if isinstance(data, dict) else None
            if not isinstance(steps, list) or not steps:
                continue
            active = next((s for s in steps if isinstance(s, dict) and s.get("status") == "running"), None)
            if active is None:
                active = next((s for s in steps if isinstance(s, dict) and s.get("status") == "pending"), None)
            if active is None:
                active = next((s for s in reversed(steps) if isinstance(s, dict)), None)
            if not isinstance(active, dict):
                continue
            index = int(active.get("step_index") or 0) + 1
            agent = active.get("execution_agent") or active.get("declared_agent") or active.get("agent") or "agent"
            action = (
                active.get("input_summary")
                or active.get("instruction_preview")
                or active.get("success_criteria")
                or active.get("status")
                or ""
            )
            return {
                "current_phase": active.get("status") or "running",
                "current_step": index,
                "total_steps": len(steps),
                "current_agent": str(agent),
                "agent": str(agent),
                "action": str(action),
            }
        return None


def _format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _clip(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


_TASK_OUTPUT_ERROR_MARKERS = (
    "traceback (most recent call last)",
    "404 not found",
    "access denied",
    "permission denied",
)
_TASK_OUTPUT_GARBAGE_MARKERS = (
    "lorem ipsum",
    "[insert",
    "<placeholder",
    "todo: fill",
    "coming soon",
    "not implemented",
)
_CODE_TASK_TYPES = {"code", "coder", "coding", "software", "programming", "implementation", "bugfix", "refactor"}
_WRITING_TASK_TYPES = {
    "writing",
    "writer",
    "article",
    "essay",
    "blog",
    "post",
    "briefing",
    "socialmedia",
    "podcast",
}


def _verification_spot_check_rate() -> float:
    try:
        from config import VERIFICATION_SPOT_CHECK_RATE

        rate = float(VERIFICATION_SPOT_CHECK_RATE)
    except (ImportError, AttributeError, TypeError, ValueError):
        rate = 0.15
    return min(max(rate, 0.0), 1.0)


def _default_verification_depth(task_type: str) -> int:
    label = str(task_type or "").strip().lower()
    if any(marker in label for marker in _CODE_TASK_TYPES):
        return 2
    if any(marker in label for marker in _WRITING_TASK_TYPES):
        return 2
    return 1


def _artifact_exists(artifact: dict) -> bool:
    path = Path(str(artifact.get("path", "")))
    if not path:
        return False
    if artifact.get("exists") is False:
        return False
    return path.exists() and path.is_file()


def _artifact_size(artifact: dict) -> int:
    try:
        return int(artifact.get("size_bytes", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _read_verification_artifact_text(artifacts: list[dict]) -> str:
    chunks = []
    for artifact in artifacts:
        path = Path(str(artifact.get("path", "")))
        if not _artifact_exists(artifact) or path.suffix.lower() not in {"", ".md", ".txt", ".py", ".json", ".html"}:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:12000])
        except OSError:
            continue
    return "\n".join(chunks)


def _verify_depth_one(claimed_output: str, actual_artifacts: list[dict]) -> tuple[bool, str]:
    if not str(claimed_output or "").strip():
        return False, "claimed output is empty"
    if not actual_artifacts:
        return False, "no output artifacts were recorded"
    missing = [str(a.get("path", "")) for a in actual_artifacts if not _artifact_exists(a)]
    if missing:
        return False, f"missing artifact(s): {', '.join(missing[:3])}"
    empty = [str(a.get("path", "")) for a in actual_artifacts if _artifact_size(a) <= 0]
    if empty:
        return False, f"empty artifact(s): {', '.join(empty[:3])}"
    return True, "artifact existence and size checks passed"


def _verify_depth_two(task_type: str, claimed_output: str, actual_artifacts: list[dict]) -> tuple[bool, str]:
    passed, details = _verify_depth_one(claimed_output, actual_artifacts)
    if not passed:
        return False, details
    text = "\n".join([str(claimed_output or ""), _read_verification_artifact_text(actual_artifacts)]).strip()
    if len(text) < 80:
        if re.search(r"(?m)^#\s+\S+", text) and len(text) >= 15:
            return True, "short structured markdown artifact passed"
        return False, f"output substance check failed: only {len(text)} text characters"
    lower = text.lower()
    marker_hits = [m for m in _TASK_OUTPUT_GARBAGE_MARKERS if m in lower]
    if marker_hits:
        return False, f"output contains placeholder marker(s): {', '.join(marker_hits[:3])}"
    error_hits = [m for m in _TASK_OUTPUT_ERROR_MARKERS if m in lower]
    if error_hits and not any(marker in str(task_type or "").lower() for marker in _CODE_TASK_TYPES):
        return False, f"output contains error marker(s): {', '.join(error_hits[:3])}"
    completion_error = _validate_completion(Path("."), "", str(claimed_output or ""))
    if completion_error:
        return False, completion_error
    return True, "substance and content pattern checks passed"


def _verify_depth_three(task_type: str, claimed_output: str, actual_artifacts: list[dict]) -> tuple[bool, str]:
    passed, details = _verify_depth_two(task_type, claimed_output, actual_artifacts)
    if not passed:
        return False, details

    label = str(task_type or "").lower()
    code_like = any(marker in label for marker in _CODE_TASK_TYPES)
    python_paths = [
        Path(str(a.get("path", "")))
        for a in actual_artifacts
        if _artifact_exists(a) and Path(str(a.get("path", ""))).suffix.lower() == ".py"
    ]
    python_blocks = _extract_python_blocks(
        "\n".join([str(claimed_output or ""), _read_verification_artifact_text(actual_artifacts)])
    )
    if not code_like and not python_paths and not python_blocks:
        return True, "depth-3 executable check not applicable; depth-2 checks passed"

    import py_compile

    for path in python_paths:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            return False, f"python compile failed for {path.name}: {exc.msg.splitlines()[0][:200]}"
    for block in python_blocks:
        try:
            ast.parse(block)
        except SyntaxError as exc:
            return False, f"python code block syntax failed at line {exc.lineno}: {exc.msg}"
    if python_paths or python_blocks:
        return True, "execution-level python compile/syntax checks passed"
    return False, "no executable depth-3 verifier matched the output"


def verify_task_output(task_type, claimed_output, actual_artifacts) -> dict:
    default_depth = _default_verification_depth(str(task_type or ""))
    depth = default_depth
    if depth < 3 and random.random() < _verification_spot_check_rate():
        depth = 3

    artifacts = [a for a in (actual_artifacts or []) if isinstance(a, dict)]
    if depth <= 0:
        passed, details = True, "verification skipped"
    elif depth == 1:
        passed, details = _verify_depth_one(str(claimed_output or ""), artifacts)
    elif depth == 2:
        passed, details = _verify_depth_two(str(task_type or ""), str(claimed_output or ""), artifacts)
    else:
        passed, details = _verify_depth_three(str(task_type or ""), str(claimed_output or ""), artifacts)

    return {"depth": int(depth), "passed": bool(passed), "details": str(details)[:500]}


_PROXY_AUDIT_WRITING_MARKERS = (
    "as an ai",
    "in conclusion",
    "delve into",
    "tapestry",
    "it's important to note",
    "in today's fast-paced",
)

_PROXY_AUDIT_ERROR_MARKERS = (
    "404 not found",
    "page not found",
    "access denied",
    "forbidden",
    "internal server error",
    "traceback",
    "exception:",
)


def _proxy_audit_sample_rate() -> float:
    try:
        from config import PROXY_AUDIT_SAMPLE_RATE

        rate = float(PROXY_AUDIT_SAMPLE_RATE)
    except (ImportError, AttributeError, TypeError, ValueError):
        rate = 0.10
    return min(max(rate, 0.0), 1.0)


def _read_proxy_audit_text(workspace: Path, summary: str, artifacts: list[dict]) -> str:
    chunks = [summary or ""]
    for artifact in artifacts:
        if artifact.get("type") != "file":
            continue
        path = Path(str(artifact.get("path", "")))
        if not path.exists() or not path.is_file():
            continue
        if path.suffix.lower() not in {"", ".md", ".txt", ".py", ".json", ".html"}:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:12000]
        except OSError:
            continue
        if path.suffix.lower() == ".py":
            chunks.append(f"```python\n{content}\n```")
        else:
            chunks.append(content)
    for name in ("output.md", "summary.txt"):
        path = workspace / name
        if path.exists() and path.is_file():
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace")[:12000])
            except OSError:
                pass
    return "\n".join(chunks)


def _extract_python_blocks(text: str) -> list[str]:
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.IGNORECASE | re.DOTALL)
    return [block.strip() for block in blocks if block.strip()]


def _check_import_validity(text: str) -> str:
    import importlib.util

    modules: set[str] = set()
    for block in _extract_python_blocks(text):
        try:
            tree = ast.parse(block)
        except SyntaxError as exc:
            return f"proxy_false_positive: python syntax error at line {exc.lineno}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".", 1)[0])
    missing = sorted(m for m in modules if importlib.util.find_spec(m) is None)
    if missing:
        return f"proxy_false_positive: missing imports {', '.join(missing[:5])}"
    return "secondary_passed"


def _check_publish_content(result: dict, text: str) -> str:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    candidates = [str(verification.get("target", ""))]
    candidates.extend(re.findall(r"https?://[^\s)>\]\"']+", text))
    url = next((c.strip().rstrip(".,") for c in candidates if c.startswith(("http://", "https://"))), "")
    if not url:
        return "secondary_passed"
    try:
        req = Request(url, headers={"User-Agent": "MiraProxyAudit/1.0"})
        with urlopen(req, timeout=8) as resp:
            body = resp.read(20000).decode("utf-8", errors="replace").lower()
            if getattr(resp, "status", 200) >= 400:
                return f"proxy_false_positive: publish URL returned HTTP {resp.status}"
    except HTTPError as exc:
        return f"proxy_false_positive: publish URL returned HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return f"proxy_false_positive: publish URL fetch failed: {exc.__class__.__name__}"
    for marker in _PROXY_AUDIT_ERROR_MARKERS:
        if marker in body:
            return f"proxy_false_positive: publish content contains '{marker}'"
    return "secondary_passed"


def _secondary_proxy_check(workspace: Path, result: dict, summary: str) -> str:
    task_type = str(result.get("task_type") or "").lower()
    agent = str(result.get("agent") or "").lower()
    tags = {str(t).lower() for t in result.get("tags", []) if isinstance(t, str)}
    artifact_type = str((result.get("verification") or {}).get("artifact_type", "")).lower()
    text = _read_proxy_audit_text(workspace, summary, result.get("artifacts_produced", []))
    lower = text.lower()

    if artifact_type == "publish" or "publish" in tags or task_type == "publish":
        return _check_publish_content(result, text)
    if agent == "coder" or "code" in tags or "coding" in tags:
        return _check_import_validity(text)
    if agent == "writer" or "writing" in tags or "写作" in tags or task_type in {"writing", "article", "essay"}:
        hits = [marker for marker in _PROXY_AUDIT_WRITING_MARKERS if marker in lower]
        if len(hits) >= 2:
            return f"proxy_false_positive: writing contains anti-AI markers {', '.join(hits[:3])}"
    for marker in _PROXY_AUDIT_ERROR_MARKERS:
        if marker in lower:
            return f"proxy_false_positive: output contains '{marker}'"
    return "secondary_passed"


def _maybe_log_proxy_audit(workspace: Path, task_id: str, summary: str) -> None:
    if random.random() >= _proxy_audit_sample_rate():
        return
    result_path = workspace / "result.json"
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    proxy_passed = bool(result.get("outcome_verified"))
    if not proxy_passed:
        return
    proxy_type = str(
        result.get("verification_method") or (result.get("verification") or {}).get("proxy_checked") or "unknown"
    )
    secondary = _secondary_proxy_check(workspace, result, summary)
    entry = {
        "task_id": task_id,
        "proxy_type": proxy_type,
        "proxy_passed": True,
        "secondary_check_result": secondary,
        "timestamp": _utc_iso(),
    }
    try:
        log_path = MIRA_ROOT / "data" / "proxy_drift.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("Proxy audit log write failed for %s: %s", task_id, exc)


# ---------------------------------------------------------------------------
# Imports from extracted modules (task_support, task_result, plan_executor)
# ---------------------------------------------------------------------------

from task_support import (
    _load_exec_history,
    _append_exec_log as _base_append_exec_log,
    _verify_output,
    _get_round_num,
    smart_classify,
    _enrich_plan_with_runtime_policy,
    _result_metadata,
    _safe_general_fallback,
    try_extract_skill as _base_try_extract_skill,
    _register_runtime_tools_created,
    _is_approval,
    _is_rejection,
    _execute_pending_publish as _base_execute_pending_publish,
    _invoke_registry_handler as _base_invoke_registry_handler,
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
    _write_result as _base_write_result,
    _snapshot_file,
    _verify_step_artifact,
    _ensure_step_result,
    _load_step_summary,
    _update_thread_memory,
)


_exec_log_token_state = _threading.local()


def _reset_exec_log_token_state():
    _exec_log_token_state.input = 0
    _exec_log_token_state.output = 0


def _token_delta_for_exec_log() -> dict | None:
    input_tokens, output_tokens, model_id = get_session_tokens()
    prev_input = int(getattr(_exec_log_token_state, "input", 0) or 0)
    prev_output = int(getattr(_exec_log_token_state, "output", 0) or 0)
    _exec_log_token_state.input = input_tokens
    _exec_log_token_state.output = output_tokens
    delta_input = max(0, int(input_tokens) - prev_input)
    delta_output = max(0, int(output_tokens) - prev_output)
    if delta_input == 0 and delta_output == 0:
        return None
    return task_log_tokens_from_counts(delta_input, delta_output, model_id)


def _add_tokens_to_last_exec_log_entry(workspace: Path, tokens: dict) -> None:
    log_file = workspace / "exec_log.jsonl"
    if not log_file.exists():
        return
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            return
        entry = json.loads(lines[-1])
        entry["tokens"] = tokens
        workflow_intent = _get_active_workflow_intent()
        if workflow_intent:
            entry["workflow_intent"] = workflow_intent[:500]
        lines[-1] = json.dumps(entry, ensure_ascii=False)
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        return


def _append_exec_log(
    workspace: Path,
    round_num: int,
    agent: str,
    status: str,
    output_preview: str,
    verification_depth: str = "",
):
    _base_append_exec_log(workspace, round_num, agent, status, output_preview, verification_depth)
    tokens = _token_delta_for_exec_log()
    if tokens is not None:
        _add_tokens_to_last_exec_log_entry(workspace, tokens)


def _write_token_usage_record(agent_name: str, model: str, input_tokens: int, output_tokens: int) -> None:
    if int(input_tokens or 0) == 0 and int(output_tokens or 0) == 0:
        return
    record = {
        "timestamp": _utc_iso(),
        "agent_name": str(agent_name or "unknown"),
        "model": str(model or ""),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
    }
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with (LOGS_DIR / "token_usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return


import task_support as _task_support_module


def try_extract_skill(task_summary: str, msg_content: str) -> None:
    _record_skill_invocation("try_extract_skill")
    _record_external_action("skill_execution", "try_extract_skill")
    return _base_try_extract_skill(task_summary, msg_content)


def _execute_pending_publish(pending_pub_file: Path, workspace: Path, task_id: str, thread_id: str):
    _record_external_action("publish", str(pending_pub_file))
    return _base_execute_pending_publish(pending_pub_file, workspace, task_id, thread_id)


_task_support_module.try_extract_skill = try_extract_skill
_task_support_module._execute_pending_publish = _execute_pending_publish
_task_support_module._append_exec_log = _append_exec_log


_SUBAGENT_TRUST_FLAG_KEYS = ("verified", "confirmed", "ground_truth")
_SUBAGENT_TRUST_STATUS_VALUES = {"complete", "verified"}


def _warn_self_verify_attempt(task_id: str, labels: list[str]) -> None:
    if labels:
        log.warning("Agent attempted self-verify on task %s - discarding flag(s): %s", task_id, ", ".join(labels))


def _audit_content_guard(publish_result: dict) -> None:
    failures = []
    for key in ("content_guard_passed", "preflight_passed"):
        if key not in publish_result:
            failures.append(f"{key}=missing")
        elif publish_result.get(key) is not True:
            failures.append(f"{key}={publish_result.get(key)!r}")
    if failures:
        raise ValueError(
            "Publish safety checkpoint failed: "
            + ", ".join(failures)
            + ". Publish tasks cannot complete until content guard and preflight pass."
        )


def _is_publish_completion_result(result: dict) -> bool:
    status = normalize_task_status(str(result.get("status", "")))
    if status not in ("done", "verified", "completed", "completed_unverified"):
        return False
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    tags = {str(tag).strip().lower() for tag in result.get("tags", []) if tag}
    return (
        "publish" in tags
        or str(result.get("task_type") or "").strip().lower() == "publish"
        or str(result.get("agent") or "").strip().lower() == "socialmedia"
        or str(verification.get("artifact_type") or "").strip().lower() == "publish"
    )


def _apply_publish_content_guard_audit(payload: dict, task_id: str) -> dict:
    result = dict(payload or {})
    if not _is_publish_completion_result(result):
        return result
    try:
        _audit_content_guard(result)
    except ValueError as exc:
        log.warning("PUBLISH_CONTENT_GUARD_AUDIT_FAILED task_id=%s reason=%s", task_id, exc)
        result["status"] = "failed"
        result["summary"] = str(exc)
        result["failure_class"] = result.get("failure_class") or "content_guard_failed"
        result["next_action"] = result.get("next_action") or "inspect-publish-guard"
    return result


def _unpack_response(resp: dict, task_id: str = "") -> dict:
    payload = dict(resp or {})
    # Trust attack surface: sub-agents can self-claim confirmatory labels
    # that make downstream skepticism sleep. Treat every sub-agent response
    # as exploratory until the super agent's own verification path checks it.
    discarded: list[str] = []
    for key in _SUBAGENT_TRUST_FLAG_KEYS:
        if key in payload:
            payload.pop(key, None)
            discarded.append(key)
    if str(payload.get("status", "")).strip().lower() == "complete":
        payload.pop("status", None)
        discarded.append("status: complete")
    verification = payload.get("verification")
    if isinstance(verification, dict):
        cleaned_verification = dict(verification)
        for key in _SUBAGENT_TRUST_FLAG_KEYS:
            if key in cleaned_verification:
                cleaned_verification.pop(key, None)
                discarded.append(f"verification.{key}")
        if str(cleaned_verification.get("status", "")).strip().lower() == "complete":
            cleaned_verification.pop("status", None)
            discarded.append("verification.status: complete")
        payload["verification"] = cleaned_verification
    _warn_self_verify_attempt(task_id, discarded)
    return payload


_task_result_canonicalize_result_payload = _canonicalize_result_payload


def _canonicalize_result_payload(workspace: Path, payload: dict, **kwargs) -> dict:
    task_id = str(kwargs.get("task_id", ""))
    status = str(kwargs.get("status", ""))
    if status.strip().lower() in _SUBAGENT_TRUST_STATUS_VALUES:
        labels = (
            []
            if status.strip().lower() == str(payload.get("status", "")).strip().lower()
            else [f"status: {status.strip().lower()}"]
        )
        _warn_self_verify_attempt(task_id, labels)
        kwargs["status"] = "done"
    unpacked = _unpack_response(payload, task_id)
    candidate = dict(unpacked)
    candidate["status"] = str(kwargs.get("status") or candidate.get("status") or "")
    candidate["summary"] = str(kwargs.get("summary") or candidate.get("summary") or "")
    if kwargs.get("tags") is not None:
        candidate["tags"] = kwargs.get("tags")
    if kwargs.get("agent") is not None:
        candidate["agent"] = kwargs.get("agent")
    if kwargs.get("metadata") is not None and isinstance(kwargs.get("metadata"), dict):
        candidate.update({k: v for k, v in kwargs["metadata"].items() if k not in candidate})
    if kwargs.get("verification") is not None:
        candidate["verification"] = kwargs.get("verification")
    audited = _apply_publish_content_guard_audit(candidate, task_id)
    if audited.get("status") == "failed" and candidate.get("status") != "failed":
        kwargs["status"] = "failed"
        kwargs["summary"] = str(audited.get("summary") or "")
        kwargs["failure_class"] = str(audited.get("failure_class") or "content_guard_failed")
        kwargs["next_action"] = str(audited.get("next_action") or "inspect-publish-guard")
    return _task_result_canonicalize_result_payload(workspace, audited, **kwargs)


import task_result as _task_result_module

_task_result_module._canonicalize_result_payload = _canonicalize_result_payload


def _task_output_verification_type(
    task_type: str,
    tags: list[str] | None,
    agent: str | None,
    verification: dict | None,
) -> str:
    parts = [
        str(task_type or ""),
        str(agent or ""),
        " ".join(str(t) for t in (tags or []) if t),
    ]
    if isinstance(verification, dict):
        parts.append(str(verification.get("task_type") or ""))
        parts.append(str(verification.get("artifact_type") or ""))
    label = " ".join(p for p in parts if p).strip()
    return label or "general"


def _actual_artifacts_for_verification(workspace: Path, metadata: dict | None) -> list[dict]:
    artifacts = _collect_result_artifacts(workspace, metadata=metadata)
    seen = {str(a.get("path", "")) for a in artifacts}
    output_path = workspace / "output.md"
    if str(output_path) not in seen:
        artifacts.append(
            {
                "type": "file",
                "path": str(output_path),
                "size_bytes": output_path.stat().st_size if output_path.exists() else 0,
                "exists": output_path.exists() and output_path.is_file(),
            }
        )
    for expected in _workspace_relative_artifact_paths(metadata):
        path = expected if expected.is_absolute() else workspace / expected
        key = str(path)
        if key in seen:
            continue
        artifacts.append(
            {
                "type": "file",
                "path": key,
                "size_bytes": path.stat().st_size if path.exists() and path.is_file() else 0,
                "exists": path.exists() and path.is_file(),
            }
        )
        seen.add(key)
    return artifacts


def _merge_task_output_verification(
    verification: dict | None,
    task_type: str,
    verification_result: dict,
) -> dict:
    existing = _normalize_verification_payload(verification)
    depth = int(verification_result.get("depth", 0) or 0)
    passed = bool(verification_result.get("passed", False))
    details = str(verification_result.get("details", ""))[:500]
    checks = list(existing.get("checks", []))
    checks.append(
        {
            "name": f"task_output_depth_{depth}",
            "passed": passed,
            "message": details,
        }
    )
    existing_status = str(existing.get("status", "not-run"))
    existing_verified = bool(existing.get("verified", False))
    existing_hard_failed = existing_status == "failed"
    verified = passed and (existing_verified or existing_status in ("not-run", ""))
    if existing_verified and not existing_hard_failed:
        verified = passed
    proxy_checked = str(existing.get("proxy_checked") or "").strip()
    depth_proxy = f"depth-{depth}"
    if proxy_checked and depth_proxy not in proxy_checked:
        proxy_checked = f"{proxy_checked} + {depth_proxy}"
    else:
        proxy_checked = proxy_checked or depth_proxy
    existing.update(
        {
            "status": "verified" if verified else "failed",
            "verified": verified,
            "artifact_type": existing.get("artifact_type") or "file",
            "summary": details,
            "checks": checks,
            "proxy_checked": proxy_checked,
            "property_assumed": existing.get("property_assumed") or "task output matches claimed completion",
            "task_type": existing.get("task_type") or task_type,
            "unverified_assumptions": ["full user intent fulfilled"] if depth < 3 else [],
        }
    )
    return existing


def _record_task_output_verification(
    workspace: Path,
    task_id: str,
    task_type: str,
    default_depth: int,
    verification_result: dict,
    *,
    escalated: bool,
    human_review: bool,
) -> None:
    depth = int(verification_result.get("depth", 0) or 0)
    passed = bool(verification_result.get("passed", False))
    details = str(verification_result.get("details", ""))[:500]
    log.info(
        "TASK_OUTPUT_VERIFICATION task_id=%s task_type=%s depth=%d default_depth=%d passed=%s escalated=%s human_review=%s details=%s",
        task_id,
        task_type,
        depth,
        default_depth,
        passed,
        escalated,
        human_review,
        details,
    )
    try:
        result_path = workspace / "result.json"
        if not result_path.exists():
            return
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["verification_depth"] = depth
        result["verification_passed"] = passed
        result["verification_details"] = details
        result["verification_spot_checked"] = escalated
        result["unfaithfulness_metric"] = {
            "verification_depth": depth,
            "accepted_plausible_unverified": bool(passed and depth < 3),
            "human_review_required": human_review,
        }
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(result_path)
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("Task output verification result update skipped for %s: %s", task_id, exc)


_REASONING_REQUIRED_STATUSES = {"done", "verified", "completed", "completed_unverified"}


def _read_result_response_text(workspace: Path, summary: str) -> str:
    output_path = workspace / "output.md"
    parts = []
    if output_path.exists():
        try:
            parts.append(output_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    if summary:
        parts.append(str(summary))
    return "\n\n".join(part for part in parts if part).strip()


def _store_reasoning_result(workspace: Path, reasoning: str) -> None:
    result_path = workspace / "result.json"
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["reasoning"] = reasoning
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(result_path)
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("Reasoning result update skipped for %s: %s", result_path, exc)


def _ensure_result_reasoning(
    workspace: Path,
    task_id: str,
    status: str,
    summary: str,
    agent: str | None,
) -> tuple[str, str, str | None]:
    if normalize_task_status(status) not in _REASONING_REQUIRED_STATUSES:
        return status, summary, None

    response_text = _read_result_response_text(workspace, summary)
    raw_response_path = workspace / "raw_response.md"
    if response_text and not raw_response_path.exists():
        try:
            raw_response_path.write_text(response_text, encoding="utf-8")
        except OSError as exc:
            log.debug("Could not store raw_response.md for %s: %s", task_id, exc)
    payload = extract_reasoning_payload(response_text)
    if payload is None and response_text:
        try:
            rewritten = claude_think(
                REASONING_REWRITE_PROMPT.format(response=response_text[:6000]),
                timeout=90,
                tier="light",
            )
            payload = extract_reasoning_payload(rewritten or "")
        except Exception as exc:
            log.warning("Reasoning rewrite failed for %s: %s", task_id, exc)

    if payload is None:
        rejected = "Task result rejected: missing required reasoning field. Agent must rewrite response with reasoning."
        log.warning("TASK_REASONING_MISSING task_id=%s agent=%s status=%s", task_id, agent or "", status)
        return "needs-input", rejected, None

    reasoning, output_text = payload
    if output_text:
        output_path = workspace / "output.md"
        try:
            output_path.write_text(output_text, encoding="utf-8")
        except OSError as exc:
            log.debug("Could not normalize output.md after reasoning extraction for %s: %s", task_id, exc)
        if not summary:
            summary = output_text[:1000]
    log.info("TASK_REASONING task_id=%s agent=%s reasoning=%s", task_id, agent or "", reasoning[:500])
    return status, summary, reasoning


def check_silent_completion(prompt: str, output: str, metadata: dict) -> None:
    try:
        from config import SILENT_COMPLETION_MIN_RATIO, SILENT_COMPLETION_HEDGE_PHRASES

        min_ratio = float(SILENT_COMPLETION_MIN_RATIO)
        hedge_phrases = list(SILENT_COMPLETION_HEDGE_PHRASES)
    except (ImportError, AttributeError, TypeError, ValueError):
        min_ratio = 0.3
        hedge_phrases = ["unfortunately", "couldn't complete", "i don't know", "unable to", "failed to"]
    min_length = max(200, int(len(prompt) * min_ratio))
    output_text = output or ""
    if len(output_text) < min_length:
        metadata["silent_completion_suspected"] = True
        metadata["reason"] = f"output_too_short: {len(output_text)} < {min_length}"
        return
    output_lower = output_text.lower()
    for phrase in hedge_phrases:
        if phrase.lower() in output_lower:
            metadata["silent_completion_suspected"] = True
            metadata["reason"] = f"hedge_phrase: {phrase}"
            return


_ATTRIBUTION_DEPTH_LIMIT_SENTINEL = "attribution depth limit reached"


def _load_step_states_for_attribution(workspace: Path) -> list[dict]:
    state_path = workspace / "step_states.json"
    if not state_path.exists():
        return []
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def _trace_upstream_failure_attribution(steps: list[dict], step_index: int, depth: int = 0) -> dict:
    if depth >= MAX_ATTRIBUTION_DEPTH:
        return {
            "status": _ATTRIBUTION_DEPTH_LIMIT_SENTINEL,
            "depth": depth,
        }

    upstream_index = step_index - 1
    if upstream_index < 0 or upstream_index >= len(steps):
        return {
            "status": "no upstream output",
            "depth": depth,
        }

    upstream = steps[upstream_index]
    attribution = {
        "step_index": upstream_index,
        "step_id": str(upstream.get("step_id") or f"step-{upstream_index + 1:02d}"),
        "agent": str(upstream.get("execution_agent") or upstream.get("declared_agent") or ""),
        "status": str(upstream.get("status") or ""),
        "output_summary": str(upstream.get("output_summary") or "")[:500],
        "depth": depth,
    }
    attribution["upstream"] = _trace_upstream_failure_attribution(steps, upstream_index, depth + 1)
    return attribution


def _record_failure_attribution(workspace: Path, status: str) -> None:
    if normalize_task_status(status) not in ("failed", "blocked"):
        return
    result_path = workspace / "result.json"
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    step_index = result.get("step_index")
    if not isinstance(step_index, int):
        return
    steps = _load_step_states_for_attribution(workspace)
    if not steps:
        return

    result["failure_attribution"] = _trace_upstream_failure_attribution(steps, step_index, 0)
    tmp = result_path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(result_path)
    except OSError:
        return


def _write_result(
    workspace: Path,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None = None,
    agent: str | None = None,
    metadata: dict | None = None,
    verification: dict | None = None,
    failure_class: str | None = None,
    next_action: str | None = None,
):
    raw_status = str(status or "").strip().lower()
    if raw_status in _SUBAGENT_TRUST_STATUS_VALUES:
        _warn_self_verify_attempt(task_id, [f"status: {raw_status}"])
        status = "done"
    status, summary, reasoning = _ensure_result_reasoning(workspace, task_id, status, summary, agent)
    normalized_status = normalize_task_status(status)
    verification_result = None
    default_depth = 0
    escalated = False
    human_review = False
    should_run_output_verification = metadata is not None or verification is not None
    if should_run_output_verification and normalized_status in (
        "done",
        "verified",
        "completed",
        "completed_unverified",
    ):
        task_type = _task_output_verification_type(
            str((verification or {}).get("task_type") if isinstance(verification, dict) else ""),
            tags,
            agent,
            verification,
        )
        actual_artifacts = _actual_artifacts_for_verification(workspace, metadata)
        default_depth = _default_verification_depth(task_type)
        verification_result = verify_task_output(task_type, summary, actual_artifacts)
        escalated = default_depth < 3 and int(verification_result.get("depth", 0) or 0) == 3
        human_review = escalated and not bool(verification_result.get("passed", False))
        verification = _merge_task_output_verification(verification, task_type, verification_result)
        if human_review:
            status = "needs-input"
            tags = sorted({*(tags or []), "needs-human", "verification-failed"})
            failure_class = failure_class or "verification_failed"
            next_action = next_action or "human-review"
            summary = (
                f"{summary}\n\nVerification failed at escalated depth "
                f"{verification_result['depth']}: {verification_result['details']}"
            ).strip()

    if normalize_task_status(status) in ("done", "verified", "completed", "completed_unverified"):
        if metadata is None:
            metadata = {}
        _sc_prompt = str(metadata.get("prompt", "") or metadata.get("instruction", "") or "")
        check_silent_completion(_sc_prompt, summary, metadata)

    _base_write_result(
        workspace,
        task_id,
        status,
        summary,
        tags=tags,
        agent=agent,
        metadata=metadata,
        verification=verification,
        failure_class=failure_class,
        next_action=next_action,
    )
    _record_failure_attribution(workspace, status)
    raw_response_path = workspace / "raw_response.md"
    if raw_response_path.exists():
        result_path = workspace / "result.json"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["raw_response_path"] = "raw_response.md"
            tmp = result_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.rename(result_path)
        except (json.JSONDecodeError, OSError) as exc:
            log.debug("Raw response result update skipped for %s: %s", task_id, exc)
    if reasoning:
        _store_reasoning_result(workspace, reasoning)
    if verification_result is not None:
        _record_task_output_verification(
            workspace,
            task_id,
            task_type,
            default_depth,
            verification_result,
            escalated=escalated,
            human_review=human_review,
        )
    try:
        _maybe_log_proxy_audit(workspace, task_id, summary)
    except Exception as exc:
        log.debug("Proxy audit skipped for %s: %s", task_id, exc)


_task_result_module._write_result = _write_result


def _log_sub_agent_format_error(exc: SubAgentFormatError) -> None:
    entry = {
        "timestamp": _utc_iso(),
        "type": "SubAgentFormatError",
        "agent": exc.agent_name,
        "missing_fields": exc.missing_keys,
        "message": str(exc),
    }
    try:
        with Path("/tmp/mira-crash.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as log_exc:
        log.warning("Failed to write sub-agent format error to crash log: %s", log_exc)


def _invoke_registry_handler(
    handler_fn,
    workspace: Path,
    task_id: str,
    instruction: str,
    sender: str,
    thread_id: str,
    tier: str,
    user_id: str = "ang",
    agent_id: str = None,
):
    _set_receipt_agent(agent_id)
    _record_external_action("skill_execution", agent_id or getattr(handler_fn, "__name__", "handler"))
    try:
        result = _base_invoke_registry_handler(
            handler_fn,
            workspace,
            task_id,
            instruction,
            sender,
            thread_id,
            tier,
            user_id=user_id,
            agent_id=agent_id,
        )
        if isinstance(result, dict):
            _validate_result(result, agent_id or result.get("agent") or "unknown")
        return result
    except SubAgentFormatError as exc:
        _log_sub_agent_format_error(exc)
        fields = ", ".join(exc.missing_keys) or "unknown"
        message = f"{exc.agent_name} returned malformed result: missing or invalid required field(s): {fields}"
        log.error("SUB_AGENT_FORMAT_ERROR task_id=%s agent=%s missing=%s", task_id, exc.agent_name, fields)
        _write_result(
            workspace,
            task_id,
            "error",
            message,
            agent=exc.agent_name,
            failure_class="sub_agent_format_error",
        )
        return ""


_task_support_module._invoke_registry_handler = _invoke_registry_handler

from plan_executor import (
    _execute_plan as _base_execute_plan,
    _execute_plan_steps as _base_execute_plan_steps,
)

import plan_executor as _plan_executor_module

_plan_executor_module._append_exec_log = _append_exec_log


def _require_reasoning_in_plan(plan: list[dict]) -> list[dict]:
    updated = []
    for step in plan:
        if not isinstance(step, dict):
            updated.append(step)
            continue
        next_step = dict(step)
        if "instruction" in next_step:
            next_step["instruction"] = require_reasoning_in_instruction(str(next_step.get("instruction", "")))
        updated.append(next_step)
    return updated


def _execute_plan(plan: list[dict], *args, **kwargs):
    return _base_execute_plan(_require_reasoning_in_plan(plan), *args, **kwargs)


def _execute_plan_steps(plan, *args, **kwargs):
    return _base_execute_plan_steps(_require_reasoning_in_plan(plan), *args, **kwargs)


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

_USER_SENDERS = {"user", "ang", "wa", "iphone", "ios"}
_CONVERSATIONAL_FEED_MARKERS = (
    "zhesi",
    "每日哲思",
    "mira thoughts",
    "daily-topic",
    "daily_topic",
    "thoughts",
    "self-assessment",
    "self assessment",
    "自检",
)
_MARKET_FEED_MARKERS = (
    "market",
    "analyst",
    "开市",
    "收市",
    "市场分析",
    "premarket",
    "postmarket",
)
_PROTECTED_APPROVAL_MARKER = "[protected-path-confirmation-approved]"
_PROTECTED_PATH_RE = re.compile(r"(?:Mira/)?agents/(?:super|coder|shared/soul)(?:/[^\s\"'`),\]]+)?")
_PROTECTED_PATH_PREFIXES = ("agents/super/", "agents/coder/", "agents/shared/soul/")
_MODIFY_ACTIONS = {"modify", "edit", "update", "change", "fix", "write", "patch", "apply"}


def _normalize_mira_path(path: str) -> str:
    raw = str(path or "").strip().strip("\"'")
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    raw = raw.replace("\\", "/").lstrip("./")
    if raw.startswith("Mira/"):
        raw = raw[len("Mira/") :]
    return raw


def _is_protected_path(path: str) -> bool:
    normalized = _normalize_mira_path(path)
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in _PROTECTED_PATH_PREFIXES)


def _strings_from_step(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings = []
        for child in value.values():
            strings.extend(_strings_from_step(child))
        return strings
    if isinstance(value, list):
        strings = []
        for child in value:
            strings.extend(_strings_from_step(child))
        return strings
    return []


def _paths_from_step(step: dict) -> list[str]:
    paths: set[str] = set()
    for text in _strings_from_step(step):
        for match in _PROTECTED_PATH_RE.findall(text):
            normalized = _normalize_mira_path(match)
            if normalized:
                paths.add(normalized)
    return sorted(paths)


def _is_coder_modify_step(step: dict) -> bool:
    if str(step.get("agent", "")).strip().lower() != "coder":
        return False
    action = str(step.get("action", "")).strip().lower()
    if action:
        return action in _MODIFY_ACTIONS
    instruction = str(step.get("instruction", ""))
    return bool(re.search(r"\b(modify|edit|update|change|fix|write|patch|apply)\b", instruction, re.IGNORECASE))


def _protected_coder_modify_paths(plan: list[dict]) -> list[str]:
    paths: set[str] = set()
    for step in plan:
        if not isinstance(step, dict) or not _is_coder_modify_step(step):
            continue
        paths.update(path for path in _paths_from_step(step) if _is_protected_path(path))
    return sorted(paths)


def _mark_protected_plan_approved(plan: list[dict]) -> list[dict]:
    for step in plan:
        if not isinstance(step, dict) or not _is_coder_modify_step(step):
            continue
        if not any(_is_protected_path(path) for path in _paths_from_step(step)):
            continue
        instruction = str(step.get("instruction", ""))
        if _PROTECTED_APPROVAL_MARKER not in instruction:
            step["instruction"] = f"{instruction}\n\n{_PROTECTED_APPROVAL_MARKER}"
    return plan


def send_confirmation(diff_summary: str, affected_paths: list[str]) -> str:
    paths = "\n".join(f"- {path}" for path in affected_paths)
    return (
        "NEEDS_APPROVAL: This coder task would modify protected Mira system files. "
        "Reply approve to continue.\n\n"
        f"{diff_summary.strip()}\n\n"
        f"Affected paths:\n{paths}"
    )


def _is_user_sender(sender: str) -> bool:
    return str(sender or "").strip().lower() in _USER_SENDERS


def _metadata_text(task_id: str, task_data: dict) -> str:
    parts = [
        task_id,
        str(task_data.get("id") or ""),
        str(task_data.get("title") or ""),
        " ".join(str(t) for t in task_data.get("tags", []) if t),
    ]
    metadata = task_data.get("metadata") or {}
    if isinstance(metadata, dict):
        parts.extend(str(v) for v in metadata.values() if isinstance(v, (str, int, float)))
    return " ".join(parts).lower()


def _looks_like_market_thread(task_id: str, task_data: dict) -> bool:
    text = _metadata_text(task_id, task_data)
    return any(marker.lower() in text for marker in _MARKET_FEED_MARKERS)


def _looks_like_conversation_feed(task_id: str, task_data: dict) -> bool:
    if task_data.get("type") == "discussion":
        return True
    if task_data.get("type") != "feed":
        return False
    text = _metadata_text(task_id, task_data)
    return any(marker.lower() in text for marker in _CONVERSATIONAL_FEED_MARKERS)


def _task_with_current_message(task_data: dict, content: str, sender: str) -> dict:
    task = dict(task_data)
    task["current_message"] = {"sender": sender or "user", "content": content or ""}
    return task


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
    receipt_started_at = _utc_iso()
    _reset_task_receipt(receipt_started_at)

    def _finish_task(exit_status: str | None = None, agent_id: str | None = None) -> None:
        _write_task_receipt(
            workspace,
            args.task_id,
            agent_id=agent_id,
            started_at=receipt_started_at,
            exit_status=exit_status,
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(workspace / "worker.log", encoding="utf-8"),
        ],
    )

    log.info("Worker started: task=%s thread=%s", args.task_id, args.thread_id)
    heartbeat = _Heartbeat(args.task_id, workspace=workspace)
    heartbeat.start()
    atexit.register(heartbeat.stop)
    task_start = time.time()
    _perf_start = time.perf_counter()
    _reset_llm_timing()

    # Read message
    try:
        msg_data = json.loads(Path(args.msg_file).read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read message: %s", e)
        _write_result(workspace, args.task_id, "error", f"Failed to read message: {e}")
        _finish_task("error")
        sys.exit(1)

    task_name = str(msg_data.get("title") or msg_data.get("name") or msg_data.get("id") or args.task_id)
    msg_content = msg_data.get("content", "")
    _set_active_workflow_intent(msg_content)
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
    set_model_policy(_model_restriction)
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
            _finish_task()
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load pending plan, re-planning: %s", e)

    # --- Check for article comment (comment_YYYY-MM-DD_suffix thread ID) ---
    if thread_id.startswith("comment_"):
        _handle_article_comment(workspace, args.task_id, thread_id, msg_content, msg_sender)
        log.info("Worker exiting (comment)")
        _finish_task()
        return

    # --- Check for in-progress video session (stateful multi-round) ---
    video_state_file = workspace / "video_state.json"
    if video_state_file.exists():
        log.info("Resuming video session (video_state.json found)")
        _handle_video(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (video)")
        _finish_task(agent_id="video")
        return

    # --- Check for in-progress photo session (stateful multi-round) ---
    photo_state_file = workspace / "photo_state.json"
    if photo_state_file.exists():
        log.info("Resuming photo session (photo_state.json found)")
        _handle_photo(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (photo)")
        _finish_task(agent_id="photo")
        return

    # --- Check for approval (user confirms a pending action) ---
    if _is_approval(msg_content):
        # Check for autowrite approval — schedule publish, don't re-preview
        if args.task_id.startswith("autowrite_"):
            _handle_autowrite_approval(workspace, args.task_id)
            log.info("Worker exiting (autowrite approval → pending publish)")
            _finish_task(agent_id="writer")
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
            _finish_task()
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
            _finish_task()
            return
        log.warning("Edit handler returned empty, falling through to task planning")

    # --- Fast-path for market feed follow-ups. These are user questions in an
    # existing market thread, so planner keyword routing adds latency and often
    # loses the thread context.
    if _is_user_sender(msg_sender) and _looks_like_market_thread(args.task_id, task_data):
        log.info("Market feed reply fast-path for task %s", args.task_id)
        _emit_status(args.task_id, "Reading market context...", "chart.line.uptrend.xyaxis")
        _handle_analyst(workspace, args.task_id, msg_content, msg_sender, thread_id, tier="heavy")
        log.info("Worker exiting (market feed reply fast-path)")
        _finish_task(agent_id="analyst")
        return

    # --- Fast-path for conversational discussions and conversational feeds
    # (no planner, no agent routing). The current message is injected
    # explicitly because daily topic threads may have autonomous agent messages
    # after the user's reply by the time the worker reads the item file.
    if _is_user_sender(msg_sender) and _looks_like_conversation_feed(args.task_id, task_data):
        log.info("Discussion fast-path for task %s", args.task_id)
        _emit_status(args.task_id, "Thinking...", "bubble.left.and.text.bubble.right")
        handle_discussion(
            _task_with_current_message(task_data, msg_content, msg_sender),
            workspace,
            args.task_id,
            thread_id,
            tier="light",
        )
        log.info("Worker exiting (discussion fast-path)")
        _finish_task(agent_id="discussion")
        return

    # --- Fixed startup: read progress from prior runs for this exact task ---
    progress = _load_matching_progress(workspace, args.task_id)

    # --- Privacy pre-routing: detect secret tasks LOCALLY before any cloud call ---
    task_tags = msg_data.get("tags", [])
    if _is_private_task(msg_content, task_id=args.task_id, tags=task_tags):
        log.info("Privacy keywords detected — routing to secret agent (local only)")
        _emit_status(args.task_id, "Private mode...", "lock.shield")
        _handle_secret(workspace, args.task_id, msg_content, msg_sender, thread_id)
        log.info("Worker exiting (secret — no cloud, no persist)")
        _finish_task(agent_id="secret")
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
    _set_receipt_agent(task_agent)
    task_tier = str(plan[0].get("tier") or "") if plan else ""
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
    _reset_exec_log_token_state()
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
        _finish_task("paused_horizon_limit", agent_id=_horizon_agent)
        return
    _act_duration = time.time() - _act_start
    log.info(
        "PHASE_TIMING task_id=%s agent=%s phase=act configured_timeout_s=%d actual_duration_s=%.2f",
        args.task_id,
        task_agent,
        CLAUDE_TIMEOUT_ACT,
        _act_duration,
    )
    record_phase_duration(task_agent, "act", CLAUDE_TIMEOUT_ACT, _act_duration)

    _in_tok, _out_tok, _model_id = get_session_tokens()
    _write_token_usage_record(task_agent, _model_id, _in_tok, _out_tok)
    _token_usage = {"input": _in_tok, "output": _out_tok}
    _result_words = 0
    _out_md = workspace / "output.md"
    if _out_md.exists():
        try:
            _result_words = len(_out_md.read_text(encoding="utf-8", errors="ignore").split())
        except OSError:
            pass
    _efficiency = _out_tok / max(1, _result_words)
    _heavy_agents = {"writer", "researcher", "podcast", "socialmedia"}
    _tier = task_tier or ("heavy" if task_agent in _heavy_agents else "light")
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
                "token_usage": _token_usage,
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
            _result["token_usage"] = _token_usage
            _tmp = _result_file.with_suffix(".tmp")
            _tmp.write_text(json.dumps(_result, ensure_ascii=False, indent=2), encoding="utf-8")
            _tmp.rename(_result_file)
    except Exception as _e:
        log.debug("Failed to write token_usage to result.json: %s", _e)

    _perf_tools_ms = round((time.perf_counter() - _perf_tools_t0) * 1000)
    _total_time = time.perf_counter() - _perf_start
    _llm_time = _get_llm_time()
    _orchestration_fraction = 1 - (_llm_time / _total_time) if _total_time > 0 else 0.0
    _perf_total_ms = round(_total_time * 1000)
    _phase_record = {
        "task_id": args.task_id,
        "task_name": task_name,
        "task_type": task_type,
        "agent": task_agent,
        "agent_tier": _tier,
        "token_count": _total_tok,
        "llm_time_s": round(_llm_time, 3),
        "total_time_s": round(_total_time, 3),
        "orchestration_fraction": round(_orchestration_fraction, 4),
        "phase_dispatch_ms": _perf_dispatch_ms,
        "phase_inference_ms": _perf_inference_ms,
        "phase_tools_ms": _perf_tools_ms,
        "total_ms": _perf_total_ms,
        "token_usage": _token_usage,
    }
    log.info("PHASE_BREAKDOWN %s", json.dumps(_phase_record, ensure_ascii=False))
    try:
        _phase_log = LOGS_DIR / "task_phase_timing.jsonl"
        with open(_phase_log, "a", encoding="utf-8") as _pf:
            _pf.write(json.dumps({**_phase_record, "ts": _utc_iso()}, ensure_ascii=False) + "\n")
    except Exception as _pe:
        log.debug("Phase timing log write failed: %s", _pe)
    _finish_task(agent_id=task_agent)
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
