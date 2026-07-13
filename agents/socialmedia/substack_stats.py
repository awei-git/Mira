"""Substack stats, reading, and export operations.

Fetches post lists, publication statistics, and exports articles as Markdown.
"""

import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("publisher.substack")


def _get_substack_config() -> dict:
    """Load Substack credentials from secrets.yml."""
    from substack import _get_substack_config as _cfg

    return _cfg()


def get_recent_posts(limit: int = 10) -> list[dict]:
    """Get recent published posts with comment counts."""
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return []

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/posts?limit={limit}",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            posts = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "id": p["id"],
                "title": p.get("title", ""),
                "slug": p.get("slug", ""),
                "comment_count": p.get("comment_count", 0),
                "post_date": p.get("post_date", ""),
            }
            for p in posts
            if isinstance(p, dict)
        ]
    except Exception as e:
        log.error("Failed to fetch posts: %s", e)
        return []


def get_published_post_count() -> int:
    """Get the number of published posts on Mira's Substack."""
    posts = get_recent_posts(limit=50)
    return len(posts)


def _fetch_post_detail(slug: str, subdomain: str, cookie: str) -> dict | None:
    """Fetch detailed data for a single post via slug (reactions, comments, restacks)."""
    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/posts/{slug}",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("Failed to fetch detail for post '%s': %s", slug, e)
        return None


