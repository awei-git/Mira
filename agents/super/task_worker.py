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
import os
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
    AI_OUTPUT_REVIEW_BUDGET_LINES,
    AI_OUTPUT_WARNING_RATIO,
    SYNTHESIS_PASSTHROUGH_AGENTS,
    DELIBERATION_MODE,
    HIGH_IMPACT_ACTION_PATTERNS,
    ENABLE_CROSS_VERIFICATION,
    CROSS_VERIFY_IMPORTANCE_THRESHOLD,
    record_phase_duration,
)
from deliberation_gate import deliberate
from execution.runtime_contract import derive_workflow_id, normalize_task_status
from memory.soul import (
    load_soul,
    format_soul,
    append_memory as _base_append_memory,
    save_skill as _base_save_skill,
    save_episode as _base_save_episode,
    recall_context,
    save_knowledge_note as _base_save_knowledge_note,
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
    REQUIRE_DECISION_TRAIL,
    SubAgentFormatError,
    audit_agent_decision,
    extract_reasoning_payload,
    log_decision,
    log_permacomputing_audit,
    require_reasoning_in_instruction,
    task_log_tokens_from_counts,
    _validate_result,
)
from trace import generate_trace

# Handler functions extracted to handlers_legacy.py (imported after all helpers
# are defined to avoid circular import — see bottom of file)


log = logging.getLogger("task_worker")
MAX_DISPATCH_HOPS = 3
FRICTION_DIRECT = "direct"
FRICTION_INFRASTRUCTURE = "infrastructure_friction"
PIPELINE_CONTRACTS_FILE = _AGENTS_DIR / "shared" / "pipeline_contracts.yaml"
_pipeline_contract_context = threading.local()
_task_scope_context = threading.local()


def _normalize_declared_scope(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_scope_expansions(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    expansions: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            expansions.append(dict(item))
            continue
        text = str(item).strip()
        if text:
            expansions.append({"action": text, "rationale": ""})
    return expansions


def _normalize_task_dispatch_payload(msg_data: dict) -> dict:
    payload = dict(msg_data or {})
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    payload["declared_scope"] = _normalize_declared_scope(
        payload.get("declared_scope", metadata.get("declared_scope", []))
    )
    return payload


def _set_task_declared_scope(value) -> None:
    _task_scope_context.declared_scope = _normalize_declared_scope(value)


def _get_task_declared_scope() -> list[str]:
    return list(getattr(_task_scope_context, "declared_scope", []) or [])


def _load_pipeline_contracts() -> dict:
    if not PIPELINE_CONTRACTS_FILE.exists():
        log.warning("PIPELINE_CONTRACT_WARNING ref=%s reason=contract_file_missing", PIPELINE_CONTRACTS_FILE)
        return {}
    try:
        import yaml

        data = yaml.safe_load(PIPELINE_CONTRACTS_FILE.read_text(encoding="utf-8")) or {}
    except ImportError:
        log.warning("PIPELINE_CONTRACT_WARNING ref=%s reason=pyyaml_unavailable", PIPELINE_CONTRACTS_FILE)
        return {}
    except (OSError, ValueError) as exc:
        log.warning(
            "PIPELINE_CONTRACT_WARNING ref=%s reason=contract_load_failed error=%s", PIPELINE_CONTRACTS_FILE, exc
        )
        return {}
    return data if isinstance(data, dict) else {}


def _contract_path_context(workspace: Path | None = None) -> dict[str, str]:
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "workspace": str(workspace) if workspace else "{workspace}",
        "artifacts_dir": str(ARTIFACTS_DIR),
        "date": today,
        "date_compact": today.replace("-", ""),
        "slot_suffix": "*",
        "slug": "*",
        "language": "*",
    }


def _expand_contract_path(path_template: str, workspace: Path | None = None) -> str:
    text = str(path_template or "").strip()
    try:
        text = text.format(**_contract_path_context(workspace))
    except (KeyError, ValueError):
        return text
    if text.startswith("~"):
        return str(Path(text).expanduser())
    return text


def _contract_path_candidates(path_template: str, workspace: Path | None = None) -> list[Path]:
    expanded = _expand_contract_path(path_template, workspace)
    if not expanded or "://" in expanded:
        return []
    path = Path(expanded)
    if not path.is_absolute():
        path = MIRA_ROOT / path
    pattern = str(path)
    if any(ch in pattern for ch in "*?["):
        import glob

        return [Path(candidate) for candidate in glob.glob(pattern) if Path(candidate).exists()]
    return [path] if path.exists() else []


def _contract_format_matches(path: Path, expected_format: str) -> bool:
    expected = str(expected_format or "").strip().lower()
    if not expected:
        return True
    if expected == "json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return True
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False
    if expected in {"markdown", "markdown_with_frontmatter"}:
        if path.suffix.lower() not in {".md", ".markdown"}:
            return False
        if expected == "markdown_with_frontmatter":
            try:
                return path.read_text(encoding="utf-8", errors="replace").lstrip().startswith("---")
            except OSError:
                return False
        return True
    if expected == "audio/mpeg":
        return path.suffix.lower() == ".mp3" and path.stat().st_size > 0
    return True


def _producer_contracts_for_consumer(contracts: dict, producer: str, consumer: str) -> list[dict]:
    pipelines = contracts.get("pipelines") if isinstance(contracts.get("pipelines"), dict) else {}
    producer_contract = pipelines.get(producer, {}) if isinstance(pipelines.get(producer), dict) else {}
    consumer_contract = pipelines.get(consumer, {}) if isinstance(pipelines.get(consumer), dict) else {}
    produces = producer_contract.get("produces", []) if isinstance(producer_contract.get("produces"), list) else []
    consumes = consumer_contract.get("consumes", []) if isinstance(consumer_contract.get("consumes"), list) else []
    expected = [
        item
        for item in consumes
        if isinstance(item, dict) and str(item.get("expected_from_pipeline") or "").strip().lower() == producer
    ]
    if not expected:
        return []
    producer_by_path = {
        str(item.get("path_template") or ""): item
        for item in produces
        if isinstance(item, dict) and item.get("path_template")
    }
    checks = []
    for consumer_item in expected:
        path_template = str(consumer_item.get("path_template") or "")
        producer_item = producer_by_path.get(path_template, {})
        checks.append(
            {
                "path_template": path_template,
                "consumer_format": consumer_item.get("format", ""),
                "producer_format": producer_item.get("format", ""),
                "producer_declared": bool(producer_item),
            }
        )
    return checks


def validate_pipeline_contracts(producer, consumer) -> bool:
    producer_name = str(producer or "").strip().lower()
    consumer_name = str(consumer or "").strip().lower()
    if not producer_name or not consumer_name or producer_name == consumer_name:
        return True

    workspace = getattr(_pipeline_contract_context, "workspace", None)
    contracts = _load_pipeline_contracts()
    checks = _producer_contracts_for_consumer(contracts, producer_name, consumer_name)
    if not checks:
        return True

    ok = True
    checkable_paths = []
    found_checkable_output = False
    for check in checks:
        ref = f"{producer_name}->{consumer_name}:{check['path_template']}"
        producer_format = str(check.get("producer_format") or "").strip()
        consumer_format = str(check.get("consumer_format") or "").strip()
        if not check.get("producer_declared"):
            ok = False
            log.warning(
                "PIPELINE_CONTRACT_WARNING ref=%s reason=producer_missing_declared_output contract=%s",
                ref,
                PIPELINE_CONTRACTS_FILE,
            )
        if producer_format and consumer_format and producer_format != consumer_format:
            ok = False
            log.warning(
                "PIPELINE_CONTRACT_WARNING ref=%s reason=format_mismatch producer_format=%s consumer_format=%s contract=%s",
                ref,
                producer_format,
                consumer_format,
                PIPELINE_CONTRACTS_FILE,
            )

        if "://" in str(check["path_template"]):
            continue
        resolved = _expand_contract_path(check["path_template"], workspace)
        checkable_paths.append(resolved)
        candidates = _contract_path_candidates(check["path_template"], workspace)
        if not candidates:
            continue
        found_checkable_output = True
        for candidate in candidates:
            if not _contract_format_matches(candidate, consumer_format or producer_format):
                ok = False
                log.warning(
                    "PIPELINE_CONTRACT_WARNING ref=%s reason=unexpected_format path=%s expected_format=%s contract=%s",
                    ref,
                    candidate,
                    consumer_format or producer_format,
                    PIPELINE_CONTRACTS_FILE,
                )
    if checkable_paths and not found_checkable_output:
        ok = False
        log.warning(
            "PIPELINE_CONTRACT_WARNING ref=%s->%s reason=missing_expected_output resolved=%s contract=%s",
            producer_name,
            consumer_name,
            checkable_paths,
            PIPELINE_CONTRACTS_FILE,
        )
    return ok


def _pipeline_contract_workspace(args: tuple, kwargs: dict) -> Path | None:
    if args:
        return Path(args[0])
    workspace = kwargs.get("workspace")
    return Path(workspace) if workspace else None


def _pipeline_contract_agents(plan: list[dict]) -> list[str]:
    agents = []
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("execution_agent") or step.get("agent") or "").strip().lower()
        if agent:
            agents.append(agent)
    return agents


