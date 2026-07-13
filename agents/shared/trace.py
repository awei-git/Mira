"""Human-readable task trace generation."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import MIRA_ROOT


TRACE_DIR = Path(MIRA_ROOT) / "logs" / "task_traces"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_task_id(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id or "").strip())
    return safe.strip("._") or "unknown-task"


def _clip(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        except TypeError:
            text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + "\n... [truncated]"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clip(value, 2000)
        if text:
            return text
    return ""


def _bullet_lines(items: list[Any], empty: str) -> list[str]:
    lines = []
    for item in items:
        text = _clip(item, 1200)
        if text:
            lines.append(f"- {text}")
    return lines or [f"- {empty}"]


def _decision_lines(agent_log: dict) -> list[str]:
    decisions = list(_as_list(agent_log.get("key_decisions")))
    plan = _as_dict(agent_log.get("plan"))
    for step in _as_list(plan.get("steps")):
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id") or f"step-{step.get('step_index', '?')}"
        agent = step.get("agent") or step.get("declared_agent") or "unknown"
        tier = step.get("tier") or "unknown"
        instruction = _clip(step.get("instruction_preview") or step.get("instruction"), 220)
        decisions.append(f"{step_id}: routed to {agent} ({tier}) - {instruction}")
    return _bullet_lines(decisions, "No key decisions captured.")


def _intermediate_lines(agent_log: dict) -> list[str]:
    outputs = list(_as_list(agent_log.get("intermediate_outputs")))
    step_states = _as_dict(agent_log.get("step_states"))
    for step in _as_list(step_states.get("steps")):
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id") or f"step-{step.get('step_index', '?')}"
        agent = step.get("execution_agent") or step.get("declared_agent") or "unknown"
        status = step.get("status") or "unknown"
        summary = step.get("output_summary") or step.get("failure_reason") or step.get("input_summary") or ""
        outputs.append(f"{step_id}: {agent} -> {status}; {_clip(summary, 300)}")
    for entry in _as_list(agent_log.get("exec_log_entries")):
        if not isinstance(entry, dict):
            continue
        outputs.append(
            "round {round}: {agent} -> {status}; {preview}".format(
                round=entry.get("round", "?"),
                agent=entry.get("agent", "unknown"),
                status=entry.get("status", "unknown"),
                preview=_clip(entry.get("output_preview", ""), 300),
            )
        )
    return _bullet_lines(outputs, "No intermediate outputs captured.")


def _verification_text(outcome: dict) -> str:
    verification = _as_dict(outcome.get("verification"))
    status = _first_text(verification.get("status"), "not-run")
    verified = outcome.get("outcome_verified")
    method = _first_text(outcome.get("verification_method"), verification.get("method"), verification.get("details"))
    parts = [f"status={status}"]
    if verified is not None:
        parts.append(f"outcome_verified={bool(verified)}")
    if method:
        parts.append(f"method={method}")
    return ", ".join(parts)


def generate_trace(task_id: str, agent_log: Any, outcome: Any, reasoning_steps: Any) -> Path:
    agent_log_data = _as_dict(agent_log)
    outcome_data = _as_dict(outcome)
    reasoning_items = _as_list(reasoning_steps) or _as_list(outcome_data.get("reasoning"))

    request = _first_text(
        agent_log_data.get("task_request"),
        outcome_data.get("task_request"),
        outcome_data.get("request"),
        _as_dict(outcome_data.get("metadata")).get("prompt"),
        _as_dict(outcome_data.get("metadata")).get("instruction"),
        "Unavailable",
    )
    agent = _first_text(
        outcome_data.get("agent"),
        outcome_data.get("execution_agent"),
        agent_log_data.get("agent"),
        "unknown",
    )
    status = _first_text(outcome_data.get("status"), agent_log_data.get("exit_status"), "unknown")
    final_result = _first_text(outcome_data.get("summary"), outcome_data.get("output"), "No final summary captured.")

    lines = [
        f"# Task Trace: {task_id}",
        "",
        f"- Generated: {_utc_iso()}",
        f"- Task ID: {task_id}",
        f"- Agent: {agent}",
        f"- Status: {status}",
        f"- Workspace: {_clip(agent_log_data.get('workspace'), 500) or 'unknown'}",
        "",
        "## Task Request",
        request,
        "",
        "## Key Decisions",
        *_decision_lines(agent_log_data),
        "",
        "## Intermediate Outputs",
        *_intermediate_lines(agent_log_data),
        "",
        "## Final Result",
        final_result,
        "",
        "## Verification Status",
        _verification_text(outcome_data),
        "",
        "## Plain-Language Reasoning Summary",
        *_bullet_lines(reasoning_items, "No reasoning summary was captured."),
    ]

    worker_tail = _clip(agent_log_data.get("worker_log_tail"), 3000)
    if worker_tail:
        lines.extend(["", "## Worker Log Tail", "```text", worker_tail, "```"])

    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    path = TRACE_DIR / f"{_safe_task_id(task_id)}.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
