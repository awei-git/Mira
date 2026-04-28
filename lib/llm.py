"""Spawn sub-agents using Claude CLI, OpenAI API, or DeepSeek API.

Supports multiple model "flavors" for different writing styles:
- Claude: precise, structured, follows instructions well
- GPT-5: creative, fluent, natural-sounding prose
- DeepSeek: strong reasoning, good Chinese writing, cost-efficient
"""

import json
import logging
import random
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from config import (
    SECRETS_FILE,
    MODELS,
    WRITING_MODELS,
    DEFAULT_MODEL,
    CLAUDE_TIMEOUT_THINK,
    OMLX_DEFAULT_MODEL,
    LOGS_DIR,
    TOKEN_USAGE_LOG_PATH,
)

# ---------------------------------------------------------------------------
# Re-export provider functions for backward compatibility
# ---------------------------------------------------------------------------
from llm_providers.claude import (  # noqa: F401
    ClaudeTimeoutError,
    claude_think,
    claude_act,
)
from llm_providers.openai_compat import (  # noqa: F401
    _api_call,
    _probe_endpoint,
)
from llm_providers.gemini import (  # noqa: F401
    _gemini_call,
)
from llm_providers.local import (  # noqa: F401
    _omlx_call,
    omlx_embed,
    _ollama_call,
    ollama_embed,
)

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Secrets redaction — prevent API keys from leaking into logs
# ---------------------------------------------------------------------------

_redact_cache: list[str] = []


def _init_redact_cache():
    """Build a list of secret values to redact from log output."""
    global _redact_cache
    if _redact_cache:
        return
    try:
        secrets = _load_secrets()
        keys = secrets.get("api_keys", {})
        for v in keys.values():
            if isinstance(v, str) and len(v) > 8:
                _redact_cache.append(v)
            elif isinstance(v, dict):
                for sv in v.values():
                    if isinstance(sv, str) and len(sv) > 8:
                        _redact_cache.append(sv)
    except Exception as e:
        logging.getLogger("mira.llm").debug("Failed to init redact cache: %s", e)


def redact_secrets(text: str) -> str:
    """Replace any known secret values in text with [REDACTED]."""
    _init_redact_cache()
    for secret in _redact_cache:
        if secret in text:
            text = text.replace(secret, "[REDACTED]")
    return text


# ---------------------------------------------------------------------------
# Token usage tracking — append to daily JSONL file
# ---------------------------------------------------------------------------

# Thread-local caller context: set by task_worker before dispatching
_caller_agent = threading.local()
_model_policy = threading.local()
_session_usage = threading.local()


def set_usage_agent(agent_name: str):
    """Set the calling agent name for token usage tracking."""
    _caller_agent.name = agent_name


def _get_usage_agent() -> str:
    return getattr(_caller_agent, "name", "unknown")


def reset_session_tokens():
    """Reset per-task token accumulator (call before each agent run)."""
    _session_usage.input = 0
    _session_usage.output = 0
    _session_usage.model = ""


def get_session_tokens() -> tuple[int, int, str]:
    """Return (input_tokens, output_tokens, model_id) accumulated since last reset."""
    return (
        getattr(_session_usage, "input", 0),
        getattr(_session_usage, "output", 0),
        getattr(_session_usage, "model", ""),
    )


def set_model_policy(policy: str | None):
    """Set per-step model policy (e.g., 'omlx'). None = default."""
    _model_policy.value = policy


def _force_local() -> bool:
    """Check if current step requires local oMLX model."""
    return getattr(_model_policy, "value", None) in ("omlx",)


def _force_ollama() -> bool:
    """Backward-compatible alias during the Ollama -> oMLX migration."""
    return _force_local()


# Cost per 1M tokens (USD) — update when prices change
_COST_PER_1M = {
    # provider/model prefix -> (input_cost, output_cost)
    "anthropic/claude-sonnet": (3.00, 15.00),
    "anthropic/claude-opus": (15.00, 75.00),
    "anthropic/claude-haiku": (0.80, 4.00),
    "openai/gpt-5": (2.00, 8.00),
    "openai/gpt-4": (2.50, 10.00),
    "deepseek/deepseek-chat": (0.27, 1.10),
    "deepseek/deepseek-reasoner": (0.55, 2.19),
    "gemini/gemini-3": (0.10, 0.40),
    "gemini/gemini-2": (0.075, 0.30),
    "omlx/": (0.0, 0.0),  # local, free
}