def _push_pipeline_contract_context(plan: list[dict], workspace: Path | None) -> dict:
    previous = {
        "agents": getattr(_pipeline_contract_context, "agents", None),
        "handoff_index": getattr(_pipeline_contract_context, "handoff_index", None),
        "workspace": getattr(_pipeline_contract_context, "workspace", None),
    }
    _pipeline_contract_context.agents = _pipeline_contract_agents(plan)
    _pipeline_contract_context.handoff_index = 0
    _pipeline_contract_context.workspace = workspace
    return previous


def _pop_pipeline_contract_context(previous: dict) -> None:
    for key, value in previous.items():
        attr = key
        if value is None:
            if hasattr(_pipeline_contract_context, attr):
                delattr(_pipeline_contract_context, attr)
        else:
            setattr(_pipeline_contract_context, attr, value)


def _maybe_validate_pipeline_handoff(workspace: Path, agent: str, status: str) -> None:
    raw_status = str(status or "").strip().lower()
    normalized = normalize_task_status(raw_status)
    if raw_status not in {"done", "unverified"} and normalized not in {
        "done",
        "verified",
        "completed",
        "completed_unverified",
    }:
        return
    agents = list(getattr(_pipeline_contract_context, "agents", []) or [])
    index = int(getattr(_pipeline_contract_context, "handoff_index", 0) or 0)
    if not agents or index >= len(agents):
        return
    producer = str(agent or agents[index]).strip().lower()
    if index < len(agents) - 1:
        _pipeline_contract_context.workspace = getattr(_pipeline_contract_context, "workspace", None) or workspace
        validate_pipeline_contracts(producer, agents[index + 1])
    _pipeline_contract_context.handoff_index = index + 1


def _require_decision_trail(result: dict, agent_name: str) -> dict:
    if not REQUIRE_DECISION_TRAIL or not isinstance(result, dict):
        return result
    if result.get("decision_trail") not in (None, "", [], {}):
        return result

    def _compact(value, limit: int = 220) -> str:
        if isinstance(value, (dict, list)):
            try:
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                value = str(value)
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)].rstrip() + "..."

    agent = str(agent_name or result.get("agent") or result.get("execution_agent") or "unknown").strip() or "unknown"
    status = str(result.get("status") or "unknown").strip() or "unknown"
    task_id = str(result.get("task_id") or "").strip()
    task_input = (
        result.get("input")
        or result.get("prompt")
        or result.get("instruction")
        or result.get("request")
        or result.get("task_request")
        or result.get("original_request")
        or ""
    )
    task_output = result.get("output")
    if task_output in (None, "", [], {}):
        task_output = result.get("summary") or result.get("result") or result.get("response") or ""

    trail = [
        f"auto-generated - {agent} completed task without explicit reasoning trail",
        f"key_decisions={_compact(result.get('reasoning') or 'agent completed task')}",
        "alternatives_considered=not captured",
        "assumptions=not captured",
        f"status={status}",
    ]
    if task_input:
        trail.append(f"input={_compact(task_input)}")
    if task_output:
        trail.append(f"output={_compact(task_output)}")

    log.warning("DECISION_TRAIL_COMPLIANCE_WARNING task_id=%s agent=%s status=%s", task_id, agent, status)
    updated = dict(result)
    updated["decision_trail"] = "; ".join(trail)
    return updated


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
    _receipt_state.deliberation_log_paths = []


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


def _matches_high_impact_action(action_type: str) -> bool:
    action = str(action_type or "").strip().lower()
    if not action:
        return False
    action_dot = action.replace("_", ".").replace("-", ".")
    action_tokens = {token for token in re.split(r"[^a-z0-9.]+", action_dot) if token}
    for pattern in HIGH_IMPACT_ACTION_PATTERNS:
        normalized = str(pattern or "").strip().lower()
        if not normalized:
            continue
        normalized_dot = normalized.replace("_", ".").replace("-", ".")
        if normalized_dot in {action, action_dot} or normalized_dot in action_tokens:
            return True
        if len(normalized_dot) > 2 and normalized_dot in action_dot:
            return True
    return False


def _coherent_deliberation_justification(justification: str) -> bool:
    text = " ".join(str(justification or "").split())
    if len(text) < 20:
        return False
    lowered = text.lower()
    if lowered in {"none", "n/a", "na", "unknown", "todo", "tbd", "because"}:
        return False
    return len(re.findall(r"[a-zA-Z0-9]+", text)) >= 4


