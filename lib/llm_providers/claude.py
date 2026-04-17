"""Claude CLI provider — claude_think, claude_act, _fallback_think."""

import logging
import os
import subprocess
import time
from pathlib import Path

from config import (
    CLAUDE_BIN,
    CLAUDE_TIMEOUT_THINK,
    CLAUDE_TIMEOUT_ACT,
    CLAUDE_FALLBACK_MODEL,
    MODELS,
    OMLX_DEFAULT_MODEL,
    CLAUDE_SONNET_MODEL,
    CLAUDE_OPUS_MODEL,
)

log = logging.getLogger("mira")


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


# Tier -> Claude model ID mapping.
# "light" uses Sonnet (fast, cheap), "heavy" uses Opus (best quality).
_CLAUDE_MODELS = {
    "light": CLAUDE_SONNET_MODEL,
    "heavy": CLAUDE_OPUS_MODEL,
}

# Tier -> OpenAI reasoning_effort mapping (for GPT-5.4 and o-series).
_OPENAI_EFFORT = {
    "light": "medium",
    "heavy": "high",
}


def _fallback_think(prompt: str, timeout: int, tier: str = "light") -> str:
    """Call the configured fallback model (default: gpt-5.4) with the same prompt."""
    from llm_providers.openai_compat import _api_call

    fallback = CLAUDE_FALLBACK_MODEL
    cfg = MODELS.get(fallback)
    if not cfg:
        log.error("Fallback model '%s' not in MODELS registry", fallback)
        return ""
    effort = _OPENAI_EFFORT.get(tier, "medium")
    log.warning("Claude quota hit — falling back to %s/%s (effort=%s)", cfg["provider"], cfg["model_id"], effort)
    return _api_call(cfg["provider"], cfg["model_id"], prompt, timeout=timeout, reasoning_effort=effort)


