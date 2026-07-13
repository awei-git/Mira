"""Gemini API provider."""

import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger("mira")


def _gemini_call(model_id: str, prompt: str, system: str = "", timeout: int = 120) -> str:
    """Call Gemini API (different format from OpenAI-compatible APIs)."""
    from llm import _get_api_key, _log_usage, redact_secrets

    api_key = _get_api_key("gemini")
    if not api_key:
        log.error("No API key for gemini")
        return ""

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/" f"{model_id}:generateContent?key={api_key}"

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
            _log_usage("gemini", model_id, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0))
            return content.strip()
    except urllib.error.HTTPError as e:
        error_body = redact_secrets(e.read().decode("utf-8", errors="replace")[:300])
        log.error("API gemini/%s HTTP %d: %s", model_id, e.code, error_body)
        return ""
    except Exception as e:
        log.error("API gemini/%s failed: %s", model_id, redact_secrets(str(e)))
        return ""
