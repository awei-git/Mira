from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ModelClass = Literal["routine", "premium", "tool", "local"]


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: list[LLMMessage]
    model_class: ModelClass = "routine"
    max_tokens: int | None = None
    timeout: int | None = None
    cwd: Path | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        parts = []
        for message in self.messages:
            parts.append(f"{message.role.upper()}:\n{message.content}")
        return "\n\n".join(parts)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
