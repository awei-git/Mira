from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from config import CLAUDE_OPUS_MODEL, CLAUDE_SONNET_MODEL

from ..types import LLMRequest, LLMResponse


class AnthropicAPIAdapter:
    """Fallback Anthropic API adapter using only stdlib HTTP."""

    name = "anthropic_api"

    def complete(self, request: LLMRequest) -> LLMResponse:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        model = CLAUDE_OPUS_MODEL if request.model_class == "premium" else CLAUDE_SONNET_MODEL
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages = [{"role": m.role, "content": m.content} for m in request.messages if m.role in {"user", "assistant"}]
        body = {
            "model": model,
            "max_tokens": request.max_tokens or 1024,
            "messages": messages or [{"role": "user", "content": request.prompt}],
        }
        if system:
            body["system"] = system
        http_req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=request.timeout or 120) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Anthropic API request failed: {exc}") from exc
        chunks = raw.get("content") or []
        text = "".join(chunk.get("text", "") for chunk in chunks if isinstance(chunk, dict))
        usage = raw.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            raw=raw,
        )
