"""30-day pilot metrics review for Mira's Substack system."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import SOCIAL_STATE_DIR, WRITINGS_OUTPUT_DIR
from models import PilotReview


_STATUS_ORDER = ["approved", "published", "podcast_en", "podcast_zh", "complete"]


def _read_json(path: Path, default):
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except (json.JSONDecodeError, OSError):
        return default


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _in_period(value: str, start: datetime, end: datetime) -> bool:
    dt = _parse_iso(value)
    return bool(dt and _as_utc(start) <= _as_utc(dt) <= _as_utc(end))


def _status_at_least(status: str, target: str) -> bool:
    try:
        return _STATUS_ORDER.index(status) >= _STATUS_ORDER.index(target)
    except ValueError:
        return False


def _podcast_followthrough(manifest: dict, start: datetime, end: datetime) -> dict[str, Any]:
    records = []
    for item in (manifest.get("articles") or {}).values():
        if not isinstance(item, dict):
            continue
        timestamps = item.get("timestamps") if isinstance(item.get("timestamps"), dict) else {}
        published_at = str(timestamps.get("published") or "")
        if not _in_period(published_at, start, end):
            continue
        if item.get("auto_podcast") is False:
            continue
        status = str(item.get("status") or "")
        en_done = _status_at_least(status, "podcast_en")
        zh_done = _status_at_least(status, "podcast_zh")
        records.append(
            {
                "slug": item.get("slug"),
                "title": item.get("title"),
                "status": status,
                "english_done": en_done,
                "chinese_done": zh_done,
                "error": item.get("error", ""),
            }
        )
    incomplete = [item for item in records if not (item["english_done"] and item["chinese_done"])]
    return {
        "required_articles": len(records),
        "english_done": sum(1 for item in records if item["english_done"]),
        "chinese_done": sum(1 for item in records if item["chinese_done"]),
        "complete": sum(1 for item in records if item["english_done"] and item["chinese_done"]),
        "incomplete": incomplete,
    }


def build_pilot_review(
    *,
    stats_path: Path | None = None,
    growth_state_path: Path | None = None,
    notes_state_path: Path | None = None,
    comment_metrics_path: Path | None = None,
    publish_manifest_path: Path | None = None,
    now: datetime | None = None,
) -> PilotReview:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    stats = _read_json(stats_path or SOCIAL_STATE_DIR / "publication_stats.json", {})
    growth = _read_json(growth_state_path or SOCIAL_STATE_DIR / "growth_state.json", {})
    notes_state = _read_json(notes_state_path or SOCIAL_STATE_DIR / "notes_state.json", {})
    comment_metrics = _read_json(comment_metrics_path or SOCIAL_STATE_DIR / "comment_metrics.json", {})
    publish_manifest = _read_json(publish_manifest_path or WRITINGS_OUTPUT_DIR / "publish_manifest.json", {})

    articles = [
        item
        for item in stats.get("articles", [])
        if isinstance(item, dict) and _in_period(str(item.get("post_date", "")), start, now)
    ]
    notes = [
        item
        for item in notes_state.get("history", [])
        if isinstance(item, dict) and _in_period(str(item.get("date", "")), start, now)
    ]
    comments = [
        item
        for item in growth.get("comment_history", [])
        if isinstance(item, dict) and _in_period(str(item.get("date", "")), start, now)
    ]

    subscribers = stats.get("subscribers") if isinstance(stats.get("subscribers"), dict) else {}
    article_likes = sum(int(item.get("likes") or 0) for item in articles)
    article_comments = sum(int(item.get("comments") or 0) for item in articles)
    article_views = sum(int(item.get("views") or 0) for item in articles)
    notes_likes = sum(int(item.get("likes") or 0) for item in notes)
    notes_comments = sum(int(item.get("comments") or 0) for item in notes)

    relationship_records = (
        growth.get("relationship_targets") if isinstance(growth.get("relationship_targets"), dict) else {}
    )
    touched_relationships = [
        key
        for key, rec in relationship_records.items()
        if isinstance(rec, dict) and _in_period(str(rec.get("last_interaction_at", "")), start, now)
    ]
    author_replies = 0
    if isinstance(comment_metrics, dict):
        for rec in comment_metrics.values():
            if isinstance(rec, dict) and _in_period(str(rec.get("posted_at", "")), start, now):
                metrics = rec.get("metrics", {}) if isinstance(rec.get("metrics"), dict) else {}
                if metrics.get("author_reply"):
                    author_replies += 1

    podcast = _podcast_followthrough(publish_manifest, start, now)

    actions = []
    if len(articles) < 1:
        actions.append("Publish one source-backed flagship article this week.")
    if len(notes) < 5:
        actions.append("Post 5 high-quality Notes this week: article follow-through plus standalone observations.")
    if len(comments) < 8:
        actions.append("Make 8-12 targeted relationship comments with concrete examples or honest questions.")
    if podcast["incomplete"]:
        actions.append("Complete English and Chinese podcast follow-through for every published flagship article.")
    if article_likes + article_comments == 0 and articles:
        actions.append("Revise title/opening strategy; latest article did not create visible engagement.")
    if author_replies == 0 and comments:
        actions.append("Improve relationship comments; comments are not starting conversations yet.")

    status = "healthy"
    if len(actions) >= 3:
        status = "revise"
    elif actions:
        status = "watch"

    return PilotReview(
        id=f"pilot_{start.date().isoformat()}_{now.date().isoformat()}",
        period_start=start.date().isoformat(),
        period_end=now.date().isoformat(),
        status=status,
        published_count=len(articles),
        notes_count=len(notes),
        comments_count=len(comments),
        subscribers_total=int(subscribers.get("total") or 0),
        subscribers_delta_30d=int(subscribers.get("delta_30d") or 0),
        article_engagement={
            "views": article_views,
            "likes": article_likes,
            "comments": article_comments,
            "best": max(
                articles, key=lambda item: int(item.get("likes") or 0) + int(item.get("comments") or 0), default={}
            ),
        },
        notes_engagement={"likes": notes_likes, "comments": notes_comments},
        relationship_engagement={
            "targets_touched": len(touched_relationships),
            "target_names": touched_relationships[:20],
            "author_replies": author_replies,
        },
        podcast_followthrough=podcast,
        actions=actions,
    )


def format_pilot_review(review: PilotReview) -> str:
    lines = [
        "# Substack 30-Day Pilot Review",
        "",
        f"Period: {review.period_start} to {review.period_end}",
        f"Status: {review.status}",
        "",
        "## Scoreboard",
        f"- Published articles: {review.published_count}",
        f"- Notes posted: {review.notes_count}",
        f"- Relationship comments: {review.comments_count}",
        f"- Subscribers: {review.subscribers_total} ({review.subscribers_delta_30d:+d} in 30d)",
        f"- Article engagement: {review.article_engagement}",
        f"- Notes engagement: {review.notes_engagement}",
        f"- Relationship engagement: {review.relationship_engagement}",
        f"- Podcast follow-through: {review.podcast_followthrough}",
        "",
        "## Actions",
    ]
    if review.actions:
        lines.extend(f"- {action}" for action in review.actions)
    else:
        lines.append("- Continue the pilot cadence; no immediate corrective action.")
    return "\n".join(lines)
