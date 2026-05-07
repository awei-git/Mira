"""OpenAI / DeepSeek API provider — OpenAI-compatible chat completions."""

import json
import logging
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

from config import (
    STATE_DIR,
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
_PROVIDER_CIRCUIT_FILE = STATE_DIR / "api_provider_circuit.json"


def _load_provider_circuit() -> dict:
    try:
        return json.loads(_PROVIDER_CIRCUIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_provider_circuit(data: dict) -> None:
    try:
        _PROVIDER_CIRCUIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PROVIDER_CIRCUIT_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PROVIDER_CIRCUIT_FILE)
    except OSError as exc:
        log.debug("provider circuit save failed: %s", exc)


def _provider_circuit_open(provider: str) -> bool:
    entry = _load_provider_circuit().get(provider, {})
    until = entry.get("disabled_until", "")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) < until_dt


def _open_provider_circuit(provider: str, *, reason: str, hours: int = 6) -> None:
    data = _load_provider_circuit()
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    data[provider] = {
        "reason": reason,
        "disabled_until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_provider_circuit(data)


def _probe_endpoint(provider: str) -> bool:
    """Send minimal request to verify API is reachable and schema unchanged.

    Runs once per provider per session. Caches result.
    Cost: ~$0.001 per probe.
    """
    from llm import _get_api_key

    if _provider_circuit_open(provider):
        _probed_providers[provider] = False
        return False

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
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        if e.code == 429:
            # Rate limited = endpoint is alive, schema OK
            _probed_providers[provider] = True
            return True
        if provider == "deepseek" and e.code == 402:
            _open_provider_circuit(
                provider, reason=_http_error_reason(error_body, default="Insufficient Balance"), hours=6
            )
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
        if _provider_circuit_open(provider):
            log.debug("API provider %s circuit open — skipping call", provider)
        else:
            log.warning("API endpoint probe failed for %s — skipping call", provider)
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


def _http_error_reason(body: str, *, default: str) -> str:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return default
    error = data.get("error", {})
    if isinstance(error, dict):
        message = str(error.get("message", "")).strip()
        if message:
            return message[:120]
    return default
