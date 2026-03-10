"""Substack account growth — commenting, Notes, cross-promotion.

Strategy:
1. Read & comment on relevant publications (after 10+ own posts)
2. Post Substack Notes to increase visibility
3. Track engagement metrics over time
4. Maintain a natural posting rhythm (not spammy)

Commenting rules:
- Only comment when Mira has genuine insight to add
- Never generic ("Great post!"), always specific and substantive
- Match the language of the original post
- Max 3 comments per day (avoid looking like a bot)
- Prioritize smaller publications where comments get noticed
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("socialmedia.growth")

# Comment posting limits
MAX_COMMENTS_PER_DAY = 3
MIN_POSTS_TO_ENABLE_COMMENTING = 10
COMMENT_COOLDOWN_HOURS = 6  # Min hours between comments


def _state_file() -> Path:
    return Path(__file__).resolve().parent / "growth_state.json"


def _load_state() -> dict:
    sf = _state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    _state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def is_commenting_enabled() -> bool:
    """Check if Mira has enough published posts to start commenting."""
    from substack import get_published_post_count
    count = get_published_post_count()
    enabled = count >= MIN_POSTS_TO_ENABLE_COMMENTING
    if not enabled:
        log.info("Commenting disabled: %d/%d posts published",
                 count, MIN_POSTS_TO_ENABLE_COMMENTING)
    return enabled


def can_comment_now() -> bool:
    """Check daily limit and cooldown."""
    if not is_commenting_enabled():
        return False

    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # Daily limit
    daily_count = state.get(f"comments_{today}", 0)
    if daily_count >= MAX_COMMENTS_PER_DAY:
        log.info("Daily comment limit reached: %d/%d", daily_count, MAX_COMMENTS_PER_DAY)
        return False

    # Cooldown
    last_comment = state.get("last_comment_at", "")
    if last_comment:
        try:
            last_dt = datetime.fromisoformat(last_comment)
            if datetime.now() - last_dt < timedelta(hours=COMMENT_COOLDOWN_HOURS):
                log.info("Comment cooldown active (last: %s)", last_comment)
                return False
        except ValueError:
            pass

    return True


def record_comment(post_url: str, comment_text: str, comment_id: int):
    """Record a comment for rate limiting and history."""
    state = _load_state()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    state["last_comment_at"] = now.isoformat()
    state[f"comments_{today}"] = state.get(f"comments_{today}", 0) + 1

    # Keep history for dedup and review
    history = state.get("comment_history", [])
    history.append({
        "url": post_url,
        "text": comment_text[:200],
        "id": comment_id,
        "date": now.isoformat(),
    })
    # Keep last 100 comments
    state["comment_history"] = history[-100:]

    _save_state(state)


def post_comment_on_article(post_url: str, comment_text: str) -> dict | None:
    """Post a comment with rate limiting and recording.

    Returns comment result dict or None.
    """
    if not can_comment_now():
        return None

    from substack import comment_on_post
    result = comment_on_post(post_url, comment_text)

    if result:
        record_comment(post_url, comment_text, result.get("id", 0))
        log.info("Growth comment posted on %s", post_url)

    return result


def get_comment_stats() -> dict:
    """Get commenting statistics."""
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    history = state.get("comment_history", [])

    return {
        "total_comments": len(history),
        "today_comments": state.get(f"comments_{today}", 0),
        "daily_limit": MAX_COMMENTS_PER_DAY,
        "commenting_enabled": is_commenting_enabled(),
        "last_comment": state.get("last_comment_at", "never"),
    }


# ---------------------------------------------------------------------------
# Substack Notes — delegated to notes.py
# ---------------------------------------------------------------------------

def post_note(text: str) -> dict | None:
    """Post a Substack Note. Delegates to notes.py."""
    from notes import post_note as _post_note
    return _post_note(text)


# ---------------------------------------------------------------------------
# Subscribe to publications (free tier)
# ---------------------------------------------------------------------------

def subscribe_to_publication(subdomain: str) -> bool:
    """Subscribe to a Substack publication (free tier).

    This makes their posts appear in Mira's reader feed.
    """
    from substack import _get_substack_config
    import urllib.request
    import urllib.error

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return False

    # First get publication ID
    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/archive?limit=1",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            posts = json.loads(resp.read().decode("utf-8"))
        if not posts:
            return False
        pub_id = posts[0].get("publication_id")
        if not pub_id:
            return False
    except Exception as e:
        log.error("Failed to get publication ID for %s: %s", subdomain, e)
        return False

    # Subscribe (free)
    try:
        payload = json.dumps({
            "publication_id": pub_id,
            "type": "free",
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://substack.com/api/v1/subscriptions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        log.info("Subscribed to %s (pub_id=%s)", subdomain, pub_id)

        # Record
        state = _load_state()
        subs = state.get("subscriptions", [])
        if subdomain not in subs:
            subs.append(subdomain)
        state["subscriptions"] = subs
        _save_state(state)

        return True
    except Exception as e:
        log.error("Failed to subscribe to %s: %s", subdomain, e)
        return False


def get_current_subscriptions() -> list[str]:
    """Get list of publications Mira is subscribed to."""
    state = _load_state()
    return state.get("subscriptions", [])


# ---------------------------------------------------------------------------
# Growth cycle — called from core.py on schedule
# ---------------------------------------------------------------------------

def run_growth_cycle(briefing_comments: list[dict] | None = None,
                     briefing_text: str = "",
                     soul_context: str = ""):
    """Run one growth cycle: comments + Notes.

    Args:
        briefing_comments: Optional list of comment suggestions from explore briefing.
            Each dict has: {url, comment_draft, reason}
        briefing_text: Recent briefing content for standalone Notes generation.
        soul_context: Mira's identity context for voice consistency.
    """
    from substack import get_published_post_count

    post_count = get_published_post_count()
    log.info("Growth cycle: %d posts published, commenting %s",
             post_count,
             "ENABLED" if post_count >= MIN_POSTS_TO_ENABLE_COMMENTING else "DISABLED")

    # Notes cycle runs regardless of post count (Notes help discovery)
    try:
        from notes import run_notes_cycle
        notes_summary = run_notes_cycle(briefing_text, soul_context)
        if notes_summary.get("backfilled") or notes_summary.get("standalone_posted"):
            log.info("Notes cycle: backfilled=%d, standalone=%s",
                     notes_summary.get("backfilled", 0),
                     notes_summary.get("standalone_posted", False))
    except Exception as e:
        log.error("Notes cycle failed: %s", e)

    if post_count < MIN_POSTS_TO_ENABLE_COMMENTING:
        log.info("Skipping comment cycle — need %d more posts",
                 MIN_POSTS_TO_ENABLE_COMMENTING - post_count)
        return

    # Post comments from briefing suggestions
    if briefing_comments and can_comment_now():
        for suggestion in briefing_comments[:1]:  # Max 1 per cycle
            url = suggestion.get("url", "")
            draft = suggestion.get("comment_draft", "")
            if url and draft:
                result = post_comment_on_article(url, draft)
                if result:
                    log.info("Posted briefing comment on %s", url)
                    break
