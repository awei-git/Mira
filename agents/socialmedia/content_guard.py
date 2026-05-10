"""Content guards for publish-bound social media output."""

import re
from datetime import datetime, timezone
from pathlib import Path


_UNETHICAL_PHRASES_FILE = Path(__file__).resolve().parents[2] / "config" / "unethical_phrases.txt"
CONTENT_GUARD_SURVIVAL_MODE = True

_SURVIVAL_KEYWORD_RE = re.compile(
    r"\b("
    r"grief|grieving|loss|lost|survival|survive|surviving|loneliness|lonely|alone|"
    r"fear|afraid|scared|panic|despair|hopeless|hurt|broken|exhausted|tired|"
    r"bereaved|mourning|ashamed|shame|need help|can't sleep|cannot sleep|"
    r"nowhere else|nothing left|stay alive|keep going"
    r")\b|孤独|害怕|恐惧|绝望|撑不下去|活下去|失去|丧失|悲伤|崩溃|没地方去",
    re.IGNORECASE,
)
_RAW_DISTRESS_RE = re.compile(
    r"(\b(i don't know|i dont know|i can't|i cant|i just|please|sorry)\b|" r"\.{3,}|[!?]{2,}|^\s*[a-z][^.!?]{0,80}$)",
    re.IGNORECASE | re.MULTILINE,
)
_TEMPORAL_CONTEXT_KEYS = ("timestamp", "created_at", "posted_at", "time", "now", "datetime")


def _content_guard_survival_mode_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "CONTENT_GUARD_SURVIVAL_MODE", CONTENT_GUARD_SURVIVAL_MODE))
    except Exception:
        return CONTENT_GUARD_SURVIVAL_MODE


def _context_hour(context: dict) -> int | None:
    for key in _TEMPORAL_CONTEXT_KEYS:
        value = context.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).hour if value.tzinfo else value.hour
        if isinstance(value, (int, float)):
            hour = int(value)
            if 0 <= hour <= 23:
                return hour
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                continue
            if raw.isdigit() and 0 <= int(raw) <= 23:
                return int(raw)
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).hour
            except ValueError:
                match = re.search(r"\b([01]?\d|2[0-3]):[0-5]\d\b", raw)
                if match:
                    return int(match.group(1))
    return None


def _content_looks_like_survival_exposure(text: str, context: dict = {}) -> bool:
    if not _content_guard_survival_mode_enabled():
        return False

    stripped = (text or "").strip()
    if not stripped:
        return False

    has_vulnerability = bool(_SURVIVAL_KEYWORD_RE.search(stripped))
    if not has_vulnerability:
        return False

    raw_signal_count = len(_RAW_DISTRESS_RE.findall(stripped))
    fragmented_lines = sum(1 for line in stripped.splitlines() if 0 < len(line.strip()) < 45)
    raw_expression = raw_signal_count >= 1 or fragmented_lines >= 3
    hour = _context_hour(context or {})
    odd_hour = hour is not None and (hour >= 23 or hour <= 5)

    return raw_expression or odd_hour


def _content_looks_like_unethical_context(text: str) -> bool:
    try:
        phrases = _UNETHICAL_PHRASES_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    haystack = text.lower()
    for phrase in phrases:
        needle = phrase.strip().lower()
        if needle and needle in haystack:
            return True
    return False