def _estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single API call."""
    key = f"{provider}/{model}"
    for prefix, (inp_cost, out_cost) in _COST_PER_1M.items():
        if key.startswith(prefix):
            return (prompt_tokens * inp_cost + completion_tokens * out_cost) / 1_000_000
    return 0.0


def _log_usage(provider: str, model: str, prompt_tokens: int, completion_tokens: int, estimated: bool = False):
    """Append one usage record to the daily JSONL log with cost estimate."""
    _session_usage.input = getattr(_session_usage, "input", 0) + prompt_tokens
    _session_usage.output = getattr(_session_usage, "output", 0) + completion_tokens
    if model:
        _session_usage.model = model
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        path = LOGS_DIR / f"usage_{today}.jsonl"
        cost = _estimate_cost(provider, model, prompt_tokens, completion_tokens)
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "agent": _get_usage_agent(),
            "provider": provider,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": round(cost, 6),
            "estimated": estimated,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        usage_record = {
            "ts": record["ts"],
            "agent_name": record["agent"],
            "task_type": record["agent"],
            "model_id": model,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        }
        with open(TOKEN_USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(usage_record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass  # Never break the call for logging


def usage_summary(date: str = "") -> dict:
    """Summarize API usage for a given date (default: today).

    Returns: {total_cost_usd, total_tokens, by_provider: {name: {cost, tokens, calls}},
              by_agent: {name: {cost, tokens, calls}}}
    """
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    path = LOGS_DIR / f"usage_{date}.jsonl"
    if not path.exists():
        return {"date": date, "total_cost_usd": 0, "total_tokens": 0, "calls": 0, "by_provider": {}, "by_agent": {}}

    by_provider: dict[str, dict] = {}
    by_agent: dict[str, dict] = {}
    total_cost = 0.0
    total_tokens = 0
    total_calls = 0

    for line in path.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        cost = r.get("cost_usd", 0.0)
        tokens = r.get("total_tokens", 0)
        provider = r.get("provider", "unknown")
        agent = r.get("agent", "unknown")

        total_cost += cost
        total_tokens += tokens
        total_calls += 1

        for bucket, key in [(by_provider, provider), (by_agent, agent)]:
            if key not in bucket:
                bucket[key] = {"cost_usd": 0.0, "tokens": 0, "calls": 0}
            bucket[key]["cost_usd"] += cost
            bucket[key]["tokens"] += tokens
            bucket[key]["calls"] += 1

    # Round costs
    for bucket in [by_provider, by_agent]:
        for v in bucket.values():
            v["cost_usd"] = round(v["cost_usd"], 4)

    return {
        "date": date,
        "total_cost_usd": round(total_cost, 4),
        "total_tokens": total_tokens,
        "calls": total_calls,
        "by_provider": by_provider,
        "by_agent": by_agent,
    }


def _log_efficiency(task_id: str, agent: str, model: str, input_tokens: int, output_tokens: int, words: int):
    """Append a per-task token efficiency record to token_efficiency.jsonl."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / "token_efficiency.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "task_id": task_id,
            "agent": agent,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "words": words,
            "efficiency": round(output_tokens / max(1, words), 4),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass


def token_efficiency_summary() -> dict:
    """Compare current vs prior 7-day token efficiency by model version.

    Flags models with >20% regression in output_tokens/word ratio.
    Returns: {by_model: {model: {avg_efficiency, samples, regression, ...}}, regressions: [...]}
    """
    from datetime import timedelta

    path = LOGS_DIR / "token_efficiency.jsonl"
    if not path.exists():
        return {"status": "no_data", "by_model": {}, "regressions": []}

    now = datetime.now(timezone.utc)
    cutoff_recent = now - timedelta(days=7)
    cutoff_prior = now - timedelta(days=14)

    recent: dict[str, list[float]] = {}
    prior: dict[str, list[float]] = {}

    for line in path.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_str = r.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        model = r.get("model", "unknown")
        eff = r.get("efficiency", 0.0)
        if ts >= cutoff_recent:
            recent.setdefault(model, []).append(eff)
        elif ts >= cutoff_prior:
            prior.setdefault(model, []).append(eff)

    by_model: dict[str, dict] = {}
    regressions: list[str] = []
    for model, effs in recent.items():
        avg_recent = sum(effs) / len(effs)
        prior_effs = prior.get(model, [])
        entry: dict = {"avg_efficiency": round(avg_recent, 4), "samples": len(effs), "regression": False}
        if prior_effs:
            avg_prior = sum(prior_effs) / len(prior_effs)
            change = (avg_recent - avg_prior) / max(avg_prior, 1e-9)
            entry["prior_avg_efficiency"] = round(avg_prior, 4)
            entry["pct_change"] = round(change * 100, 1)
            if change < -0.20:
                entry["regression"] = True
                regressions.append(model)
        by_model[model] = entry

    if regressions:
        log.warning(
            "TOKEN_EFFICIENCY_REGRESSION models=%s — output quality per token dropped >20%% vs prior 7-day avg",
            ",".join(regressions),
        )

    return {"by_model": by_model, "regressions": regressions}


