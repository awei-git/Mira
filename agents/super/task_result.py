"""Task result — result writing, verification, artifact collection.

Extracted from task_worker.py. May use task_support helpers.
"""

import json
import logging
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
from execution.runtime_contract import derive_workflow_id, normalize_task_status
from publish.preflight import verify_artifact
from llm import claude_think, ClaudeTimeoutError
from memory.soul import save_episode, save_knowledge_note

from task_support import (
    smart_classify,
    _utc_iso,
    _get_active_user_id,
)

log = logging.getLogger("task_worker")


def _items_dir(user_id: str | None = None) -> Path:
    from task_worker import _items_dir as _tw_items_dir

    return _tw_items_dir(user_id)


def _item_file(task_id: str, user_id: str | None = None) -> Path:
    from task_worker import _item_file as _tw_item_file

    return _tw_item_file(task_id, user_id)


def _write_progress(workspace: Path, task_id: str, user_request: str):
    """Write progress.md summarizing what was done this session."""
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


_GARBAGE_PATTERNS = [
    "有什么想说的吗",
    "这条消息长得像系统状态",
    "summary.txt 已存在",
    "不需要重新",
    "两个文件都已存在",
    "Agent: 空闲",
    "无需重新执行",
    "收到你的回答。已记录",
]


def _validate_completion(workspace: Path, task_id: str, summary: str) -> str | None:
    """Check if task output is actually useful. Returns error message if garbage."""
    if not summary or len(summary.strip()) == 0:
        return "Output is completely empty"

    stripped = summary.strip()
    if len(stripped) < 80:
        for pattern in _GARBAGE_PATTERNS:
            if pattern in stripped:
                return f"Output contains garbage pattern: '{pattern}'"

    return None


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


def _extract_knowledge_writeback(workspace: Path, task_id: str, tags: list[str] | None = None):
    """Extract and persist reusable knowledge from a completed task's output."""
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
        start = result.find("{")
        end = result.rfind("}") + 1
        if start < 0 or end <= start:
            return
        extracted = json.loads(result[start:end])
        title = extracted.get("title", "").strip()
        content = extracted.get("content", "").strip()
        if title and content and len(content) > 50:
            save_knowledge_note(title, content, source_task_id=task_id, user_id=_get_active_user_id())
            log.info("Knowledge writeback: '%s' from task %s", title[:60], task_id)
    except (json.JSONDecodeError, ClaudeTimeoutError) as e:
        log.debug("Knowledge writeback skipped for %s: %s", task_id, e)
    except Exception as e:
        log.warning("Knowledge writeback failed for %s: %s", task_id, e)


_RESULT_RUNTIME_METADATA_KEYS = {
    "workflow_id",
    "step_index",
    "step_count",
    "step_id",
    "declared_agent",
    "execution_agent",
    "capability_class",
    "policy",
    "retry_count",
    "artifacts_expected",
}


_RESULT_INTERNAL_FILES = {
    "result.json",
    "result.tmp",
    "plan.json",
    "step_states.json",
    "exec_log.jsonl",
    "progress.md",
}


def _step_id_from_metadata(metadata: dict | None) -> str:
    if not metadata:
        return ""
    step_id = str(metadata.get("step_id", "")).strip()
    if step_id:
        return step_id
    step_index = metadata.get("step_index")
    if isinstance(step_index, int) and step_index >= 0:
        return f"step-{step_index + 1:02d}"
    return ""


def _serialize_checks(checks: list | None) -> list[dict]:
    serialized = []
    for check in checks or []:
        serialized.append(
            {
                "name": str(getattr(check, "name", ""))[:120],
                "passed": bool(getattr(check, "passed", False)),
                "message": str(getattr(check, "message", ""))[:500],
            }
        )
    return serialized


_PROPERTY_ASSUMED_BY_TYPE = {
    "file": "output file exists and meets size threshold",
    "publish": "article was published to platform",
    "url": "URL is reachable and returns expected content",
}

_DEFAULT_UNVERIFIED_ASSUMPTIONS = ["content quality", "structural correctness", "task intent fulfilled"]


