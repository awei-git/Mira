"""Task support helpers — logging, classification, tools, approval.

Standalone helpers extracted from task_worker.py for modularity.
No circular imports: this module depends only on shared libs.
"""

import inspect
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_AGENTS_DIR = Path(__file__).resolve().parent.parent
if str(_AGENTS_DIR.parent / "lib") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR.parent / "lib"))
if str(_AGENTS_DIR / "writer") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "writer"))
if str(_AGENTS_DIR / "general") not in sys.path:
    sys.path.insert(0, str(_AGENTS_DIR / "general"))

from config import MIRA_DIR, TASKS_DIR
from execution.calibration import _track_output_quality
from execution.context import load_thread_history, load_thread_memory
from llm import claude_think
from memory.soul import append_memory, save_skill

log = logging.getLogger("task_worker")

# Re-used by other modules in this package
_ACTIVE_USER_ID_REF = {"value": "ang"}


def _get_active_user_id() -> str:
    return _ACTIVE_USER_ID_REF["value"]


def _set_active_user_id_ref(user_id: str):
    _ACTIVE_USER_ID_REF["value"] = user_id or "ang"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _append_exec_log(
    workspace: Path,
    round_num: int,
    agent: str,
    status: str,
    output_preview: str,
    verification_depth: str = "",
):
    """Append an entry to the execution log with output health metrics.

    verification_depth indicates which proxy was checked (L1–L4):
      L1  existence only, L2  existence+size, L3  existence+content guard,
      L4  existence+semantic spot-check. Empty string means no verification ran.
    """
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
        "verification_depth": verification_depth,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if verification_depth:
        log.info("task_complete [verified:%s] agent=%s status=%s", verification_depth, agent, status)

    _track_output_quality(agent, status, health)


VERIFY_OUTPUT_DEPTH = "L1"  # _verify_output checks existence only (os.path.exists)


def _verify_output(output: str, workspace: Path) -> str:
    """Verify agent output claims. Returns error string if hallucination detected, empty if OK.

    Verification depth: L1 (existence only). Callers that log on success should pass
    verification_depth=VERIFY_OUTPUT_DEPTH to _append_exec_log so the level is recorded.
    """
    import re

    issues = []

    file_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存|生成|写了)\s+(?:to\s+)?[`"\']*(/[^\s`"\',:]+(?:\.\w+))',
        output,
        re.IGNORECASE,
    )
    for path in file_claims:
        if not Path(path).exists():
            issues.append(f"Claimed file does not exist: {path}")

    rel_claims = re.findall(
        r'(?:wrote|saved|created|写入|保存)\s+(?:to\s+)?[`"\']*(?:output|result|summary|article)[\w.]*\.\w+',
        output,
        re.IGNORECASE,
    )
    for claim in rel_claims:
        fname_match = re.search(r"([\w.-]+\.\w+)", claim)
        if fname_match:
            fname = fname_match.group(1)
            full_path = workspace / fname
            if not full_path.exists() and fname != "output.md":
                issues.append(f"Claimed workspace file does not exist: {fname}")

    wrote_article = bool(
        re.search(r"写了[一篇个]|wrote\s+(?:a|an|the)\s+(?:article|post|essay|piece)", output, re.IGNORECASE)
    )
    if wrote_article:
        content_lines = [
            l for l in output.split("\n") if l.strip() and not l.startswith("#") and not l.startswith("---")
        ]
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


def smart_classify(content: str, summary: str = "") -> list[str]:
    """Use LLM to intelligently tag a task. Returns 1-5 short tags."""
    prompt = f"""Given this task request and result, generate 1-5 short tags (each 1-3 words) that classify the task. Tags should be specific and useful for search/filtering. Mix Chinese and English as appropriate. Output ONLY a JSON array of strings, nothing else.

Request: {content[:300]}
Result: {summary[:300] if summary else '(pending)'}

Example output: ["写作", "science-fiction", "自由意志"]"""

    try:
        result = claude_think(prompt, timeout=90)
        if result:
            import re

            match = re.search(r"\[.*?\]", result, re.DOTALL)
            if match:
                tags = json.loads(match.group())
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


def _result_metadata(
    step: dict, *, step_index: int, step_count: int, declared_agent: str, execution_agent: str, workflow_id: str = ""
) -> dict:
    from task_worker import _ACTIVE_WORKFLOW_ID

    return {
        "workflow_id": workflow_id or _ACTIVE_WORKFLOW_ID,
        "step_index": step_index,
        "step_count": step_count,
        "step_id": step.get("step_id", f"step-{step_index + 1:02d}"),
        "declared_agent": declared_agent,
        "execution_agent": execution_agent,
        "capability_class": step.get("capability_class", "read-only"),
        "policy": step.get("policy", {}),
        "artifacts_expected": step.get("artifacts_expected", []),
    }


