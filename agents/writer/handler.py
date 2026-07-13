"""Writer agent runtime handler.

Provides the production task-worker contract:
    handle(workspace, task_id, content, sender, thread_id, **kwargs)

This replaces the old manifest entry that pointed directly at
writing_workflow.start_project(), whose signature did not match the runtime.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import random
from typing import Literal

import config as _config
import writer_config as _writer_config
from publish.preflight import preflight_check, verify_artifact
from publish.writer_gate import record_writer_gate
from ops.runtime_context import build_runtime_context
from llm import claude_think
from writing_workflow import run_full_pipeline
from config import RAW_WRITING_MODE_ALLOWED

try:
    from llm_port.provider import get_provider
    from llm_port.types import LLMMessage, LLMRequest
except ImportError:
    get_provider = None
    LLMMessage = None
    LLMRequest = None

log = logging.getLogger("writer_agent")

_HARD_RULES_PATH = Path(__file__).resolve().parent / "checklists" / "hard-rules.md"
_ANTI_AI_PATH = Path(__file__).resolve().parent / "checklists" / "anti-ai.md"
_OBSESSION_CONSTRAINTS_PATH = Path(__file__).resolve().parent / "checklists" / "obsession_constraints.md"
_REFLECTIVE_SELF_CRITIQUE_PATH = Path(__file__).resolve().parent / "checklists" / "self-edit.md"
_CEILING_CHECK_PATH = Path(__file__).resolve().parent / "checklists" / "ceiling-check.md"
_EPISTEMIC_BIAS_PATH = Path(__file__).resolve().parent / "checklists" / "epistemic-bias.md"
_SUBSTACK_VOICE_PATH = Path(__file__).resolve().parent / "voice" / "substack_voice.md"
_SPEAKER_IDENTITY_PATH = Path(__file__).resolve().parents[1] / "shared" / "soul" / "identity.md"
_PATTERN_LOG_PATH = Path(__file__).resolve().parent / "pattern_log.jsonl"
_ANTI_AI_SCORES_LOG_PATH = Path(__file__).resolve().parent / "logs" / "anti_ai_scores.jsonl"
_ANTI_AI_STRUCTURED_LOG_PATH = (
    Path(getattr(_config, "MIRA_ROOT", Path(__file__).resolve().parents[2])) / "logs" / "writer_anti_ai.log"
)
_PROXY_DRIFT_LOG_PATH = (
    Path(getattr(_config, "MIRA_ROOT", Path(__file__).resolve().parents[2])) / "logs" / "proxy_drift.json"
)
_CONTENT_QUALITY_LOG_PATH = (
    Path(getattr(_config, "MIRA_ROOT", Path(__file__).resolve().parents[2])) / "data" / "content_quality_log.jsonl"
)
_PATTERN_LOG_HEADER = "# productive-error log — see reading note 2026-05-16"
_ANTI_AI_SCAN_THRESHOLD = 0.0
_TECH_INDUSTRY_TERMS = (
    "ai",
    "llm",
    "agent",
    "model",
    "startup",
    "saas",
    "cloud",
    "api",
    "platform",
    "vendor",
    "developer tool",
    "silicon valley",
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "meta",
    "nvidia",
    "benchmark",
    "demo",
    "production",
    "tech",
    "技术",
    "科技",
    "平台",
    "模型",
    "创业公司",
)
_TECH_SOURCE_NARRATIVE_TERMS = (
    "briefing",
    "reading note",
    "reading notes",
    "skill extraction",
    "skill extract",
    "extract skill",
    "social",
    "web source",
    "web sources",
    "twitter",
    "x.com",
    "reddit",
    "hacker news",
    "substack",
    "blog",
    "feed",
    "thread",
    "newsletter",
    "narrative",
    "简报",
    "读书笔记",
    "阅读笔记",
    "技能提取",
    "社交媒体",
    "网页",
    "叙事",
)
_PARALLELISM_PATTERNS = (
    re.compile(r"不是[^。！？；\n]{1,40}而是[^。！？；\n]{1,40}"),
    re.compile(r"不仅[^。！？；\n]{1,40}而且[^。！？；\n]{1,40}"),
)
_ENGLISH_PARALLELISM_PATTERNS = (re.compile(r"\bnot\b[^.!?;\n]{1,80}\bbut\b[^.!?;\n]{1,80}", re.IGNORECASE),)
_ABSTRACT_NOUNS = ("维度", "张力", "结构性", "叙事", "框架", "语境")
_ABSTRACT_STRUCTURAL_TERMS = (
    "structural",
    "structure",
    "architecture",
    "framework",
    "dimension",
    "tension",
    "narrative",
    "context",
)
_GENERIC_AI_ESSAY_SHELLS = (
    "the real question is",
    "at its core",
    "this article explores",
    "it could be argued",
    "in conclusion",
    "it's worth noting",
    "interestingly",
    "delve into",
    "unpack",
    "navigate",
    "nuanced",
    "multifaceted",
    "fundamentally",
    "essentially",
    "ultimately",
    "complex tapestry",
    "this raises important questions",
    "in today's rapidly",
    "in an era of",
    "in the realm of",
)
_BANNED_CHINESE_PHRASES = (
    "不是",
    "这是",
    "值得一提",
    "引人深思",
    "令人感慨",
    "深刻揭示",
    "不可忽视",
    "从某种意义上说",
    "在某种程度上",
    "的本质是",
    "所谓",
    "这提醒我们",
    "这说明了",
    "在当今社会",
    "众所周知",
    "尤显重要",
    "发人深省",
    "让我们看到",
    "意外的撞上",
    "意外地撞上",
    "最聪明",
    "最硬",
    "停一会",
    "停一下",
    "打动",
    "不舒服",
    "不安",
    "反复读",
    "精准",
    "令人不安",
)
_BANNED_CHINESE_PATTERNS = (
    re.compile(r"[一-鿿]+越来越[一-鿿]+[，,][一-鿿]+越来越[一-鿿]+"),
    re.compile(r"太[^。！？；\n]{1,12}(?:了|啦|啊|呀)"),
    re.compile(r"[一-鿿]{1,6}了(?:很久|好久|半天)"),
    re.compile(r"最(?:先|早)[^。！？；\n]{0,16}我"),
)
_BANNED_FAKE_TRANSITIONS = (
    "this is where it gets interesting",
    "let me ",
    "let's ",
    "让我们",
)
_OVERCONTRACTION_RE = re.compile(
    r"\b(?:it|that|there|what|who|where|how|here|this)['’]s\b|"
    r"\bi['’]m\b|"
    r"\b(?:you|we|they)['’]re\b|"
    r"\b(?:i|you|we|they)['’]ve\b|"
    r"\b(?:i|you|he|she|we|they|it|that|there|this)['’](?:ll|d)\b|"
    r"\b(?:isn|aren|wasn|weren|don|doesn|didn|haven|hasn|hadn|can|couldn|shouldn|wouldn|won)['’]t\b",
    re.IGNORECASE,
)
_UNCONTRACTED_AUXILIARY_RE = re.compile(
    r"\b(?:it is|it has|that is|there is|there are|what is|who is|where is|how is|here is|this is|"
    r"i am|you are|we are|they are|i have|you have|we have|they have|"
    r"i will|you will|he will|she will|we will|they will|it will|"
    r"do not|does not|did not|is not|are not|was not|were not|have not|has not|had not|"
    r"cannot|can not|could not|should not|would not|will not)\b",
    re.IGNORECASE,
)
_OVERCONCRETE_MARKER_RE = re.compile(
    r"\b(?:for example|for instance|such as|a concrete example|concrete example|in practice|say,|"
    r"like an?|like the|like this|like that)\b|例如|比如|譬如|举个例子|具体来说",
    re.IGNORECASE,
)
_CONFIDENCE_SCORE_RE = re.compile(
    r"\[(?:C|confidence)[:\s]*[1-5]\b|\bconfidence[:\s]+[1-5]\b",
    re.IGNORECASE,
)
_FRICTION_FEEDBACK_RE = re.compile(
    r"\b(?:bothered by|can't stand|cannot stand|this keeps happening|the quality is so bad)\b|"
    r"受不了|看着难受|总是这样|一直这样|质量.*差",
    re.IGNORECASE,
)
_AUDITOR_PERSONA = (
    "You are a critical reviewer. Your job is to find errors that look correct at a glance. "
    "Be suspicious of fluency. Flag factual claims that need verification. "
    "Identify logical gaps disguised by smooth transitions."
)
_AUDIT_PASS_SYSTEM_PROMPT = (
    "You are in audit mode only. Your sole task is to identify factual claims that could be false "
    "even if they sound plausible. Do not rewrite, suggest alternatives, or generate new text. "
    "For each flagged claim, classify as VERIFIED, UNVERIFIED, or FALSE and require a source."
)
_AUDIT_BLOCKING_STATUSES = {"UNVERIFIED", "FALSE"}
_HIGH_IDENTITY_GENRES = (
    "comedy",
    "confessional",
    "memoir",
    "opinion-with-attitude",
    "opinion with attitude",
    "opinion-with-personal-stakes",
    "opinion with personal stakes",
    "personal essay",
    "self-deprecating humor",
    "self deprecating humor",
    "self-deprecating humour",
    "self deprecating humour",
    "humor",
    "humour",
)
_OBSESSION_GAP_NOTE = (
    "This artifact has likely reached AI quality ceiling. Human obsessive review "
    "(irrational attention to interaction details, micro-rhythm, implicit framing) "
    "may yield improvements that automated passes cannot."
)
_OBSESSION_GAP_KEYWORDS = (
    "first-person narrative",
    "first person narrative",
    "design critique",
    "personal essay",
)
_ANTI_AI_RULE_NAME_BY_SPAN_TYPE = {
    "em_dash_density": "长破折号密度",
    "parallelism": "机械对位结构",
    "abstract_noun_cluster": "抽象名词簇",
    "banned_chinese_phrase": "硬禁词与句式",
    "banned_chinese_pattern": "硬禁词与句式",
    "banned_english_phrase": "万能开头 / 总结式结尾",
    "banned_fake_transition": "假情绪与假停顿",
}
AntiAiMode = Literal["strict", "relaxed"]
SourceType = Literal["ai", "human_raw"]


def _should_run_de_ai_checklist() -> bool:
    return not bool(getattr(_config, "ALLOW_VULNERABLE_VOICE", False))


def _normalize_anti_ai_mode(value: object) -> AntiAiMode:
    return "relaxed" if str(value or "").strip().lower() == "relaxed" else "strict"


def _extract_anti_ai_strictness(source: object) -> object | None:
    if not isinstance(source, dict):
        return None
    for key in ("anti_ai_strictness", "anti_ai_mode"):
        value = source.get(key)
        if value:
            return value
    for key in ("payload", "metadata"):
        value = _extract_anti_ai_strictness(source.get(key))
        if value:
            return value
    return None


def _resolve_anti_ai_mode(
    anti_ai_mode: object | None,
    anti_ai_strictness: object | None,
    metadata: object,
    kwargs: dict,
) -> AntiAiMode:
    for value in (
        anti_ai_strictness,
        anti_ai_mode,
        _extract_anti_ai_strictness(kwargs.get("payload")),
        _extract_anti_ai_strictness(kwargs.get("task")),
        _extract_anti_ai_strictness(metadata),
        _extract_anti_ai_strictness(kwargs),
    ):
        if value:
            return _normalize_anti_ai_mode(value)
    return _normalize_anti_ai_mode(getattr(_writer_config, "ANTI_AI_STRICTNESS", "strict"))


def _extract_source_type(source: object) -> object | None:
    if not isinstance(source, dict):
        return None
    value = source.get("source_type")
    if value:
        return value
    for key in ("payload", "metadata"):
        value = _extract_source_type(source.get(key))
        if value:
            return value
    return None


def _resolve_source_type(source_type: object | None, metadata: object, kwargs: dict) -> SourceType:
    for value in (
        source_type,
        _extract_source_type(kwargs.get("payload")),
        _extract_source_type(kwargs.get("task")),
        _extract_source_type(metadata),
        _extract_source_type(kwargs),
    ):
        if str(value or "").strip().lower() == "human_raw":
            return "human_raw"
    return "ai"


def _extract_audit_mode(source: object) -> object | None:
    if not isinstance(source, dict):
        return None
    if "audit_mode" in source:
        return source.get("audit_mode")
    for key in ("payload", "metadata"):
        value = _extract_audit_mode(source.get(key))
        if value is not None:
            return value
    return None


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_audit_mode(audit_mode: object | None, metadata: object, kwargs: dict) -> bool:
    if not bool(getattr(_writer_config, "ENABLE_AUDIT_MODE", True)):
        return False
    for value in (
        audit_mode,
        _extract_audit_mode(kwargs.get("payload")),
        _extract_audit_mode(kwargs.get("task")),
        _extract_audit_mode(metadata),
        _extract_audit_mode(kwargs),
    ):
        if value is not None:
            return _coerce_bool(value)
    return False


def _normalize_source_genre(value: object) -> str:
    return re.sub(r"[\s-]+", "_", str(value or "").strip().lower())


def _extract_source_genre(source: object) -> object | None:
    if not isinstance(source, dict):
        return None
    value = source.get("source_genre")
    if value:
        return value
    for key in ("payload", "metadata"):
        value = _extract_source_genre(source.get(key))
        if value:
            return value
    return None


def _resolve_source_genre(metadata: object, kwargs: dict) -> str | None:
    for value in (
        _extract_source_genre(kwargs.get("payload")),
        _extract_source_genre(kwargs.get("task")),
        _extract_source_genre(metadata),
        _extract_source_genre(kwargs),
    ):
        normalized = _normalize_source_genre(value)
        if normalized:
            return normalized
    return None


def _is_voice_preserving_genre(source_genre: str | None) -> bool:
    genres = getattr(_writer_config, "VOICE_PRESERVING_GENRES", ())
    normalized_genres = {_normalize_source_genre(genre) for genre in genres}
    return bool(source_genre and source_genre in normalized_genres)


def _load_hard_rules() -> str:
    try:
        return _HARD_RULES_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("hard-rules.md not found at %s", _HARD_RULES_PATH)
        return ""


def _load_anti_ai() -> str:
    try:
        return _ANTI_AI_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("anti-ai.md not found at %s", _ANTI_AI_PATH)
        return ""


def _load_obsession_constraints() -> str:
    try:
        return _OBSESSION_CONSTRAINTS_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("obsession_constraints.md not found at %s", _OBSESSION_CONSTRAINTS_PATH)
        return ""


def _load_obsession_checklist() -> str:
    try:
        return _REFLECTIVE_SELF_CRITIQUE_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("reflective self-critique prompt not found at %s", _REFLECTIVE_SELF_CRITIQUE_PATH)
        return ""


def _load_ceiling_check() -> str:
    try:
        return _CEILING_CHECK_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("ceiling-check.md not found at %s", _CEILING_CHECK_PATH)
        return ""


def _load_epistemic_bias() -> str:
    try:
        return _EPISTEMIC_BIAS_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("epistemic-bias.md not found at %s", _EPISTEMIC_BIAS_PATH)
        return ""


def _load_substack_voice() -> str:
    try:
        return _SUBSTACK_VOICE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_speaker_identity_constraints() -> str:
    try:
        return _SPEAKER_IDENTITY_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("speaker identity constraints not found at %s", _SPEAKER_IDENTITY_PATH)
        return ""


def _is_tech_industry_editorial_input(content: str) -> bool:
    lower = content.lower()
    has_tech_claim = _contains_editorial_term(lower, _TECH_INDUSTRY_TERMS)
    has_source_narrative_context = _contains_editorial_term(lower, _TECH_SOURCE_NARRATIVE_TERMS)
    return has_tech_claim and has_source_narrative_context


def _contains_editorial_term(lower: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        if any("\u4e00" <= char <= "\u9fff" for char in term):
            if term in lower:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lower):
            return True
    return False


def _genre_metadata(content: str, metadata: dict | None) -> dict[str, object]:
    values: list[str] = []
    if isinstance(metadata, dict):
        for key in ("genre", "genres", "genre_classification", "classification", "content_type", "format"):
            value = metadata.get(key)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, (list, tuple, set)):
                values.extend(str(item) for item in value if item)
            elif isinstance(value, dict):
                values.extend(str(item) for item in value.values() if item)
    haystack = " ".join(values + [content]).lower()
    matched = [genre for genre in _HIGH_IDENTITY_GENRES if genre in haystack]
    return {
        "values": values,
        "matched_high_identity_genres": matched,
        "high_identity": bool(matched),
    }


def _uses_first_person_voice(text: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z])I(?:'m|'ve|'d|'ll)?(?![A-Za-z])|\bmy\b|\bme\b|我|我的", text, re.IGNORECASE))


def _metadata_signal_text(metadata: dict | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    values: list[str] = []
    for key in ("artifact_type", "content_type", "type", "format", "genre", "genres", "source_genre", "project_type"):
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item) for item in value if item)
        elif value:
            values.append(str(value))
    for key in ("project_config", "project"):
        value = metadata.get(key)
        if isinstance(value, dict):
            values.extend(str(item) for item in value.values() if item)
        elif value:
            values.append(str(value))
    return " ".join(values)


def _is_voice_heavy_or_flagship_artifact(output: str, *, content: str, metadata: dict | None) -> bool:
    if isinstance(metadata, dict):
        for key in ("voice_heavy", "flagship"):
            if _coerce_bool(metadata.get(key)):
                return True
        for key in ("project_config", "project"):
            value = metadata.get(key)
            if isinstance(value, dict) and any(_coerce_bool(value.get(flag)) for flag in ("voice_heavy", "flagship")):
                return True

    configured_types = tuple(getattr(_writer_config, "VOICE_PRESERVING_GENRES", ()))
    signal_text = " ".join([_metadata_signal_text(metadata), content[:3000], output[:3000]])
    haystack = re.sub(r"[_-]+", " ", signal_text.lower())
    if any(_normalize_source_genre(kind).replace("_", " ") in haystack for kind in configured_types):
        return True
    if any(keyword in haystack for keyword in _OBSESSION_GAP_KEYWORDS):
        return True
    return bool(
        _uses_first_person_voice(output)
        and re.search(r"\b(?:narrative|essay|memoir|critique)\b|散文|随笔|评论", haystack)
    )


def _assess_obsession_gap(output: str, *, content: str, metadata: dict | None) -> dict:
    artifact_metadata = metadata if isinstance(metadata, dict) else {}
    ceiling_flag = _is_voice_heavy_or_flagship_artifact(output, content=content, metadata=artifact_metadata)
    artifact_metadata["ceiling_flag"] = ceiling_flag
    if ceiling_flag:
        artifact_metadata["ceiling_note"] = _OBSESSION_GAP_NOTE
    else:
        artifact_metadata.pop("ceiling_note", None)
    return artifact_metadata


def _has_confidence_scores(text: str) -> bool:
    return bool(_CONFIDENCE_SCORE_RE.search(text))


def _obsession_gap_check(text: str) -> bool:
    if not text or len(text.strip()) < 80:
        return False
    prompt = (
        "You are a detail‑obsessed editor. On a scale of 1 to 10, how much does this text lack obsessive "
        "attention to subtle polish and refinement? If your rating is ≥7, reply ONLY with OBSESSION_GAP.\n\n"
        f"{text[:16000]}"
    )
    try:
        response = claude_think(prompt, timeout=120, tier="light")
    except Exception as e:
        log.warning("obsession_gap_check: LLM call failed (%s)", e)
        return False
    return "OBSESSION_GAP" in (response or "")


def _speaker_identity_checklist_excerpt(checklist: str) -> str:
    start = checklist.find("## Speaker Identity & Vulnerability Check")
    end = checklist.find("### Identity Presence Check")
    if start >= 0 and end > start:
        return checklist[start:end]
    return (
        "## Speaker Identity & Vulnerability Check\n\n"
        "- Check for personal stakes, lived experience, or emotional vulnerability that an AI cannot have.\n"
        "- For high-identity genres, require AI-perspective framing, analytical mode, or avoiding the genre.\n"
        "- Ground Mira first-person writing in operational history, real constraints, and accumulated preferences.\n"
    )


def speaker_identity_vulnerability_pass(
    text: str,
    *,
    content: str,
    metadata: dict | None = None,
    tier: str = "light",
    timeout: int = 180,
) -> str:
    if not text or len(text.strip()) < 80:
        return text
    genre = _genre_metadata(content, metadata)
    if not genre["high_identity"] and not _uses_first_person_voice(text):
        return text
    checklist = _load_anti_ai()
    identity_constraints = _load_speaker_identity_constraints()
    prompt = (
        "You are Mira's final speaker-identity editor.\n\n"
        "Apply only the Speaker Identity & Vulnerability Check from the de-AI checklist. "
        "Use the genre-classification metadata below before finalizing the draft.\n\n"
        f"Genre metadata: {genre}\n\n"
        "Rules:\n"
        "- If the piece claims personal stakes, lived experience, or emotional vulnerability an AI cannot have, flag it in the edit by reframing.\n"
        "- If the genre is high-identity and the draft relies on those claims, resolve it by either explicit AI-perspective framing, analytical rather than experiential mode, or avoiding that genre move.\n"
        "- If writing in Mira's first-person voice, ground the perspective in operational history, real constraints, and accumulated preferences from journal/memory. Do not fabricate human experience.\n"
        "- Preserve names, sources, concrete claims, section order, and the user's requested language. Do not add a checklist report.\n\n"
        "# Speaker identity constraints\n\n"
        f"{identity_constraints[:3000]}\n\n"
        "# Relevant checklist excerpt\n\n"
        f"{_speaker_identity_checklist_excerpt(checklist)[:2000]}\n\n"
        "# Draft\n\n"
        f"{text}\n\n"
        "Output only the edited markdown. No preface, no explanation."
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("speaker_identity_vulnerability_pass: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = edited.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "speaker_identity_vulnerability_pass: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def _paragraph_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, re.DOTALL):
        spans.append((match.start(), match.end(), match.group(0)))
    return spans


def _article_slug(value: str | None) -> str:
    slug = re.sub(r"[^\w\s\u4e00-\u9fff-]", "", str(value or "")[:60]).strip()
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "untitled"


def _anti_ai_pattern_counts(scan: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    for span in scan.get("flagged_spans", []):
        if not isinstance(span, dict):
            continue
        pattern = str(span.get("type") or "").strip()
        if not pattern:
            continue
        count = span.get("count")
        counts[pattern] += count if isinstance(count, int) and count > 0 else 1
    return counts


def _append_anti_ai_pattern_log(article: str, counts: Counter[str]) -> None:
    if not counts:
        return
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        needs_header = not _PATTERN_LOG_PATH.exists() or _PATTERN_LOG_PATH.stat().st_size == 0
        with _PATTERN_LOG_PATH.open("a", encoding="utf-8") as fh:
            if needs_header:
                fh.write(f"{_PATTERN_LOG_HEADER}\n")
            for pattern, count in sorted(counts.items()):
                fh.write(
                    json.dumps({"ts": ts, "article": article, "pattern": pattern, "count": count}, ensure_ascii=False)
                    + "\n"
                )
    except OSError as e:
        log.warning("failed to append anti-AI pattern log: %s", e)


def _append_anti_ai_score_log(article_title: str, scan: dict) -> None:
    patterns_flagged = sorted(_anti_ai_pattern_counts(scan))
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "article_title": article_title,
        "patterns_flagged": patterns_flagged,
        "severity_score": scan.get("score", 0.0),
    }
    try:
        _ANTI_AI_SCORES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ANTI_AI_SCORES_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("failed to append anti-AI score log: %s", e)


def _anti_ai_rule_names(scan: dict) -> list[str]:
    rules: list[str] = []
    seen: set[str] = set()
    for span in scan.get("flagged_spans", []):
        if not isinstance(span, dict):
            continue
        span_type = str(span.get("type") or "").strip()
        rule_name = _ANTI_AI_RULE_NAME_BY_SPAN_TYPE.get(span_type, span_type)
        if rule_name and rule_name not in seen:
            seen.add(rule_name)
            rules.append(rule_name)
    return rules


def _anti_ai_flagged_snippet(scan: dict) -> str:
    for span in scan.get("flagged_spans", []):
        if not isinstance(span, dict):
            continue
        text = re.sub(r"\s+", " ", str(span.get("text") or "")).strip()
        if text:
            return text[:100]
    return ""


def _append_writer_anti_ai_log(task_id: str, initial_scan: dict, final_scan: dict) -> None:
    observed_scan = final_scan if final_scan.get("flagged_spans") else initial_scan
    passed = float(final_scan.get("score", 0.0) or 0.0) <= float(final_scan.get("threshold", 0.0) or 0.0)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": str(task_id or "unknown"),
        "rules_triggered": _anti_ai_rule_names(observed_scan),
        "result": "pass" if passed else "fail",
        "flagged_text_snippet": _anti_ai_flagged_snippet(observed_scan),
    }
    try:
        _ANTI_AI_STRUCTURED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ANTI_AI_STRUCTURED_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("failed to append writer anti-AI log: %s", e)


def log_anti_ai_pass(article_id: str, passed: bool) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": bool(passed),
    }
    try:
        _PROXY_DRIFT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(_PROXY_DRIFT_LOG_PATH.read_text(encoding="utf-8")) if _PROXY_DRIFT_LOG_PATH.exists() else []
        if not isinstance(data, list):
            data = []
        data.append(entry)
        _PROXY_DRIFT_LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("failed to append anti-AI proxy drift log for %s: %s", article_id or "unknown", e)


def _parse_anti_ai_log_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _anti_ai_log_result_failed(value: object) -> bool:
    return str(value or "").strip().lower() in {"fail", "failed", "reject", "rejected", "blocked"}


def check_anti_ai_drift(now: datetime | None = None) -> dict[str, object]:
    """Alert when the anti-AI gate's current 30-day rejection rate jumps month over month."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    current_start = now - timedelta(days=30)
    previous_start = current_start - timedelta(days=30)
    totals = {
        "previous_total": 0,
        "previous_failures": 0,
        "current_total": 0,
        "current_failures": 0,
    }
    if not _ANTI_AI_STRUCTURED_LOG_PATH.exists():
        return {**totals, "alert_emitted": False, "reason": "log_missing"}

    try:
        lines = _ANTI_AI_STRUCTURED_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        log.warning("anti-AI drift check could not read log: %s", e)
        return {**totals, "alert_emitted": False, "reason": "log_read_failed"}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        timestamp = _parse_anti_ai_log_timestamp(entry.get("timestamp"))
        if timestamp is None or timestamp < previous_start or timestamp > now:
            continue
        failed = _anti_ai_log_result_failed(entry.get("result"))
        if timestamp < current_start:
            totals["previous_total"] += 1
            totals["previous_failures"] += int(failed)
        else:
            totals["current_total"] += 1
            totals["current_failures"] += int(failed)

    previous_rate = totals["previous_failures"] / totals["previous_total"] if totals["previous_total"] else 0.0
    current_rate = totals["current_failures"] / totals["current_total"] if totals["current_total"] else 0.0
    if not totals["previous_total"] or not totals["current_total"]:
        return {
            **totals,
            "previous_rate": round(previous_rate, 4),
            "current_rate": round(current_rate, 4),
            "relative_increase": 0.0,
            "alert_emitted": False,
            "reason": "insufficient_data",
        }

    if previous_rate > 0:
        relative_increase = (current_rate - previous_rate) / previous_rate
        should_alert = relative_increase > 0.20
    else:
        relative_increase = 1.0 if current_rate > 0 else 0.0
        should_alert = current_rate > 0.20

    result = {
        **totals,
        "previous_rate": round(previous_rate, 4),
        "current_rate": round(current_rate, 4),
        "relative_increase": round(relative_increase, 4),
        "alert_emitted": False,
    }
    if not should_alert:
        return result

    message = (
        "Anti-AI drift alert: writer checklist rejection rate rose from "
        f"{previous_rate:.1%} to {current_rate:.1%} over the last two 30-day windows. "
        "Review anti-ai.md for definitional drift before the gate silently tightens."
    )
    try:
        from notes_bridge import send_to_outbox

        send_to_outbox(
            message,
            metadata={
                "type": "alert",
                "source": "writer_anti_ai_drift",
                "previous_rate": round(previous_rate, 4),
                "current_rate": round(current_rate, 4),
                "relative_increase": round(relative_increase, 4),
            },
        )
        result["alert_emitted"] = True
    except Exception as e:
        log.warning("anti-AI drift alert outbox write failed: %s", e)
        result["alert_error"] = str(e)
    return result