# ---------------------------------------------------------------------------
# Output confidence estimation (heuristic, no extra LLM call)
# ---------------------------------------------------------------------------

_LOW_CONFIDENCE_SIGNALS = re.compile(
    r"i(?:'m| am) not (?:sure|certain)|i (?:think|believe|guess)|可能|也许|不确定|"
    r"might be|could be|not entirely|roughly|approximately|据我所知|"
    r"i don't (?:know|have|remember)|我不太清楚|不太确定",
    re.IGNORECASE,
)

_HIGH_CONFIDENCE_SIGNALS = re.compile(
    r"definitely|certainly|verified|confirmed|已验证|确认|" r"the (?:answer|result|output) is|结果是|答案是",
    re.IGNORECASE,
)


def estimate_confidence(text: str) -> str:
    """Estimate confidence of LLM output from hedging language.

    Returns: "high", "medium", or "low".
    No LLM call — pure heuristic.
    """
    if not text:
        return "low"

    low_count = len(_LOW_CONFIDENCE_SIGNALS.findall(text[:2000]))
    high_count = len(_HIGH_CONFIDENCE_SIGNALS.findall(text[:2000]))

    if low_count >= 3 and high_count == 0:
        return "low"
    elif high_count >= 2 and low_count == 0:
        return "high"
    elif low_count > high_count:
        return "low"
    else:
        return "medium"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for mixed CJK/English."""
    return max(1, len(text) // 3)


# ---------------------------------------------------------------------------
# Secrets loader (lazy, cached) — reads from .config/secrets.yml (not in git)
# ---------------------------------------------------------------------------

_secrets_cache = None


def _load_secrets() -> dict:
    global _secrets_cache
    if _secrets_cache is not None:
        return _secrets_cache
    try:
        import yaml

        _secrets_cache = yaml.safe_load(SECRETS_FILE.read_text(encoding="utf-8"))
    except ImportError:
        # Fallback: basic YAML parsing for simple key: value
        _secrets_cache = _parse_secrets_simple(SECRETS_FILE)
    except Exception as e:
        log.warning("Failed to load secrets: %s", e)
        _secrets_cache = {}
    return _secrets_cache


def _parse_secrets_simple(path: Path) -> dict:
    """Minimal YAML parser for the secrets file (no dependency needed)."""
    if not path.exists():
        return {}
    result = {"api_keys": {}}
    current_section = None
    current_subsection = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0 and stripped.endswith(":"):
            current_section = stripped[:-1]
            current_subsection = None
        elif indent == 2 and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if current_section == "api_keys":
                if val:
                    result["api_keys"][key] = val
                else:
                    # Subsection like openai:
                    current_subsection = key
                    result["api_keys"][key] = {}
        elif indent == 4 and ":" in stripped and current_subsection:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            result["api_keys"][current_subsection][key] = val
    return result


def _get_api_key(provider: str) -> str:
    """Get API key for a provider from secrets.yml."""
    secrets = _load_secrets()
    keys = secrets.get("api_keys", {})
    if provider == "openai":
        openai_cfg = keys.get("openai", {})
        if isinstance(openai_cfg, dict):
            return openai_cfg.get("api_key", "")
        return ""
    elif provider == "deepseek":
        return keys.get("deepseek", "")
    elif provider == "gemini":
        val = keys.get("gemini", "")
        if isinstance(val, dict):
            # Multiple keys — return first one (api_key_1)
            for k in sorted(val.keys()):
                if val[k]:
                    return val[k]
            return ""
        return val
    elif provider == "minimax":
        return keys.get("minimax", "")
    return ""


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------


def model_think(
    prompt: str, model_name: str = DEFAULT_MODEL, system: str = "", timeout: int = CLAUDE_TIMEOUT_THINK
) -> str:
    """Call any model for thinking (no tools). Falls back to Claude on failure."""
    # Model restriction: force local oMLX if policy is set (privacy boundary)
    if _force_local():
        return _omlx_call(OMLX_DEFAULT_MODEL, prompt, timeout=timeout)
    cfg = MODELS.get(model_name)
    if not cfg:
        log.warning("Unknown model '%s', falling back to claude", model_name)
        return claude_think(prompt, timeout=timeout)

    if cfg["provider"] == "claude":
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        return claude_think(full_prompt, timeout=timeout)
    else:
        result = _api_call(cfg["provider"], cfg["model_id"], prompt, system=system, timeout=timeout)
        if not result:
            log.info("Falling back to claude after %s failure", model_name)
            full_prompt = f"{system}\n\n{prompt}" if system else prompt
            return claude_think(full_prompt, timeout=timeout)
        return result


def pick_writing_model() -> str:
    """Pick a writing model randomly for variety."""
    return random.choice(WRITING_MODELS)
