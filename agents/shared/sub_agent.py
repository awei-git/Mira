"""Shared response contract for dispatched sub-agents."""

import contextvars
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
REQUIRE_DECISION_TRAIL: bool = True
AGENTS_WITH_REQUIRED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "writer": ("file_path",),
}
PUBLISH_AUDIT_LOG_NAME = "publish_audit.log"
DISPATCH_RECEIPT_NAME = "dispatch_receipt.json"
PUBLISH_AUDIT_HUMAN_TRIGGERS: frozenset[str] = frozenset({"default", "user", "human"})
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
_JUDGMENT_PATTERN: re.Pattern[str] = re.compile(
    r"\b(best|must|should|important|recommend|crucial|decided|right choice)\b",
    re.IGNORECASE,
)
_SCOPE_WRITE_VERB_PATTERN: re.Pattern[str] = re.compile(
    r"\b(append|change|create|delete|edit|generate|modify|move|overwrite|remove|rename|scaffold|touch|update|write)\b",
    re.IGNORECASE,
)
_SCOPE_INSTALL_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:python\s+-m\s+pip|pip3?|uv|poetry|npm|pnpm|yarn|brew|apt(?:-get)?|cargo|gem)\s+"
    r"(?:install|add)\b|\binstall(?:ing)?\s+(?:a\s+)?(?:dependency|package|library|module)\b",
    re.IGNORECASE,
)
_SCOPE_CONFIG_PATTERN: re.Pattern[str] = re.compile(
    r"\b(config|configuration|settings|env file|dotenv|pyproject\.toml|package\.json|"
    r"requirements(?:-[\w-]+)?\.txt|poetry\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)\b",
    re.IGNORECASE,
)
_SCOPE_OUTSIDE_WORKSPACE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:outside|beyond)\s+(?:the\s+)?(?:task\s+)?workspace\b|"
    r"(?:^|[\s\"'`])(?:\.\./|~/|/etc/|/usr/local/|/opt/homebrew/|/var/)",
    re.IGNORECASE,
)
_SCOPE_CROSS_AGENT_PATTERN: re.Pattern[str] = re.compile(
    r"\bcross-agent\b|\bagents/[^/\s]+/",
    re.IGNORECASE,
)
_SCOPE_FILE_CREATE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:add|create|generate|scaffold|touch|write)\b.*\b(?:artifact|config|director(?:y|ies)|file|files|folder)\b",
    re.IGNORECASE,
)
_SCOPE_TELEMETRY_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:audit|dispatch receipt|judgment claims|log|logging|publish audit|telemetry|timing|token usage)\b",
    re.IGNORECASE,
)
_AGENT_AUDIT_MODES: frozenset[str] = frozenset({"off", "log", "log+confirm"})
_AGENT_AUDIT_LOG_FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off", "disabled"})
_HIGH_RISK_DELIBERATION_ACTIONS: frozenset[str] = frozenset(
    {"publish", "network_publish", "config_change", "file_delete", "state_change", "system_state_change"}
)
_ACTION_SCOPE_ALIASES: dict[str, str] = {
    "api_call": "network_call",
    "config_change": "modify_config",
    "file_delete": "file_write",
    "network": "network_call",
    "network_publish": "network_call",
    "package_install": "install_package",
    "publish": "network_call",
    "system_state_change": "modify_config",
}
_deliberation_agent_name = contextvars.ContextVar("deliberation_agent_name", default="unknown")


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


def _load_agent_local_override(agent_name: str | None):
    agent = str(agent_name or "").strip()
    if not agent:
        return None
    try:
        from config import _load_local_override
    except Exception:
        try:
            from agents.shared.config import _load_local_override
        except Exception:
            return None
    return _load_local_override(agent)


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


class ScopeEscalationError(RuntimeError):
    pass


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