def _deliberation_context(action_type: str, target: str, context: dict | None = None) -> dict:
    ctx = dict(context or {})
    ctx.setdefault("agent_name", _decision_agent())
    ctx.setdefault("action_type", action_type)
    ctx.setdefault("proposed_change", f"{action_type} {target}".strip())
    ctx.setdefault(
        "justification",
        f"Proceed with {action_type} for {target} because this action is part of the current task execution path.",
    )
    ctx.setdefault("alternatives_considered", ["defer the action", "request human review before proceeding"])
    ctx.setdefault("reversible", False)
    return ctx


def _remember_deliberation_log_path(log_path: str) -> None:
    if not log_path:
        return
    paths = getattr(_receipt_state, "deliberation_log_paths", None)
    if paths is not None and log_path not in paths:
        paths.append(log_path)
    _ctx.last_deliberation_log_path = log_path


def _maybe_deliberate_high_impact_action(
    action_type: str,
    target: str | Path,
    context: dict | None = None,
) -> str | None:
    if not DELIBERATION_MODE or not _matches_high_impact_action(action_type):
        return None
    target_text = str(target or "").strip()
    deliberation_context = _deliberation_context(action_type, target_text, context)
    log_path = deliberate(action_type, deliberation_context)
    _remember_deliberation_log_path(log_path)
    justification = str(deliberation_context.get("justification") or "")
    if not _coherent_deliberation_justification(justification):
        log.warning(
            "DELIBERATION_GATE_BLOCKED action_type=%s target=%s log_path=%s reason=incoherent_justification",
            action_type,
            target_text,
            log_path,
        )
        raise RuntimeError(
            f"Deliberation gate blocked {action_type}: empty or incoherent justification. Audit log: {log_path}"
        )
    return log_path


def _record_external_action(action_type: str, target: str | None) -> str | None:
    action_type = str(action_type or "").strip()
    target = str(target or "").strip()
    if not action_type or not target:
        return None
    deliberation_log_path = _maybe_deliberate_high_impact_action(action_type, target)
    actions = getattr(_receipt_state, "external_actions", None)
    if actions is None:
        return deliberation_log_path
    action = {"type": action_type, "target": target}
    if deliberation_log_path:
        action["deliberation_log_path"] = deliberation_log_path
    if action not in actions:
        actions.append(action)
    return deliberation_log_path


def _decision_agent(default: str = "task_worker") -> str:
    return str(getattr(_receipt_state, "agent_id", "") or default or "task_worker")


def _perma_task_summary_from_args(args: tuple, kwargs: dict) -> str:
    if len(args) >= 3:
        return str(args[2] or "")
    for key in ("content", "msg_content", "task_summary", "instruction"):
        value = kwargs.get(key)
        if value:
            return str(value)
    if len(args) >= 2:
        return f"task_id={args[1]}"
    return "execute plan"


def _perma_plan_audit_context(plan: list[dict], fallback: str) -> tuple[str, str]:
    step = next((item for item in plan or [] if isinstance(item, dict)), None)
    if step is None:
        return "task_worker", fallback
    agent = str(step.get("execution_agent") or step.get("agent") or "unknown")
    tier = str(step.get("tier") or "").strip()
    instruction = str(step.get("instruction") or "").strip()
    prediction = step.get("prediction") if isinstance(step.get("prediction"), dict) else {}
    parts = [f"Plan selected {agent}"]
    if tier:
        parts.append(f"tier={tier}")
    if instruction:
        parts.append(f"instruction={instruction}")
    if prediction.get("difficulty"):
        parts.append(f"expected_difficulty={prediction.get('difficulty')}")
    if prediction.get("success_criteria"):
        parts.append(f"success_criteria={prediction.get('success_criteria')}")
    return agent, "; ".join(parts)


def _log_worker_decision(
    action_type: str,
    target: str | Path,
    reasoning: str,
    expected_outcome: str,
    *,
    agent_name: str | None = None,
) -> dict | None:
    agent = agent_name or _decision_agent()
    log_permacomputing_audit(agent, f"{action_type}: {target}", reasoning)
    audit_agent_decision(
        agent,
        action_type,
        reasoning,
        {"target": str(target), "expected_outcome": str(expected_outcome or "")},
    )
    return log_decision(agent, action_type, str(target), reasoning, expected_outcome)


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
    deliberation_log_paths = list(getattr(_receipt_state, "deliberation_log_paths", []) or [])
    if deliberation_log_paths:
        receipt["deliberation_log_paths"] = deliberation_log_paths
    try:
        path = workspace / "receipt.json"
        tmp = path.with_suffix(".tmp")
        _log_worker_decision(
            "file_write",
            path,
            "Persist the task receipt after collecting result status, agent id, skills, and external actions.",
            "receipt.json records the completed task metadata for later audit.",
            agent_name=receipt_agent,
        )
        tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.debug("Failed to write receipt for %s: %s", task_id, exc)


