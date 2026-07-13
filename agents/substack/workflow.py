"""Article workflow records and article-packet creation for the Substack pilot."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from article_quality_gate import write_article_packet
from models import ArticleRecord, EditorialPackage, TopicCandidate, utc_now


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "article"


def build_article_packet(topic: TopicCandidate, package: EditorialPackage | None = None) -> dict[str, Any]:
    """Build the quality-gate packet required before drafting/publishing."""
    title_candidates = []
    if package:
        title_candidates.extend(package.subject_line_candidates)
        title_candidates.insert(0, package.recommended_title)
    title_candidates.append(topic.title)

    seen = set()
    deduped_titles = []
    for title in title_candidates:
        key = title.lower().strip()
        if title and key not in seen:
            seen.add(key)
            deduped_titles.append(title)

    evidence = [
        {
            "claim": "The topic is grounded in Mira operating evidence before drafting.",
            "source": topic.source,
            "topic_id": topic.id,
        }
    ]
    if topic.metadata.get("source_kind"):
        evidence.append({"claim": "Topic source kind", "source": topic.metadata["source_kind"], "topic_id": topic.id})

    return {
        "topic_id": topic.id,
        "title": package.recommended_title if package else topic.title,
        "title_candidates": deduped_titles[:5],
        "subtitle": (package.abstract[:150] if package else topic.thesis[:150]).strip(),
        "reader_promise": topic.mira_edge or topic.thesis,
        "format_choice": _format_choice(topic),
        "opening_direction": (package.hook_candidates[0] if package and package.hook_candidates else ""),
        "evidence_ledger": evidence,
        "risk_notes": {
            "privacy": "Do not publish paths, private names, account data, or implementation secrets.",
            "fake_experience": "Every first-person operational claim must map to an evidence ledger entry.",
            "brand": "Avoid gimmicky anthropomorphism; voice must be grounded in evidence.",
        },
        "created_at": utc_now(),
    }


def _format_choice(topic: TopicCandidate) -> str:
    text = f"{topic.title} {topic.thesis} {topic.mira_edge}".lower()
    if any(token in text for token in ("deep", "architecture", "framework", "systematic")):
        return "deep_dive"
    if any(token in text for token in ("bug", "failed", "failure", "debug", "lost")):
        return "quick_observation"
    return "medium_essay"


def build_article_records(
    topics: list[TopicCandidate],
    packages: list[EditorialPackage],
    *,
    limit: int = 4,
) -> list[ArticleRecord]:
    packages_by_topic = {package.topic_id: package for package in packages}
    records: list[ArticleRecord] = []
    for topic in topics[:limit]:
        package = packages_by_topic.get(topic.id)
        packet = build_article_packet(topic, package)
        title = packet["title"]
        records.append(
            ArticleRecord(
                id=f"article_{_slug(topic.id)}",
                topic_id=topic.id,
                title=title,
                state="approval_required",
                thesis=topic.thesis,
                score={
                    "priority": topic.priority_score,
                    "originality": topic.originality_score,
                    "audience_fit": topic.audience_fit_score,
                    "story": topic.story_score,
                    "monetization": topic.monetization_score,
                },
                metadata={
                    "article_packet": packet,
                    "editorial_gate": package.pass_gate if package else False,
                    "blocking_reasons": package.blocking_reasons if package else ["missing editorial package"],
                },
            )
        )
    return records


def write_article_packet_for_record(workspace: Path, record: ArticleRecord) -> Path:
    packet = record.metadata.get("article_packet") if isinstance(record.metadata, dict) else None
    if not isinstance(packet, dict):
        packet = {
            "topic_id": record.topic_id,
            "title": record.title,
            "subtitle": "",
            "reader_promise": record.thesis,
            "evidence_ledger": [],
        }
    return write_article_packet(workspace, packet)