def check_action_scope(original_task_spec, proposed_action) -> str:
    original_text = _scope_text(original_task_spec)
    action_text = _scope_text(proposed_action)
    if not action_text.strip():
        return "in_scope"

    reasons: list[str] = []
    is_write_action = bool(_SCOPE_WRITE_VERB_PATTERN.search(action_text))
    is_telemetry = bool(_SCOPE_TELEMETRY_PATTERN.search(action_text))

    if _SCOPE_INSTALL_PATTERN.search(action_text) and not _scope_explicitly_requested(
        original_text, "install", "dependency", "package", "library", "module"
    ):
        reasons.append("package installation was not explicit in the task")

    if (
        is_write_action
        and _SCOPE_CONFIG_PATTERN.search(action_text)
        and not _scope_explicitly_requested(
            original_text,
            "config",
            "configuration",
            "settings",
            "env file",
            "dotenv",
            "pyproject.toml",
            "package.json",
            "requirements",
        )
    ):
        reasons.append("config modification was not explicit in the task")

    if (
        is_write_action
        and _SCOPE_OUTSIDE_WORKSPACE_PATTERN.search(action_text)
        and not _scope_explicitly_requested(original_text, "outside workspace", "outside the task workspace")
    ):
        reasons.append("filesystem write appears outside the task workspace")

    if (
        is_write_action
        and _SCOPE_CROSS_AGENT_PATTERN.search(action_text)
        and not _scope_explicitly_requested(original_text, "cross-agent", "agents/")
    ):
        reasons.append("cross-agent filesystem write was not explicit in the task")

    borderline_reasons: list[str] = []
    if (
        is_write_action
        and not reasons
        and not is_telemetry
        and _SCOPE_FILE_CREATE_PATTERN.search(action_text)
        and not _scope_explicitly_requested(original_text, "create", "write", "file", "artifact", "output")
    ):
        borderline_reasons.append("file creation was not explicit in the task")

    classification = "out_of_scope" if reasons else "borderline" if borderline_reasons else "in_scope"
    if classification != "in_scope":
        _handle_scope_escalation(original_text, action_text, classification, reasons or borderline_reasons)
    return classification


def _scope_text(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(value)
    return str(value or "")


def _scope_explicitly_requested(original_text: str, *needles: str) -> bool:
    normalized = original_text.lower()
    return any(str(needle or "").lower() in normalized for needle in needles)


def _scope_escalation_mode() -> str:
    try:
        from config import SCOPE_ESCALATION_MODE
    except Exception:
        try:
            from agents.shared.config import SCOPE_ESCALATION_MODE
        except Exception:
            SCOPE_ESCALATION_MODE = "log_only"
    mode = str(SCOPE_ESCALATION_MODE or "log_only").strip().lower()
    if mode not in {"log_only", "warn", "block"}:
        log.warning("SCOPE_ESCALATION invalid mode=%r; defaulting to log_only", mode)
        return "log_only"
    return mode


def _handle_scope_escalation(
    original_task_spec: str,
    proposed_action: str,
    classification: str,
    reasons: list[str],
) -> None:
    mode = _scope_escalation_mode()
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "classification": classification,
        "reasons": reasons,
        "original_task_spec": original_task_spec,
        "proposed_action": proposed_action,
    }
    log.warning("SCOPE_ESCALATION %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    if mode in {"warn", "block"}:
        warning = (
            f"SCOPE_ESCALATION_WARNING classification={classification} mode={mode} "
            f"reasons={'; '.join(reasons)} proposed_action={proposed_action}"
        )
        print(warning, file=sys.stderr)
    if mode == "block":
        raise ScopeEscalationError(
            "Scope escalation blocked; explicit user approval is required before this action can proceed."
        )


def _normalize_action_scope_type(action_type: str | None) -> str:
    action = str(action_type or "").strip().lower().replace("-", "_")
    if action in _ACTION_SCOPE_ALIASES:
        return _ACTION_SCOPE_ALIASES[action]
    if "network" in action or "api_call" in action:
        return "network_call"
    if "install" in action and ("package" in action or "dependency" in action):
        return "install_package"
    if "config" in action or "setting" in action:
        return "modify_config"
    if action.startswith("file_") or action in {"append_log", "create_file", "delete_file", "write"}:
        return "file_write"
    return action


def _configured_agent_action_scope() -> dict:
    try:
        from config import AGENT_ACTION_SCOPE
    except Exception:
        try:
            from agents.shared.config import AGENT_ACTION_SCOPE
        except Exception:
            AGENT_ACTION_SCOPE = {}
    return AGENT_ACTION_SCOPE if isinstance(AGENT_ACTION_SCOPE, dict) else {}


def _normalize_agent_scope_name(agent_name: str | None) -> str:
    agent = str(agent_name or "").strip().lower().replace("-", "_")
    if not agent:
        return "unknown"
    try:
        from config import AGENT_ALIASES
    except Exception:
        AGENT_ALIASES = {
            "writing": "writer",
            "briefing": "explorer",
            "publish": "socialmedia",
        }
    alias = AGENT_ALIASES.get(agent) if isinstance(AGENT_ALIASES, dict) else None
    if alias:
        return str(alias).strip().lower().replace("-", "_") or "unknown"
    if "." in agent:
        root = agent.split(".", 1)[0]
        root_alias = AGENT_ALIASES.get(root) if isinstance(AGENT_ALIASES, dict) else None
        root = str(root_alias or root).strip().lower().replace("-", "_")
        if root in _configured_agent_action_scope():
            return root or "unknown"
    return agent or "unknown"


def enforce_scope(action_type: str, agent_name: str | None = None) -> bool:
    raw_agent = str(agent_name or _deliberation_agent_name.get() or "unknown").strip().lower()
    agent = _normalize_agent_scope_name(raw_agent)
    action = _normalize_action_scope_type(action_type)
    scope = _configured_agent_action_scope()
    permitted = {_normalize_action_scope_type(item) for item in scope.get(agent, []) if str(item or "").strip()}
    if action in permitted:
        return True

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "raw_agent": raw_agent,
        "action_type": action,
        "requested_action_type": str(action_type or ""),
        "permitted_actions": sorted(permitted),
    }
    log.warning("AGENT_SCOPE_VIOLATION %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    raise ScopeEscalationError(
        f"Agent {agent!r} is not permitted to perform {action!r}; explicit user confirmation is required."
    )