def _safe_general_fallback(
    workspace: Path,
    task_id: str,
    instruction: str,
    sender: str,
    thread_id: str,
    *,
    tier: str,
    step: dict,
    step_index: int,
    step_count: int,
    declared_agent: str,
    execution_agent: str,
    workflow_id: str,
) -> bool:
    """Run general fallback and fail the current step cleanly if it crashes."""
    from handlers_legacy import _handle_general
    from task_result import _write_result
    from execution.plan_state import mark_step_finished

    try:
        _handle_general(workspace, task_id, instruction, sender, thread_id, tier=tier)
        return True
    except Exception as e:
        fallback_msg = f"general fallback failed while handling {declared_agent}: {e}"
        log.error("%s", fallback_msg)
        _write_result(
            workspace,
            task_id,
            "error",
            fallback_msg,
            agent=execution_agent,
            metadata=_result_metadata(
                step,
                step_index=step_index,
                step_count=step_count,
                declared_agent=declared_agent,
                execution_agent=execution_agent,
                workflow_id=workflow_id,
            ),
            failure_class="fallback_error",
        )
        mark_step_finished(
            workspace,
            step_index=step_index,
            status="failed",
            declared_agent=declared_agent,
            execution_agent=execution_agent,
            failure_reason=fallback_msg,
        )
        return False


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
        result,
        re.DOTALL,
    )
    if match:
        name = match.group(1).strip()
        desc = match.group(2).strip()
        content = match.group(3).strip()
        save_skill(name, desc, content)
        append_memory(f"Learned skill from TalkBridge task: {name}", user_id=_get_active_user_id())
        log.info("Extracted skill: %s", name)


def _register_runtime_tools_created(workspace: Path) -> None:
    """Scan workspace for Python tools the agent wrote to runtime_tools/ and register them."""
    try:
        from tools.tool_forge import RUNTIME_TOOLS_DIR, list_tools, forge_tool
    except ImportError:
        return

    if not RUNTIME_TOOLS_DIR.exists():
        return

    indexed = {t["file"] for t in list_tools()}
    for py_file in RUNTIME_TOOLS_DIR.glob("*.py"):
        if py_file.name == "__init__.py" or py_file.name in indexed:
            continue
        code = py_file.read_text(encoding="utf-8")
        name = py_file.stem.replace("_", " ")
        desc = ""
        import re

        doc_match = re.search(r'"""(.+?)"""', code, re.DOTALL)
        if doc_match:
            first_line = doc_match.group(1).strip().split("\n")[0]
            desc = first_line[:200]
        if not desc:
            desc = f"Auto-discovered tool: {name}"
        ok, msg = forge_tool(name, desc, code)
        if ok:
            log.info("Auto-registered runtime tool: %s", name)
            append_memory(f"Created runtime tool: {name}", user_id=_get_active_user_id())
        else:
            log.warning("Failed to register tool %s: %s", name, msg)


_APPROVAL_PHRASES = [
    "可以",
    "好的",
    "发吧",
    "发",
    "同意",
    "ok",
    "yes",
    "确认",
    "approve",
    "go ahead",
    "continue",
    "继续",
    "行",
    "没问题",
    "可以发了",
    "lgtm",
    "approved",
    "ship it",
    "好",
    "嗯",
    "对",
]


def _is_approval(content: str) -> bool:
    """Detect if a message is approving/confirming a pending action."""
    stripped = content.strip().lower()
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _APPROVAL_PHRASES):
        return True
    return False


_REJECTION_PHRASES = [
    "reject",
    "cancel",
    "取消",
    "不发",
    "不要发",
    "别发",
    "停",
    "no",
    "nope",
    "算了",
    "不了",
]


def _is_rejection(content: str) -> bool:
    """Detect if a message is rejecting/cancelling a pending action."""
    stripped = content.strip().lower()
    if len(stripped) < 30 and any(stripped == p or stripped.startswith(p) for p in _REJECTION_PHRASES):
        return True
    return False


def _execute_pending_publish(pending_pub_file: Path, workspace: Path, task_id: str, thread_id: str):
    """Execute a pending Substack publish after user approval."""
    import re as _re
    from task_result import _write_result, _update_thread_memory

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

    article_text = ""
    if article_path:
        try:
            article_text = Path(article_path).read_text(encoding="utf-8")
            article_text = _re.sub(r"\n---\s*\n+## 修改记录.*", "", article_text, flags=_re.DOTALL)
        except Exception as e:
            log.error("Failed to read article file %s: %s", article_path, e)

    if not article_text:
        article_text = pending.get("article_text", "")

    if not article_text:
        _write_result(workspace, task_id, "error", "待发布文章内容为空，无法发布。")
        return

    try:
        pending_pub_file.unlink()
        log.info("Pending publish file cleared before publishing")
    except Exception as e:
        log.warning("Could not clear pending publish file: %s", e)

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
    """Invoke a registry-loaded handler with optional runtime context kwargs."""
    kwargs = {}
    params = inspect.signature(handler_fn).parameters
    accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    if accepts_kwargs or "tier" in params:
        kwargs["tier"] = tier
    if accepts_kwargs or "user_id" in params:
        kwargs["user_id"] = user_id
    if agent_id and (accepts_kwargs or "agent_id" in params):
        kwargs["agent_id"] = agent_id

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


def _invoke_registry_preflight(
    preflight_fn,
    workspace: Path,
    task_id: str,
    instruction: str,
    sender: str,
    thread_id: str,
    tier: str,
    user_id: str = "ang",
):
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
