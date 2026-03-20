"""Secret agent — handles privacy-sensitive tasks using LOCAL LLM only.

Nothing leaves localhost. No cloud API calls. No web requests.
Uses Ollama (qwen2.5:32b) for reasoning, nomic-embed-text for embeddings.

Route here for: personal finance, health, legal, passwords, family matters,
anything the user wouldn't want sent to OpenAI/Anthropic/DeepSeek servers.
"""
import logging
from pathlib import Path

from config import OLLAMA_DEFAULT_MODEL
from sub_agent import _ollama_call

log = logging.getLogger("secret_agent")

_SYSTEM_PROMPT = """You are Mira, a private AI assistant running entirely on local hardware.
This conversation NEVER leaves this machine. No cloud APIs, no network requests.
Be thorough and helpful. The user chose the private channel because this is sensitive.
Respond in the same language the user writes in."""


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str,
           thread_history: str = "", thread_memory: str = "") -> str | None:
    """Handle a privacy-sensitive request using only local Ollama. Returns summary or None."""
    extra = ""
    if thread_history:
        extra += f"\n\nPrevious conversation:\n{thread_history}"

    prompt = content + extra
    log.info("Secret agent: task %s, %d chars, model=%s", task_id, len(prompt), OLLAMA_DEFAULT_MODEL)

    result = _ollama_call(OLLAMA_DEFAULT_MODEL, prompt, system=_SYSTEM_PROMPT, timeout=300)

    if result:
        (workspace / "output.md").write_text(result, encoding="utf-8")
        summary = result[:300]
        return summary

    log.error("Secret agent: Ollama returned empty for task %s", task_id)
    return None
