"""Spawn sub-agents using Claude CLI, OpenAI API, or DeepSeek API.

Supports multiple model "flavors" for different writing styles:
- Claude: precise, structured, follows instructions well
- GPT-5: creative, fluent, natural-sounding prose
- DeepSeek: strong reasoning, good Chinese writing, cost-efficient
"""
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from config import (
    CLAUDE_BIN, CLAUDE_TIMEOUT_THINK, CLAUDE_TIMEOUT_ACT,
    SECRETS_FILE, MODELS, WRITING_MODELS, DEFAULT_MODEL, CLAUDE_FALLBACK_MODEL,
    LOGS_DIR,
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
    except Exception:
        pass


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


def set_usage_agent(agent_name: str):
    """Set the calling agent name for token usage tracking."""
    _caller_agent.name = agent_name


def _get_usage_agent() -> str:
    return getattr(_caller_agent, "name", "unknown")


# Cost per 1M tokens (USD) — update when prices change
_COST_PER_1M = {
    # provider/model prefix → (input_cost, output_cost)
    "anthropic/claude-sonnet": (3.00, 15.00),
    "anthropic/claude-opus": (15.00, 75.00),
    "anthropic/claude-haiku": (0.80, 4.00),
    "openai/gpt-5": (2.00, 8.00),
    "openai/gpt-4": (2.50, 10.00),
    "deepseek/deepseek-chat": (0.27, 1.10),
    "deepseek/deepseek-reasoner": (0.55, 2.19),
    "gemini/gemini-3": (0.10, 0.40),
    "gemini/gemini-2": (0.075, 0.30),
    "ollama/": (0.0, 0.0),  # local, free
}


def _estimate_cost(provider: str, model: str,
                   prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single API call."""
    key = f"{provider}/{model}"
    for prefix, (inp_cost, out_cost) in _COST_PER_1M.items():
        if key.startswith(prefix):
            return (prompt_tokens * inp_cost + completion_tokens * out_cost) / 1_000_000
    return 0.0


def _log_usage(provider: str, model: str, prompt_tokens: int,
               completion_tokens: int, estimated: bool = False):
    """Append one usage record to the daily JSONL log with cost estimate."""
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
        return {"date": date, "total_cost_usd": 0, "total_tokens": 0,
                "calls": 0, "by_provider": {}, "by_agent": {}}

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
    r"definitely|certainly|verified|confirmed|已验证|确认|"
    r"the (?:answer|result|output) is|结果是|答案是",
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
# Claude CLI calls
# ---------------------------------------------------------------------------

class ClaudeTimeoutError(Exception):
    """Raised when a Claude CLI call exceeds its timeout."""
    pass


# Substrings in Claude CLI stderr that indicate quota/rate-limit exhaustion.
_QUOTA_SIGNALS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "credit",
    "overloaded",
    "capacity",
)


def _is_quota_error(stderr: str) -> bool:
    """Return True if Claude CLI stderr indicates a quota or rate-limit error."""
    lower = stderr.lower()
    return any(sig in lower for sig in _QUOTA_SIGNALS)


# Tier → Claude model ID mapping.
# "light" uses Sonnet (fast, cheap), "heavy" uses Opus (best quality).
_CLAUDE_MODELS = {
    "light": "claude-sonnet-4-6",
    "heavy": "claude-opus-4-6",
}

# Tier → OpenAI reasoning_effort mapping (for GPT-5.4 and o-series).
_OPENAI_EFFORT = {
    "light": "medium",
    "heavy": "high",
}


def _fallback_think(prompt: str, timeout: int, tier: str = "light") -> str:
    """Call the configured fallback model (default: gpt-5.4) with the same prompt."""
    fallback = CLAUDE_FALLBACK_MODEL
    cfg = MODELS.get(fallback)
    if not cfg:
        log.error("Fallback model '%s' not in MODELS registry", fallback)
        return ""
    effort = _OPENAI_EFFORT.get(tier, "medium")
    log.warning("Claude quota hit — falling back to %s/%s (effort=%s)",
                cfg["provider"], cfg["model_id"], effort)
    return _api_call(cfg["provider"], cfg["model_id"], prompt,
                     timeout=timeout, reasoning_effort=effort)


def claude_think(prompt: str, timeout: int = CLAUDE_TIMEOUT_THINK,
                 tier: str = "light") -> str:
    """Call Claude CLI for thinking — no tools, just reasoning.

    Args:
        tier: "light" → Sonnet (fast), "heavy" → Opus (best quality).
              On OpenAI fallback maps to reasoning_effort medium/high.

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    On quota/rate-limit errors, automatically falls back to CLAUDE_FALLBACK_MODEL.
    """
    model_id = _CLAUDE_MODELS.get(tier, _CLAUDE_MODELS["light"])
    # Strip CLAUDECODE env var to allow nested Claude CLI sessions (LaunchAgent)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--setting-sources", "user",
             "--model", model_id],
            capture_output=True, text=True, timeout=timeout,
            cwd="/tmp", env=env,
        )
    except subprocess.TimeoutExpired:
        log.error("claude_think timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_think timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return _fallback_think(prompt, timeout, tier)

    if result.returncode != 0:
        log.error("claude_think failed (exit %d): %s", result.returncode, result.stderr[:300])
        if _is_quota_error(result.stderr):
            return _fallback_think(prompt, timeout, tier)
        return ""

    output = result.stdout.strip()
    _log_usage("anthropic", model_id,
               _estimate_tokens(prompt), _estimate_tokens(output), estimated=True)
    return output


def claude_act(prompt: str, cwd: Path = None, timeout: int = CLAUDE_TIMEOUT_ACT,
               tier: str = "light") -> str:
    """Call Claude CLI with tool access — can read/write files, run commands.

    Args:
        tier: "light" → Sonnet, "heavy" → Opus.
              On OpenAI fallback maps to reasoning_effort medium/high (thinking only).

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    On quota/rate-limit errors, falls back to CLAUDE_FALLBACK_MODEL (thinking only,
    no tool access — caller receives text output without file operations).
    """
    model_id = _CLAUDE_MODELS.get(tier, _CLAUDE_MODELS["light"])
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--model", model_id,
        "--allowedTools",
        "Bash(command:*),Read,Write,Edit,Glob,Grep,WebFetch(url:*)",
    ]

    # Strip CLAUDECODE env var to allow nested Claude CLI sessions (LaunchAgent)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None, env=env,
        )
    except subprocess.TimeoutExpired:
        log.error("claude_act timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_act timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return _fallback_think(prompt, timeout, tier)

    if result.returncode != 0:
        log.error("claude_act failed (exit %d): %s", result.returncode, result.stderr[:300])
        if _is_quota_error(result.stderr):
            log.warning("claude_act quota hit — falling back to thinking-only mode")
            return _fallback_think(prompt, timeout, tier)
        return ""

    output = result.stdout.strip()
    _log_usage("anthropic", model_id,
               _estimate_tokens(prompt), _estimate_tokens(output), estimated=True)
    return output


# ---------------------------------------------------------------------------
# OpenAI / DeepSeek / Gemini API calls
# ---------------------------------------------------------------------------

_API_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    # Gemini uses a different URL pattern — handled in _gemini_call
    # Ollama uses a different URL pattern — handled in _ollama_call
}

# Endpoint drift detection — probe once per session
_probed_providers: dict[str, bool] = {}  # provider → True if healthy


def _probe_endpoint(provider: str) -> bool:
    """Send minimal request to verify API is reachable and schema unchanged.

    Runs once per provider per session. Caches result.
    Cost: ~$0.001 per probe.
    """
    if provider in _probed_providers:
        return _probed_providers[provider]

    api_key = _get_api_key(provider)
    if not api_key:
        _probed_providers[provider] = False
        return False

    endpoint = _API_ENDPOINTS.get(provider, "")
    if not endpoint:
        _probed_providers[provider] = True  # non-standard providers skip probe
        return True

    payload = {
        "model": "gpt-5.4" if provider == "openai" else "deepseek-chat",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            # Verify expected schema
            if "choices" in data and isinstance(data["choices"], list):
                _probed_providers[provider] = True
                log.debug("Endpoint probe OK: %s", provider)
                return True
            else:
                log.warning("Endpoint schema drift: %s — missing 'choices' key. Keys: %s",
                           provider, list(data.keys()))
                _probed_providers[provider] = False
                return False
    except urllib.error.HTTPError as e:
        if e.code == 429:
            # Rate limited = endpoint is alive, schema OK
            _probed_providers[provider] = True
            return True
        log.warning("Endpoint probe failed: %s HTTP %d", provider, e.code)
        _probed_providers[provider] = False
        return False
    except (urllib.error.URLError, OSError) as e:
        log.warning("Endpoint unreachable: %s — %s", provider, e)
        _probed_providers[provider] = False
        return False


# ---------------------------------------------------------------------------
# Ollama (local LLM — no network, privacy-safe)
# ---------------------------------------------------------------------------

def _ollama_call(model_id: str, prompt: str,
                 system: str = "", timeout: int = 300) -> str:
    """Call local Ollama for privacy-sensitive tasks. Never leaves localhost."""
    from config import OLLAMA_HOST, OLLAMA_PORT
    endpoint = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/chat"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_id,
        "messages": messages,
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["message"]["content"]
            log.info("Ollama call: %s → %d chars", model_id, len(content))
            # Ollama may include eval_count/prompt_eval_count
            p_tok = data.get("prompt_eval_count", _estimate_tokens(prompt))
            c_tok = data.get("eval_count", _estimate_tokens(content))
            _log_usage("ollama", model_id, p_tok, c_tok,
                       estimated="prompt_eval_count" not in data)
            return content.strip()
    except Exception as e:
        log.error("Ollama %s failed: %s", model_id, str(e))
        return ""


def ollama_embed(text: str, model: str = "nomic-embed-text",
                 retries: int = 2) -> list[float]:
    """Get embedding from local Ollama. Retries on transient failures."""
    if not text or not text.strip():
        return []
    from config import OLLAMA_HOST, OLLAMA_PORT
    endpoint = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/embeddings"

    payload = {"model": model, "prompt": text}
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(1 + retries):
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["embedding"]
        except urllib.error.HTTPError as e:
            if e.code == 500 and attempt < retries:
                import time as _time
                wait = 2 ** attempt
                log.warning("Ollama embed 500, retry %d/%d in %ds", attempt + 1, retries, wait)
                _time.sleep(wait)
                continue
            log.error("Ollama embed failed (HTTP %d): %s", e.code, str(e))
            return []
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                import time as _time
                _time.sleep(2)
                continue
            log.error("Ollama embed failed: %s", str(e))
            return []
    return []


def _gemini_call(model_id: str, prompt: str,
                 system: str = "", timeout: int = 120) -> str:
    """Call Gemini API (different format from OpenAI-compatible APIs)."""
    api_key = _get_api_key("gemini")
    if not api_key:
        log.error("No API key for gemini")
        return ""

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model_id}:generateContent?key={api_key}"
    )

    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 32768,
            "temperature": 0.8,
        },
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            log.info("API call: gemini/%s → %d chars", model_id, len(content))
            # Extract real token counts from Gemini response
            usage = data.get("usageMetadata", {})
            _log_usage("gemini", model_id,
                       usage.get("promptTokenCount", 0),
                       usage.get("candidatesTokenCount", 0))
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:300])
        log.error("API gemini/%s HTTP %d: %s", model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API gemini/%s failed: %s", model_id, redact_secrets(str(e)))
        return ""


def _api_call(provider: str, model_id: str, prompt: str,
              system: str = "", timeout: int = 120,
              reasoning_effort: str = "") -> str:
    """Call OpenAI-compatible chat completion API (OpenAI, DeepSeek, Ollama)."""
    if provider == "gemini":
        return _gemini_call(model_id, prompt, system, timeout)

    # Probe endpoint on first use (detect drift before wasting a real call)
    if provider in _API_ENDPOINTS and not _probe_endpoint(provider):
        log.error("API endpoint probe failed for %s — skipping call", provider)
        return ""
    if provider == "ollama":
        return _ollama_call(model_id, prompt, system, timeout)

    api_key = _get_api_key(provider)
    if not api_key:
        log.error("No API key for provider '%s'", provider)
        return ""

    endpoint = _API_ENDPOINTS.get(provider, "")
    if not endpoint:
        log.error("Unknown provider '%s'", provider)
        return ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # Build request body — OpenAI GPT-5+ has different param names
    payload = {"model": model_id, "messages": messages}
    if provider == "openai":
        payload["max_completion_tokens"] = 32768
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
    elif provider == "deepseek":
        payload["max_tokens"] = 8192
        payload["temperature"] = 0.8
    else:
        payload["max_tokens"] = 32768
        payload["temperature"] = 0.8
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            model_used = data.get("model", model_id)
            log.info("API call: %s/%s → %d chars", provider, model_used, len(content))
            # Extract real token counts from OpenAI-compatible response
            usage = data.get("usage", {})
            _log_usage(provider, model_used,
                       usage.get("prompt_tokens", 0),
                       usage.get("completion_tokens", 0))
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:300])
        log.error("API %s/%s HTTP %d: %s", provider, model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API %s/%s failed: %s", provider, model_id, redact_secrets(str(e)))
        return ""


# ---------------------------------------------------------------------------
# Unified interface
# ---------------------------------------------------------------------------

def model_think(prompt: str, model_name: str = DEFAULT_MODEL,
                system: str = "", timeout: int = CLAUDE_TIMEOUT_THINK) -> str:
    """Call any model for thinking (no tools). Falls back to Claude on failure."""
    cfg = MODELS.get(model_name)
    if not cfg:
        log.warning("Unknown model '%s', falling back to claude", model_name)
        return claude_think(prompt, timeout=timeout)

    if cfg["provider"] == "claude":
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        return claude_think(full_prompt, timeout=timeout)
    else:
        result = _api_call(cfg["provider"], cfg["model_id"], prompt,
                           system=system, timeout=timeout)
        if not result:
            log.info("Falling back to claude after %s failure", model_name)
            full_prompt = f"{system}\n\n{prompt}" if system else prompt
            return claude_think(full_prompt, timeout=timeout)
        return result


def pick_writing_model() -> str:
    """Pick a writing model randomly for variety."""
    return random.choice(WRITING_MODELS)
