"""Shared response contract for dispatched sub-agents."""

import json
import logging
import re

log = logging.getLogger("sub_agent")

LOCAL_ONLY_AGENTS: frozenset[str] = frozenset({"secret", "health"})
LOCAL_MODEL_PATTERNS: tuple[str, ...] = ("mlx", "local", "ollama", "omlx")


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


def extract_reasoning_payload(text: str) -> tuple[str, str] | None:
    for candidate in _json_candidates(str(text or "")):
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


def _current_model_policy() -> str | None:
    try:
        from llm import _model_policy
    except Exception:
        return None
    return getattr(_model_policy, "value", None)