def _trace_read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _trace_read_jsonl_tail(path: Path, limit: int = 8) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _trace_read_text_tail(path: Path, limit: int = 12000) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _trace_task_request(task_id: str, result: dict) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    for key in ("prompt", "instruction", "request"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value

    for path in (_item_file(task_id), TASKS_DIR / f"{task_id}.json"):
        data = _trace_read_json(path)
        if not data:
            continue
        for key in ("content", "request", "title", "name"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
        messages = data.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                sender = str(message.get("sender") or "").lower()
                content = str(message.get("content") or "").strip()
                if content and sender in {"user", "human", "default"}:
                    return content
    return ""


def _write_task_trace(
    workspace: Path,
    task_id: str,
    *,
    agent_id: str | None = None,
    exit_status: str | None = None,
) -> None:
    result_status, result_agent, result = _receipt_status_from_result(workspace)
    outcome = dict(result) if result else {"status": str(exit_status or result_status or "unknown")}
    agent_log = {
        "workspace": str(workspace),
        "task_request": _trace_task_request(task_id, result),
        "agent": str(agent_id or result_agent or outcome.get("agent") or outcome.get("execution_agent") or "unknown"),
        "exit_status": str(exit_status or result_status or outcome.get("status") or "unknown"),
        "worker_log_tail": _trace_read_text_tail(workspace / "worker.log"),
        "exec_log_entries": _trace_read_jsonl_tail(workspace / "exec_log.jsonl"),
        "plan": _trace_read_json(workspace / "plan.json"),
        "step_states": _trace_read_json(workspace / "step_states.json"),
    }
    reasoning = str(outcome.get("reasoning") or "").strip()
    reasoning_steps = [reasoning] if reasoning else []
    try:
        _log_worker_decision(
            "file_write",
            f"trace:{task_id}",
            "Generate a durable execution trace from worker logs, plan state, and task outcome.",
            "Trace artifacts are available for retrospective task analysis.",
            agent_name=agent_log["agent"],
        )
        generate_trace(task_id, agent_log, outcome, reasoning_steps)
    except Exception as exc:
        log.debug("Failed to generate task trace for %s: %s", task_id, exc)


def _wrap_llm_api_call(fn):
    if getattr(fn, "_mira_timed_llm_call", False):
        return fn

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        start = time.perf_counter()
        target = getattr(fn, "__name__", "llm_api_call")
        _record_external_action("api_call", target)
        _log_worker_decision(
            "network_call",
            target,
            "Call the model API to plan, reason, rewrite, or execute an agent step.",
            "The model returns text or structured output for the next worker phase.",
            agent_name=kwargs.get("agent_id") or kwargs.get("agent_name") or kwargs.get("agent") or _decision_agent(),
        )
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
    return getattr(_ctx, "user_id", "default")


def _get_active_workflow_id() -> str:
    return getattr(_ctx, "workflow_id", "")


# Legacy module-level names — now properties via __getattr__
_ACTIVE_USER_ID = "default"  # read by external callers; kept for import compat
_ACTIVE_WORKFLOW_ID = ""


def _set_active_user(user_id: str):
    global _ACTIVE_USER_ID
    _ctx.user_id = user_id or "default"
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
from planning.planner import _plan_task, _synthesize_outputs as _base_synthesize_outputs


def _last_plan_agent(plan: list[dict]) -> str:
    if not plan or not isinstance(plan[-1], dict):
        return ""
    return str(plan[-1].get("agent") or "").strip().lower()


def _synthesize_outputs(original_request: str, plan: list[dict], final_output: str) -> str:
    agent = _last_plan_agent(plan)
    passthrough_agents = {str(name).strip().lower() for name in SYNTHESIS_PASSTHROUGH_AGENTS}
    if agent in passthrough_agents:
        log.info("SYNTHESIS_BOUNDARY mode=bypassed agent=%s reason=synthesis_passthrough", agent)
        return ""

    synthesized = _base_synthesize_outputs(original_request, plan, final_output)
    mode = "applied" if synthesized else "bypassed"
    reason = "super_synthesis" if synthesized else "planner_noop"
    log.info("SYNTHESIS_BOUNDARY mode=%s agent=%s reason=%s", mode, agent or "unknown", reason)
    return synthesized


_planner_module._synthesize_outputs = _synthesize_outputs


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


SCOPE_ESCALATION_LOG = MIRA_ROOT / "logs" / "scope_escalation.log"
_SCOPE_GUARD_WORKSPACE_KEY = "__task_workspace__"
_SCOPE_GUARD_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


def _scope_guard_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _scope_guard_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _scope_guard_roots(task_workspace: Path) -> list[Path]:
    roots: list[Path] = []
    for raw_root in (MIRA_ROOT, MIRA_DIR, ARTIFACTS_DIR, task_workspace):
        root = _scope_guard_resolve(Path(raw_root))
        if not root.exists():
            continue
        if any(_scope_guard_is_relative_to(root, existing) for existing in roots):
            continue
        roots = [existing for existing in roots if not _scope_guard_is_relative_to(existing, root)]
        roots.append(root)
    return roots


def _scope_guard_file_state(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)}


def _scope_guard_capture(task_workspace: Path) -> dict[str, dict[str, int] | str]:
    snapshot: dict[str, dict[str, int] | str] = {_SCOPE_GUARD_WORKSPACE_KEY: str(_scope_guard_resolve(task_workspace))}
    for root in _scope_guard_roots(task_workspace):
        if root.is_file():
            try:
                snapshot[str(root)] = _scope_guard_file_state(root)
            except OSError:
                pass
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in _SCOPE_GUARD_SKIP_DIRS]
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    if path.is_file():
                        snapshot[str(_scope_guard_resolve(path))] = _scope_guard_file_state(path)
                except OSError:
                    continue
    return snapshot


def _scope_guard_display_path(path: Path) -> str:
    resolved = _scope_guard_resolve(path)
    root = _scope_guard_resolve(MIRA_ROOT)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return str(resolved)


def _scope_expansion_from_path_change(change: dict) -> dict:
    path = str(change.get("path") or "").strip()
    action = str(change.get("change") or "changed").strip()
    return {
        "action": f"{action} {path}".strip(),
        "rationale": "Detected file change outside the task workspace during task execution.",
        "path": path,
        "change": action,
    }


def _record_scope_expansions(workspace: Path, expansions: list[dict]) -> None:
    normalized = _normalize_scope_expansions(expansions)
    if not normalized:
        return
    result_path = workspace / "result.json"
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            return
        existing = _normalize_scope_expansions(result.get("scope_expansions"))
        for expansion in normalized:
            if expansion not in existing:
                existing.append(expansion)
        result["scope_expansions"] = existing
        tmp = result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(result_path)
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("Scope expansion result update skipped for %s: %s", result_path, exc)


def _scope_guard(task_id, pre_execution_file_list) -> list[dict]:
    try:
        task_workspace = Path(str(pre_execution_file_list.get(_SCOPE_GUARD_WORKSPACE_KEY) or TASKS_DIR / str(task_id)))
        task_workspace = _scope_guard_resolve(task_workspace)
        before = {
            str(path): state
            for path, state in pre_execution_file_list.items()
            if path != _SCOPE_GUARD_WORKSPACE_KEY and isinstance(state, dict)
        }
        after = _scope_guard_capture(task_workspace)
        affected_paths = []
        scope_log_path = _scope_guard_resolve(SCOPE_ESCALATION_LOG)
        for raw_path, state in after.items():
            if raw_path == _SCOPE_GUARD_WORKSPACE_KEY or not isinstance(state, dict):
                continue
            path = _scope_guard_resolve(Path(raw_path))
            if path == scope_log_path or _scope_guard_is_relative_to(path, task_workspace):
                continue
            previous = before.get(str(path))
            if previous is None:
                change = "created"
            elif previous != state:
                change = "modified"
            else:
                continue
            affected_paths.append({"path": _scope_guard_display_path(path), "change": change})
        if not affected_paths:
            return []
        affected_paths = sorted(affected_paths, key=lambda item: item["path"])
        record = {
            "timestamp": _utc_iso(),
            "task_id": str(task_id),
            "task_workspace": _scope_guard_display_path(task_workspace),
            "affected_paths": affected_paths,
        }
        SCOPE_ESCALATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SCOPE_ESCALATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        log.warning("SCOPE_ESCALATION_FILESYSTEM %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
        return [_scope_expansion_from_path_change(item) for item in affected_paths]
    except Exception as exc:
        log.debug("Scope guard skipped for %s: %s", task_id, exc)
    return []


def _dispatch_hops_from_message(msg_data: dict) -> int:
    dispatch_hops = 0
    task_chain = msg_data.get("task_chain")
    if isinstance(task_chain, list) and task_chain:
        for _ in task_chain[1:]:
            dispatch_hops += 1
        return dispatch_hops

    try:
        depth = int(msg_data.get("subtask_depth", 0) or 0)
    except (TypeError, ValueError):
        return dispatch_hops
    for _ in range(max(0, depth)):
        dispatch_hops += 1
    return dispatch_hops


def _plan_agent_handoffs(plan: list[dict]) -> int:
    dispatch_hops = 0
    previous_agent = ""
    for step in plan or []:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent") or step.get("execution_agent") or "").strip().lower()
        if not agent:
            continue
        if previous_agent and agent != previous_agent:
            dispatch_hops += 1
        previous_agent = agent
    return dispatch_hops


def _friction_classification(dispatch_hops: int) -> str:
    return FRICTION_INFRASTRUCTURE if dispatch_hops >= MAX_DISPATCH_HOPS else FRICTION_DIRECT


def _record_dispatch_friction(
    workspace: Path,
    task_id: str,
    dispatch_hops: int,
    *,
    agent_id: str | None = None,
) -> None:
    classification = _friction_classification(dispatch_hops)
    result_path = workspace / "result.json"
    result_agent = ""
    result = {}
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, dict) else {}
            result_agent = str(result.get("agent") or result.get("execution_agent") or "").strip()
        except (json.JSONDecodeError, OSError):
            result = {}
    agent = str(agent_id or result_agent or "unknown").strip() or "unknown"
    suggestion = ""
    if classification == FRICTION_INFRASTRUCTURE:
        suggestion = f"route directly to {agent}" if agent != "unknown" else "route directly to execution agent"
        log.warning(
            "DISPATCH_FRICTION task_id=%s agent=%s dispatch_hops=%d classification=%s suggestion=%s",
            task_id,
            agent,
            dispatch_hops,
            classification,
            suggestion,
        )
    else:
        log.info(
            "DISPATCH_FRICTION task_id=%s agent=%s dispatch_hops=%d classification=%s",
            task_id,
            agent,
            dispatch_hops,
            classification,
        )

    if not result:
        return
    result["dispatch_hops"] = dispatch_hops
    result["friction_classification"] = classification
    result["optimized_routing_suggestion"] = suggestion
    try:
        tmp = result_path.with_suffix(".tmp")
        _log_worker_decision(
            "file_write",
            result_path,
            "Annotate result.json with dispatch-hop friction metadata after execution.",
            "Result metadata includes routing friction classification and any optimization suggestion.",
            agent_name=agent,
        )
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.rename(result_path)
    except OSError as exc:
        log.debug("Dispatch friction result update skipped for %s: %s", task_id, exc)


