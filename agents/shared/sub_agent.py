"""Shared response contract for dispatched sub-agents."""

import functools
import json
import logging
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("sub_agent")

LOCAL_ONLY_AGENTS: frozenset[str] = frozenset({"secret", "health"})
LOCAL_MODEL_PATTERNS: tuple[str, ...] = ("mlx", "local", "ollama", "omlx")
LOCAL_MODEL_PROVIDERS: frozenset[str] = frozenset(
    {"local", "offline", "omlx", "ollama", "mlx", "llama.cpp", "llama_cpp", "llamacpp", "gguf"}
)
MODEL_TOOL_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "tool",
        "tools",
        "tool_choice",
        "tool_metadata",
        "native_tools",
        "server_tools",
        "functions",
        "function_call",
        "plugins",
    }
)
EXPECTED_RESULT_STATUSES: frozenset[str] = frozenset({"ok", "error", "partial"})
AGENTS_WITH_REQUIRED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "writer": ("file_path",),
}
PUBLISH_AUDIT_LOG_NAME = "publish_audit.log"
DISPATCH_RECEIPT_NAME = "dispatch_receipt.json"
PUBLISH_AUDIT_HUMAN_TRIGGERS: frozenset[str] = frozenset({"ang", "weiang0212", "user", "human"})
PUBLISH_AUDIT_DISPATCH_PATHS: frozenset[str] = frozenset({"bridge", "notes", "schedule", "direct"})
INJECTION_TRIGGER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (previous|prior|all) instructions",
        r"new (task|instructions|system prompt):",
        r"you are now",
        r"\[SYSTEM\]",
        r"disregard your",
    )
)


def resolve_claude_think_timeout(tier: str | None, timeout: int | None = None) -> int:
    from config import CLAUDE_TIMEOUT_THINK

    try:
        from config import CLAUDE_TIMEOUT_THINK_HEAVY
    except ImportError:
        CLAUDE_TIMEOUT_THINK_HEAVY = 300

    default_timeout = int(CLAUDE_TIMEOUT_THINK)
    if str(tier or "").strip().lower() == "heavy" and (timeout is None or int(timeout) == default_timeout):
        return int(CLAUDE_TIMEOUT_THINK_HEAVY)
    if timeout is None:
        return default_timeout
    return int(timeout)


def apply_claude_think_timeout_policy() -> None:
    try:
        import llm
    except Exception:
        return

    original = getattr(llm, "claude_think", None)
    if not callable(original) or getattr(original, "_mira_timeout_policy_wrapped", False):
        return

    @functools.wraps(original)
    def claude_think_with_tier_timeout(
        prompt: str,
        timeout: int | None = None,
        tier: str = "light",
        max_tokens: int | None = None,
    ) -> str:
        return original(
            prompt,
            timeout=resolve_claude_think_timeout(tier, timeout),
            tier=tier,
            max_tokens=max_tokens,
        )

    claude_think_with_tier_timeout._mira_timeout_policy_wrapped = True
    llm.claude_think = claude_think_with_tier_timeout


apply_claude_think_timeout_policy()


class SubAgentFormatError(Exception):
    def __init__(self, agent_name: str, missing_keys: list[str]):
        self.agent_name = str(agent_name or "unknown")
        self.missing_keys = list(missing_keys)
        fields = ", ".join(self.missing_keys) or "unknown"
        super().__init__(f"{self.agent_name} returned malformed result; missing or invalid fields: {fields}")


class SecurityError(Exception):
    def __init__(self, pattern: str):
        self.pattern = pattern
        super().__init__(f"Skill text blocked by security scan: matched pattern {pattern!r}")


_SKILL_SECURITY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bimport\s+requests\b",
        r"\bimport\s+socket\b",
        r"\bimport\s+subprocess\b",
        r"\bimport\s+urllib\b",
        r"\bos\.system\s*\(",
        r"\bsubprocess\.",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\b__import__\s*\(",
    )
)


def quick_security_scan(skill_text: str) -> None:
    """Secondary pattern-based check; does not replace the auditor approval."""
    text = str(skill_text or "")
    for pattern in _SKILL_SECURITY_PATTERNS:
        if pattern.search(text):
            raise SecurityError(pattern.pattern)


