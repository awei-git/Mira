#!/usr/bin/env python3
"""Task worker — standalone sub-agent process for Mira.

Spawned by TaskManager.dispatch(). Reads a message, loads context,
calls claude_act(), writes output + result JSON.

Usage:
    python task_worker.py --msg-file <path> --workspace <path> --task-id <id> [--thread-id <id>]
"""
import argparse
import inspect
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
sys.path.insert(0, str(_AGENTS_DIR / "shared"))
sys.path.insert(0, str(_AGENTS_DIR / "writer"))
sys.path.insert(0, str(_AGENTS_DIR / "general"))

import shutil

from config import MIRA_DIR, MIRA_ROOT, ARTIFACTS_DIR, JOURNAL_DIR, BRIEFINGS_DIR, MEMORY_FILE, WORLDVIEW_FILE
from preflight import verify_artifact
from soul_manager import (load_soul, format_soul, append_memory, save_skill,
                         save_episode, recall_context, save_knowledge_note)
from sub_agent import claude_act, claude_think, ClaudeTimeoutError
from prompts import respond_prompt
from writing_workflow import run_full_pipeline

# Handler functions extracted to handlers_legacy.py (imported after all helpers
# are defined to avoid circular import — see bottom of file)


log = logging.getLogger("task_worker")

_ACTIVE_USER_ID = "ang"


def _set_active_user(user_id: str):
    global _ACTIVE_USER_ID
    _ACTIVE_USER_ID = user_id or "ang"


def _items_dir(user_id: str | None = None) -> Path:
    return MIRA_DIR / "users" / (user_id or _ACTIVE_USER_ID) / "items"


def _item_file(task_id: str, user_id: str | None = None) -> Path:
    return _items_dir(user_id) / f"{task_id}.json"


# Legacy compatibility for modules that still import ITEMS_DIR directly.
ITEMS_DIR = _items_dir()


# Task workspaces stored locally
TASKS_DIR = MIRA_ROOT / "tasks"

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
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=2),
                           encoding="utf-8")
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


def _is_private_task(content: str, task_id: str = "",
                     tags: list[str] | None = None) -> bool:
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
    _CALIBRATION_FILE, _QUALITY_LOG,
    _record_premortem, _record_postmortem,
    _track_output_quality, detect_quality_regression,
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


def _load_exec_history(workspace: Path) -> str:
    """Load execution history from previous dispatch rounds."""
    log_file = workspace / "exec_log.jsonl"
    if not log_file.exists():
        return ""
    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return ""
        entries = []
        for line in lines[-10:]:  # last 10 entries
            entry = json.loads(line)
            entries.append(
                f"- Round {entry.get('round', '?')}: agent={entry.get('agent', '?')}, "
                f"status={entry.get('status', '?')}, "
                f"output_preview={entry.get('output_preview', '')[:200]}"
            )
        return "## Previous execution rounds in this task\n" + "\n".join(entries)
    except (json.JSONDecodeError, OSError):
        return ""