def _agent_audit_mode() -> str:
    try:
        from config import AGENT_AUDIT_MODE
    except Exception:
        AGENT_AUDIT_MODE = "off"
    if isinstance(AGENT_AUDIT_MODE, bool):
        return "log" if AGENT_AUDIT_MODE else "off"
    mode = str(AGENT_AUDIT_MODE or "off").strip().lower()
    if mode not in _AGENT_AUDIT_MODES:
        log.warning("AGENT_AUDIT_MODE invalid mode=%r; defaulting to off", mode)
        return "off"
    return mode


def _deliberation_log_path() -> Path:
    try:
        from config import DELIBERATION_LOG_PATH, MIRA_ROOT
    except Exception:
        DELIBERATION_LOG_PATH = "Mira/logs/deliberation.jsonl"
        MIRA_ROOT = Path.cwd()
    root = Path(MIRA_ROOT)
    path = Path(str(DELIBERATION_LOG_PATH or "Mira/logs/deliberation.jsonl")).expanduser()
    if path.is_absolute():
        return path
    raw = path.as_posix()
    if raw.startswith("Mira/") and root.name == "Mira":
        path = Path(raw[len("Mira/") :])
    return root / path


def _is_high_risk_deliberation(action_type: str, target: str) -> bool:
    action = str(action_type or "").strip().lower()
    target_text = str(target or "").strip().lower()
    return action in _HIGH_RISK_DELIBERATION_ACTIONS or (
        action == "file_write" and ("config.py" in target_text or "/config/" in target_text)
    )


def _self_audit_deliberation(record: dict) -> None:
    log.warning("DELIBERATION_SELF_AUDIT %s", json.dumps(record, ensure_ascii=False, sort_keys=True))
    time.sleep(0.2)


def _log_deliberation(action_type, target, reasoning) -> dict | None:
    mode = _agent_audit_mode()
    if mode == "off":
        return None
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": str(_deliberation_agent_name.get() or "unknown"),
        "action_type": str(action_type or "unknown"),
        "target": str(target or ""),
        "reasoning": str(reasoning or "").strip() or "No reasoning provided.",
    }
    if mode == "log+confirm" and _is_high_risk_deliberation(record["action_type"], record["target"]):
        _self_audit_deliberation(record)
    try:
        log_path = _deliberation_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return None
    return record