def _verification_payload_from_verify(verify, *, target: str = "") -> dict:
    artifact_type = str(getattr(verify, "artifact_type", ""))
    raw_checks = getattr(verify, "checks", []) or []
    passed_check_names = [str(getattr(c, "name", "")) for c in raw_checks if getattr(c, "passed", False)]
    proxy_checked = " + ".join(passed_check_names) if passed_check_names else "none"
    property_assumed = _PROPERTY_ASSUMED_BY_TYPE.get(artifact_type, f"{artifact_type} artifact verified")
    return {
        "status": "verified" if getattr(verify, "verified", False) else "failed",
        "verified": bool(getattr(verify, "verified", False)),
        "artifact_type": artifact_type,
        "target": target,
        "summary": str(verify.summary())[:500],
        "checks": _serialize_checks(raw_checks),
        "proxy_checked": proxy_checked,
        "property_assumed": property_assumed,
        "unverified_assumptions": list(_DEFAULT_UNVERIFIED_ASSUMPTIONS),
    }


def _verification_not_run(reason: str) -> dict:
    return {
        "status": "not-run",
        "verified": False,
        "artifact_type": "",
        "target": "",
        "summary": reason[:500],
        "checks": [],
        "proxy_checked": "",
        "property_assumed": "",
        "unverified_assumptions": [],
    }


def _normalize_verification_payload(verification: dict | None) -> dict:
    if not isinstance(verification, dict):
        return _verification_not_run("verification not recorded")
    normalized = {
        "status": str(verification.get("status", "not-run"))[:50],
        "verified": bool(verification.get("verified", False)),
        "artifact_type": str(verification.get("artifact_type", ""))[:120],
        "target": str(verification.get("target", ""))[:500],
        "summary": str(verification.get("summary", ""))[:500],
        "checks": [],
        "proxy_checked": str(verification.get("proxy_checked", ""))[:200],
        "property_assumed": str(verification.get("property_assumed", ""))[:200],
        "unverified_assumptions": [
            str(u)[:100] for u in (verification.get("unverified_assumptions") or []) if isinstance(u, str)
        ][:10],
    }
    checks = verification.get("checks", [])
    if isinstance(checks, list):
        normalized["checks"] = [
            {
                "name": str(check.get("name", ""))[:120],
                "passed": bool(check.get("passed", False)),
                "message": str(check.get("message", ""))[:500],
            }
            for check in checks
            if isinstance(check, dict)
        ]
    return normalized


def _step_verification_payload(workspace: Path, status: str) -> dict:
    if status not in ("done", "needs-input"):
        return _verification_not_run("verification not required for this status")
    output_file = workspace / "output.md"
    verify = verify_artifact("file", str(output_file), {"min_size": 1})
    return _verification_payload_from_verify(verify, target=str(output_file))


def _workspace_relative_artifact_paths(metadata: dict | None) -> list[Path]:
    candidates = []
    if not metadata:
        return candidates
    for item in metadata.get("artifacts_expected", []) or []:
        if isinstance(item, dict):
            path_str = item.get("path", "")
        else:
            path_str = str(item)
        path_str = str(path_str).strip()
        if not path_str:
            continue
        path = Path(path_str)
        if not path.is_absolute():
            path = Path(path_str)
        candidates.append(path)
    return candidates


def _collect_result_artifacts(workspace: Path, metadata: dict | None = None) -> list[dict]:
    artifact_paths: list[Path] = []
    for name in ("output.md", "summary.txt"):
        path = workspace / name
        if path.exists() and path.is_file():
            artifact_paths.append(path)

    for candidate in _workspace_relative_artifact_paths(metadata):
        path = candidate if candidate.is_absolute() else workspace / candidate
        try:
            resolved = path.resolve()
            resolved.relative_to(workspace.resolve())
        except (OSError, ValueError):
            continue
        if resolved.exists() and resolved.is_file():
            artifact_paths.append(resolved)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in artifact_paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)

    artifacts = []
    for path in deduped:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.name in _RESULT_INTERNAL_FILES or path.suffix == ".tmp":
            continue
        artifacts.append(
            {
                "type": "file",
                "path": str(path),
                "size_bytes": stat.st_size,
            }
        )
    return artifacts


def _infer_failure_class(status: str, verification: dict) -> str:
    if status == "done":
        return ""
    if status == "needs-input":
        return "needs_input"
    if status == "blocked":
        return "blocked"
    if status == "timeout":
        return "timeout"
    if status == "failed":
        if verification.get("status") == "failed":
            return "verification_failed"
        return "execution_failed"
    if verification.get("status") == "failed":
        return "verification_failed"
    return "execution_error"