def _append_exec_log(workspace: Path, round_num: int, agent: str,
                     status: str, output_preview: str):
    """Append an entry to the execution log with output health metrics."""
    # Compute lightweight output health (no LLM calls)
    health = {}
    if output_preview:
        health["length"] = len(output_preview)
        health["has_content"] = len(output_preview.strip()) > 50

    log_file = workspace / "exec_log.jsonl"
    entry = {
        "round": round_num,
        "agent": agent,
        "status": status,
        "output_preview": output_preview[:300],
        "health": health,
        "timestamp": _utc_iso(),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Track output quality trends in a global file
    _track_output_quality(agent, status, health)


def _verify_output(output: str, workspace: Path) -> str:
    """Verify agent output claims. Returns error string if hallucination detected, empty if OK."""
    import re
    issues = []

    # Check for claimed file paths that don't exist
    # Match patterns like: wrote to /path/to/file, saved to /path, created /path
    file_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存|生成|写了)\s+(?:to\s+)?[`"\']*(/[^\s`"\',:]+(?:\.\w+))',
        output, re.IGNORECASE
    )
    for path in file_claims:
        if not Path(path).exists():
            issues.append(f"Claimed file does not exist: {path}")

    # Check for workspace-relative file claims
    rel_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存)\s+(?:to\s+)?[`"\']*(?:output|result|summary|article)[\w.]*\.\w+',
        output, re.IGNORECASE
    )
    for claim in rel_claims:
        # Extract filename
        fname_match = re.search(r'([\w.-]+\.\w+)', claim)
        if fname_match:
            fname = fname_match.group(1)
            full_path = workspace / fname
            if not full_path.exists() and fname != "output.md":  # output.md is the output itself
                issues.append(f"Claimed workspace file does not exist: {fname}")

    # Check for "写了一篇" / "wrote an article" claims without actual content
    wrote_article = bool(re.search(
        r'写了[一篇个]|wrote\s+(?:a|an|the)\s+(?:article|post|essay|piece)',
        output, re.IGNORECASE
    ))
    if wrote_article:
        # If claiming to have written an article, output should be substantial
        # (not just a summary saying "I wrote X")
        content_lines = [l for l in output.split('\n')
                        if l.strip() and not l.startswith('#') and not l.startswith('---')]
        if len(content_lines) < 5 and len(output) < 500:
            issues.append("Claims to have written an article but output is too short to contain it")

    return "; ".join(issues) if issues else ""


def _get_round_num(workspace: Path) -> int:
    """Get the next round number for this workspace."""
    log_file = workspace / "exec_log.jsonl"
    if not log_file.exists():
        return 1
    try:
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return 1
        last = json.loads(lines[-1])
        return last.get("round", 0) + 1
    except (json.JSONDecodeError, OSError):
        return 1


# ---------------------------------------------------------------------------
# Context helpers extracted to execution/context.py
# ---------------------------------------------------------------------------
from execution.context import (
    load_task_conversation, load_thread_history, load_thread_memory,
    compress_conversation, _truncate_messages,
    _load_recent_journals, _load_recent_briefings,
)
from execution.plan_state import (
    initialize_plan_artifacts,
    mark_step_finished,
    mark_step_running,
)


def smart_classify(content: str, summary: str = "") -> list[str]:
    """Use LLM to intelligently tag a task. Returns 1-5 short tags."""
    prompt = f"""Given this task request and result, generate 1-5 short tags (each 1-3 words) that classify the task. Tags should be specific and useful for search/filtering. Mix Chinese and English as appropriate. Output ONLY a JSON array of strings, nothing else.

Request: {content[:300]}
Result: {summary[:300] if summary else '(pending)'}

Example output: ["写作", "science-fiction", "自由意志"]"""

    try:
        result = claude_think(prompt, timeout=90)
        if result:
            # Extract JSON array from response
            import re
            match = re.search(r'\[.*?\]', result, re.DOTALL)
            if match:
                tags = json.loads(match.group())
                # Ensure all tags are strings and reasonable length
                return [str(t).strip()[:20] for t in tags if t and str(t).strip()][:5]
    except Exception as e:
        log.warning("Smart classification failed: %s", e)
    return []


def _enrich_plan_with_runtime_policy(plan: list[dict]) -> list[dict]:
    """Attach capability class + runtime policy to each validated plan step."""
    from agent_registry import get_registry

    registry = get_registry()
    enriched = []
    for step in plan:
        normalized = dict(step)
        normalized["capability_class"] = registry.get_capability_class(step["agent"])
        normalized["policy"] = registry.get_capability_policy(step["agent"])
        enriched.append(normalized)
    return enriched


def _result_metadata(step: dict, *, step_index: int, step_count: int,
                     declared_agent: str, execution_agent: str) -> dict:
    return {
        "step_index": step_index,
        "step_count": step_count,
        "declared_agent": declared_agent,
        "execution_agent": execution_agent,
        "capability_class": step.get("capability_class", "read-only"),
        "policy": step.get("policy", {}),
    }


def try_extract_skill(task_summary: str, msg_content: str) -> None:
    """Ask Claude to consider extracting a skill from the completed task."""
    if not task_summary or len(task_summary) < 100:
        return

    prompt = f"""Based on this task and its result, is there a reusable skill to extract?

Task request: {msg_content[:500]}

Task result summary: {task_summary[:1000]}

If yes, output EXACTLY in this format:
```
Name: [short skill name]
Description: [one-liner]
Content:
[The full skill — technique, pattern, or method — written in your own words, ready to reuse]
```

If no reusable skill can be extracted, just say "No new skill from this task."
"""
    import re
    result = claude_think(prompt, timeout=120)
    if not result or "no new skill" in result.lower():
        return

    match = re.search(
        r"Name:\s*(.+)\nDescription:\s*(.+)\nContent:\n(.+?)(?:\n```|$)",
        result, re.DOTALL,
    )
    if match:
        name = match.group(1).strip()
        desc = match.group(2).strip()
        content = match.group(3).strip()
        save_skill(name, desc, content)
        append_memory(f"Learned skill from TalkBridge task: {name}", user_id=_ACTIVE_USER_ID)
        log.info("Extracted skill: %s", name)


def _register_runtime_tools_created(workspace: Path) -> None:
    """Scan workspace for Python tools the agent wrote to runtime_tools/ and register them.

    Called after task completion to auto-index any tools the agent created
    during execution but didn't formally register via tool_forge.
    """
    try:
        from tool_forge import RUNTIME_TOOLS_DIR, list_tools, forge_tool
    except ImportError:
        return

    if not RUNTIME_TOOLS_DIR.exists():
        return

    indexed = {t["file"] for t in list_tools()}
    for py_file in RUNTIME_TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py" or py_file.name in indexed:
            continue
        # New unindexed tool — try to extract metadata from docstring
        code = py_file.read_text(encoding="utf-8")
        name = py_file.stem.replace("_", " ")
        desc = ""
        # Extract first docstring
        import re
        doc_match = re.search(r'"""(.+?)"""', code, re.DOTALL)
        if doc_match:
            first_line = doc_match.group(1).strip().split("\n")[0]
            desc = first_line[:200]
        if not desc:
            desc = f"Auto-discovered tool: {name}"
        # Register it (forge_tool handles audit)
        ok, msg = forge_tool(name, desc, code)
        if ok:
            log.info("Auto-registered runtime tool: %s", name)
            append_memory(f"Created runtime tool: {name}", user_id=_ACTIVE_USER_ID)
        else:
            log.warning("Failed to register tool %s: %s", name, msg)


# ---------------------------------------------------------------------------
# Discussion mode — conversational exchange, not task execution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Approval detection — user confirms a pending action
# ---------------------------------------------------------------------------

_APPROVAL_PHRASES = [
    "可以", "好的", "发吧", "发", "同意", "ok", "yes", "确认", "approve",
    "go ahead", "continue", "继续", "行", "没问题", "可以发了", "lgtm",
    "approved", "ship it", "好", "嗯", "对",
]


def _is_approval(content: str) -> bool:
    """Detect if a message is approving/confirming a pending action."""
    stripped = content.strip().lower()
    # Short affirmative → approval
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _APPROVAL_PHRASES):
        return True
    return False


_REJECTION_PHRASES = [
    "reject", "cancel", "取消", "不发", "不要发", "别发", "停",
    "no", "nope", "算了", "不了",
]


def _is_rejection(content: str) -> bool:
    """Detect if a message is rejecting/cancelling a pending action."""
    stripped = content.strip().lower()
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _REJECTION_PHRASES):
        return True
    return False


