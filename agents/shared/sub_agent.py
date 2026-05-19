"""Shared response contract for dispatched sub-agents."""

import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("sub_agent")

LOCAL_ONLY_AGENTS: frozenset[str] = frozenset({"secret", "health"})
LOCAL_MODEL_PATTERNS: tuple[str, ...] = ("mlx", "local", "ollama", "omlx")
PUBLISH_AUDIT_LOG_NAME = "publish_audit.log"
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


def assert_local_only_agent_model(agent: str | None, model_name: str | None = None, logger=None) -> str:
    agent_name = str(agent or "")
    resolved_model = str(model_name or _current_model_policy() or "omlx")
    if agent_name not in LOCAL_ONLY_AGENTS:
        return resolved_model
    if any(pattern in resolved_model.lower() for pattern in LOCAL_MODEL_PATTERNS):
        return resolved_model
    err = f"Refused to route task to {agent_name}: cloud model detected, local-only policy violated."
    (logger or log).error("LOCAL_ONLY_POLICY: %s (resolved_model=%r)", err, resolved_model)
    raise RuntimeError(err)


def require_reasoning_in_instruction(instruction: str, pipeline_context: dict | None = None) -> str:
    text = str(instruction or "")
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
