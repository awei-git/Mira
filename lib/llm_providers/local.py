"""Local LLM providers — oMLX and Ollama (backward-compatible aliases)."""

import json
import logging
import urllib.request
import urllib.error

log = logging.getLogger("mira")


def _omlx_call(model_id: str, prompt: str, system: str = "", timeout: int = 300) -> str:
    """Call local oMLX (OpenAI-compatible) for privacy-sensitive tasks. Never leaves localhost."""
    from config import OMLX_HOST, OMLX_PORT
    from llm import _estimate_tokens, _log_usage

    endpoint = f"http://{OMLX_HOST}:{OMLX_PORT}/v1/chat/completions"

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 32768,
        "temperature": 0.7,
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
            content = data["choices"][0]["message"]["content"]
            model_used = data.get("model", model_id)
            log.info("oMLX call: %s → %d chars", model_used, len(content))
            usage = data.get("usage", {})
            _log_usage(
                "omlx",
                model_used,
                usage.get("prompt_tokens", _estimate_tokens(prompt)),
                usage.get("completion_tokens", _estimate_tokens(content)),
                estimated="usage" not in data,
            )
            return content.strip()
    except Exception as e:
        log.error("oMLX %s failed: %s", model_id, str(e))
        # Fallback to secondary local model
        from config import OMLX_FALLBACK_MODEL

        if model_id != OMLX_FALLBACK_MODEL:
            log.info("oMLX falling back to %s", OMLX_FALLBACK_MODEL)
            return _omlx_call(OMLX_FALLBACK_MODEL, prompt, system=system, timeout=timeout)
        return ""


def omlx_embed(text: str, model: str = "", retries: int = 2) -> list[float]:
    """Get embedding from local oMLX (OpenAI-compatible).

    Wrapped in a circuit breaker (`net.circuit_breaker`) so that when
    the local endpoint is dying (HTTP 507 / timeouts, which baseline
    showed dominate ~91% of ERROR log volume), we fast-fail returning
    [] instead of piling retries on a sick process.
    """
    if not text or not text.strip():
        return []
    from config import OMLX_HOST, OMLX_PORT, OMLX_EMBED_MODEL
    from net.circuit_breaker import CircuitOpen, get_circuit

    if not model:
        model = OMLX_EMBED_MODEL
    endpoint = f"http://{OMLX_HOST}:{OMLX_PORT}/v1/embeddings"

    payload = {"model": model, "input": text}
    body = json.dumps(payload).encode("utf-8")

    breaker = get_circuit(
        "omlx_embed",
        window_seconds=300.0,
        min_samples=8,
        error_rate_threshold=0.5,
        cooldown_seconds=180.0,
    )

    def _single_attempt() -> list[float]:
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["data"][0]["embedding"]

    for attempt in range(1 + retries):
        try:
            return breaker.call(_single_attempt)
        except CircuitOpen:
            # Provider is tripped — skip retries, let caller fall back.
            log.debug("oMLX embed skipped: circuit OPEN")
            return []
        except urllib.error.HTTPError as e:
            if e.code == 500 and attempt < retries:
                import time as _time

                wait = 2**attempt
                log.warning("oMLX embed 500, retry %d/%d in %ds", attempt + 1, retries, wait)
                _time.sleep(wait)
                continue
            log.error("oMLX embed failed (HTTP %d): %s", e.code, str(e))
            return []
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                import time as _time

                _time.sleep(2)
                continue
            log.error("oMLX embed failed: %s", str(e))
            return []
    return []


def _ollama_call(model_id: str, prompt: str, system: str = "", timeout: int = 300) -> str:
    """Backward-compatible alias during the Ollama -> oMLX migration."""
    return _omlx_call(model_id, prompt, system=system, timeout=timeout)


def ollama_embed(text: str, model: str = "nomic-embed-text", retries: int = 2) -> list[float]:
    """Backward-compatible alias during the Ollama -> oMLX migration."""
    return omlx_embed(text, model=model, retries=retries)
