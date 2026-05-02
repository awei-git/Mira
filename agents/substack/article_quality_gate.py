"""Post-draft quality gate for Mira Substack articles.

This is the publish-time complement to ``editorial.py``. Editorial packages
shape a topic before drafting; this module inspects the actual draft and blocks
weak or risky pieces before the live publisher has side effects.
"""

from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ARTICLE_PACKET_NAME = ".substack_article_packet.json"

FORBIDDEN_TITLE_PATTERNS = (
    r"^on\s+",
    r"^toward\s+",
    r"^the\s+\w+\s+of\s+",
    r"^a\s+theory\s+of\s+",
    r"^an\s+essay\s+on\s+",
    r"^[a-z ]{3,24}$",
    r"\bnot\s+\w+,\s+but\s+\w+",
)

GENERIC_TITLE_WORDS = {
    "thoughts",
    "reflections",
    "insights",
    "musings",
    "framework",
    "guide",
    "analysis",
}

OPERATIONAL_WORDS = {
    "agent",
    "app",
    "artifact",
    "backlog",
    "bridge",
    "debug",
    "draft",
    "eval",
    "evaluation",
    "experiment",
    "failure",
    "feed",
    "log",
    "memory",
    "metric",
    "note",
    "pipeline",
    "publish",
    "reply",
    "score",
    "task",
    "thread",
    "workflow",
}


@dataclass
class ArticleQualityReport:
    title: str
    subtitle: str
    reader_promise: str
    opening: str
    evidence_ledger: list[dict[str, Any]] = field(default_factory=list)
    quality_scores: dict[str, float] = field(default_factory=dict)
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pass_gate(self) -> bool:
        return not self.blocking_reasons

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pass_gate"] = self.pass_gate
        return data


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_title(article_text: str, fallback: str = "") -> str:
    for line in article_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return _compact(stripped[2:])
    return _compact(fallback)


def _extract_opening(article_text: str) -> str:
    body = re.sub(r"^#\s+.+$", "", article_text, count=1, flags=re.MULTILINE).strip()
    paragraphs = [_compact(p) for p in re.split(r"\n\s*\n", body) if _compact(p)]
    paragraphs = [p for p in paragraphs if not p.startswith("#") and not p.startswith("*")]
    opening = paragraphs[0] if paragraphs else body[:500]
    sentences = re.split(r"(?<=[.!?])\s+", opening)
    return " ".join(sentences[:2]).strip()


def _score_title(title: str) -> float:
    lower = title.lower().strip()
    score = 5.0
    if 24 <= len(title) <= 82:
        score += 1.5
    if re.search(r"\b(i|my|mira|agent|bug|lost|broke|failed|why|how|what|\d+)\b", lower):
        score += 1.5
    if re.search(r"\b(memory|pipeline|evaluation|publish|draft|task|system|reader|experiment)\b", lower):
        score += 1.0
    if any(word in lower for word in GENERIC_TITLE_WORDS):
        score -= 2.0
    if any(re.search(pattern, lower) for pattern in FORBIDDEN_TITLE_PATTERNS):
        score -= 2.5
    if ":" in title and len(title.split(":", 1)[0].split()) <= 3:
        score -= 1.0
    return round(min(max(score, 0.0), 10.0), 2)


def _score_opening(opening: str) -> float:
    lower = opening.lower()
    score = 4.0
    if 80 <= len(opening) <= 420:
        score += 1.5
    if re.search(r"\b(i|my|mira|my human|yesterday|today|last week|on \w+day|\d+)\b", lower):
        score += 1.5
    if any(word in lower for word in ("failed", "broke", "lost", "wrong", "noticed", "asked", "ran")):
        score += 1.0
    if lower.startswith(("in today's rapidly", "as an ai agent", "the question of", "in this essay")):
        score -= 3.0
    if lower.startswith(("this article", "this essay")):
        score -= 2.0
    return round(min(max(score, 0.0), 10.0), 2)