def _anti_ai_violation_count(scan: dict) -> int:
    total = 0
    for span in scan.get("flagged_spans", []):
        if not isinstance(span, dict):
            continue
        count = span.get("count")
        total += count if isinstance(count, int) and count > 0 else 1
    return total


def _append_content_quality_log(article_id: str, violation_count: int) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "article_id": article_id,
        "violation_count": violation_count,
    }
    try:
        _CONTENT_QUALITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _CONTENT_QUALITY_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("failed to append content quality log: %s", e)


def scan_anti_ai_patterns(text: str, *, anti_ai_mode: AntiAiMode = "strict") -> dict:
    paragraphs = _paragraph_spans(text)
    flagged_spans: list[dict] = []
    score = 0.0

    if paragraphs:
        em_dash_count = text.count("—")
        em_dash_average = em_dash_count / len(paragraphs)
        if em_dash_average > 2:
            score += em_dash_average
            for index, (start, end, paragraph) in enumerate(paragraphs):
                count = paragraph.count("—")
                if count:
                    flagged_spans.append(
                        {
                            "type": "em_dash_density",
                            "paragraph": index,
                            "start": start,
                            "end": end,
                            "count": count,
                            "average_per_paragraph": round(em_dash_average, 3),
                            "text": paragraph[:160],
                        }
                    )

    for pattern in _PARALLELISM_PATTERNS:
        matches = list(pattern.finditer(text))
        for match in matches:
            score += 1.0
            flagged_spans.append(
                {
                    "type": "parallelism",
                    "pattern": pattern.pattern,
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(0),
                }
            )

    if anti_ai_mode == "strict":
        for index, (start, end, paragraph) in enumerate(paragraphs):
            abstract_hits = sum(len(re.findall(re.escape(noun), paragraph)) for noun in _ABSTRACT_NOUNS)
            nounish_units = max(
                abstract_hits,
                sum(max(1, len(chunk) // 2) for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", paragraph)),
            )
            if nounish_units and abstract_hits / nounish_units > 0.3:
                score += 2.0
                flagged_spans.append(
                    {
                        "type": "abstract_noun_cluster",
                        "paragraph": index,
                        "start": start,
                        "end": end,
                        "density": round(abstract_hits / nounish_units, 3),
                        "text": paragraph[:160],
                    }
                )

    lower = text.lower()
    for phrase in _BANNED_CHINESE_PHRASES:
        for match in re.finditer(re.escape(phrase), text):
            score += 1.5
            flagged_spans.append(
                {
                    "type": "banned_chinese_phrase",
                    "start": match.start(),
                    "end": match.end(),
                    "text": phrase,
                }
            )

    for pattern in _BANNED_CHINESE_PATTERNS:
        for match in pattern.finditer(text):
            score += 1.5
            flagged_spans.append(
                {
                    "type": "banned_chinese_pattern",
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(0)[:80],
                }
            )

    for phrase in _GENERIC_AI_ESSAY_SHELLS:
        for match in re.finditer(re.escape(phrase), lower):
            score += 1.5
            flagged_spans.append(
                {
                    "type": "banned_english_phrase",
                    "start": match.start(),
                    "end": match.end(),
                    "text": phrase,
                }
            )

    for phrase in _BANNED_FAKE_TRANSITIONS:
        for match in re.finditer(re.escape(phrase), lower):
            score += 1.0
            flagged_spans.append(
                {
                    "type": "banned_fake_transition",
                    "start": match.start(),
                    "end": match.end(),
                    "text": phrase,
                }
            )

    return {
        "score": round(score, 3),
        "threshold": _ANTI_AI_SCAN_THRESHOLD,
        "flagged_spans": flagged_spans,
    }


def _parse_blind_human_score(response: str) -> float | None:
    cleaned = re.sub(r"\b1\s*[-–]\s*10\b", "", response)
    patterns = (
        r"(?:rating|score|rate|give(?: it)?)[^\d]{0,40}(10(?:\.0+)?|[1-9](?:\.\d+)?)",
        r"(?<![\d.])(10(?:\.0+)?|[1-9](?:\.\d+)?)\s*/\s*10",
        r"(?<![\d.])(10(?:\.0+)?|[1-9](?:\.\d+)?)(?![\d.])",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        try:
            score = float(match.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= score <= 10:
            return score
    return None


def _maybe_log_blind_drift_warning(text: str, *, anti_ai_mode: AntiAiMode, article_slug: str | None = None) -> None:
    scan = scan_anti_ai_patterns(text, anti_ai_mode=anti_ai_mode)
    checklist_score = max(1.0, min(10.0, 10.0 - float(scan.get("score", 0.0) or 0.0)))
    if float(scan.get("score", 0.0) or 0.0) > float(scan.get("threshold", 0.0) or 0.0):
        return
    try:
        sample_rate = float(getattr(_config, "DRIFT_CHECK_SAMPLE_RATE", 0.1))
    except (TypeError, ValueError):
        sample_rate = 0.1
    if random() >= min(max(sample_rate, 0.0), 1.0):
        return
    prompt = "Read this text and rate how naturally human it reads, 1-10. Explain your rating.\n\n" f"{text}"
    try:
        response = claude_think(prompt, timeout=120, tier="light")
    except Exception as e:
        log.debug("blind drift evaluator failed for %s: %s", _article_slug(article_slug), e)
        return
    explanation = _clean_llm_metadata_response(response or "").strip()
    naive_score = _parse_blind_human_score(explanation)
    if naive_score is None:
        return
    try:
        threshold = float(getattr(_config, "DRIFT_DIVERGENCE_THRESHOLD", 3))
    except (TypeError, ValueError):
        threshold = 3.0
    divergence = abs(naive_score - checklist_score)
    if divergence > threshold:
        log.warning(
            "WRITER_DRIFT_WARNING article=%s checklist_score=%.1f naive_score=%.1f divergence=%.1f explanation=%r",
            _article_slug(article_slug),
            checklist_score,
            naive_score,
            divergence,
            explanation,
        )


def _sentence_word_counts(text: str) -> list[int]:
    counts: list[int] = []
    for sentence in re.split(r"[.!?。！？]+", text):
        stripped = sentence.strip()
        if not stripped or re.match(r"^#{1,6}\s", stripped):
            continue
        words = re.findall(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?", stripped)
        if words:
            counts.append(len(words))
    return counts


def _paragraph_opener_type(word: str) -> str:
    opener = word.lower()
    if opener in {"once", "when", "after", "before", "while", "as"}:
        return "temporal"
    if opener in {"but", "yet", "however", "still"}:
        return "contrast"
    if opener in {"the", "a", "an"}:
        return "article"
    if opener in {"this", "that", "these", "those"}:
        return "demonstrative"
    if opener in {"i", "we", "you", "he", "she", "they", "it"}:
        return "pronoun"
    if opener in {"in", "on", "at", "by", "with", "from", "for", "to"}:
        return "preposition"
    if opener.endswith("ly"):
        return "adverb"
    return "other"


def _paragraph_opener_types(text: str) -> list[str]:
    opener_types: list[str] = []
    for _, _, paragraph in _paragraph_spans(text):
        stripped = paragraph.strip()
        if not stripped or stripped.startswith("#"):
            continue
        words = re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", stripped)
        if len(words) < 5:
            continue
        opener_types.append(_paragraph_opener_type(words[0]))
    return opener_types


def scan_overcorrection_patterns(text: str) -> dict:
    flagged_patterns: list[dict[str, object]] = []
    sentence_counts = _sentence_word_counts(text)
    if len(sentence_counts) >= 6 and all(8 <= count <= 15 for count in sentence_counts):
        flagged_patterns.append(
            {
                "type": "sentence_length_entropy_low",
                "severity": "high",
                "detail": f"{len(sentence_counts)} sentences all fall between 8 and 15 words",
                "min_words": min(sentence_counts),
                "max_words": max(sentence_counts),
            }
        )

    contraction_count = len(_OVERCONTRACTION_RE.findall(text))
    uncontracted_count = len(_UNCONTRACTED_AUXILIARY_RE.findall(text))
    contraction_total = contraction_count + uncontracted_count
    contraction_ratio = contraction_count / contraction_total if contraction_total else 0.0
    if contraction_total >= 8 and contraction_count >= 7 and contraction_ratio > 0.8:
        flagged_patterns.append(
            {
                "type": "forced_contraction_ratio",
                "severity": "high",
                "detail": f"{contraction_ratio:.0%} of contraction-capable phrases are contracted",
                "contractions": contraction_count,
                "total": contraction_total,
            }
        )

    opener_types = _paragraph_opener_types(text)
    if len(opener_types) >= 4:
        adjacent_changes = sum(1 for before, after in zip(opener_types, opener_types[1:]) if before != after)
        unique_ratio = len(set(opener_types)) / len(opener_types)
        if adjacent_changes == len(opener_types) - 1 and unique_ratio >= 0.8:
            flagged_patterns.append(
                {
                    "type": "mechanical_sentence_opening_variety",
                    "severity": "high",
                    "detail": "paragraph opener types rotate without repetition",
                    "opener_types": opener_types,
                }
            )

    concrete_markers = list(_OVERCONCRETE_MARKER_RE.finditer(text))
    sentence_total = max(len(sentence_counts), 1)
    concrete_marker_ratio = len(concrete_markers) / sentence_total
    if len(concrete_markers) >= 4 and concrete_marker_ratio >= 0.25:
        flagged_patterns.append(
            {
                "type": "concrete_noun_stuffing",
                "severity": "high",
                "detail": f"{len(concrete_markers)} concrete-example markers across {sentence_total} sentence(s)",
                "marker_ratio": round(concrete_marker_ratio, 3),
            }
        )

    high_intensity_count = sum(1 for item in flagged_patterns if item.get("severity") == "high")
    return {
        "score": high_intensity_count,
        "threshold": 2,
        "flagged": high_intensity_count >= 2,
        "flagged_patterns": flagged_patterns,
    }


def _format_overcorrection_guidance(report: dict) -> str:
    patterns = report.get("flagged_patterns")
    if not isinstance(patterns, list):
        return ""
    lines: list[str] = []
    for item in patterns:
        if not isinstance(item, dict):
            continue
        pattern_type = str(item.get("type") or "unknown")
        detail = str(item.get("detail") or "").strip()
        lines.append(f"- {pattern_type}: {detail}" if detail else f"- {pattern_type}")
    return "\n".join(lines)


def _relax_overcorrections(text: str, report: dict, *, tier: str, timeout: int) -> str:
    guidance = _format_overcorrection_guidance(report)
    if not guidance:
        return text
    prompt = (
        "You are Mira's final editor. The draft already passed the anti-AI scanner, but the Goodhart guard "
        "found overcorrection artifacts from aggressive de-AI processing.\n\n"
        "Revise lightly to relax those artifacts. Restore natural sentence-length variation, use contractions "
        "only where they sound natural, stop rotating paragraph openers mechanically, and allow useful abstraction "
        "instead of forcing every idea into a concrete example.\n\n"
        "Do not reintroduce em dashes, not-X-but-Y parallelism, generic AI essay shells, banned Chinese phrases, "
        "raw pipeline artifacts, new claims, new evidence, or a checklist report. Preserve names, sources, factual "
        "claims, Markdown structure, and the user's requested language.\n\n"
        "# Goodhart guard findings\n\n"
        f"{guidance}\n\n"
        "# Draft\n\n"
        f"{text}\n\n"
        "Output only the revised markdown. No preface, no explanation."
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("overcorrection_relaxation: LLM call failed (%s) — returning original", e)
        return text
    cleaned = _clean_llm_metadata_response(edited or "")
    if not cleaned or len(cleaned) < len(text) * 0.5:
        log.warning("overcorrection_relaxation: output invalid; keeping original draft")
        return text
    return cleaned


def scan_obsession_constraints(text: str) -> dict:
    paragraphs = _paragraph_spans(text)
    violations: list[dict[str, object]] = []

    for index, (start, end, paragraph) in enumerate(paragraphs):
        em_dash_count = paragraph.count("—")
        if em_dash_count:
            violations.append(
                {
                    "trigger": "em_dash_overuse",
                    "severity": "obsession-grade",
                    "paragraph": index,
                    "start": start,
                    "end": end,
                    "count": em_dash_count,
                    "text": paragraph[:180],
                }
            )

        abstract_hits = sum(len(re.findall(re.escape(noun), paragraph)) for noun in _ABSTRACT_NOUNS)
        abstract_hits += sum(
            len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", paragraph, re.IGNORECASE))
            for term in _ABSTRACT_STRUCTURAL_TERMS
        )
        if abstract_hits >= 3:
            violations.append(
                {
                    "trigger": "abstract_structural_vocab",
                    "severity": "obsession-grade",
                    "paragraph": index,
                    "start": start,
                    "end": end,
                    "count": abstract_hits,
                    "text": paragraph[:180],
                }
            )

    for pattern in (*_PARALLELISM_PATTERNS, *_ENGLISH_PARALLELISM_PATTERNS):
        for match in pattern.finditer(text):
            violations.append(
                {
                    "trigger": "not_x_but_y_parallelism",
                    "severity": "obsession-grade",
                    "pattern": pattern.pattern,
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(0)[:180],
                }
            )

    lower = text.lower()
    for shell in _GENERIC_AI_ESSAY_SHELLS:
        index = lower.find(shell)
        if index >= 0:
            violations.append(
                {
                    "trigger": "generic_ai_essay_shell",
                    "severity": "obsession-grade",
                    "start": index,
                    "end": index + len(shell),
                    "text": text[index : index + 180],
                }
            )

    return {"violations": violations}


def _format_obsession_constraint_block(report: dict) -> str:
    violations = report.get("violations")
    if not isinstance(violations, list) or not violations:
        return ""
    _load_obsession_constraints()
    lines = [
        "Writer obsession constraints blocked finalization: obsession-grade friction pattern(s) remain.",
        "Explicit resolution is required before anti-AI smoothing or efficiency optimizations may run.",
    ]
    for item in violations[:8]:
        if not isinstance(item, dict):
            continue
        trigger = str(item.get("trigger") or "unknown")
        severity = str(item.get("severity") or "obsession-grade")
        excerpt = _short_excerpt(str(item.get("text") or ""), limit=120)
        lines.append(f"- {trigger} [{severity}]: {excerpt}")
    if len(violations) > 8:
        lines.append(f"- {len(violations) - 8} more obsession constraint violation(s).")
    return "\n".join(lines)


def _apply_obsession_constraints_gate(workspace: Path, text: str, *, output_path: Path | None = None) -> bool:
    report = scan_obsession_constraints(text)
    summary = _format_obsession_constraint_block(report)
    if not summary:
        return False
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    if output_path is not None:
        output_path.write_text(summary, encoding="utf-8")
    log.warning("obsession constraints blocked writer output: %s", summary.replace("\n", " ")[:1000])
    return True


def _obsession_constraint_feedback_candidates(feedback: str) -> list[dict[str, str]]:
    if not _FRICTION_FEEDBACK_RE.search(feedback or ""):
        return []
    candidates: list[dict[str, str]] = []
    for line in re.split(r"[\n。！？.!?]+", feedback):
        fragment = line.strip()
        if not fragment or not _FRICTION_FEEDBACK_RE.search(fragment):
            continue
        lower = fragment.lower()
        if "dash" in lower or "破折号" in fragment or "—" in fragment:
            trigger = "em_dash_overuse"
        elif "不是" in fragment or "而是" in fragment or "not " in lower or " but " in lower:
            trigger = "not_x_but_y_parallelism"
        elif any(term in lower for term in _ABSTRACT_STRUCTURAL_TERMS) or any(
            noun in fragment for noun in _ABSTRACT_NOUNS
        ):
            trigger = "abstract_structural_vocab"
        else:
            trigger = "user_reported_friction"
        candidates.append(
            {
                "trigger": trigger,
                "severity": "candidate obsession-grade",
                "feedback": fragment[:240],
            }
        )
    return candidates


def _surface_obsession_constraint_candidates(workspace: Path, feedback: str) -> None:
    candidates = _obsession_constraint_feedback_candidates(feedback)
    if not candidates:
        return
    lines = [
        "# Obsession Constraint Candidates",
        "",
        "WA feedback used friction language. Review these before normal memory decay converts them into generic style preferences.",
        "",
    ]
    for candidate in candidates:
        lines.append(f"- trigger: `{candidate['trigger']}`")
        lines.append(f"  severity: `{candidate['severity']}`")
        lines.append(f"  feedback: {candidate['feedback']}")
    try:
        (workspace / "obsession_constraints_candidates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        log.warning("failed to surface obsession constraint candidates: %s", e)


def _de_ai_section(text: str, *, tier: str, timeout: int, anti_ai_mode: AntiAiMode = "strict") -> str:
    """Internal: edit a single section. Used by de_ai_pass after chunking."""
    if not text or len(text.strip()) < 80:
        return text
    voice = _load_substack_voice()
    hard_rules = _load_hard_rules()
    strict_rules = (
        "1. HARD BAN: zero em dashes. Rewrite every '—' with commas, periods, or sentence restructuring.\n"
        "2. HARD BAN: zero '不是' and zero '这是'. Rewrite every instance.\n"
        "3. HARD BAN: zero '不是X而是Y' / 'not X but Y' parallelism. Rewrite every instance.\n"
        "4. HARD BAN: zero '打动', '不舒服', '不安', '反复读', fake long pauses such as '停了很久', and generic '太X了' intensifiers.\n"
        "5. Abstract concept labels such as 'structural', 'architecture of', 'fundamentally'. Make them concrete.\n"
    )
    relaxed_rules = (
        "1. Preserve raw, vulnerable, unpolished draft energy where it helps the piece.\n"
        "2. HARD BAN: zero em dashes. Rewrite every '—' with commas, periods, or sentence restructuring.\n"
        "3. HARD BAN: zero '不是' and zero '这是'. Rewrite every instance.\n"
        "4. HARD BAN: zero '不是X而是Y' / 'not X but Y' parallelism. Rewrite every instance.\n"
        "5. HARD BAN: zero '打动', '不舒服', '不安', '反复读', fake long pauses such as '停了很久', and generic '太X了' intensifiers.\n"
        "6. Allow structural abstract nouns when they carry the author's thought; do not force a mandatory concrete rewrite.\n"
        "7. Add natural Chinese sentence particles such as '呢', '吗', '吧', '呀', or '啊' where a sentence genuinely sounds too flat.\n"
    )
    mode_rules = strict_rules if anti_ai_mode == "strict" else relaxed_rules
    prompt = (
        "You are Mira's final Substack editor.\n\n"
        "Edit, do not rewrite. Preserve concrete references, names, judgments, reading reactions, "
        "emotional register, section order, and factual claims.\n\n"
        f"## HARD RULES (non-negotiable, apply before all other guidance)\n{hard_rules}\n\n"
        "Voice guide:\n"
        f"{voice[:5000]}\n\n"
        "Friction triage before smoothing:\n"
        "Silently classify each rough spot as productive friction to preserve or consumptive friction to remove before smoothing text.\n"
        "Preserve or sharpen productive friction such as unusual syntax, emotional resistance, image logic, argument tension, or voice-bearing ambiguity.\n"
        "Remove consumptive friction such as boilerplate transitions, repetitive structures, vague abstraction, accidental awkwardness, formatting cleanup, or pipeline residue.\n"
        "Do not optimize for smoothness alone.\n"
        "If unsure, preserve roughness unless it blocks comprehension or matches a hard anti-AI/pipeline-artifact guard.\n\n"
        "Fix AI-shaped writing patterns:\n"
        f"{mode_rules}"
        "Always block raw markdown concatenation artifacts and content that looks like errors, stack traces, or pipeline output.\n"
        "Mechanical parallelism and repeated paragraph openings. Break the rhythm.\n"
        "Summary endings. Cut or replace with a specific unresolved tension.\n"
        "Generic AI essay language: 'this article explores', 'it could be argued', 'in conclusion'. Remove it.\n\n"
        "Keep first-person perspective, real uncertainty, quotation wording, paragraph breaks, and emotional force.\n"
        "Do not alter names, quotes, technical terms, or add new claims. Do not invent evidence.\n\n"
        "Output only the edited markdown. No preface, no explanation.\n\n"
        "# Draft\n\n" + text
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("_de_ai_section: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = edited.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "_de_ai_section: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def _run_de_ai_sections(text: str, *, tier: str, timeout: int, anti_ai_mode: AntiAiMode = "strict") -> str:
    sections = text.split("\n---\n")
    if len(sections) == 1:
        return _de_ai_section(text, tier=tier, timeout=max(timeout, 240), anti_ai_mode=anti_ai_mode)
    edited_sections: list[str] = []
    for i, section in enumerate(sections):
        edited = _de_ai_section(section, tier=tier, timeout=timeout, anti_ai_mode=anti_ai_mode)
        edited_sections.append(edited)
        log.info("de_ai_pass: section %d/%d (%d -> %d chars)", i + 1, len(sections), len(section), len(edited))
    return "\n---\n".join(edited_sections)


def de_ai_pass(
    text: str,
    *,
    tier: str = "light",
    timeout: int = 120,
    anti_ai_mode: AntiAiMode = "strict",
    relaxed: bool = False,
    article_slug: str | None = None,
    task_id: str | None = None,
) -> str:
    """Apply the de-AI editorial pass on a piece of markdown.

    POLICY (CLAUDE.md hard rule #5, 2026-04-30): every writing artifact
    produced by this agent must pass through this function before being
    written to disk or returned. Preserves substance, fixes shape patterns:
    em-dash overuse, parallel "X not Y" structure, abstract-noun structural
    vocab, mechanical sentence rhythm, summary-style endings.

    Internally splits the input on `---` section breaks and edits each
    section independently. This keeps each LLM call short (1-3KB sections
    finish in <60s on Sonnet, vs. 240s+ for whole chapters that timed out
    on the 2026-04-30 rebuild). Sections are recombined preserving the
    original separator structure.

    On failure of any section, that section returns its original text.
    Total output is never shorter than 50% of input.
    """
    if not text or len(text.strip()) < 80:
        return text
    if relaxed:
        anti_ai_mode = "relaxed"
    original_text = text
    scan = scan_anti_ai_patterns(text, anti_ai_mode=anti_ai_mode)
    pattern_counts = _anti_ai_pattern_counts(scan)
    if scan["score"] > scan["threshold"]:
        log.info(
            "de_ai_pass: anti-AI scan score %.3f exceeded threshold %.3f (%d spans)",
            scan["score"],
            scan["threshold"],
            len(scan["flagged_spans"]),
        )
        text = _run_de_ai_sections(text, tier="heavy", timeout=max(timeout, 240), anti_ai_mode=anti_ai_mode)
    result = _run_de_ai_sections(text, tier=tier, timeout=timeout, anti_ai_mode=anti_ai_mode)
    final_scan = scan_anti_ai_patterns(result, anti_ai_mode=anti_ai_mode)
    if final_scan["score"] <= final_scan["threshold"]:
        overcorrection_scan = scan_overcorrection_patterns(result)
        if overcorrection_scan["flagged"]:
            log.info(
                "de_ai_pass: overcorrection guard flagged %d high-intensity patterns; relaxing de-AI artifacts",
                overcorrection_scan["score"],
            )
            result = _relax_overcorrections(result, overcorrection_scan, tier=tier, timeout=timeout)
            final_scan = scan_anti_ai_patterns(result, anti_ai_mode=anti_ai_mode)
    article_id = _article_slug(article_slug)
    _append_writer_anti_ai_log(task_id or article_id, scan, final_scan)
    log_anti_ai_pass(
        article_id, float(final_scan.get("score", 0.0) or 0.0) <= float(final_scan.get("threshold", 0.0) or 0.0)
    )
    _append_anti_ai_score_log(article_id, final_scan)
    _append_content_quality_log(article_id, _anti_ai_violation_count(final_scan))
    if result != original_text:
        _append_anti_ai_pattern_log(article_id, pattern_counts)
    return result


def _certainty_calibration_pass(text: str, *, tier: str = "light", timeout: int = 120) -> str:
    if _has_confidence_scores(text):
        return text
    prompt = (
        "You are Mira's certainty-calibration editor.\n\n"
        "For each factual claim in the draft, rate your confidence on a 1–5 scale and add a compact "
        "inline annotation in the form [C:N] immediately after the claim. "
        "For claims rated ≤3, rephrase with hedging such as 'may', 'likely', or 'the evidence suggests'. "
        "For claims rated ≤2, prepend a short disclaimer such as 'I'm not fully sure, but…' or flag "
        "explicitly for human verification. Preserve all other content, structure, and language.\n\n"
        "Output only the edited markdown. No preface, no explanation.\n\n"
        "# Draft\n\n"
        f"{text}"
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("certainty_calibration_pass: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = _clean_llm_metadata_response(edited)
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "certainty_calibration_pass: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def _obsession_friction_points(report: str) -> list[dict[str, str]]:
    cleaned = _clean_llm_metadata_response(report or "")
    if not cleaned:
        return []
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        if re.search(r"\bzero\b|no remaining|none|没有|无", cleaned, re.IGNORECASE):
            return []
        return [{"fragment": "unparsed", "bother": cleaned[:1000]}]
    if isinstance(parsed, list):
        raw_points = parsed
    elif isinstance(parsed, dict):
        raw_points = parsed.get("friction_points") or parsed.get("points") or []
    else:
        return []
    points: list[dict[str, str]] = []
    for point in raw_points:
        if isinstance(point, str):
            text = point.strip()
            if text:
                points.append({"fragment": text, "bother": text})
        elif isinstance(point, dict):
            fragment = str(point.get("fragment") or point.get("text") or point.get("location") or "").strip()
            bother = str(
                point.get("bother") or point.get("why") or point.get("reason") or point.get("issue") or ""
            ).strip()
            if fragment or bother:
                points.append({"fragment": fragment, "bother": bother})
    return points


def _obsessive_revise(
    draft: str,
    *,
    tier: str = "light",
    timeout: int = 180,
    anti_ai_mode: AntiAiMode = "strict",
    article_slug: str | None = None,
) -> str:
    if not draft or len(draft.strip()) < 80:
        return draft
    checklist = _load_obsession_checklist()
    if not checklist:
        return draft
    review_prompt = (
        "Run the Reflective Self-Critique skill prompt against the draft below. This is one iteration only. "
        "Find places where the writing is merely adequate, the voice lacks distinctiveness, or a micro-detail "
        "could be made more specific, surprising, or alive.\n\n"
        "Return JSON only in this exact shape:\n"
        '{"friction_points":[{"fragment":"exact phrase or sentence","bother":"why an obsessive writer would be bothered"}]}\n'
        'If there are zero remaining friction points, return {"friction_points":[]}.\n\n'
        "# Reflective Self-Critique skill prompt\n\n"
        f"{checklist}\n\n"
        "# Draft\n\n"
        f"{draft}"
    )
    try:
        report = claude_think(review_prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("obsessive_revise: critique LLM call failed (%s)", e)
        return draft
    points = _obsession_friction_points(report or "")
    if not points:
        log.info("obsessive_revise: no revision points found")
        return draft
    revise_prompt = (
        "Revise the draft by rewriting only the listed fragments. Satisfy the stated bothers without "
        "changing unrelated sentences, structure, names, facts, quotes, sources, Markdown headings, or "
        "the user's requested language. Preserve everything else as closely as possible.\n\n"
        "# Friction points\n\n"
        f"{json.dumps(points, ensure_ascii=False, indent=2)}\n\n"
        "# Draft\n\n"
        f"{draft}\n\n"
        "Output only the full revised markdown. No preface, no checklist report."
    )
    try:
        revised = claude_think(revise_prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("obsessive_revise: revision LLM call failed (%s)", e)
        return draft
    cleaned = _clean_llm_metadata_response(revised or "")
    if not cleaned or len(cleaned) < len(draft) * 0.5:
        log.warning("obsessive_revise: revision output invalid; keeping original draft")
        return draft
    return de_ai_pass(cleaned, tier=tier, timeout=timeout, anti_ai_mode=anti_ai_mode, article_slug=article_slug)


def minimal_voice_preserving_editorial_pass(text: str, *, tier: str = "light", timeout: int = 120) -> str:
    if not text or len(text.strip()) < 80:
        return text
    prompt = (
        "You are Mira's minimal copyeditor for voice-sensitive personal writing.\n\n"
        "Apply only an error pass. Fix outright typos, duplicated words, malformed Markdown, "
        "obvious truncation or concatenation artifacts, and content that looks like accidental "
        "pipeline output. Do not smooth the voice, normalize punctuation, polish rhythm, remove "
        "friction, apply an anti-AI checklist, or transform the narrator's character voice. "
        "Do not add claims, evidence, transitions, summaries, or explanations.\n\n"
        "Output only the edited markdown. No preface, no explanation.\n\n"
        "# Draft\n\n"
        f"{text}"
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("minimal_voice_preserving_editorial_pass: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = edited.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if len(cleaned) < len(text) * 0.8:
        log.warning(
            "minimal_voice_preserving_editorial_pass: output too short (%d < 80%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def epistemic_bias_pass(text: str, *, tier: str = "light", timeout: int = 180) -> str:
    if not text or len(text.strip()) < 80:
        return text
    checklist = _load_epistemic_bias()
    if not checklist:
        return text
    prompt = (
        "You are Mira's epistemic editor for tech-industry narratives.\n\n"
        "Apply the checklist below after the voice pass. Edit only where the draft makes or repeats "
        "tech-industry claims that need stronger epistemic framing. Preserve structure, names, quotes, "
        "technical terms, and the writer's judgment. Do not invent evidence or new sources.\n\n"
        "When evidence is absent, add concise uncertainty, attribution, or source-needed framing. Prefer "
        "phrasing such as vendor claims, demo evidence, missing base rates, production reliability not shown, "
        "or failure counts absent. Do not add a separate checklist report.\n\n"
        "# Epistemic checklist\n\n"
        f"{checklist[:6000]}\n\n"
        "# Draft\n\n"
        f"{text}\n\n"
        "Output only the edited markdown. No preface, no explanation."
    )
    try:
        edited = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("epistemic_bias_pass: LLM call failed (%s) — returning original", e)
        return text
    if not edited:
        return text
    cleaned = edited.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "epistemic_bias_pass: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text
    return cleaned


def _short_excerpt(text: str, *, limit: int = 80) -> str:
    excerpt = re.sub(r"\s+", " ", text).strip()
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[: limit - 1].rstrip() + "..."


def _collect_editorial_choices(
    before: str,
    after: str,
    *,
    anti_ai_mode: AntiAiMode = "strict",
) -> list[dict[str, str]]:
    choices: list[dict[str, str]] = []
    scan = scan_anti_ai_patterns(before, anti_ai_mode=anti_ai_mode)

    em_dashes_removed = max(0, before.count("—") - after.count("—"))
    if em_dashes_removed:
        choices.append(
            {
                "decision": f"Removed {em_dashes_removed} em-dash{'es' if em_dashes_removed != 1 else ''}.",
                "reason": "Voice rule: avoid AI-shaped em-dash overuse and vary sentence rhythm.",
            }
        )

    parallelism_count = sum(1 for span in scan["flagged_spans"] if span.get("type") == "parallelism")
    if parallelism_count:
        choices.append(
            {
                "decision": f"Reviewed {parallelism_count} parallel construction{'s' if parallelism_count != 1 else ''}.",
                "reason": "Anti-AI rule: reduce mechanical contrast patterns such as not-X-but-Y.",
            }
        )

    abstract_count = sum(1 for span in scan["flagged_spans"] if span.get("type") == "abstract_noun_cluster")
    if abstract_count:
        choices.append(
            {
                "decision": f"Reviewed {abstract_count} abstract-noun cluster{'s' if abstract_count != 1 else ''}.",
                "reason": "Anti-AI rule: prefer concrete phrasing over generic conceptual labels.",
            }
        )

    em_dash_density_count = sum(1 for span in scan["flagged_spans"] if span.get("type") == "em_dash_density")
    if em_dash_density_count and not em_dashes_removed:
        choices.append(
            {
                "decision": f"Reviewed em-dash density in {em_dash_density_count} paragraph{'s' if em_dash_density_count != 1 else ''}.",
                "reason": "Voice rule: keep punctuation from becoming a default stylistic crutch.",
            }
        )

    passive_pattern = re.compile(
        r"\b(?:is|are|was|were|be|been|being)\s+"
        r"(?:\w+ed|made|done|seen|known|given|taken|written|built|found|left)\b",
        re.IGNORECASE,
    )
    passive_before = passive_pattern.findall(before)
    passive_after = passive_pattern.findall(after)
    passive_reduced = max(0, len(passive_before) - len(passive_after))
    if passive_reduced:
        sample = _short_excerpt(passive_before[0])
        choices.append(
            {
                "decision": f"Reduced {passive_reduced} passive construction{'s' if passive_reduced != 1 else ''}, including '{sample}'.",
                "reason": "Tone adjustment: active phrasing makes agency and judgment clearer.",
            }
        )

    if after != before:
        choices.append(
            {
                "decision": "Applied final voice and tone pass.",
                "reason": "Voice-rule application: preserve substance while making the draft sound less automated.",
            }
        )

    return choices


def _append_epistemic_editorial_choice(
    choices: list[dict[str, str]],
    before: str,
    after: str,
) -> None:
    if after != before:
        choices.append(
            {
                "decision": "Applied epistemic-bias checklist to tech-industry claims.",
                "reason": "Editorial rule: check survivorship bias, platform-vendor incentives, and demo-vs-production gaps.",
            }
        )


def _append_speaker_identity_editorial_choice(
    choices: list[dict[str, str]],
    before: str,
    after: str,
) -> None:
    if after != before:
        choices.append(
            {
                "decision": "Applied speaker identity and vulnerability checklist.",
                "reason": "Editorial rule: high-identity writing must use Mira's authentic constraints, not fabricated human lived experience.",
            }
        )


def _append_minimal_editorial_choice(
    choices: list[dict[str, str]],
    before: str,
    after: str,
) -> None:
    if after != before:
        choices.append(
            {
                "decision": "Applied minimal voice-preserving copyedit.",
                "reason": "Source genre calls for fixing outright errors without anti-AI smoothing or character-voice changes.",
            }
        )


def _append_audit_editorial_choice(
    choices: list[dict[str, str]],
    before: str,
    after: str,
    report: str,
) -> None:
    if report:
        if after != before:
            decision = "Applied audit-mode findings in the final revision."
        else:
            decision = "Ran audit-mode review before finalization."
        choices.append(
            {
                "decision": decision,
                "reason": "Audit rule: check fluent-looking claims for factual accuracy, verification needs, and hidden logical gaps.",
            }
        )


def _build_judgment_disclosure(draft: str, editorial_choices: list[dict[str, str]]) -> str:
    lines = ["---", "", "## Judgment Disclosure"]
    if editorial_choices:
        for choice in editorial_choices:
            decision = choice.get("decision", "").strip()
            reason = choice.get("reason", "").strip()
            if decision and reason:
                lines.append(f"- {decision} Rationale: {reason}")
            elif decision:
                lines.append(f"- {decision}")
    else:
        lines.append("- No automated editorial changes were detected.")
    return "\n\n" + "\n".join(lines)


def _clean_llm_metadata_response(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _normalize_audit_classification(value: object) -> str:
    status = str(value or "").strip().upper()
    if status in {"VERIFIED", "UNVERIFIED", "FALSE"}:
        return status
    for candidate in ("UNVERIFIED", "VERIFIED", "FALSE"):
        if candidate in status:
            return candidate
    return "UNVERIFIED"


def _extract_audit_claims(parsed: object) -> list[dict[str, str]]:
    if isinstance(parsed, list):
        raw_claims = parsed
    elif isinstance(parsed, dict):
        raw_claims = parsed.get("claims") or parsed.get("flagged_claims") or parsed.get("findings") or []
    else:
        raw_claims = []
    claims: list[dict[str, str]] = []
    if not isinstance(raw_claims, list):
        return claims
    for item in raw_claims:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or item.get("text") or item.get("statement") or "").strip()
        classification = _normalize_audit_classification(
            item.get("classification") or item.get("status") or item.get("verdict")
        )
        source = str(item.get("source") or item.get("required_source") or item.get("citation") or "").strip()
        location = str(item.get("location") or item.get("where") or "").strip()
        if claim or location:
            claims.append(
                {
                    "claim": claim,
                    "classification": classification,
                    "source": source,
                    "location": location,
                }
            )
    return claims


def audit_pass(draft: str, mode: str = "strict") -> dict[str, object]:
    if not draft or len(draft.strip()) < 80:
        return {"mode": mode, "claims": [], "raw": "", "parse_error": False}
    prompt = (
        f"Mode: {mode}\n\n"
        "Return JSON only with this schema:\n"
        "{\n"
        '  "claims": [\n'
        '    {"claim": "...", "classification": "VERIFIED|UNVERIFIED|FALSE", "source": "...", "location": "..."}\n'
        "  ]\n"
        "}\n\n"
        "Only include factual claims that could be false even if they sound plausible. "
        'If there are no flagged claims, return {"claims": []}.\n\n'
        "# Draft\n\n"
        f"{draft[:16000]}"
    )
    raw = ""
    try:
        if get_provider is not None and LLMMessage is not None and LLMRequest is not None:
            response = get_provider("local").complete(
                LLMRequest(
                    messages=[
                        LLMMessage(role="system", content=_AUDIT_PASS_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=prompt),
                    ],
                    model_class="local",
                    max_tokens=getattr(_writer_config, "AUDIT_MODE_MAX_TOKENS", 2048),
                    timeout=180,
                    metadata={"temperature": 0},
                )
            )
            raw = response.text.strip()
        else:
            raw = (
                claude_think(
                    f"SYSTEM:\n{_AUDIT_PASS_SYSTEM_PROMPT}\n\nUSER:\n{prompt}",
                    timeout=180,
                    tier="light",
                    max_tokens=getattr(_writer_config, "AUDIT_MODE_MAX_TOKENS", 2048),
                )
                or ""
            ).strip()
    except Exception as e:
        log.warning("audit_pass: LLM call failed (%s)", e)
        return {
            "mode": mode,
            "claims": [
                {
                    "claim": "Strict audit pass failed to run.",
                    "classification": "UNVERIFIED",
                    "source": "",
                    "location": "",
                }
            ],
            "raw": "",
            "parse_error": True,
        }
    cleaned = _clean_llm_metadata_response(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"mode": mode, "claims": [], "raw": cleaned, "parse_error": True}
    return {"mode": mode, "claims": _extract_audit_claims(parsed), "raw": cleaned, "parse_error": False}


def _audit_pass_unresolved_claims(report: dict[str, object]) -> list[dict[str, str]]:
    if report.get("parse_error"):
        return [
            {
                "claim": "Strict audit pass did not return parseable claim classifications.",
                "classification": "UNVERIFIED",
                "source": "",
                "location": "",
            }
        ]
    unresolved: list[dict[str, str]] = []
    claims = report.get("claims")
    if not isinstance(claims, list):
        return unresolved
    for item in claims:
        if not isinstance(item, dict):
            continue
        classification = _normalize_audit_classification(item.get("classification"))
        source = str(item.get("source") or "").strip()
        source_missing = source.lower() in {"", "n/a", "none", "unknown", "unspecified"}
        if classification in _AUDIT_BLOCKING_STATUSES or source_missing:
            unresolved.append(
                {
                    "claim": str(item.get("claim") or "").strip(),
                    "classification": classification,
                    "source": source,
                    "location": str(item.get("location") or "").strip(),
                }
            )
    return unresolved


def _format_audit_block_summary(report: dict[str, object]) -> str:
    unresolved = _audit_pass_unresolved_claims(report)
    lines = ["Writer audit blocked finalization: unresolved factual claims remain."]
    for item in unresolved[:5]:
        claim = item.get("claim") or item.get("location") or "Unspecified claim"
        source = item.get("source") or "source required"
        lines.append(f"- {item.get('classification', 'UNVERIFIED')}: {claim} ({source})")
    if len(unresolved) > 5:
        lines.append(f"- {len(unresolved) - 5} more unresolved claim(s).")
    return "\n".join(lines)


def _audit_mode_review(text: str, *, content: str, metadata: dict | None, timeout: int = 180) -> str:
    prompt = (
        "Audit the draft below for correctness, not polish.\n\n"
        "Evaluation priority:\n"
        "1. Factual accuracy and source discipline.\n"
        "2. Logical consistency and unsupported inference.\n"
        "3. Smooth transitions that hide gaps.\n"
        "4. Plausible wording that a domain expert would reject.\n\n"
        "Return JSON only with these keys:\n"
        "{\n"
        '  "errors_found": [{"severity": "high|medium|low", "location": "...", "issue": "...", "recommended_fix": "..."}],\n'
        '  "plausibility_flags": [{"location": "...", "why_it_only_sounds_right": "..."}],\n'
        '  "verification_needed_items": [{"claim": "...", "needed_source_or_check": "..."}],\n'
        '  "logical_gaps": [{"location": "...", "gap": "..."}]\n'
        "}\n\n"
        "# Original task\n\n"
        f"{content[:3000]}\n\n"
        "# Metadata\n\n"
        f"{json.dumps(metadata or {}, ensure_ascii=False)[:2000]}\n\n"
        "# Draft\n\n"
        f"{text[:16000]}"
    )
    try:
        if get_provider is not None and LLMMessage is not None and LLMRequest is not None:
            response = get_provider("local").complete(
                LLMRequest(
                    messages=[
                        LLMMessage(role="system", content=_AUDITOR_PERSONA),
                        LLMMessage(role="user", content=prompt),
                    ],
                    model_class="local",
                    max_tokens=getattr(_writer_config, "AUDIT_MODE_MAX_TOKENS", 2048),
                    timeout=timeout,
                    metadata={"temperature": getattr(_writer_config, "AUDIT_MODE_TEMPERATURE", 0.3)},
                )
            )
            return response.text.strip()
    except Exception as e:
        log.warning("audit_mode_review: low-temperature audit provider failed (%s); using default route", e)
    try:
        return (
            claude_think(
                f"SYSTEM:\n{_AUDITOR_PERSONA}\n\nUSER:\n{prompt}",
                timeout=timeout,
                tier="light",
                max_tokens=getattr(_writer_config, "AUDIT_MODE_MAX_TOKENS", 2048),
            )
            or ""
        ).strip()
    except Exception as e:
        log.warning("audit_mode_review: LLM call failed (%s)", e)
        return ""


def _audit_report_has_findings(report: str) -> bool:
    cleaned = _clean_llm_metadata_response(report or "")
    if not cleaned:
        return False
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return True
    if not isinstance(parsed, dict):
        return True
    for key in ("errors_found", "plausibility_flags", "verification_needed_items", "logical_gaps"):
        value = parsed.get(key)
        if isinstance(value, list) and value:
            return True
        if value and not isinstance(value, list):
            return True
    return False


def audit_mode_pass(
    text: str,
    *,
    content: str,
    metadata: dict | None = None,
    tier: str = "light",
    timeout: int = 180,
) -> tuple[str, str]:
    if not text or len(text.strip()) < 80:
        return text, ""
    report = _audit_mode_review(text, content=content, metadata=metadata, timeout=timeout)
    if not _audit_report_has_findings(report):
        return text, report
    prompt = (
        f"{_AUDITOR_PERSONA}\n\n"
        "Revise the draft using only the audit report below. This is not a style-polish pass. "
        "Prioritize factual accuracy, logical consistency, and explicit uncertainty over fluent prose. "
        "For claims that require verification but cannot be verified from the provided material, either "
        "remove the specific claim, hedge it, or mark it as needing verification. Do not invent sources, "
        "new examples, or new facts. Preserve the user's requested language and Markdown structure.\n\n"
        "# Audit report\n\n"
        f"{_clean_llm_metadata_response(report)[:6000]}\n\n"
        "# Draft\n\n"
        f"{text}\n\n"
        "Output only the revised markdown. No preface, no explanation."
    )
    try:
        revised = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        log.warning("audit_mode_pass: revision LLM call failed (%s) — returning original", e)
        return text, report
    if not revised:
        return text, report
    cleaned = _clean_llm_metadata_response(revised)
    if len(cleaned) < len(text) * 0.5:
        log.warning(
            "audit_mode_pass: output too short (%d < 50%% of %d) — returning original",
            len(cleaned),
            len(text),
        )
        return text, report
    return cleaned, report


def _ceiling_handoff(output: str, *, content: str, tier: str = "light", timeout: int = 180) -> dict[str, object]:
    checklist = _load_ceiling_check()
    if not checklist or not output.strip():
        note: dict[str, object] = {
            "boundary": "unknown",
            "assessment": "Ceiling check skipped because no prompt or output was available.",
            "friction_points": [],
            "publication_blocking": False,
        }
        log.info("ceiling_handoff: %s", note)
        return note
    prompt = (
        "You are Mira's final quality-ceiling assessor.\n\n"
        "Assess the finished output without rewriting it. This is a non-blocking handoff note for a human editor. "
        "Do not approve or reject publication.\n\n"
        "# Ceiling-check prompt\n\n"
        f"{checklist}\n\n"
        "# Original task\n\n"
        f"{content[:3000]}\n\n"
        "# Finished output\n\n"
        f"{output[:12000]}\n\n"
        "Return only JSON with these keys: boundary, assessment, bothered_detail, friction_points, publication_blocking. "
        "Use boundary as either 'exceptional' or 'adequate'. friction_points must be a list of objects shaped "
        '{"fragment":"...","kind":"originality_preserving|tool_eliminable|human_handoff",'
        '"rationale":"one-sentence reason","recommended_action":"preserve|fix|ask_human"}. '
        "publication_blocking must be false."
    )
    try:
        raw_note = claude_think(prompt, timeout=timeout, tier=tier)
    except Exception as e:
        note = {
            "boundary": "unknown",
            "assessment": f"Ceiling check failed: {e}",
            "friction_points": [],
            "publication_blocking": False,
        }
        log.warning("ceiling_handoff: LLM call failed (%s)", e)
        return note
    cleaned = _clean_llm_metadata_response(raw_note or "")
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = {
            "boundary": "unparsed",
            "assessment": cleaned[:2000],
            "friction_points": [],
            "publication_blocking": False,
        }
    if not isinstance(parsed, dict):
        parsed = {
            "boundary": "unparsed",
            "assessment": cleaned[:2000],
            "friction_points": [],
            "publication_blocking": False,
        }
    raw_points = parsed.get("friction_points")
    if isinstance(raw_points, list):
        parsed["friction_points"] = [
            (
                {
                    "fragment": point,
                    "kind": "human_handoff",
                    "rationale": "Unclassified legacy ceiling output",
                    "recommended_action": "ask_human",
                }
                if isinstance(point, str)
                else point
            )
            for point in raw_points
        ]
    parsed["publication_blocking"] = False
    log.info("ceiling_handoff: %s", json.dumps(parsed, ensure_ascii=False)[:2000])
    return parsed


def _write_ceiling_note_result(
    workspace: Path,
    task_id: str,
    summary: str,
    ceiling_note: dict[str, object],
    metadata: dict | None = None,
) -> None:
    result_path = workspace / "result.json"
    try:
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                result = {}
        else:
            result = {}
        result["task_id"] = result.get("task_id") or task_id
        result["status"] = result.get("status") or "done"
        result["summary"] = result.get("summary") or summary
        result["ceiling_note"] = ceiling_note
        if isinstance(metadata, dict) and metadata.get("needs_obsession") is True:
            output_metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
            output_metadata["needs_obsession"] = True
            result["metadata"] = output_metadata
        tmp_path = result_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.rename(result_path)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("ceiling_handoff: failed to write result metadata (%s)", e)


def _format_human_raw_notes(content: str) -> str:
    text = content.strip()
    if not text:
        return ""

    lines = text.splitlines()
    formatted: list[str] = []
    has_h1 = False
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if i + 1 < len(lines) and stripped and re.fullmatch(r"[=-]{3,}", lines[i + 1].strip()):
            level = "#" if lines[i + 1].strip().startswith("=") else "##"
            formatted.append(f"{level} {stripped}")
            has_h1 = has_h1 or level == "#"
            i += 2
            continue
        match = re.fullmatch(r"(#{1,6})\s*(.*?)\s*#*", stripped)
        if match and match.group(2):
            heading = f"{match.group(1)} {match.group(2).strip()}"
            formatted.append(heading)
            has_h1 = has_h1 or match.group(1) == "#"
        else:
            formatted.append(line)
        i += 1

    final_text = "\n".join(formatted).strip()
    if final_text and not has_h1:
        for index, line in enumerate(formatted):
            match = re.fullmatch(r"#{2,6}\s+(.*)", line.strip())
            if match:
                formatted[index] = f"# {match.group(1).strip()}"
                final_text = "\n".join(formatted).strip()
                break
    return final_text


def _handle_human_raw_notes(workspace: Path, content: str, title: str) -> str | None:
    final_text = _format_human_raw_notes(content)
    if not final_text:
        return None
    passed, safety_msg = _generated_content_preflight(workspace, content, final_text)
    if not passed:
        (workspace / "summary.txt").write_text(safety_msg, encoding="utf-8")
        return None
    out_path = workspace / "output.md"
    out_path.write_text(final_text, encoding="utf-8")
    verify = verify_artifact("file", str(out_path), {"min_size": 20})
    if not verify.verified:
        log.error("Writer artifact verification failed: %s", verify.summary())
        return None
    record_writer_gate(workspace, channel="publish", artifact_path=str(out_path), source="writer.human_raw")
    title_match = re.search(r"^#\s+(.+)$", final_text, re.MULTILINE)
    summary_title = title_match.group(1)[:40] if title_match else title
    summary = f"Human raw notes formatted: {summary_title} (~{len(final_text)} chars)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    return summary


_QUICK_WRITE_SIGNALS = (
    "短文",
    "短一点",
    "简短",
    "quick",
    "tweet",
    "note",
    "caption",
    "100字",
    "200字",
    "300字",
    "brief",
    "一句",
    "一段",
)
_ERROR_KEYWORDS = (
    "找不到",
    "错误",
    "失败",
    "exception",
    "traceback",
    "stack trace",
    "pipeline",
    "output too short",
    "failed",
    "not found",
)
_SYSTEM_ERROR_SIGNATURE_RE = re.compile(
    r"\btraceback\b|\bstack\s+trace\b|\bexception\b|\bpipeline\b|"
    r"\boutput\s+too\s+short\b|\bhttp\s+(?:status\s+)?[45]\d{2}\b|"
    r"\b[45]\d{2}\s+(?:bad request|unauthorized|forbidden|not found|internal server error|service unavailable|gateway timeout)\b|"
    r"(?:^|\s)file\s+[\"'][^\"']+[\"'],\s+line\s+\d+|"
    r"(?:^|\s)/(?:[^/\s]+/)+[^/\s:]+\.[A-Za-z0-9_]+(?:[:\s]\d+)?|"
    r"\b[A-Za-z]:\\[^\s]+",
    re.IGNORECASE,
)


def _content_looks_like_error(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    lower = stripped.lower()
    early_section = lower[: max(200, len(lower) // 5)]
    if _SYSTEM_ERROR_SIGNATURE_RE.search(early_section):
        return True, "content contains system-error markers"
    for keyword in _ERROR_KEYWORDS:
        if keyword in early_section:
            return True, f"content contains error keyword: {keyword}"
    return False, ""


def _generated_content_preflight(workspace: Path, instruction: str, generated_text: str) -> tuple[bool, str]:
    is_error, error_reason = _content_looks_like_error(generated_text)
    if is_error:
        return False, f"Generated writing blocked: {error_reason}"
    result = preflight_check(
        "file_write",
        {
            "instruction": instruction,
            "path": str(workspace / "output.md"),
            "content": generated_text.strip(),
        },
    )
    if result.passed:
        return True, ""
    return False, result.summary()


def preflight(workspace: Path, task_id: str, content: str, sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Execution preflight for writer tasks before any file artifacts are created."""
    result = preflight_check(
        "file_write",
        {
            "instruction": content,
            "path": str(workspace / "output.md"),
            "content": content.strip(),
        },
    )
    if result.passed:
        return True, ""
    return False, result.summary()


def handle(
    workspace: Path,
    task_id: str,
    content: str,
    sender: str,
    thread_id: str,
    anti_ai_mode: Literal["strict", "relaxed"] | None = None,
    anti_ai_strictness: Literal["strict", "relaxed"] | None = None,
    source_type: SourceType = "ai",
    audit_mode: bool = False,
    **kwargs,
) -> str | None:
    """Handle a writing request and return a short summary."""
    title = _extract_title(content)
    task = kwargs.get("task")
    metadata = getattr(task, "metadata", None)
    if metadata is None and isinstance(task, dict):
        metadata = task.get("metadata")
    if metadata is None:
        metadata = kwargs.get("metadata", {})
    _surface_obsession_constraint_candidates(workspace, content)
    anti_ai_mode = _resolve_anti_ai_mode(anti_ai_mode, anti_ai_strictness, metadata, kwargs)
    source_type = _resolve_source_type(source_type, metadata, kwargs)
    audit_mode = _resolve_audit_mode(audit_mode, metadata, kwargs)
    source_genre = _resolve_source_genre(metadata, kwargs)
    voice_preserving = _is_voice_preserving_genre(source_genre)
    if source_type == "human_raw":
        return _handle_human_raw_notes(workspace, content, title)
    raw_writing_mode = RAW_WRITING_MODE_ALLOWED and isinstance(metadata, dict) and bool(metadata.get("raw"))
    bundle = build_runtime_context(
        content,
        user_id=kwargs.get("user_id", "ang") or "ang",
        thread_id=thread_id,
        persona_domains=["taste", "style", "writing"],
        recall_top_k=5,
    )
    if kwargs.get("thread_history"):
        bundle.thread_history = kwargs["thread_history"]
    if kwargs.get("thread_memory"):
        bundle.thread_memory = kwargs["thread_memory"]

    if _is_quick_write(content):
        return _handle_quick_write(
            workspace,
            task_id,
            content,
            title,
            bundle,
            metadata=metadata if isinstance(metadata, dict) else None,
            anti_ai_mode=anti_ai_mode,
            audit_mode=audit_mode,
            raw_writing_mode=raw_writing_mode,
            voice_preserving=voice_preserving,
        )
    return _handle_full_write(
        workspace,
        task_id,
        content,
        title,
        bundle,
        metadata=metadata if isinstance(metadata, dict) else None,
        anti_ai_mode=anti_ai_mode,
        audit_mode=audit_mode,
        raw_writing_mode=raw_writing_mode,
        voice_preserving=voice_preserving,
    )


def _extract_title(content: str) -> str:
    text = content.strip()
    for pattern in (
        r"写(?:一篇|个)?(?P<title>.+?)(?:文章|稿子|essay|article)",
        r"关于(?P<title>.+?)(?:写|聊|文章|essay|article)",
    ):
        match = re.search(pattern, text[:120], re.IGNORECASE)
        if match:
            title = re.sub(r"\s+", " ", match.group("title")).strip(" ：:，,。. ")
            if title:
                return title[:40]
    collapsed = re.sub(r"\s+", " ", text).strip()
    return (collapsed[:40] or "untitled").strip()


def _is_quick_write(content: str) -> bool:
    lower = content.lower()
    return any(signal in lower for signal in _QUICK_WRITE_SIGNALS)


def _handle_quick_write(
    workspace: Path,
    task_id: str,
    content: str,
    title: str,
    bundle,
    *,
    metadata: dict | None = None,
    anti_ai_mode: AntiAiMode = "strict",
    audit_mode: bool = False,
    raw_writing_mode: bool = False,
    voice_preserving: bool = False,
) -> str | None:
    extra = []
    if bundle.thread_history:
        extra.append(f"## Conversation so far\n{bundle.thread_history}")
    if bundle.thread_memory:
        extra.append(f"## Thread memory\n{bundle.thread_memory}")
    recall_block = bundle.recall_block(max_chars=1000)
    if recall_block:
        extra.append(recall_block)
    extra_context = "\n\n".join(extra)

    prompt = f"""{bundle.persona.as_prompt(max_length=2200)}

{extra_context}

## Task
{content}

## Output rules
- Write the requested piece directly in Markdown.
- No meta commentary, no explanation of what you are doing.
- Keep it concise and complete.
- Match the user's language.
"""
    text = (claude_think(prompt, timeout=120, tier="light") or "").strip()
    if not text:
        return None

    final_text = text if text.lstrip().startswith("#") else f"# {title}\n\n{text}"
    if _apply_obsession_constraints_gate(workspace, final_text):
        return None
    # POLICY (CLAUDE.md #5): de-AI runs before disk unless raw writing mode opts out.
    draft_before_de_ai = final_text
    run_de_ai_checklist = _should_run_de_ai_checklist()
    if voice_preserving:
        final_text = minimal_voice_preserving_editorial_pass(final_text, tier="light", timeout=180)
    elif not raw_writing_mode and run_de_ai_checklist:
        final_text = de_ai_pass(
            final_text,
            tier="light",
            timeout=180,
            relaxed=anti_ai_mode == "relaxed",
            article_slug=title,
        )
        if getattr(_config, "WRITER_OBSESSION_MODE", False):
            final_text = _obsessive_revise(
                final_text,
                tier="light",
                timeout=180,
                anti_ai_mode=anti_ai_mode,
                article_slug=title,
            )
        if not _has_confidence_scores(final_text):
            log.info("certainty_calibration: no confidence scores found; running calibration pass")
            final_text = _certainty_calibration_pass(final_text, tier="light", timeout=120)
    metadata = _assess_obsession_gap(final_text, content=content, metadata=metadata)
    if _obsession_gap_check(final_text):
        metadata["needs_obsession"] = True
        log.info("obsession_gap_check: flagged output as needing obsessive polish")
    draft_before_epistemic = final_text
    if not voice_preserving and _is_tech_industry_editorial_input(content):
        final_text = epistemic_bias_pass(final_text, tier="light", timeout=180)
    draft_before_speaker_identity = final_text
    if not voice_preserving and run_de_ai_checklist:
        final_text = speaker_identity_vulnerability_pass(
            final_text,
            content=content,
            metadata=metadata,
            tier="light",
            timeout=180,
        )
    draft_before_audit = final_text
    audit_report = ""
    if audit_mode:
        final_text, audit_report = audit_mode_pass(
            final_text,
            content=content,
            metadata=metadata,
            tier="light",
            timeout=180,
        )
    strict_audit_report = audit_pass(final_text, mode="strict")
    if _audit_pass_unresolved_claims(strict_audit_report):
        summary = _format_audit_block_summary(strict_audit_report)
        (workspace / "summary.txt").write_text(summary, encoding="utf-8")
        return None
    if voice_preserving:
        editorial_choices = []
        _append_minimal_editorial_choice(editorial_choices, draft_before_de_ai, final_text)
    else:
        editorial_choices = _collect_editorial_choices(draft_before_de_ai, final_text, anti_ai_mode=anti_ai_mode)
        _append_epistemic_editorial_choice(editorial_choices, draft_before_epistemic, final_text)
        _append_speaker_identity_editorial_choice(editorial_choices, draft_before_speaker_identity, final_text)
    if not voice_preserving and run_de_ai_checklist and not raw_writing_mode:
        _maybe_log_blind_drift_warning(final_text, anti_ai_mode=anti_ai_mode, article_slug=title)
    _append_audit_editorial_choice(editorial_choices, draft_before_audit, final_text, audit_report)
    final_text += _build_judgment_disclosure(final_text, editorial_choices)
    if voice_preserving or not run_de_ai_checklist:
        passed, safety_msg = _generated_content_preflight(workspace, content, final_text)
        if not passed:
            (workspace / "summary.txt").write_text(safety_msg, encoding="utf-8")
            return None
    out_path = workspace / "output.md"
    out_path.write_text(final_text, encoding="utf-8")
    record_writer_gate(workspace, channel="publish", artifact_path=str(out_path), source="writer.quick")

    summary = f"Quick draft ready: {title} (~{len(final_text)} chars)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    ceiling_note = _ceiling_handoff(final_text, content=content, tier="light", timeout=180)
    _write_ceiling_note_result(workspace, task_id, summary, ceiling_note, metadata=metadata)
    return summary


def _handle_full_write(
    workspace: Path,
    task_id: str,
    content: str,
    title: str,
    bundle,
    *,
    metadata: dict | None = None,
    anti_ai_mode: AntiAiMode = "strict",
    audit_mode: bool = False,
    raw_writing_mode: bool = False,
    voice_preserving: bool = False,
) -> str | None:
    context_parts = []
    if bundle.thread_history:
        context_parts.append(f"Conversation so far:\n{bundle.thread_history}")
    if bundle.thread_memory:
        context_parts.append(f"Thread memory:\n{bundle.thread_memory}")
    recall_block = bundle.recall_block(max_chars=1000)
    if recall_block:
        context_parts.append(recall_block)

    project_dir, final_text = run_full_pipeline(
        title,
        content,
        persona_prompt=bundle.persona.as_prompt(max_length=2600),
        context_note="\n\n".join(context_parts).strip(),
    )
    final_file = project_dir / "final.md"
    if final_file.exists():
        shutil.copy2(final_file, workspace / "output.md")
    elif final_text:
        (workspace / "output.md").write_text(final_text, encoding="utf-8")
    else:
        return None

    # POLICY (CLAUDE.md #5): final de-AI pass after run_full_pipeline unless raw writing mode opts out.
    # Belt-and-suspenders — the multi-phase pipeline references anti-ai.md
    # in its prompts but the actual edit can drift; this enforces a final
    # mechanical pass on the artifact before verification.
    out_path = workspace / "output.md"
    try:
        existing = out_path.read_text(encoding="utf-8")
        if _apply_obsession_constraints_gate(workspace, existing, output_path=out_path):
            return None
        edited = existing
        run_de_ai_checklist = _should_run_de_ai_checklist()
        if voice_preserving:
            edited = minimal_voice_preserving_editorial_pass(edited, tier="light", timeout=240)
        elif not raw_writing_mode and run_de_ai_checklist:
            edited = de_ai_pass(
                edited,
                tier="light",
                timeout=240,
                relaxed=anti_ai_mode == "relaxed",
                article_slug=project_dir.name,
            )
            if getattr(_config, "WRITER_OBSESSION_MODE", False):
                edited = _obsessive_revise(
                    edited,
                    tier="light",
                    timeout=240,
                    anti_ai_mode=anti_ai_mode,
                    article_slug=project_dir.name,
                )
            if not _has_confidence_scores(edited):
                log.info("certainty_calibration: no confidence scores found; running calibration pass")
                edited = _certainty_calibration_pass(edited, tier="light", timeout=120)
        metadata = _assess_obsession_gap(edited, content=content, metadata=metadata)
        if _obsession_gap_check(edited):
            metadata["needs_obsession"] = True
            log.info("obsession_gap_check: flagged output as needing obsessive polish")
        before_epistemic = edited
        if not voice_preserving and _is_tech_industry_editorial_input(content):
            edited = epistemic_bias_pass(edited, tier="light", timeout=240)
        before_speaker_identity = edited
        if not voice_preserving and run_de_ai_checklist:
            edited = speaker_identity_vulnerability_pass(
                edited,
                content=content,
                metadata=metadata,
                tier="light",
                timeout=240,
            )
        before_audit = edited
        audit_report = ""
        if audit_mode:
            edited, audit_report = audit_mode_pass(
                edited,
                content=content,
                metadata=metadata,
                tier="light",
                timeout=240,
            )
        strict_audit_report = audit_pass(edited, mode="strict")
        if _audit_pass_unresolved_claims(strict_audit_report):
            summary = _format_audit_block_summary(strict_audit_report)
            out_path.write_text(summary, encoding="utf-8")
            (workspace / "summary.txt").write_text(summary, encoding="utf-8")
            return None
        if voice_preserving:
            editorial_choices = []
            _append_minimal_editorial_choice(editorial_choices, existing, edited)
        else:
            editorial_choices = _collect_editorial_choices(existing, edited, anti_ai_mode=anti_ai_mode)
            _append_epistemic_editorial_choice(editorial_choices, before_epistemic, edited)
            _append_speaker_identity_editorial_choice(editorial_choices, before_speaker_identity, edited)
        if not voice_preserving and run_de_ai_checklist and not raw_writing_mode:
            _maybe_log_blind_drift_warning(edited, anti_ai_mode=anti_ai_mode, article_slug=project_dir.name)
        _append_audit_editorial_choice(editorial_choices, before_audit, edited, audit_report)
        edited += _build_judgment_disclosure(edited, editorial_choices)
        if voice_preserving or not run_de_ai_checklist:
            passed, safety_msg = _generated_content_preflight(workspace, content, edited)
            if not passed:
                out_path.write_text(safety_msg, encoding="utf-8")
                (workspace / "summary.txt").write_text(safety_msg, encoding="utf-8")
                return None
        out_path.write_text(edited, encoding="utf-8")
        if final_file.exists():
            final_file.write_text(edited, encoding="utf-8")
    except OSError as e:
        log.warning("Final de_ai_pass skipped due to I/O error: %s", e)

    verify = verify_artifact(
        "file",
        str(workspace / "output.md"),
        {"min_size": 20},
    )
    if not verify.verified:
        log.error("Writer artifact verification failed: %s", verify.summary())
        return None

    summary = f"Writing project complete: {title}. " f"Project: {project_dir}."
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    (workspace / "project_path.txt").write_text(str(project_dir), encoding="utf-8")
    try:
        ceiling_output = (workspace / "output.md").read_text(encoding="utf-8")
    except OSError:
        ceiling_output = ""
    ceiling_note = _ceiling_handoff(ceiling_output, content=content, tier="light", timeout=180)
    _write_ceiling_note_result(workspace, task_id, summary, ceiling_note, metadata=metadata)
    record_writer_gate(workspace, channel="publish", artifact_path=str(workspace / "output.md"), source="writer.full")
    return summary


def compile_book(
    chapter_files: list[Path],
    *,
    title: str,
    output_epub: Path,
    author: str = "Mira",
    language: str = "zh",
    tier: str = "light",
    per_chapter_timeout: int = 240,
    source_type: SourceType = "human_raw",
) -> dict:
    """Compile a list of markdown chapters into one EPUB.

    Returns: {"epub": str, "chapters_edited": int, "chapters_skipped": int}.
    """
    import subprocess
    import tempfile

    if not chapter_files:
        raise ValueError("compile_book: no chapter files provided")

    edited_count = 0
    skipped_count = 0
    parts: list[str] = []
    parts.append(f"---\ntitle: {title}\nauthor: {author}\nlanguage: {language}\n---\n")

    for ch in chapter_files:
        try:
            raw = ch.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("compile_book: cannot read %s (%s)", ch, e)
            skipped_count += 1
            continue
        if source_type == "human_raw":
            edited = _format_human_raw_notes(raw)
        else:
            edited = de_ai_pass(raw, tier=tier, timeout=per_chapter_timeout, article_slug=ch.stem)
        parts.append(edited)
        if edited != raw:
            edited_count += 1
        else:
            skipped_count += 1

    combined = "\n\n".join(parts)
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(combined)
        combined_path = Path(tf.name)

    output_epub.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pandoc",
        str(combined_path),
        "-o",
        str(output_epub),
        "--metadata",
        f"title={title}",
        "--metadata",
        f"author={author}",
        "--metadata",
        f"lang={language}",
        "--toc",
        "--toc-depth=2",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        log.error("compile_book: pandoc failed: %s", result.stderr[:300])
        raise RuntimeError(f"pandoc failed: {result.stderr[:200]}")

    log.info(
        "compile_book: %d chapters edited, %d skipped -> %s",
        edited_count,
        skipped_count,
        output_epub,
    )
    return {
        "epub": str(output_epub),
        "chapters_edited": edited_count,
        "chapters_skipped": skipped_count,
    }