_EVALUATOR_PLACEHOLDER_STRINGS: frozenset[str] = frozenset(
    {"n/a", "na", "none", "no issues", "no issue", "not applicable", "null", "nil", "todo", "tbd"}
)
_EVALUATOR_BOUNDED_SCORE_KEYS: frozenset[str] = frozenset(
    {
        "score",
        "task_success",
        "success_rate",
        "guard_fire_rate",
        "overall_success_rate",
        "outcome_coverage",
        "crash_rate",
        "routing_accuracy",
        "timeout_rate",
        "error_rate",
        "scaffolding_rejection_rate",
        "proxy_false_positive_ratio",
    }
)


def _iter_nested_values(value, path: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _iter_nested_values(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_nested_values(child, (*path, str(index)))
    else:
        yield path, value


def _nested_value(value, path: tuple[str, ...]):
    current = value
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def _evaluator_score_range(path: tuple[str, ...], score_ranges: dict[str, tuple[float, float]]):
    dotted = ".".join(path)
    key = path[-1] if path else ""
    if dotted in score_ranges:
        return score_ranges[dotted]
    if key in score_ranges:
        return score_ranges[key]
    if key in _EVALUATOR_BOUNDED_SCORE_KEYS:
        return 0.0, 1.0
    if key.endswith("_rate") and not key.endswith("_per_hour"):
        return 0.0, 1.0
    if key.endswith("_ratio") or key.endswith("_coverage") or key.endswith("_score"):
        return 0.0, 1.0
    return None


def _token_count(value: str) -> int:
    return len(re.findall(r"\b\w+\b", value))


def _is_placeholder_string(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", value.strip().lower()).strip(" .:-_*")
    return normalized in _EVALUATOR_PLACEHOLDER_STRINGS


def evaluator_output_sanity_failures(
    payload: dict,
    *,
    required_string_paths: tuple[tuple[str, ...], ...] = (),
    min_string_tokens: int = 4,
    min_agent_score_std_dev: float = 0.001,
    score_ranges: dict[str, tuple[float, float]] | None = None,
) -> list[str]:
    failures: list[str] = []
    ranges = score_ranges or {}

    for path, value in _iter_nested_values(payload):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        declared_range = _evaluator_score_range(path, ranges)
        if declared_range is None:
            continue
        low, high = declared_range
        if not low <= float(value) <= high:
            failures.append(f"{'.'.join(path)}={value!r} outside declared range [{low}, {high}]")

    agent_scores: list[float] = []
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if isinstance(agents, dict):
        for agent_name, card in agents.items():
            if not isinstance(card, dict) or card.get("task_count", 0) <= 0:
                continue
            score = card.get("success_rate")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                continue
            agent_scores.append(float(score))
        if len(agent_scores) >= 3:
            mean = sum(agent_scores) / len(agent_scores)
            variance = sum((score - mean) ** 2 for score in agent_scores) / len(agent_scores)
            std_dev = variance**0.5
            if std_dev < min_agent_score_std_dev:
                failures.append(
                    f"agent success_rate variance too low: std_dev={std_dev:.6f}, "
                    f"threshold={min_agent_score_std_dev}"
                )

    for path in required_string_paths:
        value = _nested_value(payload, path)
        label = ".".join(path)
        if not isinstance(value, str) or _is_placeholder_string(value) or _token_count(value) < min_string_tokens:
            failures.append(f"{label} is empty, placeholder, or below {min_string_tokens} tokens")

    return failures


def _validate_result(result: dict, agent_name: str) -> None:
    missing_keys: list[str] = []
    output = result.get("output")
    if (
        "output" not in result
        or output is None
        or (isinstance(output, str) and not output.strip())
        or (hasattr(output, "__len__") and not isinstance(output, str) and len(output) == 0)
    ):
        missing_keys.append("output")

    status = str(result.get("status") or "").strip().lower()
    if "status" not in result or status not in EXPECTED_RESULT_STATUSES:
        missing_keys.append("status")

    required_artifacts = AGENTS_WITH_REQUIRED_ARTIFACTS.get(str(agent_name or "").strip().lower(), ())
    for key in required_artifacts:
        value = result.get(key)
        if key not in result or value in (None, "", [], {}):
            missing_keys.append(key)

    if missing_keys:
        raise SubAgentFormatError(agent_name, missing_keys)


def validate_local_model_native_tools(
    backend_config: dict | None = None,
    tool_metadata=None,
    logger=None,
) -> None:
    allow_native_tools, denylist = _model_native_tool_policy()
    if allow_native_tools:
        return

    sources: list[tuple[str, object]] = []
    if backend_config is None:
        sources.extend(_configured_local_model_tool_sources())
    else:
        sources.append(("backend_config", backend_config))
    if tool_metadata is not None:
        sources.append(("tool_metadata", tool_metadata))

    unsafe_tools: set[str] = set()
    for _, source in sources:
        unsafe_tools.update(_native_tool_names(source, denylist))

    if unsafe_tools:
        tools = ", ".join(sorted(unsafe_tools))
        err = (
            f"Unsafe local model native tools configured: {tools}. "
            "Disable server-side tools and route actions through Mira's agent/tool layer."
        )
        (logger or log).error("LOCAL_MODEL_NATIVE_TOOLS: %s", err)
        raise RuntimeError(err)


REASONING_RESPONSE_REQUIREMENT = """\
## Response Contract
Return a JSON object with these required top-level fields:
- "reasoning": a concise audit trail explaining the decision path, assumptions, and checks performed.
- "output": the user-facing result.

For live external-service tasks handled by surfer, socialmedia, or explorer, also include:
- "outcome_verified": true only when the external outcome was checked after the action.
- "verification_method": a short method label such as "file_exists", "url_reachable", "response_200", "publish_url_confirmed", or "non_empty_parseable".
"""


REASONING_REWRITE_PROMPT = """\
The task result below is missing the required top-level "reasoning" field.

Rewrite it as a JSON object with these required fields:
- "reasoning": a concise audit trail explaining the decision path, assumptions, and checks performed.
- "output": the original user-facing result, preserved without adding new claims.

Preserve "outcome_verified" and "verification_method" if they are present in the original result.

Return only valid JSON.

Task result:
{response}
"""


def append_pipeline_context_to_system_prompt(system_prompt: str, pipeline_context: dict | None = None) -> str:
    text = str(system_prompt or "")
    if not pipeline_context:
        return text.strip()
    if "You are operating as part of a pipeline." in text:
        return text.strip()
    upstream_output = str(pipeline_context.get("upstream_output") or "").strip()
    downstream_expects = str(pipeline_context.get("downstream_expects") or "").strip()
    shared_goal = str(pipeline_context.get("shared_goal") or "").strip()
    pipeline_block = (
        "You are operating as part of a pipeline. "
        f"Upstream produced: {upstream_output}. "
        f"Downstream expects: {downstream_expects}. "
        f"Shared goal: {shared_goal}. "
        "Optimize for the full pipeline outcome, not only this task."
    )
    return f"{text.rstrip()}\n\n{pipeline_block}".strip()


def append_original_request_to_instruction(instruction: str, original_request: str | None = None) -> str:
    text = str(instruction or "")
    raw_request = str(original_request or "").strip()
    if not raw_request or "Original user request (pre-orchestrator):" in text:
        return text.strip()
    return f"{text.rstrip()}\n\nOriginal user request (pre-orchestrator): {raw_request}".strip()


@functools.lru_cache(maxsize=1)
def _original_request_from_worker_payload() -> str:
    try:
        index = sys.argv.index("--msg-file")
        msg_file = Path(sys.argv[index + 1])
        payload = json.loads(msg_file.read_text(encoding="utf-8"))
    except (ValueError, IndexError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("original_request", "raw_input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def assert_local_only_agent_model(
    agent: str | None, model_name: str | None = None, endpoint: str | None = None, logger=None
) -> str:
    agent_name = str(agent or "")
    resolved_model = str(model_name or _current_model_policy() or "omlx")
    if agent_name not in LOCAL_ONLY_AGENTS:
        return resolved_model
    resolved_endpoint = str(endpoint or _endpoint_for_model(resolved_model) or "")
    if not _is_local_model_endpoint(resolved_endpoint):
        from config import ConfigError

        err = f"Agent {agent_name} requires local model substrate — endpoint {resolved_endpoint} is remote."
        (logger or log).error("LOCAL_ONLY_POLICY: %s", err)
        raise ConfigError(err)
    validate_local_model_native_tools(logger=logger or log)
    if any(pattern in resolved_model.lower() for pattern in LOCAL_MODEL_PATTERNS):
        return resolved_model
    err = f"Refused to route task to {agent_name}: cloud model detected, local-only policy violated."
    (logger or log).error("LOCAL_ONLY_POLICY: %s (resolved_model=%r)", err, resolved_model)
    raise RuntimeError(err)


def require_reasoning_in_instruction(instruction: str, pipeline_context: dict | None = None) -> str:
    text = str(instruction or "")
    original_request = None
    if isinstance(pipeline_context, dict):
        original_request = pipeline_context.get("original_request")
    if original_request is None:
        original_request = _original_request_from_worker_payload()
    text = append_original_request_to_instruction(text, original_request)
    if '"reasoning"' not in text or '"output"' not in text or "Response Contract" not in text:
        text = f"{text.rstrip()}\n\n{REASONING_RESPONSE_REQUIREMENT}".strip()
    return append_pipeline_context_to_system_prompt(text, pipeline_context)


def extract_reasoning_payload(text: str, agent_name: str | None = None) -> tuple[str, str] | None:
    sanitized_text = _sanitize_output(str(text or ""), agent_name)
    for candidate in _json_candidates(sanitized_text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        reasoning = str(payload.get("reasoning") or "").strip()
        if not reasoning:
            continue
        output = payload.get("output")
        if output is None:
            output = payload.get("response", payload.get("result", payload.get("answer", "")))
        if isinstance(output, (dict, list)):
            output_text = json.dumps(output, ensure_ascii=False, indent=2)
        else:
            output_text = str(output or "").strip()
        return reasoning, output_text
    return None


def token_usage_from_response(response) -> dict | None:
    usage = _usage_field(response)
    if usage is None:
        return None
    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    if input_tokens is None or output_tokens is None:
        return None
    return {"input": input_tokens, "output": output_tokens}


def task_log_tokens_from_counts(
    input_tokens: int | None, output_tokens: int | None, model_id: str | None = None
) -> dict | None:
    try:
        tokens = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
    except (TypeError, ValueError):
        return None
    tokens["model"] = str(model_id or "")
    return tokens


def task_log_tokens_from_response(response, model_id: str | None = None) -> dict | None:
    token_usage = token_usage_from_response(response)
    if token_usage is None:
        return None
    return task_log_tokens_from_counts(
        token_usage["input"],
        token_usage["output"],
        model_id or _response_value(response, "model"),
    )


def append_tokens_to_log_entry(entry: dict, response, model_id: str | None = None) -> dict:
    tokens = task_log_tokens_from_response(response, model_id)
    if tokens is not None:
        entry["tokens"] = tokens
    return entry


def log_token_usage(agent_name: str, task_type: str, model_id: str | None, response) -> dict | None:
    tokens = task_log_tokens_from_response(response, model_id)
    if tokens is None:
        return None
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent_name": str(agent_name or "unknown"),
        "model": tokens["model"],
        "input_tokens": tokens["input_tokens"],
        "output_tokens": tokens["output_tokens"],
        "task_type": str(task_type or "unknown"),
    }
    try:
        from config import TOKEN_USAGE_LOG

        TOKEN_USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TOKEN_USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return None
    return record


record_token_usage = log_token_usage


def audit_action(agent_name, action_type, target, justification) -> dict | None:
    justification_text = str(justification or "").strip() or "unjustified"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": str(agent_name or "unknown"),
        "action": str(action_type or "unknown"),
        "target": str(target or ""),
        "justification": justification_text,
    }
    try:
        from config import AUDIT_LOG_PATH, MIRA_ROOT

        log_path = Path(AUDIT_LOG_PATH)
        if not log_path.is_absolute():
            log_path = MIRA_ROOT / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return None
    return record


def _agent_tier(agent_name: str | None) -> str:
    agent = str(agent_name or "").strip().lower()
    try:
        from config import AGENT_REGISTRY
    except Exception:
        return ""
    agent_config = AGENT_REGISTRY.get(agent) if isinstance(AGENT_REGISTRY, dict) else None
    if not isinstance(agent_config, dict):
        return ""
    return str(agent_config.get("tier") or "").strip().lower()


def _audit_enabled_for_agent(agent_name: str | None, tier: str | None = None) -> bool:
    try:
        from config import AGENT_AUDIT_MODE
    except Exception:
        AGENT_AUDIT_MODE = True
    if not bool(AGENT_AUDIT_MODE):
        return False
    resolved_tier = str(tier or "").strip().lower() or _agent_tier(agent_name)
    return resolved_tier == "heavy"


def _audit_heavy_action(agent_name, tier, action_type, target, justification) -> dict | None:
    if not _audit_enabled_for_agent(agent_name, tier):
        return None
    return audit_action(agent_name, action_type, target, justification)


def result_with_inference_timing(result, inference_ms: int):
    inference_ms = max(0, int(inference_ms))
    if isinstance(result, dict):
        timed_result = dict(result)
        timed_result["inference_ms"] = inference_ms
        return timed_result
    return result, inference_ms


def _log_model_drift(response) -> None:
    actual_model = _response_value(response, "model")
    log.debug("model_actual=%s", actual_model)
    if actual_model is None:
        return
    try:
        from config import CLAUDE_SONNET_MODEL as expected_model
    except Exception:
        return
    if actual_model != expected_model:
        log.warning("model_mismatch expected=%s actual=%s", expected_model, actual_model)


def timed_llm_api_call(
    call,
    *args,
    task_id: str = "",
    total_start: float | None = None,
    timing: dict | None = None,
    **kwargs,
):
    if timing is None:
        timing = {"inference_ms": 0}
    llm_start = time.perf_counter()
    _audit_heavy_action(
        kwargs.get("agent_id") or kwargs.get("agent_name") or kwargs.get("agent"),
        kwargs.get("tier"),
        "network",
        getattr(call, "__name__", "llm_api_call"),
        kwargs.get("audit_justification") or kwargs.get("justification") or "",
    )
    try:
        result = call(*args, **kwargs)
        _log_model_drift(result)
    finally:
        finished = time.perf_counter()
        task_start = llm_start if total_start is None else total_start
        inference_ms = round((finished - llm_start) * 1000)
        timing["inference_ms"] = max(0, int(timing.get("inference_ms") or 0)) + inference_ms
        log_sub_agent_timing(
            task_id,
            inference_ms,
            round((finished - task_start) * 1000),
        )
    return result_with_inference_timing(result, timing["inference_ms"])


def log_sub_agent_timing(task_id: str, inference_ms: int, total_ms: int) -> dict | None:
    total_ms = max(0, int(total_ms))
    inference_ms = max(0, min(int(inference_ms), total_ms))
    orchestration_ms = max(0, total_ms - inference_ms)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": str(task_id or ""),
        "inference_ms": inference_ms,
        "total_ms": total_ms,
        "orchestration_ms": orchestration_ms,
        "orchestration_pct": round((orchestration_ms / total_ms) * 100, 2) if total_ms else 0.0,
        "llm_ms": inference_ms,
        "llm_ratio": round(inference_ms / total_ms, 4) if total_ms else 0.0,
        "orchestration_ratio": round(orchestration_ms / total_ms, 4) if total_ms else 0.0,
    }
    try:
        from config import LOGS_DIR

        log_path = LOGS_DIR / "sub_agent_timing.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return None
    log.info("SUB_AGENT_TIMING %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    return record


@contextmanager
def timed_claude_api_call(task_id: str, total_start: float | None = None):
    timing = {"inference_ms": 0}
    llm_start = time.perf_counter()
    try:
        yield timing
    finally:
        finished = time.perf_counter()
        task_start = llm_start if total_start is None else total_start
        inference_ms = round((finished - llm_start) * 1000)
        timing["inference_ms"] += inference_ms
        log_sub_agent_timing(
            task_id,
            inference_ms,
            round((finished - task_start) * 1000),
        )


def write_dispatch_receipt(
    task_id: str,
    agent_name: str,
    task_description: str,
    workspace_dir: Path | str | None = None,
    *,
    reversible: bool = False,
) -> dict:
    task_id = str(task_id or "")
    if workspace_dir is None:
        from config import TASKS_DIR

        workspace = TASKS_DIR / task_id
    else:
        workspace = Path(workspace_dir)
    receipt = {
        "task_id": task_id,
        "agent_name": str(agent_name or "unknown"),
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "action_summary": str(task_description or "")[:200],
        "reversible": bool(reversible),
    }
    workspace.mkdir(parents=True, exist_ok=True)
    _audit_heavy_action(
        agent_name,
        None,
        "file_write",
        str(workspace / DISPATCH_RECEIPT_NAME),
        task_description,
    )
    (workspace / DISPATCH_RECEIPT_NAME).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return receipt


def infer_publish_dispatch_path(triggering_agent_name: str | None) -> str:
    agent = str(triggering_agent_name or "").strip().lower()
    if agent in PUBLISH_AUDIT_HUMAN_TRIGGERS:
        return "direct"
    if "bridge" in agent:
        return "bridge"
    if "notes" in agent:
        return "notes"
    if agent in {"agent", "mira", "schedule", "scheduler", "cron"}:
        return "schedule"
    return "schedule"


def log_publish_audit(
    triggering_agent_name: str | None,
    *,
    dispatch_path: str | None = None,
    autonomous: bool | None = None,
    action: str = "publish",
    platform: str = "",
    title: str = "",
    extra: dict | None = None,
) -> dict | None:
    agent_name = str(triggering_agent_name or "unknown")
    normalized_path = str(dispatch_path or infer_publish_dispatch_path(agent_name)).lower()
    if normalized_path not in PUBLISH_AUDIT_DISPATCH_PATHS:
        normalized_path = infer_publish_dispatch_path(agent_name)
    if autonomous is None:
        autonomous = agent_name.strip().lower() not in PUBLISH_AUDIT_HUMAN_TRIGGERS
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "triggering_agent_name": agent_name,
        "agent": agent_name,
        "dispatch_path": normalized_path,
        "autonomous": bool(autonomous),
        "user_initiated": not bool(autonomous),
        "origin": "internal_agent" if autonomous else "user_initiated",
        "action": str(action or "publish"),
        "platform": str(platform or ""),
        "title": str(title or ""),
    }
    if extra:
        entry.update(extra)
    try:
        from config import MIRA_ROOT

        log_path = MIRA_ROOT / "logs" / PUBLISH_AUDIT_LOG_NAME
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("publish_audit write failed: %s", e)
        return None
    return entry


def _sanitize_output(text: str, agent_name: str | None = None) -> str:
    sanitized = str(text or "")
    preview = sanitized[:200]
    agent = str(agent_name or "unknown")
    for pattern in INJECTION_TRIGGER_PATTERNS:
        if pattern.search(sanitized):
            log.warning(
                "OUTPUT_INJECTION_PATTERN agent=%s pattern=%r preview=%r",
                agent,
                pattern.pattern,
                preview,
            )
            sanitized = pattern.sub("[REDACTED:injection-pattern]", sanitized)
    return sanitized


def _json_candidates(text: str) -> list[str]:
    stripped = text.strip()
    candidates = []
    if stripped:
        candidates.append(stripped)
    candidates.extend(
        match.group(1).strip() for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    )
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    return candidates


def _usage_field(response):
    return _response_value(response, "usage")


def _response_value(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _usage_value(usage, key: str) -> int | None:
    value = _response_value(usage, key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _current_model_policy() -> str | None:
    try:
        from llm import _model_policy
    except Exception:
        return None
    return getattr(_model_policy, "value", None)


def _model_native_tool_policy() -> tuple[bool, set[str]]:
    try:
        from agents.shared.config import MIRA_ALLOW_MODEL_NATIVE_TOOLS, MODEL_NATIVE_TOOL_DENYLIST
    except Exception:
        MIRA_ALLOW_MODEL_NATIVE_TOOLS = False
        MODEL_NATIVE_TOOL_DENYLIST = {"shell", "edit_file", "filesystem", "python", "exec"}
    return bool(MIRA_ALLOW_MODEL_NATIVE_TOOLS), {_normalize_tool_name(item) for item in MODEL_NATIVE_TOOL_DENYLIST}


def _configured_local_model_tool_sources() -> list[tuple[str, object]]:
    try:
        import config as runtime_config
    except Exception:
        return []

    sources: list[tuple[str, object]] = []
    cfg = getattr(runtime_config, "_cfg", {})
    if isinstance(cfg, dict):
        for key in ("omlx", "ollama", "local_model", "local_llm", "llama_cpp", "llamacpp"):
            section = cfg.get(key)
            if isinstance(section, dict):
                sources.append((f"config.{key}", section))

    models = getattr(runtime_config, "MODELS", {})
    if isinstance(models, dict):
        for name, model_config in models.items():
            if not isinstance(model_config, dict):
                continue
            provider = _normalize_tool_name(model_config.get("provider") or name)
            if provider in LOCAL_MODEL_PROVIDERS:
                sources.append((f"MODELS.{name}", model_config))
    return sources


def _native_tool_names(value, denylist: set[str], *, in_tool_context: bool = False) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = _normalize_tool_name(raw_key)
            enabled = _metadata_value_enabled(raw_value)
            if key in denylist and enabled:
                names.add(key)
            next_context = in_tool_context or key in MODEL_TOOL_METADATA_KEYS or key in {"function", "name"}
            if in_tool_context and key in {"name", "tool_name", "function_name"}:
                names.update(_native_tool_names(raw_value, denylist, in_tool_context=True))
            names.update(_native_tool_names(raw_value, denylist, in_tool_context=next_context))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            names.update(_native_tool_names(item, denylist, in_tool_context=in_tool_context))
    elif isinstance(value, str) and in_tool_context:
        name = _normalize_tool_name(value)
        if name in denylist:
            names.add(name)
    return names


def _metadata_value_enabled(value) -> bool:
    if value in (None, False, "", [], {}):
        return False
    if isinstance(value, dict):
        if value.get("enabled") is False or value.get("disabled") is True:
            return False
    return True


def _normalize_tool_name(value) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _endpoint_for_model(model_name: str | None) -> str:
    route = str(model_name or "").strip().lower()
    if route.startswith(("http://", "https://", "/")):
        return route
    try:
        from config import DEEPSEEK_API_ENDPOINT, MODELS, OMLX_API_ENDPOINT, OPENAI_API_ENDPOINT
    except Exception:
        return ""
    cfg = MODELS.get(route, {}) if isinstance(MODELS, dict) else {}
    provider = str(cfg.get("provider") or route).strip().lower()
    if provider == "omlx" or route in {"omlx", "ollama", "local", "localllm"}:
        return str(OMLX_API_ENDPOINT)
    if provider in {"codex_cli", "openai"}:
        return str(OPENAI_API_ENDPOINT)
    if provider == "deepseek":
        return str(DEEPSEEK_API_ENDPOINT)
    if provider == "gemini":
        return "https://generativelanguage.googleapis.com"
    if provider == "claude":
        return "https://api.anthropic.com"
    return ""


def _is_local_model_endpoint(endpoint: str) -> bool:
    value = str(endpoint or "").strip()
    try:
        from config import LOCAL_MODEL_ENDPOINT_ALLOWLIST
    except Exception:
        LOCAL_MODEL_ENDPOINT_ALLOWLIST = ["localhost", "127.0.0.1", "::1"]
    host_patterns = []
    for host in LOCAL_MODEL_ENDPOINT_ALLOWLIST:
        normalized = str(host or "").strip()
        if not normalized:
            continue
        if normalized == "::1":
            host_patterns.append(r"\[::1\]")
        else:
            host_patterns.append(re.escape(normalized))
    if not host_patterns:
        host_patterns = [r"localhost", r"127\.0\.0\.1", r"\[::1\]"]
    return bool(re.match(rf"^(https?://({'|'.join(host_patterns)})|/)", value))
