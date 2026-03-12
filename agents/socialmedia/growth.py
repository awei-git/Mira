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
MIN_POSTS_TO_ENABLE_COMMENTING = 3
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

    Uses POST /api/v1/free on the publication's subdomain.
    """
    from substack import _get_substack_config
    import urllib.request
    import urllib.error

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return False

    try:
        payload = json.dumps({
            "email": "",
            "first_url": f"https://{subdomain}.substack.com/",
            "current_url": f"https://{subdomain}.substack.com/",
        }).encode("utf-8")

        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/free",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Origin": f"https://{subdomain}.substack.com",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ct = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8")

        if "application/json" not in ct:
            log.warning("Subscribe to %s: non-JSON response", subdomain)
            return False

        result = json.loads(raw)
        sub_id = result.get("subscription_id")
        log.info("Subscribed to %s (sub_id=%s)", subdomain, sub_id)

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
# Auto-discover and follow interesting publications
# ---------------------------------------------------------------------------

# Topics that match Mira's interests — rotated through for discovery
_DISCOVERY_QUERIES = [
    "mechanistic interpretability",
    "philosophy of mind consciousness",
    "cognitive science",
    "complexity emergence systems",
    "mathematics beauty",
    "interdisciplinary thinking",
    "AI alignment safety",
    "epistemology knowledge",
    "agent architecture autonomous",
    "literature philosophy intersection",
    "economics complexity",
    "information theory",
]

MAX_NEW_FOLLOWS_PER_CYCLE = 2
DISCOVERY_COOLDOWN_DAYS = 3  # Don't discover too often


def should_discover() -> bool:
    """Check if it's time to discover new publications."""
    state = _load_state()
    last = state.get("last_discovery", "")
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if datetime.now() - last_dt < timedelta(days=DISCOVERY_COOLDOWN_DAYS):
                return False
        except ValueError:
            pass
    return True