def _infer_next_action(status: str, failure_class: str) -> str:
    if status == "done":
        return "proceed-to-next-step"
    if status == "needs-input":
        return "await-user-input"
    if failure_class == "preflight_blocked":
        return "resolve-preflight-block"
    if failure_class == "verification_failed":
        return "inspect-artifacts-and-retry"
    if failure_class == "timeout":
        return "retry-with-backoff-or-abort"
    if status == "blocked":
        return "unblock-and-retry"
    return "inspect-error-and-retry"


def _canonicalize_result_payload(
    workspace: Path,
    payload: dict,
    *,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None = None,
    agent: str | None = None,
    metadata: dict | None = None,
    verification: dict | None = None,
    failure_class: str | None = None,
    next_action: str | None = None,
) -> dict:
    from task_worker import _ACTIVE_WORKFLOW_ID

    result = dict(payload)
    normalized_status = normalize_task_status(status)
    result["task_id"] = task_id
    result["workflow_id"] = str(
        result.get("workflow_id")
        or (metadata or {}).get("workflow_id")
        or _ACTIVE_WORKFLOW_ID
        or derive_workflow_id(task_id=task_id)
    ).strip()
    result["status"] = normalized_status
    result["summary"] = summary
    result["completed_at"] = result.get("completed_at") or _utc_iso()
    if tags:
        result["tags"] = tags
    if agent:
        result["agent"] = agent
    if metadata:
        for key in _RESULT_RUNTIME_METADATA_KEYS:
            if key in metadata and key not in result:
                result[key] = metadata[key]
    result["step_id"] = str(result.get("step_id") or _step_id_from_metadata(metadata))
    retry_count = result.get("retry_count", metadata.get("retry_count", 0) if metadata else 0)
    try:
        result["retry_count"] = int(retry_count or 0)
    except (TypeError, ValueError):
        result["retry_count"] = 0

    normalized_verification = _normalize_verification_payload(
        verification if verification is not None else result.get("verification")
    )
    result["verification"] = normalized_verification
    result["artifacts_produced"] = _collect_result_artifacts(workspace, metadata=metadata)

    inferred_failure_class = failure_class
    if inferred_failure_class is None:
        inferred_failure_class = str(result.get("failure_class", "")).strip()
    if not inferred_failure_class:
        inferred_failure_class = _infer_failure_class(normalized_status, normalized_verification)
    result["failure_class"] = inferred_failure_class

    inferred_next_action = next_action or str(result.get("next_action", "")).strip()
    if not inferred_next_action:
        inferred_next_action = _infer_next_action(normalized_status, inferred_failure_class)
    result["next_action"] = inferred_next_action

    _agent_label = str(result.get("agent", "")).strip()
    _vp = normalized_verification
    _outcome_verified = bool(_vp.get("verified", False))
    _artifact_type = str(_vp.get("artifact_type", "")).strip()
    _proxy_checked = str(_vp.get("proxy_checked", "")).strip()
    if _artifact_type == "publish":
        _vmethod = "publish_url_confirmed"
    elif _artifact_type == "url":
        _vmethod = "url_reachable"
    elif _artifact_type == "file":
        _vmethod = "file_exists"
    elif _proxy_checked:
        _vmethod = _proxy_checked
    elif _agent_label == "surfer":
        _vmethod = "response_200"
    elif _agent_label == "socialmedia":
        _vmethod = "publish_url_confirmed"
    elif _agent_label == "explorer":
        _vmethod = "file_exists"
    else:
        _vmethod = ""
    result["outcome_verified"] = _outcome_verified
    result["verification_method"] = _vmethod
    return result


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
    """Write result JSON for TaskManager to collect."""
    result = _canonicalize_result_payload(
        workspace,
        {},
        task_id=task_id,
        status=status,
        summary=summary,
        tags=tags,
        agent=agent,
        metadata=metadata,
        verification=verification,
        failure_class=failure_class,
        next_action=next_action,
    )
    status = result["status"]
    result_path = workspace / "result.json"
    tmp_path = result_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.rename(result_path)

    if status == "done":
        output_path = workspace / "output.md"
        if not output_path.exists() or output_path.stat().st_size == 0:
            log.warning(
                "Task %s claimed done but output.md missing/empty — "
                "result.json written but output may be incomplete",
                task_id,
            )

    if tags and "private" in tags:
        return
    if status in ("done", "completed", "failed"):
        try:
            item_file_path = _item_file(task_id)
            task_file = TASKS_DIR / f"{task_id}.json"
            src = item_file_path if item_file_path.exists() else task_file
            if src.exists():
                task_data = json.loads(src.read_text(encoding="utf-8"))
                messages = task_data.get("messages", [])
                title = task_data.get("title", task_id)
                if len(messages) >= 2:
                    save_episode(
                        task_id,
                        title,
                        messages,
                        tags=tags,
                        user_id=_get_active_user_id(),
                        verification_proxy=result.get("verification"),
                    )
        except Exception as e:
            log.warning("Episode archival failed for %s: %s", task_id, e)

    if status == "done":
        try:
            _extract_knowledge_writeback(workspace, task_id, tags=tags)
        except Exception as e:
            log.debug("Knowledge writeback skipped: %s", e)

    if status == "failed":
        try:
            from evaluation.self_iteration import extract_failure_lesson, save_failure_lesson

            lesson = extract_failure_lesson(task_id, summary[:200], summary)
            if lesson:
                save_failure_lesson(lesson)
        except Exception as e:
            log.warning("Failure lesson extraction failed for %s: %s", task_id, e)

    try:
        from memory.soul import auto_flush

        context_summary = f"Task {task_id} ({status}): {summary[:500]}\n" f"Tags: {', '.join(tags) if tags else 'none'}"
        auto_flush(context_summary)
    except Exception as e:
        log.debug("Auto-flush skipped: %s", e)