def _execute_pending_publish(pending_pub_file: Path, workspace: Path,
                              task_id: str, thread_id: str):
    """Execute a pending Substack publish after user approval.

    Reads the pending_publish.json, calls publish_to_substack(), then clears
    the pending file so the same article can't be published twice.
    """
    import re as _re
    try:
        pending = json.loads(pending_pub_file.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to read pending publish file: %s", e)
        _write_result(workspace, task_id, "error", f"无法读取待发布记录: {e}")
        return

    pub_title = pending.get("pub_title", "")
    subtitle = pending.get("subtitle", "")
    source = pending.get("source", "auto")
    article_path = pending.get("article_path", "")
    project_dir = pending.get("project_dir", str(workspace))

    # Get article text: from file path (auto) or inline (manual)
    article_text = ""
    if article_path:
        try:
            article_text = Path(article_path).read_text(encoding="utf-8")
            # Strip revision tables
            article_text = _re.sub(
                r'\n---\s*\n+## 修改记录.*', '', article_text, flags=_re.DOTALL
            )
        except Exception as e:
            log.error("Failed to read article file %s: %s", article_path, e)

    if not article_text:
        article_text = pending.get("article_text", "")

    if not article_text:
        _write_result(workspace, task_id, "error", "待发布文章内容为空，无法发布。")
        return

    # Delete pending file BEFORE publishing to prevent double-publish on retry
    try:
        pending_pub_file.unlink()
        log.info("Pending publish file cleared before publishing")
    except Exception as e:
        log.warning("Could not clear pending publish file: %s", e)

    # Publish to Substack
    try:
        sm_dir = str(_AGENTS_DIR / "socialmedia")
        if sm_dir not in sys.path:
            sys.path.insert(0, sm_dir)
        from substack import publish_to_substack

        proj_path = Path(project_dir)
        log.info("Executing approved publish: '%s' (source=%s)", pub_title, source)
        pub_result = publish_to_substack(
            title=pub_title,
            subtitle=subtitle,
            article_text=article_text,
            workspace=proj_path,
        )
        log.info("Publish complete: %s", pub_result[:120])

        (workspace / "output.md").write_text(pub_result, encoding="utf-8")
        _write_result(workspace, task_id, "done", pub_result, tags=["publish"])
        if thread_id:
            _update_thread_memory(thread_id, "approve publish", pub_result)

        # Queue 5 Notes for the new article (posted gradually over next cycles)
        try:
            notes_dir = str(_AGENTS_DIR / "socialmedia")
            if notes_dir not in sys.path:
                sys.path.insert(0, notes_dir)
            from notes import queue_notes_for_article
            pub_json = proj_path / "published.json"
            pub_post_id = None
            if pub_json.exists():
                pub_info = json.loads(pub_json.read_text(encoding="utf-8"))
                pub_post_id = pub_info.get("draft_id")
            queue_notes_for_article(
                title=pub_title,
                article_text=article_text[:3000],
                post_url=pub_info.get("url", "") if pub_json.exists() else "",
                post_id=pub_post_id,
            )
        except Exception as e:
            log.error("Notes queueing failed for '%s': %s", pub_title, e)

    except Exception as e:
        log.error("Publish on approval failed for '%s': %s", pub_title, e)
        _write_result(workspace, task_id, "error", f"发布失败: {e}")



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

    # --- User access control context ---
    _user_id = msg_data.get("user_id", "ang")
    _set_active_user(_user_id)
    _user_role = msg_data.get("user_role", "admin")
    _model_restriction = msg_data.get("model_restriction")
    _content_filter = msg_data.get("content_filter", False)
    _allowed_agents = msg_data.get("allowed_agents", [])
    log.info("User context: user=%s role=%s model_restriction=%s content_filter=%s allowed_agents=%s",
             _user_id, _user_role, _model_restriction, _content_filter,
             ",".join(_allowed_agents) if _allowed_agents else "all")

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
            initialize_plan_artifacts(
                workspace,
                task_id=args.task_id,
                user_id=_user_id,
                request=msg_content,
                plan=plan,
            )
            log.info("Resuming pending plan (%d steps): %s", len(plan), plan)
            _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id,
                         user_id=_user_id, allowed_agents=_allowed_agents,
                         content_filter=_content_filter, model_restriction=_model_restriction)
            log.info("Worker exiting")
            return
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load pending plan, re-planning: %s", e)

    # --- Check for article comment (comment_YYYY-MM-DD_suffix thread ID) ---
    if thread_id.startswith("comment_"):
        _handle_article_comment(workspace, args.task_id, thread_id,
                                msg_content, msg_sender)
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
                _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id,
                         user_id=_user_id, allowed_agents=_allowed_agents,
                         content_filter=_content_filter, model_restriction=_model_restriction)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load pending plan on approval: %s", e)
                _write_result(workspace, args.task_id, "error",
                              f"Could not resume: {e}")
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
        response = _handle_edit_artifact(task_data, workspace, args.task_id,
                                          msg_content, msg_sender, thread_id)
        if response:
            log.info("Worker exiting (edit)")
            return
        log.warning("Edit handler returned empty, falling through to task planning")

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

    plan = _plan_task(msg_content, conversation=conversation, exec_history=exec_history,
                      prior_context=planning_context,
                      allowed_agents=_allowed_agents,
                      content_filter=_content_filter)
    plan = _enrich_plan_with_runtime_policy(plan)
    initialize_plan_artifacts(
        workspace,
        task_id=args.task_id,
        user_id=_user_id,
        request=msg_content,
        plan=plan,
    )
    log.info("Plan: %s", plan)

    _execute_plan(plan, workspace, args.task_id, msg_content, msg_sender, thread_id,
                 user_id=_user_id, allowed_agents=_allowed_agents,
                 content_filter=_content_filter, model_restriction=_model_restriction)

    # --- Write progress.md for next session ---
    _write_progress(workspace, args.task_id, msg_content)

    log.info("Worker exiting")


# _plan_task → imported from planning/planner.py above