def claude_think(prompt: str, timeout: int = CLAUDE_TIMEOUT_THINK, tier: str = "light") -> str:
    """Call Claude CLI for thinking — no tools, just reasoning.

    Args:
        tier: "light" -> Sonnet (fast), "heavy" -> Opus (best quality).
              On OpenAI fallback maps to reasoning_effort medium/high.

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    On quota/rate-limit errors, automatically falls back to CLAUDE_FALLBACK_MODEL.
    """
    # Lazy imports to avoid circular dependency
    from llm import _force_local, _estimate_tokens, _log_usage
    from llm_providers.local import _omlx_call

    # Model restriction: force local oMLX if set (e.g. for child users)
    if _force_local():
        return _omlx_call(OMLX_DEFAULT_MODEL, prompt, timeout=timeout)
    model_id = _CLAUDE_MODELS.get(tier, _CLAUDE_MODELS["light"])
    # Strip CLAUDECODE env var to allow nested Claude CLI sessions (LaunchAgent)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    _start = time.monotonic()
    try:
        result = subprocess.run(
            [CLAUDE_BIN, "-p", prompt, "--setting-sources", "user", "--model", model_id],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.error("claude_think timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_think timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return _fallback_think(prompt, timeout, tier)
    _elapsed = time.monotonic() - _start
    _ratio = _elapsed / timeout if timeout else 0
    if _elapsed > 0.75 * timeout:
        log.warning(
            "claude_think approaching timeout: elapsed=%.1fs timeout_think=%ds ratio=%.2f",
            _elapsed,
            timeout,
            _ratio,
        )
    else:
        log.info(
            "claude_think elapsed=%.1fs timeout_think=%ds ratio=%.2f",
            _elapsed,
            timeout,
            _ratio,
        )

    if result.returncode != 0:
        log.error("claude_think failed (exit %d): %s", result.returncode, result.stderr[:300])
        reason = "quota/rate-limit" if _is_quota_error(result.stderr) else f"cli exit {result.returncode}"
        log.warning("claude_think unavailable (%s) — using fallback model", reason)
        return _fallback_think(prompt, timeout, tier)

    output = result.stdout.strip()
    _log_usage("anthropic", model_id, _estimate_tokens(prompt), _estimate_tokens(output), estimated=True)
    return output


def _resolve_allowed_tools(agent_id: str | None) -> str:
    """Resolve the --allowedTools string for a given agent.

    If agent_id is provided and the agent's manifest declares allowed_tools,
    use those. Otherwise fall back to the default full tool set.
    An empty allowed_tools list means the agent should use claude_think,
    not claude_act — but if called anyway, grant read-only as a safety net.
    """
    _DEFAULT_TOOLS = "Bash(command:*),Read,Write,Edit,Glob,Grep,WebFetch(url:*)"
    if not agent_id:
        return _DEFAULT_TOOLS
    try:
        from agent_registry import get_registry

        tools = get_registry().get_allowed_tools(agent_id)
        if tools is None:
            # No allowed_tools declared — legacy agent, full access
            return _DEFAULT_TOOLS
        if not tools:
            # Empty list — this agent shouldn't use claude_act at all
            # Grant read-only as a safety net
            log.warning("Agent '%s' has empty allowed_tools but called claude_act — granting read-only", agent_id)
            return "Read,Glob,Grep"
        return ",".join(tools)
    except Exception as e:
        log.warning("Failed to resolve allowed_tools for '%s': %s — using defaults", agent_id, e)
        return _DEFAULT_TOOLS


def claude_act(
    prompt: str, cwd: Path = None, timeout: int = CLAUDE_TIMEOUT_ACT, tier: str = "light", agent_id: str = None
) -> str:
    """Call Claude CLI with tool access — can read/write files, run commands.

    Args:
        tier: "light" -> Sonnet, "heavy" -> Opus.
              On OpenAI fallback maps to reasoning_effort medium/high (thinking only).
        agent_id: If provided, tool access is restricted to the agent's
                  manifest allowed_tools. Without this, full tool access is granted.

    Raises ClaudeTimeoutError on timeout so callers can distinguish
    timeout from a genuine empty response.
    On quota/rate-limit errors, falls back to CLAUDE_FALLBACK_MODEL (thinking only,
    no tool access — caller receives text output without file operations).
    """
    # Lazy imports to avoid circular dependency
    from llm import _force_local, _estimate_tokens, _log_usage
    from llm_providers.local import _omlx_call

    # Model restriction: force local oMLX if set (e.g. for child users)
    if _force_local():
        return _omlx_call(OMLX_DEFAULT_MODEL, prompt, timeout=timeout)
    model_id = _CLAUDE_MODELS.get(tier, _CLAUDE_MODELS["light"])
    allowed_tools = _resolve_allowed_tools(agent_id)
    cmd = [
        CLAUDE_BIN,
        "-p",
        prompt,
        "--model",
        model_id,
        "--allowedTools",
        allowed_tools,
    ]

    # Strip CLAUDECODE env var to allow nested Claude CLI sessions (LaunchAgent)
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    _start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        log.error("claude_act timed out (%ds)", timeout)
        raise ClaudeTimeoutError(f"claude_act timed out after {timeout}s")
    except FileNotFoundError:
        log.error("Claude CLI not found at %s", CLAUDE_BIN)
        return _fallback_think(prompt, timeout, tier)
    _elapsed = time.monotonic() - _start
    _ratio = _elapsed / timeout if timeout else 0
    if _elapsed > 0.75 * timeout:
        log.warning(
            "claude_act approaching timeout: elapsed=%.1fs timeout_act=%ds ratio=%.2f",
            _elapsed,
            timeout,
            _ratio,
        )
    else:
        log.info(
            "claude_act elapsed=%.1fs timeout_act=%ds ratio=%.2f",
            _elapsed,
            timeout,
            _ratio,
        )

    if result.returncode != 0:
        log.error("claude_act failed (exit %d): %s", result.returncode, result.stderr[:300])
        reason = "quota/rate-limit" if _is_quota_error(result.stderr) else f"cli exit {result.returncode}"
        log.warning("claude_act unavailable (%s) — falling back to thinking-only mode", reason)
        return _fallback_think(prompt, timeout, tier)

    output = result.stdout.strip()
    _log_usage("anthropic", model_id, _estimate_tokens(prompt), _estimate_tokens(output), estimated=True)
    return output
