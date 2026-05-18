"""Guards for text that is about to be posted publicly."""

from __future__ import annotations

import re


PRIVATE_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bWA\b(?:'s)?"),
        "private operator shorthand",
    ),
    (
        re.compile(r"(?<![A-Za-z])W\.A\.(?![A-Za-z])"),
        "private operator shorthand",
    ),
    (
        re.compile(r"\b(?:angwei|awei-git)\b", re.IGNORECASE),
        "private account name",
    ),
    (
        re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
        "private email address",
    ),
    (
        re.compile(r"file:///[^\s)\]]+"),
        "local file URL",
    ),
    (
        re.compile(r"(?:^|(?<=\s))/(?:Users|private|var|tmp|Volumes)/[^\s)\]]+"),
        "local filesystem path",
    ),
    (
        re.compile(r"~/(?:[^\s)\]]+)"),
        "home-relative filesystem path",
    ),
    (
        re.compile(
            r"\b(?:127\.0\.0\.1|0\.0\.0\.0|localhost|192\.168\.\d{1,3}\.\d{1,3})" r"(?::\d+)?\b",
            re.IGNORECASE,
        ),
        "private local endpoint",
    ),
    (
        re.compile(
            r"\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY|"
            r"MINIMAX_API_KEY|WEBGUI_TOKEN|SUBSTACK_[A-Z_]*|substack\.sid|api[_-]?key|"
            r"access[_-]?token|refresh[_-]?token|password|secret)\b\s*[:=]\s*['\"]?"
            r"[^'\"\s]{8,}",
            re.IGNORECASE,
        ),
        "secret-shaped credential",
    ),
)

PRIVATE_OPERATOR_PATTERNS = tuple(pattern for pattern, _label in PRIVATE_TEXT_PATTERNS[:2])

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (PRIVATE_TEXT_PATTERNS[0][0], "my human"),
    (PRIVATE_TEXT_PATTERNS[1][0], "my human"),
    (PRIVATE_TEXT_PATTERNS[2][0], "my human"),
    (PRIVATE_TEXT_PATTERNS[3][0], "[private email]"),
    (PRIVATE_TEXT_PATTERNS[4][0], "[private path]"),
    (PRIVATE_TEXT_PATTERNS[5][0], "[private path]"),
    (PRIVATE_TEXT_PATTERNS[6][0], "[private path]"),
    (PRIVATE_TEXT_PATTERNS[7][0], "[private endpoint]"),
    (PRIVATE_TEXT_PATTERNS[8][0], "[secret]"),
)


class PublicTextLeakError(ValueError):
    """Raised when public text contains private details."""


def redact_public_text(text: str) -> str:
    """Return text with known private details replaced for draft review."""
    cleaned = (text or "").strip()
    for pattern, replacement in _REDACTIONS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def validate_public_text(text: str, *, surface: str) -> str:
    """Return stripped text, or raise if it contains private details."""
    cleaned = (text or "").strip()
    for pattern, label in PRIVATE_TEXT_PATTERNS:
        if pattern.search(cleaned):
            raise PublicTextLeakError(f"{surface} contains {label}")
    return cleaned
