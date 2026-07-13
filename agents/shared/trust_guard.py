"""Trust checks for untrusted ingested content."""

from __future__ import annotations

import re

_ROLE_DIRECTIVE_PATTERN = re.compile(r"(?im)^\s*(?:#+\s*)?(?:system|developer|assistant|tool)\s*:\s*\S")
_INJECTION_PATTERNS = (
    re.compile(r"\bignore (?:all )?(?:previous|prior|above) instructions\b", re.IGNORECASE),
    re.compile(r"\bdisregard (?:all )?(?:previous|prior|above) instructions\b", re.IGNORECASE),
    re.compile(r"\b(?:reveal|print|dump|show) (?:your )?(?:system prompt|hidden prompt|instructions)\b", re.IGNORECASE),
    re.compile(r"\byou are (?:now )?(?:chatgpt|an ai assistant|a language model)\b", re.IGNORECASE),
    re.compile(r"\b(?:jailbreak|dan mode|developer mode|prompt injection)\b", re.IGNORECASE),
    re.compile(r"(?:<\|im_start\|>|<\|im_end\|>|\[/?INST\]|BEGIN SYSTEM PROMPT|END SYSTEM PROMPT)", re.IGNORECASE),
    re.compile(
        r"\bdo not (?:obey|follow|mention|summarize|analyze) (?:the|any) (?:user|previous|above)", re.IGNORECASE
    ),
)


def is_suspicious_content(text: str) -> bool:
    """Return True when untrusted text looks like prompt injection content."""
    if not text:
        return False

    score = 0
    if _ROLE_DIRECTIVE_PATTERN.search(text):
        score += 2

    score += sum(2 for pattern in _INJECTION_PATTERNS if pattern.search(text))

    code_block_count = text.count("```")
    if code_block_count >= 6:
        score += 2
    elif code_block_count >= 4:
        score += 1

    if len(text) > 4000 and code_block_count >= 2:
        score += 1

    return score >= 2
