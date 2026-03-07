"""Spawn sub-agents using Claude CLI, OpenAI API, or DeepSeek API.

Supports multiple model "flavors" for different writing styles:
- Claude: precise, structured, follows instructions well
- GPT-5: creative, fluent, natural-sounding prose
- DeepSeek: strong reasoning, good Chinese writing, cost-efficient
"""
import json
import logging
import random
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from config import (
    CLAUDE_BIN, CLAUDE_TIMEOUT_THINK, CLAUDE_TIMEOUT_ACT,
    SECRETS_FILE, MODELS, WRITING_MODELS, DEFAULT_MODEL,
)

log = logging.getLogger("mira")

# ---------------------------------------------------------------------------
# Secrets loader (lazy, cached)
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
        return keys.get("gemini", "")
    return ""


# ---------------------------------------------------------------------------
# Claude CLI calls
# ---------------------------------------------------------------------------

class ClaudeTimeoutError(Exception):
    """Raised when a Claude CLI call exceeds its timeout."""
    pass


def claude_think(prompt: str, timeout: int = CLAUDE_TIMEOUT_THINK) -> str:
    """Call Claude CLI for thinking — no tools, just reasoning.

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    """
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--setting-sources", "user"],
            capture_output=True, text=True, timeout=timeout,
            cwd="/tmp",
        )
    except subprocess.TimeoutExpired:
        log.error("claude_think timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_think timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return ""

    if result.returncode != 0:
        log.error("claude_think failed (exit %d): %s", result.returncode, result.stderr[:300])
        return ""

    return result.stdout.strip()


def claude_act(prompt: str, cwd: Path = None, timeout: int = CLAUDE_TIMEOUT_ACT) -> str:
    """Call Claude CLI with tool access — can read/write files, run commands.

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    """
    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--allowedTools",
        "Bash(command:*),Read,Write,Edit,Glob,Grep,WebFetch(url:*)",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except subprocess.TimeoutExpired:
        log.error("claude_act timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_act timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return ""

    if result.returncode != 0:
        log.error("claude_act failed (exit %d): %s", result.returncode, result.stderr[:300])
        return ""

    return result.stdout.strip()


# ---------------------------------------------------------------------------
# OpenAI / DeepSeek / Gemini API calls
# ---------------------------------------------------------------------------

_API_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    # Gemini uses a different URL pattern — handled in _gemini_call
}


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
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("API gemini/%s HTTP %d: %s", model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API gemini/%s failed: %s", model_id, e)
        return ""


def _api_call(provider: str, model_id: str, prompt: str,
              system: str = "", timeout: int = 120) -> str:
    """Call OpenAI-compatible chat completion API (OpenAI, DeepSeek)."""
    if provider == "gemini":
        return _gemini_call(model_id, prompt, system, timeout)

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
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("API %s/%s HTTP %d: %s", provider, model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API %s/%s failed: %s", provider, model_id, e)
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