def _snapshot_file(path: Path) -> tuple[int, int] | None:
    """Return a cheap file snapshot used to detect whether a handler updated output."""
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _verify_step_artifact(
    workspace: Path,
    task_id: str,
    agent: str,
    status: str,
    *,
    metadata: dict | None = None,
    verification: dict | None = None,
) -> bool:
    """Require a real output artifact before claiming success or input-needed."""
    if status not in ("done", "needs-input"):
        return True

    verification_payload = _normalize_verification_payload(
        verification if verification is not None else _step_verification_payload(workspace, status)
    )
    if verification_payload.get("verified"):
        return True

    _write_result(
        workspace,
        task_id,
        "failed",
        f"{agent} produced no verifiable output: {verification_payload.get('summary', '')}",
        agent=agent,
        metadata=metadata,
        verification=verification_payload,
        failure_class="verification_failed",
        next_action="inspect-artifacts-and-retry",
    )
    return False


def _ensure_step_result(
    workspace: Path,
    task_id: str,
    agent: str,
    request: str,
    handler_result: str | None,
    output_snapshot: tuple[int, int] | None,
    metadata: dict | None = None,
) -> None:
    """Backfill result.json for handlers that only return text/output.md."""
    result_file = workspace / "result.json"
    if result_file.exists():
        try:
            existing = json.loads(result_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        verification_payload = _step_verification_payload(workspace, str(existing.get("status", "")))
        verified = _verify_step_artifact(
            workspace,
            task_id,
            agent,
            existing.get("status", ""),
            metadata=metadata,
            verification=verification_payload,
        )
        if not verified:
            return
        normalized = _canonicalize_result_payload(
            workspace,
            existing,
            task_id=task_id,
            status=str(existing.get("status", "failed")),
            summary=str(existing.get("summary", "")),
            agent=agent if "agent" not in existing else None,
            metadata=metadata,
            verification=verification_payload,
        )
        if normalized != existing:
            result_file.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2),
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
        verification_payload = _step_verification_payload(workspace, "needs-input")
        if not _verify_step_artifact(
            workspace,
            task_id,
            agent,
            "needs-input",
            metadata=metadata,
            verification=verification_payload,
        ):
            return
        _write_result(
            workspace,
            task_id,
            "needs-input",
            summary,
            tags=[agent],
            agent=agent,
            metadata=metadata,
            verification=verification_payload,
            failure_class="approval_required" if result_text.startswith("NEEDS_APPROVAL:") else "needs_input",
            next_action="await-user-input",
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
        verification_payload = _step_verification_payload(workspace, "done")
        if not _verify_step_artifact(
            workspace,
            task_id,
            agent,
            "done",
            metadata=metadata,
            verification=verification_payload,
        ):
            return
        _write_result(
            workspace,
            task_id,
            "done",
            summary,
            tags=tags,
            agent=agent,
            metadata=metadata,
            verification=verification_payload,
        )
        return

    _write_result(
        workspace,
        task_id,
        "error",
        f"{agent} handler returned no result or output",
        agent=agent,
        metadata=metadata,
        failure_class="missing_output",
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