def _execute_plan(plan: list[dict], workspace: Path, task_id: str,
                  content: str, sender: str, thread_id: str,
                  user_id: str = "ang", allowed_agents: list | None = None,
                  content_filter: bool = False, model_restriction: str | None = None):
    """Execute a multi-step plan. Each step's output feeds into the next."""
    initialize_plan_artifacts(
        workspace,
        task_id=task_id,
        user_id=user_id,
        request=content,
        plan=plan,
    )
    prev_output = None
    is_multi = len(plan) > 1
    round_num = _get_round_num(workspace)

    # Start heartbeat for long tasks (emits status every 60s)
    heartbeat = _Heartbeat(task_id)
    heartbeat.start()
    try:
        _execute_plan_steps(plan, workspace, task_id, content, sender, thread_id,
                           prev_output, is_multi, round_num,
                           user_id=user_id, allowed_agents=allowed_agents or [],
                           content_filter=content_filter,
                           model_restriction=model_restriction)
    finally:
        heartbeat.stop()


def _execute_plan_steps(plan, workspace, task_id, content, sender, thread_id,
                        prev_output, is_multi, round_num, *,
                        user_id: str = "ang", allowed_agents: list | None = None,
                        content_filter: bool = False, model_restriction: str | None = None):
    """Inner loop extracted so heartbeat can be stopped in finally block."""
    # Set thread-local task_id so agents can emit progress via emit_progress()
    _set_streaming_task_id(task_id)
    if not (workspace / "step_states.json").exists():
        initialize_plan_artifacts(
            workspace,
            task_id=task_id,
            user_id=user_id,
            request=content,
            plan=plan,
        )
    from agent_registry import get_registry

    registry = get_registry()
    step_count = len(plan)

    for i, step in enumerate(plan):
        declared_agent = step["agent"]
        execution_agent = declared_agent
        get_capability_class = getattr(registry, "get_capability_class", lambda name: "read-only")
        get_capability_policy = getattr(
            registry,
            "get_capability_policy",
            lambda name: {
                "capability_class": get_capability_class(name),
                "requires_preflight": getattr(registry, "requires_preflight", lambda _: False)(name),
                "requires_approval": False,
                "requires_verification": True,
                "fail_closed": getattr(registry, "requires_preflight", lambda _: False)(name),
                "allow_fallback_to_general": not getattr(registry, "requires_preflight", lambda _: False)(name),
                "auto_retry": True,
            },
        )
        step.setdefault("capability_class", get_capability_class(declared_agent))
        step.setdefault("policy", get_capability_policy(declared_agent))
        policy = step["policy"]
        capability_class = step["capability_class"]
        instruction = step["instruction"]
        tier = step.get("tier", "light")
        prediction = step.get("prediction")
        is_last = (i == len(plan) - 1)
        log.info("Step %d/%d: agent=%s tier=%s capability=%s instruction=%s",
                 i + 1, len(plan), declared_agent, tier, capability_class, instruction[:80])

        # Record pre-mortem prediction before execution
        _record_premortem(task_id, i, declared_agent, instruction, prediction)

        # If previous step produced output, append it as context
        if prev_output and declared_agent != "clarify":
            instruction = f"{instruction}\n\n--- 上一步的输出 ---\n{prev_output[:3000]}"

        # Emit status card for current step
        _step_icons = {
            "briefing": ("Fetching feeds...", "newspaper"),
            "writing": ("Writing...", "doc.text"),
            "publish": ("Publishing...", "paperplane"),
            "analyst": ("Analyzing...", "chart.bar"),
            "video": ("Processing video...", "film"),
            "photo": ("Editing photo...", "camera"),
            "podcast": ("Generating audio...", "waveform"),
            "socialmedia": ("Checking Substack...", "at"),
            "surfer": ("Browsing...", "globe"),
            "discussion": ("Thinking...", "bubble.left.and.text.bubble.right"),
            "general": ("Working...", "gear"),
            "secret": ("Private mode...", "lock.shield"),
            "clarify": ("Need your input", "questionmark.bubble"),
        }
        status_text, status_icon = _step_icons.get(declared_agent, ("Working...", "gear"))
        if is_multi:
            status_text = f"Step {i+1}/{len(plan)}: {status_text}"
        _emit_status(task_id, status_text, status_icon)
        mark_step_running(
            workspace,
            step_index=i,
            declared_agent=declared_agent,
            execution_agent=execution_agent,
            input_summary=instruction,
        )

        # Special case: clarify (not a real agent, just asks user)
        if declared_agent == "clarify":
            (workspace / "output.md").write_text(instruction, encoding="utf-8")
            _write_result(
                workspace,
                task_id,
                "needs-input",
                instruction,
                tags=["clarify"],
                metadata=_result_metadata(
                    step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                ),
            )
            mark_step_finished(
                workspace,
                step_index=i,
                status="needs-input",
                declared_agent=declared_agent,
                execution_agent=execution_agent,
                output_summary=instruction,
            )
            _append_exec_log(workspace, round_num, "clarify", "needs-input", instruction)
            return

        # --- Access control: verify agent is allowed for this user ---
        # NOTE: allowed_agents is loaded fresh from get_user_config() at dispatch time
        # (in core.py), so permission revocation takes effect on next message cycle.
        if allowed_agents and declared_agent not in allowed_agents and declared_agent not in ("clarify", "discussion"):
            log.warning("ACCESS DENIED: user=%s agent=%s not in allowed_agents=%s",
                        user_id, declared_agent, allowed_agents)
            denied_msg = (
                f"Sorry, you don't have access to the {declared_agent} agent. "
                f"Available: {', '.join(allowed_agents)}"
            )
            _write_result(
                workspace,
                task_id,
                "blocked",
                denied_msg,
                metadata=_result_metadata(
                    step,
                    step_index=i,
                    step_count=step_count,
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                ),
            )
            mark_step_finished(
                workspace,
                step_index=i,
                status="blocked",
                declared_agent=declared_agent,
                execution_agent=execution_agent,
                failure_reason=denied_msg,
            )
            return

        # --- Content filter: prepend safety prompt for child users ---
        if content_filter:
            from config import CHILD_SAFETY_PROMPT
            instruction = f"{CHILD_SAFETY_PROMPT}\n\n---\n\n{instruction}"

        # --- Model restriction: force local model for restricted users ---
        from sub_agent import set_usage_agent, set_model_policy
        if model_restriction:
            set_model_policy(model_restriction)
            log.info("Model policy: %s for user=%s", model_restriction, user_id)
        else:
            set_model_policy(None)

        # Registry-based dispatch: load handler dynamically from manifest
        requires_preflight = bool(policy.get("requires_preflight"))
        fail_closed = bool(policy.get("fail_closed"))
        allow_fallback = bool(policy.get("allow_fallback_to_general"))
        set_usage_agent(declared_agent)

        output_file = workspace / "output.md"
        result_file = workspace / "result.json"
        output_snapshot = _snapshot_file(output_file)
        handler_result = None
        used_fallback = False
        preflight_fn = None

        try:
            try:
                preflight_fn = getattr(registry, "load_preflight", lambda name: None)(declared_agent)
            except KeyError:
                if requires_preflight or fail_closed:
                    preflight_msg = f"{declared_agent} preflight missing from registry"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.warning("Agent '%s' not in registry during preflight load, falling back to general", declared_agent)
                execution_agent = "general"
                _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
                used_fallback = True
            except ImportError as e:
                if fail_closed:
                    preflight_msg = f"{declared_agent} preflight load failed: {e}"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.error("ImportError loading preflight for agent '%s': %s — falling back to general", declared_agent, e)
                execution_agent = "general"
                _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
                used_fallback = True
            except Exception as e:
                if fail_closed:
                    preflight_msg = f"{declared_agent} preflight load failed: {e}"
                    log.error("%s", preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                log.error("Registry preflight for '%s' failed to load: %s — falling back to general", declared_agent, e)
                execution_agent = "general"
                _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
                used_fallback = True

            if not used_fallback and not preflight_fn and requires_preflight:
                preflight_msg = f"{declared_agent} preflight missing"
                log.error("%s", preflight_msg)
                (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                _write_result(
                    workspace,
                    task_id,
                    "blocked",
                    preflight_msg,
                    agent=declared_agent,
                    metadata=_result_metadata(
                        step,
                        step_index=i,
                        step_count=step_count,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                    ),
                )
                mark_step_finished(
                    workspace,
                    step_index=i,
                    status="blocked",
                    declared_agent=declared_agent,
                    execution_agent=execution_agent,
                    failure_reason=preflight_msg,
                )
                return

            if not used_fallback and preflight_fn:
                try:
                    passed, preflight_msg = _invoke_registry_preflight(
                        preflight_fn, workspace, task_id, instruction, sender, thread_id, tier,
                        user_id=user_id,
                    )
                except Exception as e:
                    preflight_msg = f"{declared_agent} preflight failed: {e}"
                    log.error("Preflight for '%s' raised: %s", declared_agent, e)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "error",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="error",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return
                if not passed:
                    log.warning("Preflight blocked agent '%s': %s", declared_agent, preflight_msg)
                    (workspace / "output.md").write_text(preflight_msg, encoding="utf-8")
                    _write_result(
                        workspace,
                        task_id,
                        "blocked",
                        preflight_msg,
                        agent=declared_agent,
                        metadata=_result_metadata(
                            step,
                            step_index=i,
                            step_count=step_count,
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                        ),
                    )
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status="blocked",
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        failure_reason=preflight_msg,
                    )
                    return

            if not used_fallback:
                try:
                    handler_fn = registry.load_handler(declared_agent)
                    handler_result = _invoke_registry_handler(
                        handler_fn, workspace, task_id, instruction, sender, thread_id, tier,
                        user_id=user_id,
                    )
                except KeyError as e:
                    handler_msg = f"{declared_agent} handler missing from registry"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="error",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.warning("Agent '%s' not in registry, falling back to general: %s", declared_agent, e)
                    execution_agent = "general"
                    _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
                except ImportError as e:
                    handler_msg = f"{declared_agent} handler load failed: {e}"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="error",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.error("ImportError loading agent '%s': %s — falling back to general", declared_agent, e)
                    execution_agent = "general"
                    _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
                except Exception as e:
                    handler_msg = f"{declared_agent} handler failed to load: {e}"
                    if fail_closed or not allow_fallback:
                        log.error("%s", handler_msg)
                        _write_result(
                            workspace,
                            task_id,
                            "error",
                            handler_msg,
                            agent=declared_agent,
                            metadata=_result_metadata(
                                step,
                                step_index=i,
                                step_count=step_count,
                                declared_agent=declared_agent,
                                execution_agent=execution_agent,
                            ),
                        )
                        mark_step_finished(
                            workspace,
                            step_index=i,
                            status="error",
                            declared_agent=declared_agent,
                            execution_agent=execution_agent,
                            failure_reason=handler_msg,
                        )
                        return
                    log.error("Registry handler for '%s' failed: %s — falling back to general", declared_agent, e)
                    execution_agent = "general"
                    _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
        finally:
            set_model_policy(None)  # always reset after step

        # Check if this step failed (result.json says error)
        # Also stamp the agent name for evaluator tracking
        _ensure_step_result(
            workspace,
            task_id,
            execution_agent,
            content,
            handler_result,
            output_snapshot,
            metadata=_result_metadata(
                step,
                step_index=i,
                step_count=step_count,
                declared_agent=declared_agent,
                execution_agent=execution_agent,
            ),
        )
        step_status = "done"
        step_output_preview = ""
        if result_file.exists():
            try:
                r = json.loads(result_file.read_text(encoding="utf-8"))
                # Stamp agent name for evaluator tracking
                changed = False
                if "agent" not in r:
                    r["agent"] = execution_agent
                    changed = True
                if "declared_agent" not in r:
                    r["declared_agent"] = declared_agent
                    changed = True
                if "execution_agent" not in r:
                    r["execution_agent"] = execution_agent
                    changed = True
                if "capability_class" not in r:
                    r["capability_class"] = capability_class
                    changed = True
                if "policy" not in r:
                    r["policy"] = policy
                    changed = True
                if changed:
                    result_file.write_text(
                        json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
                execution_agent = r.get("agent", execution_agent)
                step_status = r.get("status", "done")
                step_output_preview = r.get("summary", "")[:200]
                if step_status in ("error", "blocked", "needs-input"):
                    outcome = "error" if step_status == "error" else step_status
                    _record_postmortem(task_id, i, declared_agent, prediction, outcome,
                                       step_output_preview)
                    _append_exec_log(workspace, round_num, execution_agent, step_status,
                                     r.get("summary", ""))
                    mark_step_finished(
                        workspace,
                        step_index=i,
                        status=step_status,
                        declared_agent=declared_agent,
                        execution_agent=execution_agent,
                        output_summary=step_output_preview,
                        failure_reason=r.get("summary", "") if step_status != "needs-input" else "",
                    )
                    log.error("Step %d/%d stopped plan with status=%s: %s",
                              i + 1, len(plan), step_status, r.get("summary", ""))
                    return
            except (json.JSONDecodeError, OSError):
                pass

        # Read output from this step for chaining
        if output_file.exists():
            prev_output = output_file.read_text(encoding="utf-8")
            step_output_preview = prev_output[:200]
            # Verify output — detect hallucinated file/action claims
            verification = _verify_output(prev_output, workspace)
            if verification:
                log.warning("HALLUCINATION DETECTED: %s", verification)
                prev_output += f"\n\n⚠️ VERIFICATION FAILED: {verification}"
                _append_exec_log(workspace, round_num, execution_agent, "unverified",
                                 f"HALLUCINATION: {verification}")
                _record_postmortem(task_id, i, declared_agent, prediction,
                                   "hallucination", step_output_preview)
            else:
                _append_exec_log(workspace, round_num, execution_agent, "done",
                                 prev_output[:300])
                _record_postmortem(task_id, i, declared_agent, prediction,
                                   "done", step_output_preview)
            # Emit intermediate result to iOS app (streaming progress)
            if not is_last and prev_output.strip():
                snippet = prev_output.strip()[:300]
                emit_progress(f"Step {i+1} done: {snippet}", "checkmark.circle")
            # Save numbered copy so future rounds don't lose it
            numbered = workspace / f"output_r{round_num}.md"
            shutil.copy2(output_file, numbered)

        mark_step_finished(
            workspace,
            step_index=i,
            status="done",
            declared_agent=declared_agent,
            execution_agent=execution_agent,
            output_summary=step_output_preview,
        )

        # For multi-step plans, delete intermediate result.json so next step writes fresh
        if is_multi and not is_last and result_file.exists():
            result_file.unlink()

    # Synthesize outputs for multi-step plans
    if is_multi and prev_output:
        synthesized = _synthesize_outputs(content, plan, prev_output)
        if synthesized:
            (workspace / "output.md").write_text(synthesized, encoding="utf-8")
            prev_output = synthesized

    # Auto-register any runtime tools created during execution
    try:
        _register_runtime_tools_created(workspace)
    except Exception as e:
        log.warning("Runtime tool registration failed: %s", e)

    log.info("Plan execution complete (%d steps)", len(plan))


# _synthesize_outputs → imported from planning/planner.py above

# Handler functions (_handle_briefing, _handle_writing, etc.) have been
# extracted to handlers_legacy.py and are imported at the top of this file.


def _write_progress(workspace: Path, task_id: str, user_request: str):
    """Write progress.md summarizing what was done this session.

    Next session reads this first to understand prior state.
    """
    result_file = workspace / "result.json"
    output_file = workspace / "output.md"

    status = "unknown"
    summary = ""
    if result_file.exists():
        try:
            r = json.loads(result_file.read_text(encoding="utf-8"))
            status = r.get("status", "unknown")
            summary = r.get("summary", "")[:500]
        except (json.JSONDecodeError, OSError):
            pass

    output_preview = ""
    if output_file.exists():
        try:
            output_preview = output_file.read_text(encoding="utf-8")[:500]
        except OSError:
            pass

    progress = f"""# Progress — {task_id}

## User request
{user_request[:300]}

## Status: {status}

## Summary
{summary}

## Output preview
{output_preview}

## Workspace files
{', '.join(f.name for f in workspace.iterdir() if f.is_file() and not f.name.startswith('.'))}
"""
    (workspace / "progress.md").write_text(progress, encoding="utf-8")


# Patterns that indicate garbage output ONLY when they dominate the response.
# Short responses (< 80 chars) containing these are likely status/placeholder messages.
# Longer responses containing these as substrings are valid content — don't reject them.
_GARBAGE_PATTERNS = [
    "有什么想说的吗", "这条消息长得像系统状态",
    "summary.txt 已存在", "不需要重新", "两个文件都已存在",
    "Agent: 空闲", "无需重新执行", "收到你的回答。已记录",
]


def _validate_completion(workspace: Path, task_id: str, summary: str) -> str | None:
    """Check if task output is actually useful. Returns error message if garbage.

    Only flags truly empty outputs or known garbage patterns.
    Short but valid responses (confirmations, simple answers) are NOT garbage.
    Longer responses (>= 80 chars) that happen to contain a garbage pattern as a
    substring are treated as valid — the pattern is incidental, not dominant.
    """
    if not summary or len(summary.strip()) == 0:
        return "Output is completely empty"

    stripped = summary.strip()
    # Only check garbage patterns for short responses where the pattern dominates
    if len(stripped) < 80:
        for pattern in _GARBAGE_PATTERNS:
            if pattern in stripped:
                return f"Output contains garbage pattern: '{pattern}'"

    return None


# ---------------------------------------------------------------------------
# Knowledge write-back — extract reusable knowledge from task outputs
# ---------------------------------------------------------------------------

_WRITEBACK_SKIP_TAGS = {"private", "status", "greeting", "podcast", "tts", "audio"}
_WRITEBACK_MIN_OUTPUT_CHARS = 300

_WRITEBACK_EXTRACTION_PROMPT = """\
You are reviewing the output of a completed task. Determine if it contains
reusable factual knowledge, a technique, a useful pattern, or a non-obvious insight
that would be worth storing permanently.

## Task output (truncated):
{output}

## Instructions:
- If the output contains reusable knowledge, respond with a JSON object:
  {{"title": "concise title", "content": "the extracted knowledge (2-5 paragraphs, focus on what's reusable)"}}
- If the output is routine (status update, greeting, opinion, simple lookup, creative writing),
  respond with exactly: NONE
- Be selective — only extract knowledge that would help answer future questions.
"""


def _extract_knowledge_writeback(workspace: Path, task_id: str,
                                 tags: list[str] | None = None):
    """Extract and persist reusable knowledge from a completed task's output.

    Runs a lightweight LLM call to judge whether the output contains
    knowledge worth storing. If so, saves it as a reading note with provenance.
    """
    # Skip conditions
    if tags and _WRITEBACK_SKIP_TAGS & set(tags):
        return
    output_path = workspace / "output.md"
    if not output_path.exists():
        return
    try:
        output_text = output_path.read_text(encoding="utf-8")
    except OSError:
        return
    if len(output_text) < _WRITEBACK_MIN_OUTPUT_CHARS:
        return

    try:
        prompt = _WRITEBACK_EXTRACTION_PROMPT.format(output=output_text[:3000])
        result = claude_think(prompt, timeout=60, tier="light")
        if not result or "NONE" in result[:20]:
            return
        # Parse JSON from response
        # Find the JSON object in the response
        start = result.find("{")
        end = result.rfind("}") + 1
        if start < 0 or end <= start:
            return
        extracted = json.loads(result[start:end])
        title = extracted.get("title", "").strip()
        content = extracted.get("content", "").strip()
        if title and content and len(content) > 50:
            save_knowledge_note(title, content, source_task_id=task_id, user_id=_ACTIVE_USER_ID)
            log.info("Knowledge writeback: '%s' from task %s", title[:60], task_id)
    except (json.JSONDecodeError, ClaudeTimeoutError) as e:
        log.debug("Knowledge writeback skipped for %s: %s", task_id, e)
    except Exception as e:
        log.warning("Knowledge writeback failed for %s: %s", task_id, e)


def _write_result(workspace: Path, task_id: str, status: str, summary: str,
                  tags: list[str] | None = None, agent: str | None = None,
                  metadata: dict | None = None):
    """Write result JSON for TaskManager to collect."""
    result = {
        "task_id": task_id,
        "status": status,
        "summary": summary,
        "completed_at": _utc_iso(),
    }
    if tags:
        result["tags"] = tags
    if agent:
        result["agent"] = agent
    if metadata:
        result.update(metadata)
    result_path = workspace / "result.json"
    tmp_path = result_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.rename(result_path)

    # Guard: verify output.md exists when claiming task is done
    # (learned from real failures — agent claimed completion but produced no output)
    if status == "done":
        output_path = workspace / "output.md"
        if not output_path.exists() or output_path.stat().st_size == 0:
            log.warning("Task %s claimed done but output.md missing/empty — "
                        "result.json written but output may be incomplete", task_id)

    # --- Archive conversation as episode for long-term recall ---
    # SKIP for private tasks — never persist sensitive content
    if tags and "private" in tags:
        return
    if status in ("done", "completed", "error", "failed"):
        try:
            # Try items/ first, fallback to legacy tasks/
            item_file = _item_file(task_id)
            task_file = TASKS_DIR / f"{task_id}.json"
            src = item_file if item_file.exists() else task_file
            if src.exists():
                task_data = json.loads(src.read_text(encoding="utf-8"))
                messages = task_data.get("messages", [])
                title = task_data.get("title", task_id)
                if len(messages) >= 2:  # Only archive meaningful conversations
                    save_episode(task_id, title, messages, tags=tags, user_id=_ACTIVE_USER_ID)
        except Exception as e:
            log.warning("Episode archival failed for %s: %s", task_id, e)

    # --- Knowledge write-back: extract reusable knowledge from successful tasks ---
    if status == "done":
        try:
            _extract_knowledge_writeback(workspace, task_id, tags=tags)
        except Exception as e:
            log.debug("Knowledge writeback skipped: %s", e)

    # --- Self-iteration: extract lessons from failures ---
    if status in ("error", "failed"):
        try:
            from self_iteration import extract_failure_lesson, save_failure_lesson
            lesson = extract_failure_lesson(task_id, summary[:200], summary)
            if lesson:
                save_failure_lesson(lesson)
        except Exception as e:
            log.warning("Failure lesson extraction failed for %s: %s", task_id, e)

    # --- Auto-flush context before worker exits ---
    try:
        from soul_manager import auto_flush
        context_summary = (
            f"Task {task_id} ({status}): {summary[:500]}\n"
            f"Tags: {', '.join(tags) if tags else 'none'}"
        )
        auto_flush(context_summary)
    except Exception as e:
        log.debug("Auto-flush skipped: %s", e)


def _snapshot_file(path: Path) -> tuple[int, int] | None:
    """Return a cheap file snapshot used to detect whether a handler updated output."""
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _verify_step_artifact(workspace: Path, task_id: str, agent: str, status: str) -> bool:
    """Require a real output artifact before claiming success or input-needed."""
    if status not in ("done", "needs-input"):
        return True

    output_file = workspace / "output.md"
    verify = verify_artifact("file", str(output_file), {"min_size": 1})
    if verify.verified:
        return True

    _write_result(
        workspace,
        task_id,
        "error",
        f"{agent} produced no verifiable output: {verify.summary()}",
        agent=agent,
    )
    return False


def _invoke_registry_handler(handler_fn, workspace: Path, task_id: str, instruction: str,
                             sender: str, thread_id: str, tier: str,
                             user_id: str = "ang"):
    """Invoke a registry-loaded handler with optional runtime context kwargs.

    Registry handlers have drifted signatures: some support `tier`, some also
    accept `thread_history` / `thread_memory`, while others only accept the core
    positional contract. Inspect the signature and pass only supported kwargs.
    """
    kwargs = {}
    params = inspect.signature(handler_fn).parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    if accepts_kwargs or "tier" in params:
        kwargs["tier"] = tier
    if accepts_kwargs or "user_id" in params:
        kwargs["user_id"] = user_id

    needs_thread_history = accepts_kwargs or "thread_history" in params
    needs_thread_memory = accepts_kwargs or "thread_memory" in params
    if needs_thread_history:
        try:
            kwargs["thread_history"] = load_thread_history(thread_id, user_id=user_id)
        except TypeError:
            kwargs["thread_history"] = load_thread_history(thread_id)
    if needs_thread_memory:
        try:
            kwargs["thread_memory"] = load_thread_memory(thread_id, user_id=user_id)
        except TypeError:
            kwargs["thread_memory"] = load_thread_memory(thread_id)

    return handler_fn(workspace, task_id, instruction, sender, thread_id, **kwargs)


def _invoke_registry_preflight(preflight_fn, workspace: Path, task_id: str, instruction: str,
                               sender: str, thread_id: str, tier: str,
                               user_id: str = "ang"):
    """Invoke an optional registry preflight hook with matching runtime kwargs."""
    kwargs = {}
    params = inspect.signature(preflight_fn).parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    if accepts_kwargs or "tier" in params:
        kwargs["tier"] = tier
    if accepts_kwargs or "user_id" in params:
        kwargs["user_id"] = user_id

    needs_thread_history = accepts_kwargs or "thread_history" in params
    needs_thread_memory = accepts_kwargs or "thread_memory" in params
    if needs_thread_history:
        try:
            kwargs["thread_history"] = load_thread_history(thread_id, user_id=user_id)
        except TypeError:
            kwargs["thread_history"] = load_thread_history(thread_id)
    if needs_thread_memory:
        try:
            kwargs["thread_memory"] = load_thread_memory(thread_id, user_id=user_id)
        except TypeError:
            kwargs["thread_memory"] = load_thread_memory(thread_id)

    return preflight_fn(workspace, task_id, instruction, sender, thread_id, **kwargs)


def _ensure_step_result(workspace: Path, task_id: str, agent: str, request: str,
                        handler_result: str | None,
                        output_snapshot: tuple[int, int] | None,
                        metadata: dict | None = None) -> None:
    """Backfill result.json for handlers that only return text/output.md."""
    result_file = workspace / "result.json"
    if result_file.exists():
        try:
            existing = json.loads(result_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        verified = _verify_step_artifact(
            workspace,
            task_id,
            agent,
            existing.get("status", ""),
        )
        if not verified:
            return
        if metadata:
            changed = False
            for key, value in metadata.items():
                if key not in existing:
                    existing[key] = value
                    changed = True
            if changed:
                result_file.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        return

    output_file = workspace / "output.md"
    output_changed = _snapshot_file(output_file) != output_snapshot
    result_text = handler_result.strip() if isinstance(handler_result, str) else ""

    if result_text.startswith("NEEDS_APPROVAL:") or result_text.startswith("NEEDS_INPUT:"):
        summary = result_text.split(":", 1)[1].strip()
        if not output_changed:
            output_file.write_text(summary, encoding="utf-8")
        if not _verify_step_artifact(workspace, task_id, agent, "needs-input"):
            return
        _write_result(
            workspace,
            task_id,
            "needs-input",
            summary,
            tags=[agent],
            agent=agent,
            metadata=metadata,
        )
        return

    summary = _load_step_summary(workspace)
    if not summary and result_text:
        summary = result_text
    if not output_changed and result_text:
        output_file.write_text(result_text, encoding="utf-8")
        output_changed = True
    if output_changed and not summary and output_file.exists():
        summary = output_file.read_text(encoding="utf-8")[:300]

    if summary:
        tags = smart_classify(request, summary)
        if not _verify_step_artifact(workspace, task_id, agent, "done"):
            return
        _write_result(
            workspace,
            task_id,
            "done",
            summary,
            tags=tags,
            agent=agent,
            metadata=metadata,
        )
        return

    _write_result(
        workspace,
        task_id,
        "error",
        f"{agent} handler returned no result or output",
        agent=agent,
        metadata=metadata,
    )


def _load_step_summary(workspace: Path) -> str:
    """Load a handler-provided summary if present."""
    summary_file = workspace / "summary.txt"
    if summary_file.exists():
        try:
            return summary_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


def _update_thread_memory(thread_id: str, request: str, summary: str):
    """Append task summary to per-thread memory."""
    thread_dir = MIRA_DIR / "threads" / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    mem_file = thread_dir / "memory.md"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- [{ts}] Request: {request[:80]} → {summary[:120]}\n"

    if mem_file.exists():
        text = mem_file.read_text(encoding="utf-8")
    else:
        text = "# Thread Memory\n\n"
    text += entry
    mem_file.write_text(text, encoding="utf-8")


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