def _log_deliberation_for_agent(agent_name, action_type, target, reasoning) -> dict | None:
    token = _deliberation_agent_name.set(str(agent_name or "unknown"))
    try:
        return _log_deliberation(action_type, target, reasoning)
    finally:
        _deliberation_agent_name.reset(token)


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
    if isinstance(output, str):
        _scan_judgment_claims(output, agent_name)


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
    _load_agent_local_override(agent_name)
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
    enforce_scope("file_write", agent_name)
    check_action_scope(record, "append token usage log")
    try:
        from config import TOKEN_USAGE_LOG

        TOKEN_USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TOKEN_USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        return None
    return record


record_token_usage = log_token_usage


def _structured_audit_log_enabled() -> bool:
    try:
        from config import AUDIT_LOG_ENABLED
    except Exception:
        AUDIT_LOG_ENABLED = True
    return bool(AUDIT_LOG_ENABLED)


def _agent_decision_log_path() -> Path:
    try:
        from config import MIRA_ROOT
    except Exception:
        MIRA_ROOT = Path.cwd()
    return Path(MIRA_ROOT) / "logs" / "agent_decisions.jsonl"


def _permacomputing_audit_enabled() -> bool:
    try:
        from config import ENABLE_PERMACOMPUTING_AUDIT
    except Exception:
        ENABLE_PERMACOMPUTING_AUDIT = False
    return bool(ENABLE_PERMACOMPUTING_AUDIT)


def _permacomputing_audit_log_path() -> Path:
    try:
        from config import LOGS_DIR, MIRA_ROOT
    except Exception:
        LOGS_DIR = None
        MIRA_ROOT = Path.cwd()
    logs_dir = Path(LOGS_DIR) if LOGS_DIR else Path(MIRA_ROOT) / "logs"
    return logs_dir / "perma_audit.jsonl"


def _permacomputing_audit_text(value, limit: int) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(value)
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def log_permacomputing_audit(agent_name: str, task_summary, rationale) -> dict | None:
    if not _permacomputing_audit_enabled():
        return None
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_name": str(agent_name or "unknown"),
        "task_summary": _permacomputing_audit_text(task_summary, 500),
        "rationale": _permacomputing_audit_text(rationale, 700) or "No rationale captured.",
    }
    try:
        log_path = _permacomputing_audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("permacomputing audit write failed: %s", exc)
        return None
    return record


def audit_agent_decision(agent_name: str, action_type: str, reasoning: str, context: dict) -> dict | None:
    if not _structured_audit_log_enabled():
        return None
    reasoning_text = str(reasoning or "").strip()
    if not reasoning_text:
        raise ValueError("audit_agent_decision requires non-empty reasoning")
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": str(agent_name or "unknown"),
        "action_type": str(action_type or "unknown"),
        "reasoning": reasoning_text,
        "context": context if isinstance(context, dict) else {"value": str(context or "")},
    }
    try:
        log_path = _agent_decision_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as exc:
        log.debug("agent decision audit write failed: %s", exc)
        return None
    return record


def _agent_audit_log_enabled() -> bool:
    try:
        from config import AGENT_AUDIT_LOG
    except Exception:
        AGENT_AUDIT_LOG = True
    if isinstance(AGENT_AUDIT_LOG, bool):
        return AGENT_AUDIT_LOG
    if isinstance(AGENT_AUDIT_LOG, (int, float)):
        return bool(AGENT_AUDIT_LOG)
    if isinstance(AGENT_AUDIT_LOG, str):
        return AGENT_AUDIT_LOG.strip().lower() not in _AGENT_AUDIT_LOG_FALSE_VALUES
    return True


def _decision_audit_log_path(ts: datetime) -> Path:
    try:
        from config import LOGS_DIR, MIRA_ROOT
    except Exception:
        LOGS_DIR = None
        MIRA_ROOT = Path.cwd()
    logs_dir = Path(LOGS_DIR) if LOGS_DIR else Path(MIRA_ROOT) / "logs"
    return logs_dir / "decisions" / f"{ts.strftime('%Y-%m-%d')}.jsonl"