def _score_voice(article_text: str) -> float:
    lower = article_text.lower()
    score = 5.0
    if any(token in lower for token in ("my human", "mira", "my pipeline", "my memory", "my draft", "i ran")):
        score += 1.5
    if re.search(r"\bi (traced|caught|noticed|found|realized|ran|scored|published|tested)\b", lower):
        score += 0.75
    if sum(1 for token in OPERATIONAL_WORDS if token in lower) >= 4:
        score += 1.0
    if re.search(r"\b(i was wrong|i did not|i didn't|i still do not know|i changed my mind)\b", lower):
        score += 1.0
    if any(phrase in lower for phrase in ("in conclusion", "this article explores", "it could be argued")):
        score -= 2.0
    if lower.count("—") > max(3, len(article_text) // 1200):
        score -= 1.0
    return round(min(max(score, 0.0), 10.0), 2)


def _score_reader_value(article_text: str, subtitle: str, reader_promise: str = "") -> float:
    lower = f"{article_text} {subtitle}".lower()
    score = 5.0
    if subtitle and 40 <= len(subtitle) <= 160:
        score += 1.0
    if reader_promise:
        score += 0.75
    if any(token in lower for token in ("because", "the reason", "what this means", "if this is true")):
        score += 1.0
    if any(token in lower for token in ("checklist", "standard", "rule", "framework", "lesson", "test")):
        score += 1.0
    if len(article_text) >= 3500:
        score += 1.0
    if len(article_text) < 1800:
        score -= 1.0
    return round(min(max(score, 0.0), 10.0), 2)


def first_person_operational_claims(article_text: str) -> list[str]:
    """Return source-sensitive first-person claims that need evidence."""
    claims = []
    for sentence in re.split(r"(?<=[.!?])\s+", _compact(article_text)):
        lower = sentence.lower()
        if not re.search(r"\b(i|my|mira)\b", lower):
            continue
        if any(word in lower for word in OPERATIONAL_WORDS) or re.search(r"\b\d+\b", lower):
            claims.append(sentence)
    return claims


def _ledger_covers_claims(claims: list[str], evidence_ledger: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    if not claims:
        return True, []
    if not evidence_ledger:
        return False, claims[:5]
    usable = [
        entry
        for entry in evidence_ledger
        if isinstance(entry, dict) and (entry.get("source") or entry.get("path") or entry.get("task_id"))
    ]
    if not usable:
        return False, claims[:5]
    if len(usable) < min(len(claims), 3):
        return False, claims[len(usable) : len(usable) + 5]
    return True, []


def evaluate_article_quality(
    *,
    article_text: str,
    title: str = "",
    subtitle: str = "",
    reader_promise: str = "",
    evidence_ledger: list[dict[str, Any]] | None = None,
) -> ArticleQualityReport:
    title = _extract_title(article_text, fallback=title)
    subtitle = _compact(subtitle)
    opening = _extract_opening(article_text)
    ledger = evidence_ledger or []

    scores = {
        "title": _score_title(title),
        "opening": _score_opening(opening),
        "voice": _score_voice(article_text),
        "reader_value": _score_reader_value(article_text, subtitle, reader_promise),
    }
    blocking: list[str] = []
    warnings: list[str] = []

    thresholds = {
        "title": 8.0,
        "opening": 8.0,
        "voice": 8.0,
        "reader_value": 7.5,
    }
    for key, threshold in thresholds.items():
        if scores[key] < threshold:
            blocking.append(f"{key} score {scores[key]} below pilot threshold {threshold}")

    claims = first_person_operational_claims(article_text)
    covered, uncovered = _ledger_covers_claims(claims, ledger)
    if not covered:
        blocking.append(
            "first-person operational claims need evidence ledger entries: "
            + " | ".join(claim[:140] for claim in uncovered[:3])
        )
    if not subtitle:
        blocking.append("subtitle is required")
    if not reader_promise:
        warnings.append("reader_promise missing; include it in the article packet")

    return ArticleQualityReport(
        title=title,
        subtitle=subtitle,
        reader_promise=reader_promise,
        opening=opening,
        evidence_ledger=ledger,
        quality_scores=scores,
        blocking_reasons=blocking,
        warnings=warnings,
    )


def load_article_packet(workspace: Path) -> dict[str, Any]:
    packet_path = Path(workspace) / ARTICLE_PACKET_NAME
    if not packet_path.exists():
        return {}
    try:
        data = json.loads(packet_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_article_packet(workspace: Path, packet: dict[str, Any]) -> Path:
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    packet_path = workspace / ARTICLE_PACKET_NAME
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return packet_path


def evaluate_workspace_article(
    *,
    workspace: Path,
    article_text: str,
    title: str = "",
    subtitle: str = "",
) -> ArticleQualityReport:
    packet = load_article_packet(Path(workspace))
    return evaluate_article_quality(
        article_text=article_text,
        title=title or str(packet.get("title") or ""),
        subtitle=subtitle or str(packet.get("subtitle") or ""),
        reader_promise=str(packet.get("reader_promise") or subtitle or ""),
        evidence_ledger=packet.get("evidence_ledger") if isinstance(packet.get("evidence_ledger"), list) else [],
    )


def format_quality_report(report: ArticleQualityReport) -> str:
    status = "PASS" if report.pass_gate else "BLOCK"
    lines = [
        f"Substack article quality gate: {status}",
        f"title: {report.title}",
        f"subtitle: {report.subtitle}",
        f"scores: {report.quality_scores}",
    ]
    if report.blocking_reasons:
        lines.append("blocking:")
        lines.extend(f"- {reason}" for reason in report.blocking_reasons)
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines)
