"""Writer agent runtime handler.

Provides the production task-worker contract:
    handle(workspace, task_id, content, sender, thread_id, **kwargs)

This replaces the old manifest entry that pointed directly at
writing_workflow.start_project(), whose signature did not match the runtime.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Literal

import config as _config
import writer_config as _writer_config
from publish.preflight import preflight_check, verify_artifact
from publish.writer_gate import record_writer_gate
from ops.runtime_context import build_runtime_context
from llm import claude_think
from writing_workflow import run_full_pipeline
from config import RAW_WRITING_MODE_ALLOWED

log = logging.getLogger("writer_agent")

_ANTI_AI_PATH = Path(__file__).resolve().parent / "checklists" / "anti-ai.md"
_EPISTEMIC_BIAS_PATH = Path(__file__).resolve().parent / "checklists" / "epistemic-bias.md"
_SUBSTACK_VOICE_PATH = Path(__file__).resolve().parent / "voice" / "substack_voice.md"
_SPEAKER_IDENTITY_PATH = Path(__file__).resolve().parents[1] / "shared" / "soul" / "identity.md"
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
_ABSTRACT_NOUNS = ("维度", "张力", "结构性", "叙事", "框架", "语境")
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
AntiAiMode = Literal["strict", "relaxed"]


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


def _load_anti_ai() -> str:
    try:
        return _ANTI_AI_PATH.read_text(encoding="utf-8")
    except OSError:
        log.warning("anti-ai.md not found at %s", _ANTI_AI_PATH)
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


def scan_anti_ai_patterns(text: str, *, anti_ai_mode: AntiAiMode = "strict") -> dict:
    paragraphs = _paragraph_spans(text)
    flagged_spans: list[dict] = []
    score = 0.0

    if paragraphs:
        em_dash_count = text.count("—")
        em_dash_average = em_dash_count / len(paragraphs)
        if anti_ai_mode == "strict" and em_dash_average > 2:
            score += em_dash_average - 2
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
                            "text": paragraph[:160],
                        }
                    )
        elif anti_ai_mode == "relaxed":
            for index, (start, end, paragraph) in enumerate(paragraphs):
                count = paragraph.count("—")
                if count > 5:
                    score += count - 5
                    flagged_spans.append(
                        {
                            "type": "em_dash_density",
                            "paragraph": index,
                            "start": start,
                            "end": end,
                            "count": count,
                            "text": paragraph[:160],
                        }
                    )

    for pattern in _PARALLELISM_PATTERNS:
        matches = list(pattern.finditer(text))
        if anti_ai_mode == "relaxed" and pattern.pattern.startswith("不是"):
            matches = matches[1:]
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

    return {
        "score": round(score, 3),
        "threshold": _ANTI_AI_SCAN_THRESHOLD,
        "flagged_spans": flagged_spans,
    }


def _de_ai_section(text: str, *, tier: str, timeout: int, anti_ai_mode: AntiAiMode = "strict") -> str:
    """Internal: edit a single section. Used by de_ai_pass after chunking."""
    if not text or len(text.strip()) < 80:
        return text
    voice = _load_substack_voice()
    strict_rules = (
        "1. Em-dash overuse: max one em dash per paragraph. Prefer commas, periods, or sentence restructuring.\n"
        "2. Repetitive 'not X but Y' / 'the real question is...' reversals. Rewrite most of them.\n"
        "3. Abstract concept labels such as 'structural', 'architecture of', 'fundamentally'. Make them concrete.\n"
    )
    relaxed_rules = (
        "1. Preserve raw, vulnerable, unpolished draft energy where it helps the piece.\n"
        "2. Allow up to five em dashes per paragraph; edit only beyond that or when punctuation obscures meaning.\n"
        "3. Allow one 'not X but Y' / '不是X而是Y' contrast per article when it carries real judgment.\n"
        "4. Allow structural abstract nouns when they carry the author's thought; do not force a mandatory concrete rewrite.\n"
    )
    mode_rules = strict_rules if anti_ai_mode == "strict" else relaxed_rules
    prompt = (
        "You are Mira's final Substack editor.\n\n"
        "Edit, do not rewrite. Preserve concrete references, names, judgments, reading reactions, "
        "emotional register, section order, and factual claims.\n\n"
        "Voice guide:\n"
        f"{voice[:5000]}\n\n"
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
    scan = scan_anti_ai_patterns(text, anti_ai_mode=anti_ai_mode)
    if scan["score"] > scan["threshold"]:
        log.info(
            "de_ai_pass: anti-AI scan score %.3f exceeded threshold %.3f (%d spans)",
            scan["score"],
            scan["threshold"],
            len(scan["flagged_spans"]),
        )
        text = _run_de_ai_sections(text, tier="heavy", timeout=max(timeout, 240), anti_ai_mode=anti_ai_mode)
    return _run_de_ai_sections(text, tier=tier, timeout=timeout, anti_ai_mode=anti_ai_mode)


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
    anti_ai_mode = _resolve_anti_ai_mode(anti_ai_mode, anti_ai_strictness, metadata, kwargs)
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
            content,
            title,
            bundle,
            metadata=metadata if isinstance(metadata, dict) else None,
            anti_ai_mode=anti_ai_mode,
            raw_writing_mode=raw_writing_mode,
        )
    return _handle_full_write(
        workspace,
        content,
        title,
        bundle,
        metadata=metadata if isinstance(metadata, dict) else None,
        anti_ai_mode=anti_ai_mode,
        raw_writing_mode=raw_writing_mode,
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
    content: str,
    title: str,
    bundle,
    *,
    metadata: dict | None = None,
    anti_ai_mode: AntiAiMode = "strict",
    raw_writing_mode: bool = False,
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
    # POLICY (CLAUDE.md #5): de-AI runs before disk unless raw writing mode opts out.
    draft_before_de_ai = final_text
    run_de_ai_checklist = _should_run_de_ai_checklist()
    if not raw_writing_mode and run_de_ai_checklist:
        final_text = de_ai_pass(final_text, tier="light", timeout=180, relaxed=anti_ai_mode == "relaxed")
    draft_before_epistemic = final_text
    if _is_tech_industry_editorial_input(content):
        final_text = epistemic_bias_pass(final_text, tier="light", timeout=180)
    draft_before_speaker_identity = final_text
    if run_de_ai_checklist:
        final_text = speaker_identity_vulnerability_pass(
            final_text,
            content=content,
            metadata=metadata,
            tier="light",
            timeout=180,
        )
    editorial_choices = _collect_editorial_choices(draft_before_de_ai, final_text, anti_ai_mode=anti_ai_mode)
    _append_epistemic_editorial_choice(editorial_choices, draft_before_epistemic, final_text)
    _append_speaker_identity_editorial_choice(editorial_choices, draft_before_speaker_identity, final_text)
    final_text += _build_judgment_disclosure(final_text, editorial_choices)
    if not run_de_ai_checklist:
        passed, safety_msg = _generated_content_preflight(workspace, content, final_text)
        if not passed:
            (workspace / "summary.txt").write_text(safety_msg, encoding="utf-8")
            return None
    out_path = workspace / "output.md"
    out_path.write_text(final_text, encoding="utf-8")
    record_writer_gate(workspace, channel="publish", artifact_path=str(out_path), source="writer.quick")

    summary = f"Quick draft ready: {title} (~{len(final_text)} chars)"
    (workspace / "summary.txt").write_text(summary, encoding="utf-8")
    return summary


def _handle_full_write(
    workspace: Path,
    content: str,
    title: str,
    bundle,
    *,
    metadata: dict | None = None,
    anti_ai_mode: AntiAiMode = "strict",
    raw_writing_mode: bool = False,
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
        edited = existing
        run_de_ai_checklist = _should_run_de_ai_checklist()
        if not raw_writing_mode and run_de_ai_checklist:
            edited = de_ai_pass(edited, tier="light", timeout=240, relaxed=anti_ai_mode == "relaxed")
        before_epistemic = edited
        if _is_tech_industry_editorial_input(content):
            edited = epistemic_bias_pass(edited, tier="light", timeout=240)
        before_speaker_identity = edited
        if run_de_ai_checklist:
            edited = speaker_identity_vulnerability_pass(
                edited,
                content=content,
                metadata=metadata,
                tier="light",
                timeout=240,
            )
        editorial_choices = _collect_editorial_choices(existing, edited, anti_ai_mode=anti_ai_mode)
        _append_epistemic_editorial_choice(editorial_choices, before_epistemic, edited)
        _append_speaker_identity_editorial_choice(editorial_choices, before_speaker_identity, edited)
        edited += _build_judgment_disclosure(edited, editorial_choices)
        if not run_de_ai_checklist:
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
) -> dict:
    """Compile a list of markdown chapters into one de-AI'd EPUB.

    POLICY (CLAUDE.md #5): the canonical path for "compile reading notes
    into a book artifact". Each chapter is run through `de_ai_pass()`
    BEFORE concatenation. Never bypass with raw pandoc on raw chapters.

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
        edited = de_ai_pass(raw, tier=tier, timeout=per_chapter_timeout)
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
