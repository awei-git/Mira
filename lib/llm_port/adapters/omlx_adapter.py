from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from config import (
    LOCAL_LLM_NATIVE_TOOLS_ALLOWED,
    OMLX_API_ENDPOINT,
    OMLX_DEFAULT_MODEL,
    OMLX_DISABLE_SERVER_TOOLS,
)

from ..types import LLMRequest, LLMResponse

log = logging.getLogger("mira")


class OMLXAdapter:
    """OpenAI-compatible adapter for the local oMLX server."""

    name = "omlx"

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not LOCAL_LLM_NATIVE_TOOLS_ALLOWED:
            forbidden = {"tools", "tool_choice", "functions", "function_call", "shell", "edit_file"}
            if forbidden.intersection(request.metadata):
                raise RuntimeError(
                    "Refused local LLM request with native tool metadata; "
                    "tools must be brokered outside the model server."
                )

        model = str(request.metadata.get("model") or OMLX_DEFAULT_MODEL)
        endpoint = _chat_completions_endpoint(OMLX_API_ENDPOINT)
        body = {
            "model": model,
            "messages": [{"role": message.role, "content": message.content} for message in request.messages],
            "temperature": request.metadata.get("temperature", 0),
        }
        if OMLX_DISABLE_SERVER_TOOLS:
            body["tools"] = []
            body["tool_choice"] = "none"
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        http_req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=request.timeout or 120) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"oMLX request failed: {exc}") from exc
        choices = raw.get("choices") or []
        if OMLX_DISABLE_SERVER_TOOLS:
            for choice in choices:
                if isinstance(choice, dict):
                    message = choice.get("message") or {}
                    if isinstance(message, dict) and ("tool_calls" in message or "function_call" in message):
                        log.error("Rejected oMLX response containing server-side tool call")
                        raise RuntimeError("Rejected oMLX response containing server-side tool call")
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = str(message.get("content") or choices[0].get("text") or "")
        usage = raw.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=str(raw.get("model") or model),
            input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
            output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),
            raw=raw,
        )


def _chat_completions_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"
