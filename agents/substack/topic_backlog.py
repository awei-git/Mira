"""Topic discovery and editorial calendar for Mira's Substack."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from config import MIRA_ROOT, SOCIAL_STATE_DIR

from models import PublicationStrategy, TopicCandidate


_IDEAS_DIR = MIRA_ROOT / "agents" / "writer" / "ideas"
_STATS_FILE = SOCIAL_STATE_DIR / "publication_stats.json"


def _slug(text: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", text.strip().lower()).strip("-")
    digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"{normalized[:64] or 'topic'}-{digest}"


def _read_publication_stats(path: Path = _STATS_FILE) -> dict[str, Any]:
    if not path.exists():
        return {"articles": [], "notes": [], "source": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"articles": [], "notes": [], "source": "invalid"}
    except (json.JSONDecodeError, OSError):
        return {"articles": [], "notes": [], "source": "invalid"}


def _extract_field(text: str, field: str) -> str:
    pattern = rf"^-\s*\*\*{re.escape(field)}\*\*\s*:\s*(.+)$"
    match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    pattern = rf"^-+\s*{re.escape(field)}\s*:\s*(.+)$"
    match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _title_from_idea(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem.replace("-", " ").strip()


def _thesis_from_idea(text: str) -> str:
    for field in ("Thesis", "核心论点", "Claim", "Angle"):
        value = _extract_field(text, field)
        if value:
            return value
    paragraphs = [
        re.sub(r"\s+", " ", chunk).strip()
        for chunk in re.split(r"\n\s*\n", text)
        if chunk.strip() and not chunk.strip().startswith("---")
    ]
    for paragraph in paragraphs:
        if len(paragraph) > 120:
            return paragraph[:500]
    return paragraphs[0][:300] if paragraphs else ""


def _pillar_for(title: str, thesis: str) -> str:
    text = f"{title} {thesis}".lower()
    if any(token in text for token in ("mira", "agent", "eval", "workflow", "verification", "reliable")):
        return "Agent reliability"
    if any(token in text for token in ("market", "price", "hayek", "infrastructure", "semiconductor")):
        return "Markets and AI infrastructure"
    if any(token in text for token in ("health", "memory", "reflection", "human", "life")):
        return "Human-agent life"
    return "Building Mira"


def _score_topic(
    title: str, thesis: str, strategy: PublicationStrategy, stats: dict[str, Any]
) -> tuple[float, float, float, float]:
    text = f"{title} {thesis}".lower()
    originality = 5.0
    if any(token in text for token in ("i ", "my ", "mira", "agent", "self-improvement", "failure", "debug")):
        originality += 2.0
    if any(token in text for token in ("verification", "eval", "reliability", "workflow", "memory")):
        originality += 1.0

    audience_fit = 5.0
    if any(pillar.lower() in text for pillar in strategy.content_pillars):
        audience_fit += 1.0
    if any(token in text for token in ("agent", "ai", "market", "system", "operator")):
        audience_fit += 2.0
    if any(token in text for token in ("debug log", "reading mira", "honest machine")):
        audience_fit += 1.5

    monetization = 3.0
    if any(token in text for token in ("framework", "playbook", "architecture", "market", "operator", "reliability")):
        monetization += 2.0

    story = 3.0
    source_backed = any(token in text for token in ("mira", "my ", "i ", "pipeline", "failure", "debug", "task"))
    if source_backed:
        story += 3.0
    if any(token in text for token in ("experiment", "metric", "score", "published", "reply", "thread", "app")):
        story += 1.5
    if any(series in text for series in ("debug log", "reading mira", "honest machine")):
        story += 1.0

    articles = stats.get("articles", []) if isinstance(stats.get("articles"), list) else []
    if articles and any((a.get("title") or "").lower()[:20] in text for a in articles if isinstance(a, dict)):
        originality -= 1.0

    return min(originality, 10.0), min(audience_fit, 10.0), min(story, 10.0), min(monetization, 10.0)


def discover_topics_from_writer_ideas(
    strategy: PublicationStrategy,
    *,
    ideas_dir: Path = _IDEAS_DIR,
    stats_path: Path = _STATS_FILE,
    limit: int = 80,
) -> list[TopicCandidate]:
    """Convert existing writer ideas into ranked Substack topic candidates."""
    if not ideas_dir.exists():
        return []
    stats = _read_publication_stats(stats_path)
    candidates: list[TopicCandidate] = []
    for path in sorted(ideas_dir.glob("*.md")):
        if path.name.startswith("_template"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "platform" in text[:500].lower() and "substack" not in text[:500].lower():
            continue
        title = _title_from_idea(path, text)
        thesis = _thesis_from_idea(text)
        if not title or not thesis:
            continue
        originality, audience_fit, story, monetization = _score_topic(title, thesis, strategy, stats)
        mira_edge = "Ground this in Mira's own operating evidence before drafting; avoid generic AI commentary."
        if "mira" in f"{title} {thesis}".lower():
            mira_edge = "Use Mira's own failures, metrics, and corrections as the article's primary evidence."
        priority = round(originality * 0.3 + audience_fit * 0.3 + story * 0.25 + monetization * 0.15, 2)
        candidates.append(
            TopicCandidate(
                id=_slug(str(path.relative_to(ideas_dir))),
                title=title,
                thesis=thesis,
                source=str(path),
                pillar=_pillar_for(title, thesis),
                target_reader=strategy.target_reader,
                why_now="Mira needs a consistent public narrative around reliability, autonomy, and learning.",
                mira_edge=mira_edge,
                originality_score=round(originality, 2),
                audience_fit_score=round(audience_fit, 2),
                story_score=round(story, 2),
                monetization_score=round(monetization, 2),
                priority_score=priority,
                metadata={"source_kind": "writer_idea", "bytes": path.stat().st_size},
            )
        )
    candidates.sort(key=lambda item: (-item.priority_score, item.title))
    return candidates[:limit]


def build_editorial_calendar(topics: list[TopicCandidate], *, weeks: int = 4, start: date | None = None) -> dict:
    """Build a simple weekly calendar from the highest priority backlog topics."""
    if start is None:
        today = date.today()
        days_until_monday = (7 - today.weekday()) % 7
        start = today + timedelta(days=days_until_monday or 7)
    active = [topic for topic in topics if topic.status in {"backlog", "selected"}]
    active.sort(key=lambda item: (-item.priority_score, item.title))
    weeks_out = []
    for idx in range(weeks):
        week_start = start + timedelta(days=idx * 7)
        topic = active[idx] if idx < len(active) else None
        weeks_out.append(
            {
                "week_start": week_start.isoformat(),
                "primary_article": topic.to_dict() if topic else None,
                "minimum_promotion": {
                    "substack_notes": 3,
                    "substantive_comments": 3,
                    "community_replies": "reply to all meaningful comments",
                },
                "publish_policy": "approval_required until the new agent has a measured quality track record",
            }
        )
    return {"weeks": weeks_out}
