"""Guards for text that is about to be posted publicly."""

from __future__ import annotations

import re


PRIVATE_OPERATOR_PATTERNS = (
    re.compile(r"\bWA\b(?:'s)?"),
    re.compile(r"(?<![A-Za-z])W\.A\.(?![A-Za-z])"),
)


class PublicTextLeakError(ValueError):
    """Raised when public text contains private operator shorthand."""


def validate_public_text(text: str, *, surface: str) -> str:
    """Return stripped text, or raise if it contains private shorthand."""
    cleaned = (text or "").strip()
    for pattern in PRIVATE_OPERATOR_PATTERNS:
        if pattern.search(cleaned):
            raise PublicTextLeakError(f"{surface} contains private operator shorthand")
    return cleaned
