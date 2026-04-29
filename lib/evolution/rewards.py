"""Reward signal collectors.

Each function gathers reward signals from one external source and converts
them into experience records via record_experience().
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .config import EXPERIENCE_DIR
from .experience import record_experience

log = logging.getLogger("mira.evolution")

from config import SOCIAL_STATE_DIR


def collect_substack_rewards() -> list[str]:
    """Read publication_stats.json and record engagement as experiences.

    Called during growth cycle. Deduplicates by tracking seen IDs per day.
    Returns list of IDs recorded.
    """
    stats_file = SOCIAL_STATE_DIR / "publication_stats.json"
    if not stats_file.exists():
        return []

    try:
        stats = json.loads(stats_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    recorded: list[str] = []
    today = date.today().isoformat()

    # Dedup tracking
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    seen_file = EXPERIENCE_DIR / f".seen_{today}.json"
    seen: set[str] = set()
    if seen_file.exists():
        try:
            seen = set(json.loads(seen_file.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass

    # Articles
    for article in stats.get("articles", []):
        aid = str(article.get("id", ""))
        if not aid or aid in seen:
            continue
        likes = article.get("likes", 0)
        comments = article.get("comments", 0)
        restacks = article.get("restacks", 0)
        views = article.get("views", 0)
        if likes + comments + restacks + views == 0:
            continue

        record_experience(
            action=f"published article: {article.get('title', '')[:80]}",
            outcome=f"views={views} likes={likes} comments={comments} restacks={restacks}",
            reward={"views": views, "likes": likes, "comments": comments, "restacks": restacks},
            context={"type": "article", "slug": article.get("slug", ""), "post_date": article.get("post_date", "")},
            agent="writer",
        )
        seen.add(aid)
        recorded.append(aid)

    # Notes
    for note in stats.get("notes", []):
        nid = str(note.get("id", ""))
        if not nid or nid in seen:
            continue
        likes = note.get("likes", 0)
        comments = note.get("comments", 0)
        restacks = note.get("restacks", 0)
        if likes + comments + restacks == 0:
            continue

        record_experience(
            action=f"posted note: {note.get('text_preview', '')[:80]}",
            outcome=f"likes={likes} comments={comments} restacks={restacks}",
            reward={"likes": likes, "comments": comments, "restacks": restacks},
            context={"type": "note", "date": note.get("date", "")},
            agent="growth",
        )
        seen.add(nid)
        recorded.append(nid)

    # Persist seen set
    if recorded:
        try:
            seen_file.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        log.info("collect_substack_rewards: recorded %d engagement experiences", len(recorded))

    return recorded


def record_user_feedback(feedback: str, sentiment: str = "negative", task_id: str = ""):
    """Record explicit user feedback as a high-weight experience.

    Args:
        feedback: The user's words
        sentiment: "positive", "negative", or "repeated_failure"
        task_id: Related task if any
    """
    reward_key = {
        "positive": "wa_positive",
        "negative": "wa_negative",
        "repeated_failure": "wa_repeated_failure",
    }.get(sentiment, "wa_negative")

    record_experience(
        action=f"user feedback ({sentiment}): {feedback[:200]}",
        outcome=feedback[:500],
        reward={reward_key: 1},
        context={"sentiment": sentiment, "source": "wa_direct"},
        agent="super",
        task_id=task_id,
    )