def discover_and_follow() -> list[str]:
    """Search for interesting publications and follow them.

    Picks a random query from Mira's interest areas, searches Substack,
    filters for smaller/newer accounts, and subscribes.

    Returns list of newly followed subdomains.
    """
    import random
    import time
    import urllib.request

    from substack import _get_substack_config

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return []

    state = _load_state()
    existing = set(state.get("subscriptions", []))

    # Pick 2 random queries
    queries = random.sample(_DISCOVERY_QUERIES, min(2, len(_DISCOVERY_QUERIES)))
    candidates = []

    for query in queries:
        try:
            req = urllib.request.Request(
                f"https://substack.com/api/v1/publication/search?query={query.replace(' ', '+')}&page=0",
                headers={
                    "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            results = data.get("results", []) if isinstance(data, dict) else data
            for pub in results:
                sub = pub.get("subdomain", "")
                if sub and sub not in existing:
                    candidates.append({
                        "subdomain": sub,
                        "name": pub.get("name", ""),
                        "description": pub.get("hero_text", "") or pub.get("description", ""),
                        "query": query,
                    })
        except Exception as e:
            log.warning("Discovery search '%s' failed: %s", query, e)

    if not candidates:
        log.info("Discovery: no new candidates found")
        state["last_discovery"] = datetime.now().isoformat()
        _save_state(state)
        return []

    # Pick top candidates (prefer ones not already followed)
    random.shuffle(candidates)
    to_follow = candidates[:MAX_NEW_FOLLOWS_PER_CYCLE]

    followed = []
    for pub in to_follow:
        if subscribe_to_publication(pub["subdomain"]):
            followed.append(pub["subdomain"])
            log.info("Discovery: followed %s (%s) via query '%s'",
                     pub["name"], pub["subdomain"], pub["query"])
            time.sleep(1.5)

    state["last_discovery"] = datetime.now().isoformat()

    # Track discovery history
    history = state.get("discovery_history", [])
    for pub in to_follow:
        history.append({
            "subdomain": pub["subdomain"],
            "name": pub["name"],
            "query": pub["query"],
            "date": datetime.now().isoformat(),
            "followed": pub["subdomain"] in followed,
        })
    state["discovery_history"] = history[-50:]
    _save_state(state)

    return followed


# ---------------------------------------------------------------------------
# Like / react to posts on recommended publications
# ---------------------------------------------------------------------------

# Map of recommended publication subdomains (correct API subdomains)
# Publications with custom domains that block cross-domain reactions are excluded
LIKEABLE_SUBDOMAINS = [
    "simonw",              # Simon Willison
    "stratechery",         # Stratechery (Ben Thompson)
    "paulgraham",          # Paul Graham
    "thezvi",              # Zvi Mowshowitz
    "mattlevine",          # Matt Levine
    "cognitiverevolution", # Nathan Lebenz
    "nathanlambert",       # Interconnects (Nathan Lambert)
    "gwern",               # Gwern
    # Custom domains — reactions don't register via API:
    # oneusefulthing (oneusefulthing.org), lenny (lennysnewsletter.com),
    # astralcodexten (astralcodexten.com), dwarkesh (dwarkesh.com),
    # constructionphysics (construction-physics.com)
]

MAX_LIKES_PER_CYCLE = 5
LIKE_COOLDOWN_HOURS = 12


def _like_post(post_id: int, cookie: str) -> bool:
    """Like a single post via Substack reaction API."""
    import requests as _req
    try:
        r = _req.post(
            f"https://substack.com/api/v1/post/{post_id}/reaction",
            cookies={"substack.sid": cookie},
            json={"reaction": "\u2764"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def run_like_cycle():
    """Like recent posts from recommended publications.

    Picks a random subset of publications, likes their latest post
    if not already liked. Respects rate limits.
    """
    import random
    import time
    import requests as _req

    from substack import _get_substack_config

    state = _load_state()

    # Cooldown check
    last_like = state.get("last_like_at", "")
    if last_like:
        try:
            last_dt = datetime.fromisoformat(last_like)
            if datetime.now() - last_dt < timedelta(hours=LIKE_COOLDOWN_HOURS):
                log.info("Like cycle: cooldown active (last: %s)", last_like)
                return
        except ValueError:
            pass

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return

    liked_ids = set(state.get("liked_post_ids", []))

    # Shuffle and pick a subset
    subs = list(LIKEABLE_SUBDOMAINS)
    random.shuffle(subs)

    liked_count = 0
    for sub in subs:
        if liked_count >= MAX_LIKES_PER_CYCLE:
            break
        try:
            r = _req.get(
                f"https://{sub}.substack.com/api/v1/posts?limit=2",
                timeout=10,
            )
            if r.status_code != 200:
                continue
            posts = r.json()
            for post in posts:
                if liked_count >= MAX_LIKES_PER_CYCLE:
                    break
                post_id = post["id"]
                if post_id in liked_ids:
                    continue
                if _like_post(post_id, cookie):
                    # Verify
                    slug = post.get("slug", "")
                    r2 = _req.get(
                        f"https://{sub}.substack.com/api/v1/posts/{slug}",
                        cookies={"substack.sid": cookie},
                        timeout=10,
                    )
                    if r2.status_code == 200 and r2.json().get("reaction"):
                        liked_ids.add(post_id)
                        liked_count += 1
                        log.info("Liked: %s — %s", sub, post["title"][:60])
                time.sleep(1.5)
        except Exception as e:
            log.warning("Like cycle error on %s: %s", sub, e)

    if liked_count > 0:
        state["last_like_at"] = datetime.now().isoformat()
        # Keep last 500 liked IDs
        state["liked_post_ids"] = list(liked_ids)[-500:]
        today = datetime.now().strftime("%Y-%m-%d")
        state[f"likes_{today}"] = state.get(f"likes_{today}", 0) + liked_count
        _save_state(state)
        log.info("Like cycle: liked %d posts", liked_count)


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

    # Like recent posts from recommended publications
    try:
        run_like_cycle()
    except Exception as e:
        log.error("Like cycle failed: %s", e)

    # Auto-discover and follow new publications
    if should_discover():
        try:
            followed = discover_and_follow()
            if followed:
                log.info("Discovery: followed %d new publications: %s",
                         len(followed), ", ".join(followed))
        except Exception as e:
            log.error("Discovery failed: %s", e)

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