def fetch_publication_stats(*, force: bool = False, min_interval_hours: float = 6.0) -> dict:
    """Fetch stats for all published articles and recent Notes.

    Rate-limited: if `publication_stats.json` was updated within
    `min_interval_hours`, return the cached disk copy instead of
    hitting the Substack API. This tames the 429 storm the baseline
    flagged (multiple "Failed to fetch posts: HTTP Error 429" across
    the week).

    Wrapped by a per-provider circuit breaker so that during rate-
    limit cool-offs we return cached data fast instead of piling more
    requests on a rate-limited host.

    Args:
        force: skip the freshness check and force an API call.
        min_interval_hours: return cached if the on-disk file is
            younger than this.

    Returns the stats dict (also saved to disk on refresh).
    """
    from datetime import datetime, timezone
    from config import SOCIAL_STATE_DIR
    from net.circuit_breaker import CircuitOpen, get_circuit

    stats_file = SOCIAL_STATE_DIR / "publication_stats.json"

    # Freshness short-circuit — if disk copy is fresh enough, reuse it.
    if not force and stats_file.exists():
        try:
            age_hours = (datetime.now(timezone.utc).timestamp() - stats_file.stat().st_mtime) / 3600
            if age_hours < min_interval_hours:
                return json.loads(stats_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass  # fall through to a live fetch

    breaker = get_circuit(
        "substack",
        window_seconds=3600.0,  # 1h window: 429s are infrequent
        min_samples=3,  # trip on any cluster
        error_rate_threshold=0.5,
        cooldown_seconds=1800.0,  # 30min backoff keeps us away from the rate limiter
    )

    try:
        return breaker.call(lambda: _fetch_publication_stats_inner())
    except CircuitOpen:
        log.warning("Substack fetch skipped: circuit OPEN (recent failures)")
        if stats_file.exists():
            try:
                return json.loads(stats_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"articles": [], "notes": [], "fetched_at": None, "circuit_open": True}
    except Exception as e:
        log.warning("Substack fetch failed (%s); returning cached", e)
        if stats_file.exists():
            try:
                return json.loads(stats_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"articles": [], "notes": [], "fetched_at": None, "error": str(e)[:120]}


def fetch_subscriber_snapshot() -> dict:
    """Fetch inbound subscriber count + details from the writer dashboard.

    Uses the dashboard XHR endpoints:
    - /api/v1/publish-dashboard/summary-v2?range=30 — total/paid subs + 30d delta
    - /api/v1/subscriber-stats — per-subscriber rows (name, uid, rating, signup)

    Returns dict with keys: total, paid, delta_30d, subscribers (list).
    Empty dict on failure.
    """
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return {}

    headers = {
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    result: dict = {"total": 0, "paid": 0, "delta_30d": 0, "subscribers": []}

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/publish-dashboard/summary-v2?range=30",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            s = json.loads(r.read().decode("utf-8"))
        result["total"] = int(s.get("totalSubscribersEnd") or 0)
        result["paid"] = int(s.get("paidSubscribersEnd") or 0)
        start = int(s.get("totalSubscribersStart") or 0)
        result["delta_30d"] = result["total"] - start
    except Exception as e:
        log.warning("summary-v2 fetch failed: %s", e)

    try:
        # Note: /api/v1/subscriber-stats takes POST in browser but GET also works
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/subscriber-stats",
            data=b"{}",
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            s = json.loads(r.read().decode("utf-8"))
        subs = s.get("subscribers", []) or []
        result["subscribers"] = [
            {
                "name": x.get("user_name"),
                "user_id": x.get("user_id"),
                "email": x.get("user_email_address"),
                "activity_rating": x.get("activity_rating"),
                "signup_at": x.get("subscription_created_at"),
                "interval": x.get("subscription_interval"),
            }
            for x in subs
        ]
    except Exception as e:
        log.warning("subscriber-stats fetch failed: %s", e)

    return result


def _fetch_publication_stats_inner() -> dict:
    from datetime import datetime, timezone

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        log.error("Substack not configured — cannot fetch stats")
        return {}

    from config import SOCIAL_STATE_DIR

    stats_file = SOCIAL_STATE_DIR / "publication_stats.json"

    # --- Articles ---
    posts = get_recent_posts(limit=50)
    articles = []
    total_views = 0
    total_likes = 0
    total_comments = 0
    total_restacks = 0
    best_title = ""
    best_views = 0

    for post in posts:
        detail = _fetch_post_detail(post.get("slug", ""), subdomain, cookie)
        if not detail:
            # Fall back to basic data from list endpoint
            articles.append(
                {
                    "id": post["id"],
                    "title": post.get("title", ""),
                    "slug": post.get("slug", ""),
                    "views": 0,
                    "likes": 0,
                    "comments": post.get("comment_count", 0),
                    "restacks": 0,
                    "post_date": post.get("post_date", ""),
                }
            )
            total_comments += post.get("comment_count", 0)
            continue

        views = detail.get("views", 0) or 0
        # Substack uses "reactions" for likes (heart reactions)
        reactions = detail.get("reactions", {})
        likes = reactions.get("\u2764", 0) if isinstance(reactions, dict) else 0
        # Also check top-level reaction_count as fallback
        if not likes:
            likes = detail.get("reaction_count", 0) or 0
        comments = detail.get("comment_count", 0) or 0
        restacks = detail.get("restacks", 0) or detail.get("restack_count", 0) or 0

        articles.append(
            {
                "id": post["id"],
                "title": detail.get("title", post.get("title", "")),
                "slug": detail.get("slug", post.get("slug", "")),
                "views": views,
                "likes": likes,
                "comments": comments,
                "restacks": restacks,
                "post_date": detail.get("post_date", post.get("post_date", "")),
            }
        )

        total_views += views
        total_likes += likes
        total_comments += comments
        total_restacks += restacks

        if views > best_views:
            best_views = views
            best_title = detail.get("title", post.get("title", ""))

    # --- Notes ---
    notes_file = SOCIAL_STATE_DIR / "notes_state.json"
    notes_entries = []
    if notes_file.exists():
        try:
            notes_data = json.loads(notes_file.read_text(encoding="utf-8"))
            for note in notes_data.get("history", []):
                note_text = note.get("text", "")
                notes_entries.append(
                    {
                        "id": note.get("id"),
                        "text_preview": note_text[:120],
                        "likes": note.get("likes", 0),
                        "comments": note.get("comments", 0),
                        "restacks": note.get("restacks", 0),
                        "date": note.get("date", ""),
                    }
                )
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read notes_state.json: %s", e)

    # --- Summary ---
    summary_parts = [
        f"Total articles: {len(articles)}",
        f"Total views: {total_views}",
        f"Total likes: {total_likes}",
        f"Total comments: {total_comments}",
        f"Total restacks: {total_restacks}",
        f"Total notes: {len(notes_entries)}",
    ]
    if best_title:
        summary_parts.append(f'Best performing: "{best_title}" ({best_views} views)')

    # --- Subscribers (inbound) ---
    subscriber_snapshot = fetch_subscriber_snapshot()
    if subscriber_snapshot:
        summary_parts.append(
            f"Subscribers: {subscriber_snapshot.get('total', 0)} "
            f"(+{subscriber_snapshot.get('delta_30d', 0)} in 30d, "
            f"{subscriber_snapshot.get('paid', 0)} paid)"
        )

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
        "notes": notes_entries,
        "subscribers": subscriber_snapshot,
        "summary": ". ".join(summary_parts),
    }

    # Save atomically
    tmp_fd, tmp_path = tempfile.mkstemp(dir=SOCIAL_STATE_DIR, suffix=".tmp", prefix="pub_stats_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, stats_file)
        log.info("Publication stats saved: %d articles, %d notes", len(articles), len(notes_entries))
    except Exception as e:
        log.warning("Stats file save failed: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return result


def export_articles_as_markdown(output_dir: str | Path | None = None) -> list[Path]:
    """Export all published Substack articles as Markdown files.

    Each file has YAML frontmatter (title, date, url, subtitle, wordcount, cover)
    followed by the article body in Markdown.

    Returns list of written file paths.
    """
    from substack_format import _html_to_markdown

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        log.error("Substack not configured")
        return []

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "artifacts" / "writings" / "_published"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    posts = get_recent_posts(limit=50)
    written = []

    for post in posts:
        slug = post["slug"]
        detail = _fetch_post_detail(slug, subdomain, cookie)
        if not detail:
            continue

        title = detail.get("title", slug)
        body_html = detail.get("body_html", "")
        post_date = detail.get("post_date", "")[:10]
        subtitle = detail.get("subtitle", "")
        cover = detail.get("cover_image", "")
        wordcount = detail.get("wordcount", 0)
        url = detail.get("canonical_url", f"https://{subdomain}.substack.com/p/{slug}")

        md = _html_to_markdown(body_html)

        content = (
            f"---\n"
            f'title: "{title}"\n'
            f"date: {post_date}\n"
            f"url: {url}\n"
            f'subtitle: "{subtitle}"\n'
            f"wordcount: {wordcount}\n"
            f"cover: {cover}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"{md}\n"
        )

        filename = f"{post_date}_{slug}.md"
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
        log.info("Exported: %s (%d words)", filename, wordcount)

    log.info("Exported %d articles to %s", len(written), output_dir)
    return written
