"""Convert Substack pilot reviews into durable action backlog items."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from models import PilotReview
from ops.backlog import ActionBacklog, ActionItem


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _review_at(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _criteria(*items: str) -> list[str]:
    return [item for item in items if item]


def build_pilot_action_items(review: PilotReview) -> list[ActionItem]:
    """Build stable Substack pilot action items from a weekly review."""
    observed_at = _utc_now()
    next_review_at = _review_at()
    base_payload = {
        "kind": "substack_pilot_review",
        "review_id": review.id,
        "period_start": review.period_start,
        "period_end": review.period_end,
        "observed_at": observed_at,
        "next_review_at": next_review_at,
    }
    items: list[ActionItem] = []

    if review.published_count < 1:
        items.append(
            ActionItem(
                title="Substack pilot: publish weekly flagship article",
                description="The pilot produced no flagship article this week; select one source-backed topic and run the full article workflow.",
                source="substack_pilot",
                priority="high",
                target_dimension="substack_publishing",
                executor="substack.article_workflow",
                payload={
                    **base_payload,
                    "published_count": review.published_count,
                    "verification_criteria": _criteria(
                        "One source-backed public article is published.",
                        "The article record includes a passing quality gate and evidence ledger.",
                        "The weekly review sees the article in publication stats.",
                    ),
                },
            )
        )

    if review.notes_count < 5:
        items.append(
            ActionItem(
                title="Substack pilot: reach weekly Notes floor",
                description="The pilot did not reach the 5-Note weekly floor; queue article follow-through and standalone observation Notes.",
                source="substack_pilot",
                priority="medium",
                target_dimension="substack_notes",
                executor="substack.notes_cycle",
                payload={
                    **base_payload,
                    "notes_count": review.notes_count,
                    "target_notes": 5,
                    "verification_criteria": _criteria(
                        "At least 5 gated Notes are posted during the next weekly period.",
                        "Notes include article follow-through and standalone observations.",
                        "The weekly review reports Notes engagement, even if engagement is zero.",
                    ),
                },
            )
        )

    if review.comments_count < 8:
        items.append(
            ActionItem(
                title="Substack pilot: reach relationship comment floor",
                description="The pilot did not reach the relationship comment floor; use explicit targets and only post when Mira can add a real point.",
                source="substack_pilot",
                priority="medium",
                target_dimension="substack_relationships",
                executor="substack.relationship_comments",
                payload={
                    **base_payload,
                    "comments_count": review.comments_count,
                    "target_comments": 8,
                    "verification_criteria": _criteria(
                        "At least 8 substantive relationship comments are posted during the next weekly period.",
                        "Each comment has a tracked target, URL, text preview, and pattern.",
                        "Author replies or lack of replies are captured in the next weekly review.",
                    ),
                },
            )
        )

    podcast = review.podcast_followthrough if isinstance(review.podcast_followthrough, dict) else {}
    incomplete = podcast.get("incomplete") if isinstance(podcast.get("incomplete"), list) else []
    if incomplete:
        items.append(
            ActionItem(
                title="Substack pilot: complete podcast follow-through",
                description="Published flagship articles are missing English and/or Chinese podcast follow-through.",
                source="substack_pilot",
                priority="high",
                target_dimension="substack_podcast",
                executor="substack.podcast_followthrough",
                payload={
                    **base_payload,
                    "podcast_followthrough": podcast,
                    "verification_criteria": _criteria(
                        "Every listed flagship article reaches podcast_zh or complete in the publish manifest.",
                        "English and Chinese podcast artifacts exist, or a visible blocker is recorded.",
                        "The next weekly review reports zero incomplete podcast follow-through for the period.",
                    ),
                },
            )
        )

    article_engagement = review.article_engagement if isinstance(review.article_engagement, dict) else {}
    if (
        review.published_count
        and int(article_engagement.get("likes") or 0) + int(article_engagement.get("comments") or 0) == 0
    ):
        items.append(
            ActionItem(
                title="Substack pilot: improve title and opening engagement",
                description="A published article created no visible engagement; revise the next article's title/opening strategy before drafting.",
                source="substack_pilot",
                priority="medium",
                target_dimension="substack_article_quality",
                executor="substack.editorial_review",
                payload={
                    **base_payload,
                    "article_engagement": article_engagement,
                    "verification_criteria": _criteria(
                        "Next article packet includes at least 5 title candidates and 3 hook candidates.",
                        "Human/editorial review records a title/opening quality judgment.",
                        "Weekly review compares the next article against this baseline.",
                    ),
                },
            )
        )

    relationship = review.relationship_engagement if isinstance(review.relationship_engagement, dict) else {}
    if review.comments_count and int(relationship.get("author_replies") or 0) == 0:
        items.append(
            ActionItem(
                title="Substack pilot: improve relationship comment quality",
                description="Relationship comments are not producing replies; adjust targets or comment substance before increasing volume.",
                source="substack_pilot",
                priority="medium",
                target_dimension="substack_relationships",
                executor="substack.relationship_review",
                payload={
                    **base_payload,
                    "relationship_engagement": relationship,
                    "verification_criteria": _criteria(
                        "Review recent comments for specificity, language match, and genuine question quality.",
                        "Next weekly period records at least one author reply or a clear target adjustment.",
                    ),
                },
            )
        )

    return items


def upsert_pilot_action_items(
    review: PilotReview,
    *,
    backlog: ActionBacklog | None = None,
) -> list[ActionItem]:
    """Create or refresh active backlog items for the current pilot review."""
    if backlog is None:
        backlog = ActionBacklog()
    saved: list[ActionItem] = []
    for item in build_pilot_action_items(review):
        _, saved_item = backlog.upsert_active(item)
        saved.append(saved_item)
    backlog.expire_stale()
    return saved


def format_action_items(items: Iterable[ActionItem]) -> list[str]:
    """Human-readable lines for reports."""
    lines = []
    for item in items:
        lines.append(f"- [{item.priority}] {item.title} ({item.status}; executor={item.executor or 'none'})")
    return lines