AI_OUTPUT_SESSION_FILE = MIRA_ROOT / "data" / "ai_output_session.json"
AI_OUTPUT_WARNING_LOG = LOGS_DIR / "ai_output_warnings.log"
_AI_OUTPUT_PATH_FIELDS = (
    "artifacts_produced",
    "artifacts_expected",
    "files_modified",
    "modified_files",
    "files_written",
    "written_files",
    "code_files_written",
    "code_files_modified",
)
_AI_OUTPUT_RESET_KEYS = ("manual_reset", "reset_ai_output_session", "ai_output_session_reset")


def _read_ai_output_session() -> dict:
    if not AI_OUTPUT_SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(AI_OUTPUT_SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_ai_output_session(session: dict) -> None:
    try:
        AI_OUTPUT_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AI_OUTPUT_SESSION_FILE.with_suffix(".tmp")
        _log_worker_decision(
            "file_write",
            AI_OUTPUT_SESSION_FILE,
            "Update generated-output accounting for the current review-budget session.",
            "AI output session totals reflect this task before future budget checks.",
        )
        tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(AI_OUTPUT_SESSION_FILE)
    except OSError as exc:
        log.debug("AI output session write failed: %s", exc)


def _truthy_reset(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "reset"}
    return False


def _manual_ai_output_reset_requested(msg_data: dict, session: dict) -> bool:
    metadata = msg_data.get("metadata") if isinstance(msg_data.get("metadata"), dict) else {}
    for source in (session, msg_data, metadata):
        if any(_truthy_reset(source.get(key)) for key in _AI_OUTPUT_RESET_KEYS):
            return True
    return False


def _is_human_ai_output_session_start(msg_data: dict) -> bool:
    sender = str(msg_data.get("sender") or "").strip().lower()
    if sender in _USER_SENDERS:
        return True
    origin = str(msg_data.get("origin") or msg_data.get("source") or "").strip().lower()
    return origin in {"inbox", "user"} and sender not in {"agent", "mira", "system", "scheduler"}


def _new_ai_output_session(task_id: str, reason: str) -> dict:
    now = _utc_iso()
    return {
        "session_id": f"{now}:{task_id}",
        "started_at": now,
        "updated_at": now,
        "reset_reason": reason,
        "generated_lines": 0,
        "generated_bytes": 0,
        "task_count": 0,
        "counted_tasks": [],
    }


def _maybe_reset_ai_output_session(task_id: str, msg_data: dict) -> None:
    session = _read_ai_output_session()
    if _manual_ai_output_reset_requested(msg_data, session):
        _write_ai_output_session(_new_ai_output_session(task_id, "manual_reset"))
    elif _is_human_ai_output_session_start(msg_data):
        _write_ai_output_session(_new_ai_output_session(task_id, "human_inbox_message"))


def _iter_ai_output_path_entries(result: dict):
    seen: set[str] = set()

    def add(raw_path, metadata: dict | None = None):
        path_text = str(raw_path or "").strip()
        if not path_text:
            return
        key = path_text
        if key in seen:
            return
        seen.add(key)
        yield path_text, metadata or {}

    for field in _AI_OUTPUT_PATH_FIELDS:
        value = result.get(field)
        if not value:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                raw_path = item.get("path") or item.get("file") or item.get("target")
                yield from add(raw_path, item)
            else:
                yield from add(item, {})


def _resolve_ai_output_path(workspace: Path, path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = workspace / path
    return path


def _file_line_byte_count(path: Path) -> tuple[int, int]:
    try:
        size = path.stat().st_size
        if size <= 0:
            return 0, 0
        lines = 0
        last_byte = b""
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                lines += chunk.count(b"\n")
                last_byte = chunk[-1:]
        if last_byte != b"\n":
            lines += 1
        return lines, int(size)
    except OSError:
        return 0, 0


def _ai_output_generated_counts(workspace: Path, result: dict) -> tuple[int, int]:
    total_lines = 0
    total_bytes = 0
    for path_text, metadata in _iter_ai_output_path_entries(result):
        path = _resolve_ai_output_path(workspace, path_text)
        lines, byte_count = _file_line_byte_count(path)
        if byte_count == 0:
            try:
                byte_count = int(metadata.get("size_bytes", 0) or 0)
            except (TypeError, ValueError):
                byte_count = 0
        total_lines += lines
        total_bytes += byte_count
    return total_lines, total_bytes


def _accumulate_ai_output_session(workspace: Path, task_id: str) -> None:
    result_path = workspace / "result.json"
    if not result_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(result, dict):
        return

    session = _read_ai_output_session() or _new_ai_output_session(task_id, "worker_start")
    counted_tasks = [str(item) for item in session.get("counted_tasks", []) if item]
    if str(task_id) in counted_tasks:
        return

    lines, byte_count = _ai_output_generated_counts(workspace, result)
    session["generated_lines"] = int(session.get("generated_lines", 0) or 0) + int(lines)
    session["generated_bytes"] = int(session.get("generated_bytes", 0) or 0) + int(byte_count)
    session["task_count"] = int(session.get("task_count", 0) or 0) + 1
    session["last_task_id"] = str(task_id)
    session["last_task_lines"] = int(lines)
    session["last_task_bytes"] = int(byte_count)
    session["updated_at"] = _utc_iso()
    counted_tasks.append(str(task_id))
    session["counted_tasks"] = counted_tasks[-200:]
    _write_ai_output_session(session)


def _maybe_log_ai_output_warning(task_id: str) -> None:
    try:
        budget = int(AI_OUTPUT_REVIEW_BUDGET_LINES)
        warning_ratio = float(AI_OUTPUT_WARNING_RATIO)
    except (TypeError, ValueError):
        return
    if budget <= 0 or warning_ratio <= 0:
        return
    session = _read_ai_output_session()
    current_count = int(session.get("generated_lines", 0) or 0)
    if current_count < budget * warning_ratio:
        return
    pct = int(round((current_count / budget) * 100))
    budget_remaining = max(0, budget - current_count)
    message = f"Review budget at {pct}% — human audit capacity may be saturated. " "Pause and review before continuing."
    entry = {
        "timestamp": _utc_iso(),
        "event": "ai_output_review_budget_warning",
        "task_id": str(task_id),
        "current_count": current_count,
        "budget_remaining": budget_remaining,
        "budget_lines": budget,
        "warning_ratio": warning_ratio,
        "pct": pct,
        "message": message,
    }
    try:
        AI_OUTPUT_WARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
        _log_worker_decision(
            "file_write",
            AI_OUTPUT_WARNING_LOG,
            "Record that generated-output volume is approaching the human review budget.",
            "The warning log captures the budget pressure signal for later audit.",
        )
        with AI_OUTPUT_WARNING_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.debug("AI output warning log write failed: %s", exc)
    log.warning("AI_OUTPUT_REVIEW_BUDGET_WARNING %s", json.dumps(entry, ensure_ascii=False))


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
            _log_worker_decision(
                "file_write",
                item_file,
                "Append or replace the current task status card in the item message stream.",
                "The UI can display the latest task progress without stale status cards.",
            )
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
            _log_worker_decision(
                "file_write",
                task_file,
                "Append or replace the current task status card in the legacy task message stream.",
                "Legacy task readers can display the latest task progress.",
            )
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
            _log_worker_decision(
                "file_write",
                path,
                "Persist the worker heartbeat so long-running task activity remains observable.",
                "heartbeat.json reflects the current task status and elapsed runtime.",
            )
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
    _is_publication_approval,
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
    _maybe_validate_pipeline_handoff(workspace, agent, status)


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


def save_skill(*args, **kwargs):
    skill_name = kwargs.get("name") or kwargs.get("skill_name") or (args[0] if args else "")
    audit_agent_decision(
        _decision_agent(),
        "skill.save",
        "Save a skill only after the current task path has selected it for persistence.",
        {"target": str(skill_name or "unknown")},
    )
    _record_external_action("skill.save", str(skill_name or "unknown"))
    return _base_save_skill(*args, **kwargs)


def _execute_pending_publish(pending_pub_file: Path, workspace: Path, task_id: str, thread_id: str):
    audit_agent_decision(
        _decision_agent(),
        "publish",
        "Execute the pending publish because user approval or task flow has moved this publish request out of preview.",
        {"target": str(pending_pub_file), "task_id": str(task_id), "thread_id": str(thread_id)},
    )
    _record_external_action("publish", str(pending_pub_file))
    return _base_execute_pending_publish(pending_pub_file, workspace, task_id, thread_id)


_task_support_module.try_extract_skill = try_extract_skill
_task_support_module.save_skill = save_skill
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
    if "declared_scope" in candidate:
        candidate["declared_scope"] = _normalize_declared_scope(candidate.get("declared_scope"))
    elif _get_task_declared_scope():
        candidate["declared_scope"] = _get_task_declared_scope()
    candidate["scope_expansions"] = _normalize_scope_expansions(candidate.get("scope_expansions"))
    audited = _apply_publish_content_guard_audit(candidate, task_id)
    audited = _require_decision_trail(audited, str(audited.get("agent") or kwargs.get("agent") or "unknown"))
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
_CONVERSATION_RESULT_TAGS = {"discussion", "daily-collab", "daily collab", "conversation"}


def _is_conversation_result(task_id: str, tags: list[str] | None, agent: str | None) -> bool:
    tag_keys = {str(tag or "").strip().casefold().replace("_", "-") for tag in tags or []}
    return (
        str(task_id or "").strip() == "disc_daily_collab"
        or str(agent or "").strip().casefold() == "discussion"
        or bool(tag_keys & _CONVERSATION_RESULT_TAGS)
    )


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


_CROSS_VERIFY_FINAL_STATUSES = {"done", "verified", "completed", "completed_unverified"}
_CROSS_VERIFY_PRIVATE_TAGS = {"private", "secret"}


def _coerce_0_1(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, 0.0), 1.0)


def _task_importance_from_mapping(data: dict | None) -> float | None:
    if not isinstance(data, dict):
        return None
    containers = [data]
    for key in ("metadata", "task", "root_task"):
        value = data.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        if "importance" in container:
            return _coerce_0_1(container.get("importance"))
    return None


def _task_importance(workspace: Path, task_id: str, metadata: dict | None) -> float:
    for source in (
        metadata,
        _trace_read_json(workspace / "message.json"),
        _trace_read_json(_item_file(task_id)),
        _trace_read_json(TASKS_DIR / f"{task_id}.json"),
    ):
        importance = _task_importance_from_mapping(source)
        if importance is not None:
            return importance
    return 0.0


def _cross_verify_threshold() -> float:
    return _coerce_0_1(CROSS_VERIFY_IMPORTANCE_THRESHOLD, 0.7)


def _cross_verify_clip(value, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[truncated]"


def _cross_verify_request(workspace: Path, task_id: str, metadata: dict | None) -> str:
    candidates = [
        metadata or {},
        _trace_read_json(workspace / "message.json"),
        _trace_read_json(_item_file(task_id)),
        _trace_read_json(TASKS_DIR / f"{task_id}.json"),
    ]
    for data in candidates:
        if not isinstance(data, dict):
            continue
        metadata_value = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        for key in ("original_intent", "prompt", "instruction", "request", "content", "title", "name"):
            value = str(data.get(key) or metadata_value.get(key) or "").strip()
            if value:
                return value
    return ""


def _cross_verify_output(workspace: Path, summary: str) -> str:
    output_path = workspace / "output.md"
    if output_path.exists():
        try:
            text = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
        except OSError:
            pass
    return str(summary or "").strip()


def _parse_cross_verification_response(raw: str) -> dict:
    text = str(raw or "").strip()
    match = re.match(r"^\s*(PASS|FAIL)\b[:\s-]*(.*)$", text, re.IGNORECASE | re.DOTALL)
    if match:
        decision = match.group(1).lower()
        reason = match.group(2).strip() or text
    else:
        decision = "fail"
        reason = text or "evaluator returned an empty or unclear response"
    return {"decision": decision, "reason": _cross_verify_clip(reason, 1000)}


def _should_cross_verify(
    workspace: Path,
    task_id: str,
    status: str,
    tags: list[str] | None,
    agent: str | None,
    metadata: dict | None,
) -> tuple[bool, float, float]:
    threshold = _cross_verify_threshold()
    importance = _task_importance(workspace, task_id, metadata)
    tag_keys = {str(tag or "").strip().lower() for tag in tags or []}
    agent_key = str(agent or "").strip().lower()
    if not ENABLE_CROSS_VERIFICATION:
        return False, importance, threshold
    if normalize_task_status(status) not in _CROSS_VERIFY_FINAL_STATUSES:
        return False, importance, threshold
    if agent_key in {"evaluator", "secret"} or tag_keys & _CROSS_VERIFY_PRIVATE_TAGS:
        return False, importance, threshold
    return importance >= threshold, importance, threshold


def _run_cross_verification(
    workspace: Path,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None,
    agent: str | None,
    metadata: dict | None,
    verification: dict | None,
    importance: float,
    threshold: float,
) -> dict:
    request = _cross_verify_request(workspace, task_id, metadata)
    output = _cross_verify_output(workspace, summary)
    prompt = f"""You are the evaluator agent validating a completed Mira task before finalization.
Does this output appear correct, complete, and non-hallucinated? Respond PASS or FAIL with reason.

Task context:
- task_id: {task_id}
- source_agent: {agent or "unknown"}
- status: {status}
- importance: {importance:.3f}
- threshold: {threshold:.3f}
- tags: {", ".join(str(tag) for tag in (tags or [])) or "none"}
- verification_so_far: {_cross_verify_clip(json.dumps(verification or {}, ensure_ascii=False), 1500)}

Original task:
{_cross_verify_clip(request, 4000)}

Primary output:
{_cross_verify_clip(output, 12000)}
"""
    try:
        _log_worker_decision(
            "cross_verification",
            f"evaluator:{task_id}",
            "Ask the evaluator agent to verify a high-importance task output before result finalization.",
            "The evaluator returns PASS or FAIL with a reason within the verification timeout.",
            agent_name="evaluator",
        )
        raw = claude_think(prompt, timeout=30, tier="light")
        assessment = _parse_cross_verification_response(raw or "")
    except Exception as exc:
        assessment = {
            "decision": "fail",
            "reason": _cross_verify_clip(f"cross-verification unavailable: {exc}", 1000),
        }
    assessment.update(
        {
            "verifier_agent": "evaluator",
            "source_agent": str(agent or "unknown"),
            "importance": importance,
            "threshold": threshold,
            "timeout_seconds": 30,
        }
    )
    return assessment


def _merge_cross_verification(verification: dict | None, assessment: dict) -> dict:
    existing = _normalize_verification_payload(verification)
    passed = str(assessment.get("decision") or "").strip().lower() == "pass"
    reason = str(assessment.get("reason") or "").strip()[:500]
    checks = list(existing.get("checks", []))
    checks.append(
        {
            "name": "cross_verification",
            "passed": passed,
            "message": reason,
        }
    )
    proxy_checked = str(existing.get("proxy_checked") or "").strip()
    if proxy_checked and "cross_verification" not in proxy_checked:
        proxy_checked = f"{proxy_checked} + cross_verification"
    else:
        proxy_checked = proxy_checked or "cross_verification"
    existing.update(
        {
            "checks": checks,
            "proxy_checked": proxy_checked,
            "property_assumed": existing.get("property_assumed") or "output correct, complete, and non-hallucinated",
            "summary": reason or existing.get("summary", ""),
        }
    )
    if passed:
        if existing.get("status") in ("not-run", ""):
            existing["status"] = "verified"
            existing["verified"] = True
        return existing
    existing["status"] = "failed"
    existing["verified"] = False
    return existing


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
    conversation_result = _is_conversation_result(task_id, tags, agent)
    if conversation_result:
        reasoning = None
    else:
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

    if not conversation_result and normalize_task_status(status) in (
        "done",
        "verified",
        "completed",
        "completed_unverified",
    ):
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
    user_id: str = "default",
    agent_id: str = None,
):
    handler_agent = agent_id or getattr(handler_fn, "__name__", "handler")
    _set_receipt_agent(agent_id)
    _record_external_action("skill_execution", handler_agent)
    log_permacomputing_audit(
        handler_agent,
        instruction,
        f"Invoke {handler_agent} at tier={tier} because the active plan step selected this specialist.",
    )
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
            result = _require_decision_trail(result, agent_id or result.get("agent") or "unknown")
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
_plan_executor_module._synthesize_outputs = _synthesize_outputs


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
    checked_plan = _require_reasoning_in_plan(plan)
    if _pause_for_protected_coder_modify_plan(checked_plan, *args, **kwargs):
        return None
    agent, rationale = _perma_plan_audit_context(checked_plan, "Execute the checked plan for this task.")
    log_permacomputing_audit(agent, _perma_task_summary_from_args(args, kwargs), rationale)
    previous = _push_pipeline_contract_context(checked_plan, _pipeline_contract_workspace(args, kwargs))
    try:
        return _base_execute_plan(checked_plan, *args, **kwargs)
    finally:
        _pop_pipeline_contract_context(previous)


def _execute_plan_steps(plan, *args, **kwargs):
    checked_plan = _require_reasoning_in_plan(plan)
    if _pause_for_protected_coder_modify_plan(checked_plan, *args, **kwargs):
        return None
    agent, rationale = _perma_plan_audit_context(checked_plan, "Execute the checked plan steps for this task.")
    log_permacomputing_audit(agent, _perma_task_summary_from_args(args, kwargs), rationale)
    previous = _push_pipeline_contract_context(checked_plan, _pipeline_contract_workspace(args, kwargs))
    try:
        return _base_execute_plan_steps(checked_plan, *args, **kwargs)
    finally:
        _pop_pipeline_contract_context(previous)


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

_USER_SENDERS = {"user", "default", "wa", "iphone", "ios"}
_CONVERSATIONAL_FEED_MARKERS = (
    "zhesi",
    "每日哲思",
    "mira thoughts",
    "daily-topic",
    "daily_topic",
    "daily-collab",
    "daily collab",
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
    if str(step.get("execution_agent") or step.get("agent") or "").strip().lower() != "coder":
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


def _unapproved_protected_coder_modify_paths(plan: list[dict]) -> list[str]:
    paths: set[str] = set()
    for step in plan:
        if not isinstance(step, dict) or not _is_coder_modify_step(step):
            continue
        step_paths = [path for path in _paths_from_step(step) if _is_protected_path(path)]
        if step_paths and _PROTECTED_APPROVAL_MARKER not in str(step.get("instruction", "")):
            paths.update(step_paths)
    return sorted(paths)


def _pause_for_protected_coder_modify_plan(plan: list[dict], *args, **kwargs) -> bool:
    protected_paths = _unapproved_protected_coder_modify_paths(plan)
    if not protected_paths:
        return False
    workspace = _pipeline_contract_workspace(args, kwargs)
    task_id = str(args[1] if len(args) >= 2 else kwargs.get("task_id") or "")
    if workspace is None or not task_id:
        raise RuntimeError("Protected coder modify plan requires task workspace and task_id for confirmation")
    prompt = send_confirmation(
        f"Task {task_id} plans to invoke the coder agent with a modify action on protected paths.",
        protected_paths,
    )
    pending_plan_file = workspace / "pending_plan.json"
    pending_plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (workspace / "output.md").write_text(prompt, encoding="utf-8")
    _write_result(
        workspace,
        task_id,
        "needs-input",
        prompt,
        tags=["coder", "protected-path-confirmation"],
        agent="super",
        failure_class="approval_required",
        next_action="await-user-input",
    )
    log.warning("Protected coder modify plan paused for approval: %s", ", ".join(protected_paths))
    return True


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
        parts.extend(str(v) for v in _strings_from_step(metadata))
    return " ".join(parts).lower()


def _task_item_type(task_data: dict) -> str:
    metadata = task_data.get("metadata") or {}
    item_type = metadata.get("item_type") if isinstance(metadata, dict) else ""
    return str(task_data.get("type") or item_type or "").strip().lower()


def _looks_like_market_thread(task_id: str, task_data: dict) -> bool:
    text = _metadata_text(task_id, task_data)
    return any(marker.lower() in text for marker in _MARKET_FEED_MARKERS)


def _looks_like_conversation_feed(task_id: str, task_data: dict) -> bool:
    item_type = _task_item_type(task_data)
    if item_type == "discussion":
        return True
    text = _metadata_text(task_id, task_data)
    if "daily-collab" in text:
        return True
    if item_type != "feed":
        return False
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
    dispatch_hops = 0
    pre_execution_file_list = _scope_guard_capture(workspace)
    scope_guard_recorded = False

    def _finish_task(exit_status: str | None = None, agent_id: str | None = None) -> None:
        nonlocal scope_guard_recorded
        if not scope_guard_recorded:
            _record_scope_expansions(workspace, _scope_guard(args.task_id, pre_execution_file_list))
            scope_guard_recorded = True
        _record_dispatch_friction(
            workspace,
            args.task_id,
            dispatch_hops,
            agent_id=agent_id,
        )
        _write_task_receipt(
            workspace,
            args.task_id,
            agent_id=agent_id,
            started_at=receipt_started_at,
            exit_status=exit_status,
        )
        _write_task_trace(
            workspace,
            args.task_id,
            agent_id=agent_id,
            exit_status=exit_status,
        )
        _accumulate_ai_output_session(workspace, args.task_id)

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
        msg_path = Path(args.msg_file)
        msg_data = json.loads(msg_path.read_text(encoding="utf-8"))
        msg_data = _normalize_task_dispatch_payload(msg_data)
        try:
            msg_path.write_text(json.dumps(msg_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.debug("Dispatch payload scope default write-back skipped for %s: %s", msg_path, exc)
        _set_task_declared_scope(msg_data.get("declared_scope"))
    except Exception as e:
        log.error("Failed to read message: %s", e)
        _write_result(workspace, args.task_id, "error", f"Failed to read message: {e}")
        _finish_task("error")
        sys.exit(1)

    dispatch_hops = _dispatch_hops_from_message(msg_data)
    task_name = str(msg_data.get("title") or msg_data.get("name") or msg_data.get("id") or args.task_id)
    msg_content = msg_data.get("content", "")
    _set_active_workflow_intent(msg_content)
    msg_sender = msg_data.get("sender", "unknown")
    thread_id = args.thread_id or msg_data.get("thread_id", "")
    _maybe_reset_ai_output_session(args.task_id, msg_data)
    _maybe_log_ai_output_warning(args.task_id)
    workflow_id = derive_workflow_id(
        task_id=args.task_id,
        thread_id=thread_id,
        workflow_id=msg_data.get("workflow_id", ""),
    )
    _set_active_workflow(workflow_id)

    # --- User access control context ---
    _user_id = msg_data.get("user_id", "default")
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
            if not _is_approval(msg_content):
                protected_paths = _protected_coder_modify_paths(plan)
                if protected_paths:
                    prompt = send_confirmation(
                        f"Task {args.task_id} is waiting for approval before modifying protected paths.",
                        protected_paths,
                    )
                else:
                    prompt = "NEEDS_APPROVAL: This task has a pending plan. Reply approve to continue."
                (workspace / "output.md").write_text(prompt, encoding="utf-8")
                _write_result(
                    workspace,
                    args.task_id,
                    "needs-input",
                    prompt,
                    tags=["protected-path-confirmation"] if protected_paths else ["approval"],
                    agent="super",
                    failure_class="approval_required",
                    next_action="await-user-input",
                )
                log.info("Worker waiting for explicit approval before resuming pending plan")
                _finish_task("needs-input")
                return
            plan = _mark_protected_plan_approved(plan)
            _record_external_action("unlink", str(pending_plan_file))
            pending_plan_file.unlink()  # consumed
            plan = _enrich_plan_with_runtime_policy(plan)
            dispatch_hops += _plan_agent_handoffs(plan)
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
            if _is_publication_approval(msg_content):
                _handle_autowrite_approval(workspace, args.task_id)
                log.info("Worker exiting (autowrite publication approval → pending publish)")
            else:
                _write_result(
                    workspace,
                    args.task_id,
                    "needs-input",
                    "Publication still needs explicit approval. Reply with 'publish this' or 'approve publication' if this draft should go public.",
                )
                log.info("Worker exiting (autowrite approval too vague for publication)")
            _finish_task(agent_id="writer")
            return

        pending_plan_file = workspace / "pending_plan.json"
        if pending_plan_file.exists():
            log.info("Approval detected, resuming pending plan")
            _emit_status(args.task_id, "Resuming...", "play.circle")
            try:
                plan = json.loads(pending_plan_file.read_text(encoding="utf-8"))
                plan = _mark_protected_plan_approved(plan)
                _record_external_action("unlink", str(pending_plan_file))
                pending_plan_file.unlink()
                plan = _enrich_plan_with_runtime_policy(plan)
                dispatch_hops += _plan_agent_handoffs(plan)
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

    dispatch_hops += _plan_agent_handoffs(plan)
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
