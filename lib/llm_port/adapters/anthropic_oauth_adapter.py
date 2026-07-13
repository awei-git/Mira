from __future__ import annotations

from config import CLAUDE_OPUS_MODEL, CLAUDE_SONNET_MODEL, CLAUDE_TIMEOUT_ACT, CLAUDE_TIMEOUT_THINK
from llm_providers.claude import claude_act, claude_think

from ..types import LLMRequest, LLMResponse


class AnthropicOAuthAdapter:
    """Primary personal-use adapter backed by the Claude Code CLI OAuth session."""

    name = "anthropic_oauth"

    def complete(self, request: LLMRequest) -> LLMResponse:
        tier = "heavy" if request.model_class == "premium" else "light"
        timeout = request.timeout or (CLAUDE_TIMEOUT_ACT if request.model_class == "tool" else CLAUDE_TIMEOUT_THINK)
        if request.model_class == "tool":
            text = claude_act(
                request.prompt,
                cwd=request.cwd,
                timeout=timeout,
                tier=tier,
                agent_id=request.agent_id,
                max_tokens=request.max_tokens,
            )
        else:
            text = claude_think(request.prompt, timeout=timeout, tier=tier, max_tokens=request.max_tokens)
        model = CLAUDE_OPUS_MODEL if tier == "heavy" else CLAUDE_SONNET_MODEL
        return LLMResponse(text=text, provider=self.name, model=model)
