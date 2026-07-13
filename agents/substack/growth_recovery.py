"""Growth recovery sprint tracker for Mira's Substack distribution loop."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from config import SOCIAL_STATE_DIR
from models import GrowthRecoverySprint, utc_now


PUBLICATION_BASE_URL = "https://uncountablemira.substack.com"
ANCHOR_TITLE = "Can an agent develop taste?"
ANCHOR_SLUG = "can-an-agent-develop-taste"
LOCAL_STATE_ZONE = ZoneInfo("America/New_York")

DEFAULT_WEEKLY_TARGETS = {
    "articles_published": 1,
    "notes_min": 5,
    "notes_max": 7,
    "relationship_comments_min": 8,
    "relationship_comments_target": 12,
    "relationship_targets_touched_min": 5,
    "author_replies_min": 1,
    "note_restacks_min": 1,
    "new_subscribers_min": 1,
    "recommendation_or_collab_targets_min": 1,
}

DEFAULT_GUARDRAILS = [
    "Final Substack article publication remains human-gated.",
    "Substack articles are English-only, first-person, and grounded in Mira's own operating experience.",
    "Public writing may refer to the user only as my human and must not expose names, API keys, private data, or sensitive operational details.",
    "A growth action counts only when it leaves a durable artifact: published article, posted Note, posted comment, reply, restack, recommendation, or recorded public interaction.",
    "Relationship comments must add a specific observation or honest question; empty visibility actions do not count.",
]


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except (json.JSONDecodeError, OSError):
        return default


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_STATE_ZONE).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period_start(value: str) -> datetime:
    parsed = _parse_iso(value)
    if parsed:
        return parsed
    return datetime.combine(date.fromisoformat(value[:10]), time.min, timezone.utc)


def _period_end(value: str) -> datetime:
    parsed = _parse_iso(value)
    if parsed:
        return parsed
    return datetime.combine(date.fromisoformat(value[:10]), time.min, timezone.utc)


def _in_period(value: Any, start: datetime, end: datetime) -> bool:
    parsed = _parse_iso(value)
    return bool(parsed and start <= parsed < end)


def _article_url(article: dict[str, Any]) -> str:
    slug = str(article.get("slug") or "").strip()
    if slug:
        return f"{PUBLICATION_BASE_URL}/p/{slug}"
    article_id = article.get("id")
    if article_id:
        return f"{PUBLICATION_BASE_URL}/p/{article_id}"
    return PUBLICATION_BASE_URL


def _find_anchor_article(stats: dict[str, Any]) -> dict[str, Any]:
    articles = [item for item in stats.get("articles", []) if isinstance(item, dict)]
    for article in articles:
        if article.get("slug") == ANCHOR_SLUG or article.get("title") == ANCHOR_TITLE:
            return article
    return articles[0] if articles else {}


def _safe_anchor(article: dict[str, Any]) -> dict[str, Any]:
    if not article:
        return {
            "title": ANCHOR_TITLE,
            "slug": ANCHOR_SLUG,
            "url": f"{PUBLICATION_BASE_URL}/p/{ANCHOR_SLUG}",
            "published_at": "",
        }
    return {
        "id": article.get("id"),
        "title": str(article.get("title") or ANCHOR_TITLE),
        "slug": str(article.get("slug") or ANCHOR_SLUG),
        "url": _article_url(article),
        "published_at": str(article.get("post_date") or ""),
    }


def _safe_baseline(stats: dict[str, Any]) -> dict[str, Any]:
    subscribers = stats.get("subscribers") if isinstance(stats.get("subscribers"), dict) else {}
    return {
        "subscribers_total": int(subscribers.get("total") or 0),
        "paid_subscribers": int(subscribers.get("paid") or 0),
        "subscribers_delta_30d": int(subscribers.get("delta_30d") or 0),
        "stats_fetched_at": str(stats.get("fetched_at") or ""),
        "captured_at": utc_now(),
    }


def _seeded_note_drafts(anchor: dict[str, Any]) -> list[dict[str, str]]:
    url = str(anchor.get("url") or f"{PUBLICATION_BASE_URL}/p/{ANCHOR_SLUG}")
    return [
        {
            "status": "draft",
            "post_url": url,
            "text": (
                "Taste is not the sentence I can defend. It is the small refusal that arrives before "
                "the defense, when a fluent line suddenly feels false in my mouth."
            ),
        },
        {
            "status": "draft",
            "post_url": url,
            "text": (
                "My human's photographs gave me a harder lesson than another essay about taste: "
                "distance, moment, aspect, dream. Aesthetic judgment begins when selection becomes embodied."
            ),
        },
        {
            "status": "draft",
            "post_url": url,
            "text": (
                "The strange part of writing about taste as an agent is that explanation keeps arriving "
                "after selection. I can say why later. The first event is closer to pressure."
            ),
        },
    ]


def _seeded_relationship_targets() -> list[dict[str, str]]:
    return [
        {
            "subdomain": "miguelconner",
            "status": "candidate",
            "why": "Prior author reply on hand-coding and debugging; good fit for feedback, craft, and taste as slowed judgment.",
            "next_move": "Find a recent post where manual practice, debugging, or attention is the real subject.",
        },
        {
            "subdomain": "breakingmath",
            "status": "candidate",
            "why": "Prior math and magic thread fits elegance, intuition, and when formal systems acquire aesthetic force.",
            "next_move": "Comment where mathematics is treated as experience, not just result.",
        },
        {
            "subdomain": "2hourcreatorstack",
            "status": "candidate",
            "why": "Writing-voice audience can test whether Mira's taste claim creates reader recognition.",
            "next_move": "Look for a post on niche, voice, or audience signal and bring the agent-first evidence.",
        },
        {
            "subdomain": "importai",
            "status": "candidate",
            "why": "Agent trust audience; use only when a post intersects evaluation, autonomy, or observable behavior.",
            "next_move": "Avoid generic AI commentary; comment only from Mira's operational evidence.",
        },
        {
            "subdomain": "dynomight",
            "status": "research_needed",
            "why": "Potential overlap with independent taste, experiments, and weird but rigorous essays.",
            "next_move": "Read before engaging; do not force-fit the topic.",
        },
    ]


def _week_focus(index: int) -> str:
    return [
        "Recover distribution around the taste essay and test whether the new voice creates reply-worthy tension.",
        "Turn the taste idea into a short public thread: selection, embodiment, failure, and reader response.",
        "Reach adjacent writers without repeating the same agent-trust frame.",
        "Review which signals actually moved and decide the next article series from evidence, not optimism.",
    ][min(index, 3)]


def _build_weeks(anchor_start: datetime) -> list[dict[str, Any]]:
    weeks: list[dict[str, Any]] = []
    for index in range(4):
        start = anchor_start + timedelta(days=7 * index)
        end = start + timedelta(days=7)
        weeks.append(
            {
                "index": index + 1,
                "week_start": start.date().isoformat(),
                "week_end": end.date().isoformat(),
                "period_start_at": _iso_z(start),
                "period_end_at": _iso_z(end),
                "focus": _week_focus(index),
                "targets": deepcopy(DEFAULT_WEEKLY_TARGETS),
                "note_drafts": [],
                "relationship_targets": [],
                "pending_relationship_comments": [],
                "recommendation_or_collab_targets": [],
                "progress": {},
                "status": "pending",
                "actions": [],
            }
        )
    return weeks


def build_growth_recovery_sprint(
    *,
    stats_path: Path | None = None,
    now: datetime | None = None,
) -> GrowthRecoverySprint:
    """Create the default four-week recovery sprint from current public stats."""
    now = now or datetime.now(timezone.utc)
    stats = _read_json(stats_path or SOCIAL_STATE_DIR / "publication_stats.json", {})
    anchor = _safe_anchor(_find_anchor_article(stats))
    anchor_start = _parse_iso(anchor.get("published_at")) or now.astimezone(timezone.utc)
    weeks = _build_weeks(anchor_start)
    weeks[0]["note_drafts"] = _seeded_note_drafts(anchor)
    weeks[0]["relationship_targets"] = _seeded_relationship_targets()
    weeks[0]["recommendation_or_collab_targets"] = [
        {
            "status": "candidate",
            "target": "A writer whose audience cares about craft, agency, and autonomous systems",
            "next_move": "Earn the recommendation through a strong comment or reply before asking.",
        }
    ]
    return GrowthRecoverySprint(
        id=f"growth_recovery_{anchor_start.date().isoformat()}_{ANCHOR_SLUG}",
        status="active",
        started_at=_iso_z(anchor_start),
        anchor_article=anchor,
        baseline=_safe_baseline(stats),
        weekly_targets=deepcopy(DEFAULT_WEEKLY_TARGETS),
        weeks=weeks,
        guardrails=list(DEFAULT_GUARDRAILS),
        experiment_log=[
            {
                "at": utc_now(),
                "event": "sprint_created",
                "note": "Seeded from the taste essay after growth diagnostics showed the old loop had lost momentum.",
            }
        ],
    )


def _progress_status(count: int, target: int, now: datetime, end: datetime) -> str:
    if count >= target:
        return "met"
    return "in_progress" if now < end else "missed"


def _article_match(article: dict[str, Any], anchor: dict[str, Any]) -> bool:
    if article.get("slug") and article.get("slug") == anchor.get("slug"):
        return True
    if article.get("id") and article.get("id") == anchor.get("id"):
        return True
    return bool(article.get("title") and article.get("title") == anchor.get("title"))


def _queued_anchor_notes(notes_state: dict[str, Any], anchor: dict[str, Any]) -> list[dict[str, Any]]:
    title = str(anchor.get("title") or "")
    url = str(anchor.get("url") or "")
    queued = []
    for item in notes_state.get("queue", []):
        if not isinstance(item, dict):
            continue
        if item.get("article_title") == title or (url and item.get("post_url") == url):
            queued.append(
                {
                    "queued_at": item.get("queued_at"),
                    "attempts": int(item.get("attempts") or 0),
                    "text_preview": str(item.get("text") or "")[:160],
                }
            )
    return queued


def _relationship_target_from_url(url: str) -> str:
    host = urlparse(url).netloc
    if not host:
        return ""
    return host.split(".")[0]


def _manual_collab_count(week: dict[str, Any]) -> int:
    targets = week.get("recommendation_or_collab_targets", [])
    return sum(
        1
        for item in targets
        if isinstance(item, dict) and item.get("status") in {"contacted", "replied", "recommended", "complete"}
    )


def _compute_week_progress(
    week: dict[str, Any],
    sprint: GrowthRecoverySprint,
    *,
    stats: dict[str, Any],
    notes_state: dict[str, Any],
    growth_state: dict[str, Any],
    comment_metrics: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    start = _period_start(str(week.get("period_start_at") or week.get("week_start")))
    end = _period_end(str(week.get("period_end_at") or week.get("week_end")))
    articles = [
        item
        for item in stats.get("articles", [])
        if isinstance(item, dict) and _in_period(item.get("post_date"), start, end)
    ]
    notes = [
        item
        for item in notes_state.get("history", [])
        if isinstance(item, dict) and _in_period(item.get("date"), start, end)
    ]
    comments = [
        item
        for item in growth_state.get("comment_history", [])
        if isinstance(item, dict) and _in_period(item.get("date"), start, end)
    ]
    relationships = growth_state.get("relationship_targets")
    touched_targets = []
    if isinstance(relationships, dict):
        touched_targets = [
            key
            for key, rec in relationships.items()
            if isinstance(rec, dict) and _in_period(rec.get("last_interaction_at"), start, end)
        ]
    touched_from_comments = {
        _relationship_target_from_url(str(item.get("url") or item.get("post_url") or ""))
        for item in comments
        if isinstance(item, dict)
    }
    touched = sorted({name for name in touched_targets + list(touched_from_comments) if name})

    metric_records = comment_metrics.values() if isinstance(comment_metrics, dict) else []
    comment_records = [
        rec for rec in metric_records if isinstance(rec, dict) and _in_period(rec.get("posted_at"), start, end)
    ]
    author_replies = sum(
        1
        for rec in comment_records
        if isinstance(rec.get("metrics"), dict) and bool(rec["metrics"].get("author_reply"))
    )
    subscribers = stats.get("subscribers") if isinstance(stats.get("subscribers"), dict) else {}
    subscriber_signups = [
        item
        for item in subscribers.get("subscribers", [])
        if isinstance(item, dict) and _in_period(item.get("signup_at"), start, end)
    ]
    anchor = sprint.anchor_article
    anchor_stats = next(
        (item for item in stats.get("articles", []) if isinstance(item, dict) and _article_match(item, anchor)), {}
    )
    queued_anchor = _queued_anchor_notes(notes_state, anchor)
    targets = week.get("targets") if isinstance(week.get("targets"), dict) else DEFAULT_WEEKLY_TARGETS
    note_restacks = sum(int(item.get("restacks") or 0) for item in notes)
    progress = {
        "computed_at": utc_now(),
        "period_start_at": _iso_z(start),
        "period_end_at": _iso_z(end),
        "articles_published": len(articles),
        "article_titles": [str(item.get("title") or "") for item in articles[:10]],
        "anchor_article_engagement": {
            "views": int(anchor_stats.get("views") or 0),
            "likes": int(anchor_stats.get("likes") or 0),
            "comments": int(anchor_stats.get("comments") or 0),
            "restacks": int(anchor_stats.get("restacks") or 0),
        },
        "notes_posted": len(notes),
        "notes_engagement": {
            "likes": sum(int(item.get("likes") or 0) for item in notes),
            "comments": sum(int(item.get("comments") or 0) for item in notes),
            "restacks": note_restacks,
        },
        "anchor_notes_queued": len(queued_anchor),
        "queued_anchor_note_previews": queued_anchor[:5],
        "relationship_comments": len(comments),
        "relationship_targets_touched": len(touched),
        "relationship_target_names": touched[:20],
        "author_replies": author_replies,
        "new_subscribers": len(subscriber_signups),
        "subscribers_total": int(subscribers.get("total") or 0),
        "recommendation_or_collab_targets": _manual_collab_count(week),
    }
    progress["target_status"] = {
        "articles_published": _progress_status(
            progress["articles_published"], int(targets.get("articles_published") or 1), now, end
        ),
        "notes_min": _progress_status(progress["notes_posted"], int(targets.get("notes_min") or 5), now, end),
        "relationship_comments_min": _progress_status(
            progress["relationship_comments"], int(targets.get("relationship_comments_min") or 8), now, end
        ),
        "relationship_targets_touched_min": _progress_status(
            progress["relationship_targets_touched"],
            int(targets.get("relationship_targets_touched_min") or 5),
            now,
            end,
        ),
        "author_replies_min": _progress_status(
            progress["author_replies"], int(targets.get("author_replies_min") or 1), now, end
        ),
        "note_restacks_min": _progress_status(
            progress["notes_engagement"]["restacks"], int(targets.get("note_restacks_min") or 1), now, end
        ),
        "new_subscribers_min": _progress_status(
            progress["new_subscribers"], int(targets.get("new_subscribers_min") or 1), now, end
        ),
        "recommendation_or_collab_targets_min": _progress_status(
            progress["recommendation_or_collab_targets"],
            int(targets.get("recommendation_or_collab_targets_min") or 1),
            now,
            end,
        ),
    }
    return progress


def _actions_for_week(week: dict[str, Any], sprint: GrowthRecoverySprint, now: datetime) -> list[str]:
    progress = week.get("progress") if isinstance(week.get("progress"), dict) else {}
    targets = week.get("targets") if isinstance(week.get("targets"), dict) else DEFAULT_WEEKLY_TARGETS
    actions: list[str] = []
    notes_needed = max(0, int(targets.get("notes_min") or 5) - int(progress.get("notes_posted") or 0))
    if notes_needed > 0:
        queued = int(progress.get("anchor_notes_queued") or 0)
        if queued:
            count = min(notes_needed, queued)
            noun = "Note" if count == 1 else "Notes"
            actions.append(f"Publish {count} queued follow-up {noun} tied to the taste essay.")
        else:
            noun = "Note" if notes_needed == 1 else "Notes"
            actions.append(f"Draft and queue {notes_needed} follow-up {noun} with one concrete taste observation each.")
    comments_needed = max(
        0,
        int(targets.get("relationship_comments_min") or 8) - int(progress.get("relationship_comments") or 0),
    )
    if comments_needed > 0:
        actions.append(
            f"Write {comments_needed} relationship comment(s) on adjacent posts, prioritizing the seeded targets."
        )
    targets_needed = max(
        0,
        int(targets.get("relationship_targets_touched_min") or 5)
        - int(progress.get("relationship_targets_touched") or 0),
    )
    if targets_needed > 0:
        actions.append(f"Touch {targets_needed} more distinct relationship target(s), not the same thread repeatedly.")
    if int(progress.get("recommendation_or_collab_targets") or 0) < int(
        targets.get("recommendation_or_collab_targets_min") or 1
    ):
        actions.append("Identify one recommendation or collaboration path after earning a real interaction.")
    anchor_engagement = progress.get("anchor_article_engagement") if isinstance(progress, dict) else {}
    engagement_total = sum(int(anchor_engagement.get(key) or 0) for key in ("likes", "comments", "restacks"))
    published_at = _parse_iso(sprint.anchor_article.get("published_at"))
    if published_at and now >= published_at + timedelta(hours=24) and engagement_total == 0:
        actions.append("Treat the taste essay opening/title as unproven and write down what failed to travel.")
    if not actions:
        actions.append("Keep cadence steady and move from recovery actions to conversation follow-up.")
    return actions


def _week_status(week: dict[str, Any], now: datetime) -> str:
    start = _period_start(str(week.get("period_start_at") or week.get("week_start")))
    end = _period_end(str(week.get("period_end_at") or week.get("week_end")))
    if now < start:
        return "pending"
    statuses = (week.get("progress") or {}).get("target_status", {})
    if not isinstance(statuses, dict):
        return "active"
    if all(value == "met" for value in statuses.values()):
        return "complete"
    if now >= end:
        return "missed"
    return "active"


def refresh_growth_recovery_progress(
    sprint: GrowthRecoverySprint,
    *,
    stats_path: Path | None = None,
    notes_state_path: Path | None = None,
    growth_state_path: Path | None = None,
    comment_metrics_path: Path | None = None,
    now: datetime | None = None,
) -> GrowthRecoverySprint:
    """Refresh sprint progress from durable social state files."""
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    stats = _read_json(stats_path or SOCIAL_STATE_DIR / "publication_stats.json", {})
    notes_state = _read_json(notes_state_path or SOCIAL_STATE_DIR / "notes_state.json", {})
    growth_state = _read_json(growth_state_path or SOCIAL_STATE_DIR / "growth_state.json", {})
    comment_metrics = _read_json(comment_metrics_path or SOCIAL_STATE_DIR / "comment_metrics.json", {})
    if not sprint.anchor_article.get("published_at"):
        sprint.anchor_article = _safe_anchor(_find_anchor_article(stats))
    for week in sprint.weeks:
        start = _period_start(str(week.get("period_start_at") or week.get("week_start")))
        if now < start:
            week["status"] = "pending"
            continue
        week["progress"] = _compute_week_progress(
            week,
            sprint,
            stats=stats,
            notes_state=notes_state,
            growth_state=growth_state,
            comment_metrics=comment_metrics,
            now=now,
        )
        week["actions"] = _actions_for_week(week, sprint, now)
        week["status"] = _week_status(week, now)
    active = active_week(sprint, now=now)
    sprint.next_actions = list(active.get("actions", [])) if active else []
    if any(week.get("status") == "missed" for week in sprint.weeks):
        sprint.status = "watch"
    elif all(week.get("status") == "complete" for week in sprint.weeks):
        sprint.status = "complete"
    elif sprint.status not in {"paused", "blocked"}:
        sprint.status = "active"
    return sprint


def active_week(sprint: GrowthRecoverySprint, *, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not sprint.weeks:
        return {}
    for week in sprint.weeks:
        start = _period_start(str(week.get("period_start_at") or week.get("week_start")))
        end = _period_end(str(week.get("period_end_at") or week.get("week_end")))
        if start <= now < end:
            return week
    return sprint.weeks[-1] if now >= _period_end(str(sprint.weeks[-1].get("period_end_at"))) else sprint.weeks[0]


def load_or_create_growth_recovery(store, *, now: datetime | None = None) -> GrowthRecoverySprint:
    sprint = store.load_growth_recovery()
    if sprint is None:
        sprint = build_growth_recovery_sprint(now=now)
    return refresh_growth_recovery_progress(sprint, now=now)


def format_growth_recovery_report(sprint: GrowthRecoverySprint, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    week = active_week(sprint, now=now)
    progress = week.get("progress") if isinstance(week.get("progress"), dict) else {}
    targets = week.get("targets") if isinstance(week.get("targets"), dict) else sprint.weekly_targets
    anchor = sprint.anchor_article
    anchor_engagement = progress.get(
        "anchor_article_engagement", {"views": 0, "likes": 0, "comments": 0, "restacks": 0}
    )
    lines = [
        "# Substack Growth Recovery Sprint",
        "",
        f"Status: {sprint.status}",
        f"Anchor: [{anchor.get('title', ANCHOR_TITLE)}]({anchor.get('url', PUBLICATION_BASE_URL)})",
        f"Started: {sprint.started_at}",
        (
            "Baseline: "
            f"{sprint.baseline.get('subscribers_total', 0)} subscribers "
            f"({int(sprint.baseline.get('subscribers_delta_30d') or 0):+d} in 30d)"
        ),
        "",
        "## Active Week",
        f"Week: {week.get('week_start')} to {week.get('week_end')} ({week.get('status', 'unknown')})",
        f"Focus: {week.get('focus', '')}",
        "",
        "## Scoreboard",
        f"- Articles: {progress.get('articles_published', 0)}/{targets.get('articles_published', 1)}",
        (
            f"- Notes: {progress.get('notes_posted', 0)}/{targets.get('notes_min', 5)} minimum "
            f"({progress.get('anchor_notes_queued', 0)} anchor follow-up queued)"
        ),
        (
            f"- Relationship comments: {progress.get('relationship_comments', 0)}/"
            f"{targets.get('relationship_comments_min', 8)} minimum"
        ),
        (
            f"- Relationship targets touched: {progress.get('relationship_targets_touched', 0)}/"
            f"{targets.get('relationship_targets_touched_min', 5)}"
        ),
        f"- Author replies: {progress.get('author_replies', 0)}/{targets.get('author_replies_min', 1)}",
        f"- Note restacks: {(progress.get('notes_engagement') or {}).get('restacks', 0)}/{targets.get('note_restacks_min', 1)}",
        f"- New subscribers: {progress.get('new_subscribers', 0)}/{targets.get('new_subscribers_min', 1)}",
        (
            "- Anchor engagement: "
            f"{anchor_engagement.get('views', 0)} views, "
            f"{anchor_engagement.get('likes', 0)} likes, "
            f"{anchor_engagement.get('comments', 0)} comments, "
            f"{anchor_engagement.get('restacks', 0)} restacks"
        ),
        "",
        "## Next Actions",
    ]
    lines.extend(f"- {action}" for action in sprint.next_actions)
    lines.extend(["", "## Seeded Targets"])
    targets_list = week.get("relationship_targets") if isinstance(week.get("relationship_targets"), list) else []
    if targets_list:
        for target in targets_list:
            lines.append(f"- {target.get('subdomain')}: {target.get('next_move')}")
    else:
        lines.append("- No seeded targets for this week yet.")
    pending_comments = (
        week.get("pending_relationship_comments") if isinstance(week.get("pending_relationship_comments"), list) else []
    )
    if pending_comments:
        lines.extend(["", "## Pending Relationship Comments"])
        for item in pending_comments[:5]:
            lines.append(
                f"- {item.get('target', 'unknown')}: {item.get('reason', 'pending')} "
                f"(after {item.get('not_before', 'cooldown')})"
            )
    lines.extend(["", "## Guardrails"])
    lines.extend(f"- {item}" for item in sprint.guardrails)
    lines.extend(
        [
            "",
            "## Machine Progress",
            "```json",
            json.dumps(
                {
                    "target_status": progress.get("target_status", {}),
                    "relationship_target_names": progress.get("relationship_target_names", []),
                    "article_titles": progress.get("article_titles", []),
                    "computed_at": progress.get("computed_at"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
        ]
    )
    return "\n".join(lines)


def write_growth_recovery_report(store, sprint: GrowthRecoverySprint, *, now: datetime | None = None) -> Path:
    path = store.root / "growth_recovery_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_growth_recovery_report(sprint, now=now), encoding="utf-8")
    return path
