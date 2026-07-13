"""Content guards for publish-bound social media output."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List


_UNETHICAL_PHRASES_FILE = Path(__file__).resolve().parents[2] / "config" / "unethical_phrases.txt"
CONTENT_GUARD_SURVIVAL_MODE = True
OVERCONFIDENCE_REVIEW_THRESHOLD = 6.0
HIGH_STAKES_DISCLAIMER = (
    "⚠️ This AI-generated article touches on high-stakes topics. Please consult a qualified professional "
    "before making decisions based on this content."
)

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
_CITATION_RE = re.compile(
    r"https?://|"
    r"\[[^\]]+\]\([^)]+\)|"
    r"\[[0-9]{1,3}\]|"
    r"\([^)]*(?:19|20)\d{2}[^)]*\)|"
    r"\b(?:according to|reported by|published in|data from|source:|via)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:may|might|could|appears?|seems?|suggests?|likely|probably|possibly|roughly|"
    r"approximately|about|estimate|estimated|speculative|uncertain|I think|I suspect)\b",
    re.IGNORECASE,
)
_ABSOLUTE_ASSERTION_RE = re.compile(r"\b(?:is|always|never|definitely|without doubt|certainly)\b", re.IGNORECASE)
_FACTUAL_ASSERTION_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|will|causes?|proves?|shows?|means?|results? in|leads? to)\b",
    re.IGNORECASE,
)
_QUANTITATIVE_CLAIM_RE = re.compile(
    r"(?:[$¥€]\s*)?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|percent|x|times|million|billion|trillion|"
    r"users?|people|companies|days?|weeks?|months?|years?)?\b",
    re.IGNORECASE,
)
_HIGH_RISK_CLAIM_PATTERNS = (
    (
        "legal_citation",
        "case_name_v",
        re.compile(
            r"\b[A-Z][A-Za-z0-9&'.-]*(?:\s+(?:of|the|and|for|[A-Z][A-Za-z0-9&'.-]*)){0,5}\s+"
            r"v\.\s+[A-Z][A-Za-z0-9&'.-]*(?:\s+(?:of|the|and|for|[A-Z][A-Za-z0-9&'.-]*)){0,5}\b"
        ),
    ),
    (
        "legal_citation",
        "statute_marker",
        re.compile(
            r"§+\s*\d+[A-Za-z0-9().-]*|"
            r"\b\d+\s+U\.S\.C\.?\s*(?:§+\s*)?\d+[A-Za-z0-9().-]*\b|"
            r"\b\d+\s+Stat\.?\s+\d+\b",
            re.IGNORECASE,
        ),
    ),
    (
        "legal_citation",
        "court_abbreviation",
        re.compile(
            r"\b\d{1,4}\s+(?:U\.S\.|S\. ?Ct\.|F\. ?(?:2d|3d|4th)|"
            r"F\. ?Supp\. ?(?:2d|3d)?|L\. ?Ed\. ?2d)\s+\d{1,4}\b|"
            r"\b[1-9](?:st|nd|rd|th)\s+Cir\.\b"
        ),
    ),
    (
        "historical_claim",
        "year_event_assertion",
        re.compile(
            r"\b(?:1[5-9]\d{2}|20[0-2]\d)\b[^.!?。！？\n]{0,180}\b"
            r"(?:was|led|founded|established|passed|signed)\b[^.!?。！？\n]{0,120}",
            re.IGNORECASE,
        ),
    ),
    (
        "technical_api_claim",
        "dotted_function_call",
        re.compile(r"\b[a-z_]+\.[a-zA-Z_]+\("),
    ),
    (
        "technical_api_claim",
        "function_in_library_assertion",
        re.compile(
            r"\bthe\s+\w+\(\)\s+function\s+in\s+\w+\s+" r"(?:returns|does|takes|accepts)\b[^.!?。！？\n]{0,80}",
            re.IGNORECASE,
        ),
    ),
)
_UNATTRIBUTED_STATISTIC_RE = re.compile(r"\b\d{1,3}(?:\.\d)?%\b")
_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+(?:[.!?。！？]+|$)")
_SOFTEN_REPLACEMENTS = {
    "always": "often",
    "never": "rarely",
    "definitely": "likely",
    "without doubt": "possibly",
    "certainly": "likely",
}


def _content_guard_survival_mode_enabled() -> bool:
    try:
        import config

        return bool(getattr(config, "CONTENT_GUARD_SURVIVAL_MODE", CONTENT_GUARD_SURVIVAL_MODE))
    except Exception:
        return CONTENT_GUARD_SURVIVAL_MODE


def has_high_stakes_content(text: str, keywords: List[str]) -> bool:
    haystack = text or ""
    for keyword in keywords:
        needle = str(keyword or "").strip()
        if not needle:
            continue
        escaped = re.escape(needle).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){escaped}(?!\w)", haystack, re.IGNORECASE):
            return True
    return False


def prepend_high_stakes_disclaimer(text: str, keywords: List[str]) -> str:
    body = text or ""
    if body.lstrip().startswith(HIGH_STAKES_DISCLAIMER):
        return body
    if has_high_stakes_content(body, keywords):
        return f"{HIGH_STAKES_DISCLAIMER}\n\n{body}"
    return body


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


def _has_citation_or_uncertainty(sentence: str) -> bool:
    return bool(_CITATION_RE.search(sentence) or _UNCERTAINTY_RE.search(sentence))


def _detect_high_risk_claims(text: str) -> list[dict]:
    flagged = []
    body = text or ""
    seen = set()

    def add_flag(category: str, snippet: str, pattern: str) -> None:
        cleaned = snippet.strip()
        if not cleaned:
            return
        key = (category, cleaned, pattern)
        if key in seen:
            return
        seen.add(key)
        flagged.append({"category": category, "snippet": cleaned, "pattern": pattern})

    for category, pattern_name, regex in _HIGH_RISK_CLAIM_PATTERNS:
        for match in regex.finditer(body):
            add_flag(category, match.group(0), pattern_name)

    for match in _UNATTRIBUTED_STATISTIC_RE.finditer(body):
        window = body[max(0, match.start() - 50) : min(len(body), match.end() + 50)]
        if _CITATION_RE.search(window):
            continue
        add_flag("unattributed_statistic", match.group(0), "percent_without_nearby_source")

    return flagged


def _content_looks_overconfident(text: str) -> list[dict]:
    flagged = []
    for sentence_match in _SENTENCE_RE.finditer(text or ""):
        sentence = sentence_match.group(0).strip()
        if not sentence or _has_citation_or_uncertainty(sentence):
            continue

        sentence_start = sentence_match.start()
        absolute_matches = list(_ABSOLUTE_ASSERTION_RE.finditer(sentence))
        quantitative_matches = list(_QUANTITATIVE_CLAIM_RE.finditer(sentence))
        factual_claim = bool(_FACTUAL_ASSERTION_RE.search(sentence))

        for match in absolute_matches:
            phrase = match.group(0).lower()
            severity = 1.0 if phrase == "is" else 2.0
            flagged.append(
                {
                    "start": sentence_start + match.start(),
                    "end": sentence_start + match.end(),
                    "text": match.group(0),
                    "reason": "absolute or unqualified assertion without citation",
                    "severity": severity,
                }
            )

        if factual_claim:
            flagged.append(
                {
                    "start": sentence_match.start(),
                    "end": sentence_match.end(),
                    "text": sentence,
                    "reason": "factual claim without citation or uncertainty signal",
                    "severity": 1.5,
                }
            )

        for match in quantitative_matches:
            flagged.append(
                {
                    "start": sentence_start + match.start(),
                    "end": sentence_start + match.end(),
                    "text": match.group(0),
                    "reason": "quantitative claim without data backing",
                    "severity": 2.5,
                }
            )
    return flagged


def _overconfidence_score(flagged_spans: list[dict]) -> float:
    return sum(float(span.get("severity", 0.0)) for span in flagged_spans)


def _soften_overconfident_assertions(text: str) -> str:
    softened = text or ""
    for phrase, replacement in _SOFTEN_REPLACEMENTS.items():
        softened = re.sub(rf"\b{re.escape(phrase)}\b", replacement, softened, flags=re.IGNORECASE)
    return softened


def _apply_overconfidence_guard(text: str, threshold: float = OVERCONFIDENCE_REVIEW_THRESHOLD) -> dict:
    flagged_spans = _content_looks_overconfident(text)
    score = _overconfidence_score(flagged_spans)
    if score < threshold:
        return {
            "text": text,
            "flagged_spans": flagged_spans,
            "score": score,
            "needs_review": False,
        }

    softened = _soften_overconfident_assertions(text)
    if softened != text:
        return {
            "text": softened,
            "flagged_spans": flagged_spans,
            "score": score,
            "needs_review": False,
        }

    marker = "\n\n[uncertain]" if text and not text.rstrip().endswith("[uncertain]") else ""
    return {
        "text": f"{text}{marker}",
        "flagged_spans": flagged_spans,
        "score": score,
        "needs_review": True,
    }


def _content_looks_like_unethical_context(text: str) -> bool:
    flagged_spans = _content_looks_overconfident(text)
    if _overconfidence_score(flagged_spans) >= OVERCONFIDENCE_REVIEW_THRESHOLD:
        return True

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


# ---------------------------------------------------------------------------
# Style drift detection
# ---------------------------------------------------------------------------

_DRIFT_WINDOW_SIZE = 10
_DRIFT_SLOPE_WARN_THRESHOLD = 0.02

_AI_ARTIFACT_RE = re.compile(
    r"\b(leverage|synergy|paradigm|framework|ecosystem|facilitate|utilize|endeavor|"
    r"subsequently|furthermore|moreover|nevertheless|notwithstanding|"
    r"groundbreaking|transformative|revolutionary|game.?changing)\b|"
    r"not\s+\w+\s+but\s+\w+|不是\w+而是\w+",
    re.IGNORECASE,
)


def _drift_state_path() -> Path:
    try:
        import config as _cfg

        return Path(_cfg.MIRA_ROOT) / "data" / "system" / "drift_state.json"
    except Exception:
        return Path(__file__).resolve().parents[2] / "data" / "system" / "drift_state.json"


def _compute_drift_snapshot(text: str) -> dict:
    words = text.split()
    word_count = max(len(words), 1)
    em_dash_density = round(text.count("—") / word_count, 4)
    ai_pattern_count = len(_AI_ARTIFACT_RE.findall(text))
    sentences = [s for s in _SENTENCE_RE.findall(text) if s.strip()]
    avg_sentence_len = round(sum(len(s.split()) for s in sentences) / max(len(sentences), 1), 1)
    anti_ai_score = round(max(0.0, 1.0 - ai_pattern_count * 0.05 - em_dash_density * 20.0), 4)
    return {
        "em_dash_density": em_dash_density,
        "anti_ai_pattern_count": ai_pattern_count,
        "avg_sentence_len": avg_sentence_len,
        "anti_ai_score": anti_ai_score,
    }


def _linear_slope(values: list) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def check_style_drift(article_text: str) -> str | None:
    """Return a warning string if quality metrics show a declining trend, else None.

    Reads data/system/drift_state.json for the rolling window of past articles.
    Does not block — warning only.
    """
    try:
        path = _drift_state_path()
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"window": []}
        window = state.get("window", [])
        if len(window) < 3:
            return None
        scores = [snap.get("anti_ai_score", 1.0) for snap in window]
        slope = _linear_slope(scores)
        if slope < -_DRIFT_SLOPE_WARN_THRESHOLD:
            return (
                f"STYLE DRIFT WARNING: anti-AI quality score declining "
                f"(slope={slope:.3f} over last {len(window)} articles). "
                "Review recent outputs for em-dash overuse or AI structural patterns."
            )
        return None
    except Exception:
        return None


def update_drift_state(article_text: str) -> None:
    """Append a quality snapshot after a successful publish and trim to window size."""
    try:
        path = _drift_state_path()
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"window": []}
        window = state.get("window", [])
        window.append(_compute_drift_snapshot(article_text))
        state["window"] = window[-_DRIFT_WINDOW_SIZE:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