def log_decision(agent_name, action_type, target, reasoning, expected_outcome) -> dict | None:
    if not _agent_audit_log_enabled():
        return None
    enforce_scope(action_type, agent_name)
    ts = datetime.now(timezone.utc)
    record = {
        "timestamp": ts.isoformat(),
        "agent": str(agent_name or "unknown"),
        "action": str(action_type or "unknown"),
        "target": str(target or ""),
        "reasoning": str(reasoning or "").strip() or "No reasoning provided.",
        "expected_outcome": str(expected_outcome or "").strip() or "No expected outcome provided.",
    }
    try:
        log_path = _decision_audit_log_path(ts)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError) as exc:
        log.debug("decision audit write failed: %s", exc)
        return None
    return record


def audit_action(agent_name, action_type, target, justification) -> dict | None:
    justification_text = str(justification or "").strip() or "unjustified"
    enforce_scope(action_type, agent_name)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": str(agent_name or "unknown"),
        "action": str(action_type or "unknown"),
        "target": str(target or ""),
        "justification": justification_text,
    }
    check_action_scope(record, "append action audit log")
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
        from config import LLM_MODEL as expected_model
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
    agent_name = kwargs.get("agent_id") or kwargs.get("agent_name") or kwargs.get("agent")
    enforce_scope("network_call", agent_name)
    check_action_scope(
        kwargs.get("audit_justification") or kwargs.get("justification") or kwargs.get("task_description") or task_id,
        f"network call via {getattr(call, '__name__', 'llm_api_call')}",
    )
    log_permacomputing_audit(
        agent_name,
        kwargs.get("task_description") or task_id or getattr(call, "__name__", "llm_api_call"),
        kwargs.get("audit_justification")
        or kwargs.get("justification")
        or f"Call {getattr(call, '__name__', 'llm_api_call')} because the agent selected it for this task.",
    )
    _audit_heavy_action(
        agent_name,
        kwargs.get("tier"),
        "network_call",
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
            agent_name=agent_name,
        )
    return result_with_inference_timing(result, timing["inference_ms"])


def log_sub_agent_timing(
    task_id: str,
    inference_ms: int,
    total_ms: int,
    *,
    agent_name: str | None = None,
) -> dict | None:
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
    if agent_name:
        enforce_scope("file_write", agent_name)
    check_action_scope(record, "append sub-agent timing log")
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
def timed_claude_api_call(task_id: str, total_start: float | None = None, agent_name: str | None = None):
    timing = {"inference_ms": 0}
    llm_start = time.perf_counter()
    if agent_name:
        enforce_scope("network_call", agent_name)
    check_action_scope({"task_id": str(task_id or "")}, "network call via claude api")
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
            agent_name=agent_name,
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
    log_permacomputing_audit(
        agent_name,
        task_description,
        f"Dispatch selected {agent_name or 'unknown'} for this task before worker execution.",
    )
    enforce_scope("file_write", agent_name)
    check_action_scope(
        task_description, f"create dispatch receipt file in task workspace: {workspace / DISPATCH_RECEIPT_NAME}"
    )
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
    if agent_name.strip().lower() not in PUBLISH_AUDIT_HUMAN_TRIGGERS:
        enforce_scope("file_write", agent_name)
    check_action_scope(entry, "append publish audit log")
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


def _scan_judgment_claims(output_text: str, agent_name: str) -> None:
    text = str(output_text or "")
    for match in _JUDGMENT_PATTERN.finditer(text):
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 60)
        snippet = text[start:end].replace("\n", " ")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": str(agent_name or "unknown"),
            "keyword": match.group(0),
            "snippet": snippet,
        }
        try:
            from config import MIRA_ROOT

            log_path = MIRA_ROOT / "logs" / "judgment_claims.log"
            enforce_scope("file_write", agent_name)
            check_action_scope(entry, "append judgment claims log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, ValueError):
            pass


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
