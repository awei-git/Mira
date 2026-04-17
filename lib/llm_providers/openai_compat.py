"""OpenAI / DeepSeek API provider — OpenAI-compatible chat completions."""

import json
import logging
import urllib.request
import urllib.error

from config import (
    OPENAI_API_ENDPOINT,
    DEEPSEEK_API_ENDPOINT,
    GPT5_MODEL,
    DEEPSEEK_CHAT_MODEL,
    DEEPSEEK_MAX_TOKENS,
    DEEPSEEK_TEMPERATURE,
)

log = logging.getLogger("mira")


_API_ENDPOINTS = {
    "openai": OPENAI_API_ENDPOINT,
    "deepseek": DEEPSEEK_API_ENDPOINT,
}

# Endpoint drift detection — probe once per session
_probed_providers: dict[str, bool] = {}  # provider -> True if healthy


def _probe_endpoint(provider: str) -> bool:
    """Send minimal request to verify API is reachable and schema unchanged.

    Runs once per provider per session. Caches result.
    Cost: ~$0.001 per probe.
    """
    from llm import _get_api_key

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
        "model": GPT5_MODEL if provider == "openai" else DEEPSEEK_CHAT_MODEL,
        "messages": [{"role": "user", "content": "hi"}],
    }
    if provider == "openai":
        payload["max_completion_tokens"] = 16
        payload["reasoning_effort"] = "medium"
    else:
        payload["max_tokens"] = 1
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
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
                log.warning("Endpoint schema drift: %s — missing 'choices' key. Keys: %s", provider, list(data.keys()))
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


def _api_call(
    provider: str, model_id: str, prompt: str, system: str = "", timeout: int = 120, reasoning_effort: str = ""
) -> str:
    """Call OpenAI-compatible chat completion API (OpenAI, DeepSeek, oMLX)."""
    from llm import _get_api_key, _log_usage, redact_secrets

    if provider == "gemini":
        from llm_providers.gemini import _gemini_call

        return _gemini_call(model_id, prompt, system, timeout)

    # Probe endpoint on first use (detect drift before wasting a real call)
    if provider in _API_ENDPOINTS and not _probe_endpoint(provider):
        log.error("API endpoint probe failed for %s — skipping call", provider)
        return ""
    if provider == "omlx":
        from llm_providers.local import _omlx_call

        return _omlx_call(model_id, prompt, system, timeout)

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
        payload["max_tokens"] = DEEPSEEK_MAX_TOKENS
        payload["temperature"] = DEEPSEEK_TEMPERATURE
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
            _log_usage(provider, model_used, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:300])
        log.error("API %s/%s HTTP %d: %s", provider, model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API %s/%s failed: %s", provider, model_id, redact_secrets(str(e)))
        return ""
