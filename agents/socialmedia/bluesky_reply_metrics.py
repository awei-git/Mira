"""Per-reply metric tracking for Bluesky growth-loop learning.

Mirrors `comment_metrics.py` for Substack. Each outbound reply on Bluesky
creates one record; a background poll fetches likes / quote count /
author replies / downstream replies; a separate attribution step matches
new followers against users who engaged with a reply thread.

Storage: DATA_DIR/social/bluesky_reply_metrics.json — dict keyed by
reply URI.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("socialmedia.bluesky.metrics")

MAX_AGE_DAYS = 14
MIN_POLL_INTERVAL_MINUTES = 60

_APPVIEW = "https://public.api.bsky.app"
_PDS = "https://bsky.social"


def _metrics_file() -> Path:
    from config import SOCIAL_STATE_DIR

    return SOCIAL_STATE_DIR / "bluesky_reply_metrics.json"


def _load() -> dict:
    f = _metrics_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("bluesky_reply_metrics.json unreadable — starting fresh")
        return {}


def _save(data: dict) -> None:
    f = _metrics_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _client():
    from bluesky.client import get_client

    return get_client()


# ---------------------------------------------------------------------------
# Record creation
# ---------------------------------------------------------------------------


def record_new_reply(
    reply_uri: str,
    reply_cid: str,
    parent_uri: str,
    parent_author_did: str,
    parent_author_handle: str,
    text: str,
    pattern: str | None = None,
) -> None:
    """Create a metric record for a newly posted reply on another user's post.

    parent_author_did: the DID of the user whose post we replied to.
    Used to detect author_reply later (did == parent_author_did).
    """
    if not reply_uri:
        return
    data = _load()
    if reply_uri in data:
        return

    data[reply_uri] = {
        "reply_uri": reply_uri,
        "reply_cid": reply_cid,
        "parent_uri": parent_uri,
        "parent_author_did": parent_author_did,
        "parent_author_handle": parent_author_handle,
        "posted_at": _now_iso(),
        "text": text,
        "pattern": pattern,
        "metrics": {
            "likes": 0,
            "reposts": 0,
            "quotes": 0,
            "liker_dids": [],
            "author_reply": False,
            "author_reply_text": None,
            "other_replies": 0,
            "replier_dids": [],
            "follows_attributed": 0,
            "attributed_followers": [],
        },
        "last_polled_at": None,
        "poll_count": 0,
        "closed": False,
        "closed_reason": None,
    }
    _save(data)
    log.info("bluesky_reply_metrics: recorded %s (pattern=%s)", reply_uri, pattern or "untagged")


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


def _get_post_thread(uri: str) -> dict | None:
    """Fetch full post thread including children (replies)."""
    try:
        c = _client()
        return c.get_post_thread(uri, depth=2)
    except Exception as e:
        log.debug("get_post_thread failed for %s: %s", uri, e)
        return None


def _get_likes(uri: str, cookie: str | None = None, limit: int = 100) -> list[dict]:
    """Fetch likers of a post via AppView. No auth needed."""
    try:
        url = f"{_APPVIEW}/xrpc/app.bsky.feed.getLikes?uri={urllib.parse.quote(uri, safe='')}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "mira-agent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data.get("likes", []) or []
    except Exception as e:
        log.debug("getLikes failed for %s: %s", uri, e)
        return []


def _extract_reply_features(thread: dict, parent_author_did: str) -> dict:
    """Walk `replies` under our reply-post to gather author_reply and counts."""
    author_reply = False
    author_reply_text = None
    other_replies = 0
    replier_dids: list[str] = []

    def walk(node: dict) -> None:
        nonlocal author_reply, author_reply_text, other_replies
        for child in node.get("replies") or []:
            if not isinstance(child, dict):
                continue
            post = child.get("post") or {}
            author = post.get("author") or {}
            did = author.get("did") or ""
            record = post.get("record") or {}
            if did:
                replier_dids.append(did)
            if did == parent_author_did and not author_reply:
                author_reply = True
                author_reply_text = (record.get("text") or "")[:400]
            else:
                other_replies += 1
            # Recurse into nested replies
            if child.get("replies"):
                walk(child)

    # thread shape: {"thread": {"post": {...}, "replies": [...]}}
    root = thread.get("thread") or thread
    walk(root)
    return {
        "author_reply": author_reply,
        "author_reply_text": author_reply_text,
        "other_replies": other_replies,
        "replier_dids": sorted(set(replier_dids)),
    }


def poll_open_records(limit: int = 15, rate_limit_sleep: float = 1.5) -> dict:
    """Update metrics for open records. Sleeps between calls to stay polite."""
    data = _load()
    now = datetime.now(timezone.utc)
    counters = {"polled": 0, "updated": 0, "closed": 0, "errored": 0}

    def sort_key(item: tuple[str, dict]) -> datetime:
        last = item[1].get("last_polled_at")
        if not last:
            return datetime.min.replace(tzinfo=timezone.utc)
        dt = _parse_iso(last)
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    open_items = sorted(
        [(k, v) for k, v in data.items() if not v.get("closed")],
        key=sort_key,
    )

    for uri, rec in open_items[:limit]:
        posted = _parse_iso(rec.get("posted_at", ""))
        if posted and (now - posted) > timedelta(days=MAX_AGE_DAYS):
            rec["closed"] = True
            rec["closed_reason"] = "max_age_reached"
            counters["closed"] += 1
            continue

        last_polled = _parse_iso(rec.get("last_polled_at") or "")
        if last_polled and (now - last_polled) < timedelta(minutes=MIN_POLL_INTERVAL_MINUTES):
            continue

        thread = _get_post_thread(uri)
        time.sleep(rate_limit_sleep)
        counters["polled"] += 1
        if not thread:
            counters["errored"] += 1
            continue

        post = (thread.get("thread") or {}).get("post") or {}
        old_metrics = dict(rec["metrics"])

        rec["metrics"]["likes"] = int(post.get("likeCount") or 0)
        rec["metrics"]["reposts"] = int(post.get("repostCount") or 0)
        rec["metrics"]["quotes"] = int(post.get("quoteCount") or 0)

        # Fetch liker DIDs for attribution
        likes = _get_likes(uri, limit=100)
        time.sleep(rate_limit_sleep)
        liker_dids = [(l.get("actor") or {}).get("did") for l in likes if (l.get("actor") or {}).get("did")]
        rec["metrics"]["liker_dids"] = sorted(set(liker_dids))

        # Reply features
        reply_features = _extract_reply_features(thread, rec.get("parent_author_did", ""))
        rec["metrics"].update(reply_features)

        rec["last_polled_at"] = _now_iso()
        rec["poll_count"] = rec.get("poll_count", 0) + 1

        if rec["metrics"] != old_metrics:
            counters["updated"] += 1

    _save(data)
    log.info(
        "bluesky_reply_metrics.poll: polled=%d updated=%d closed=%d errored=%d",
        counters["polled"],
        counters["updated"],
        counters["closed"],
        counters["errored"],
    )
    return counters


# ---------------------------------------------------------------------------
# Follow attribution
# ---------------------------------------------------------------------------


def _get_my_followers(limit_total: int = 200) -> list[dict]:
    """Return the current follower list with DIDs + indexedAt (= when they
    followed me). Uses the PDS/AppView getFollowers endpoint."""
    try:
        c = _client()
        c.ensure_session()
        handle = c.handle
    except Exception as e:
        log.warning("bluesky metrics attribution: cannot get handle: %s", e)
        return []

    followers: list[dict] = []
    cursor = ""
    while True:
        url = f"{_APPVIEW}/xrpc/app.bsky.graph.getFollowers?actor={handle}&limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mira-agent/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
            followers.extend(d.get("followers", []) or [])
            cursor = d.get("cursor") or ""
            if not cursor or len(followers) >= limit_total:
                break
        except Exception as e:
            log.debug("getFollowers pagination failed: %s", e)
            break
    return followers[:limit_total]


def attribute_follows(lookback_days: int = 14) -> dict:
    """For each follower whose follow record is newer than each reply record,
    check if they liked or replied to one of our reply posts. If yes,
    attribute the follow to the earliest such reply.

    Bluesky exposes DID on both reactors and followers, so this is a
    strict equality match (stronger than Substack's name-match).
    """
    data = _load()
    if not data:
        return {"attributed": 0, "followers_checked": 0}

    followers = _get_my_followers(limit_total=200)
    if not followers:
        return {"attributed": 0, "followers_checked": 0}

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=lookback_days)

    # Already-attributed set across all records
    already: set[str] = set()
    for rec in data.values():
        for a in rec.get("metrics", {}).get("attributed_followers", []):
            if isinstance(a, dict) and a.get("did"):
                already.add(a["did"])

    recent_followers: list[dict] = []
    for f in followers:
        # Bluesky returns `indexedAt` on the follower profile (when we first
        # saw them); no direct follow-creation timestamp in getFollowers.
        # Use indexedAt as a proxy for follow time.
        idx = _parse_iso(f.get("indexedAt") or f.get("createdAt") or "")
        did = f.get("did") or ""
        if not did or did in already:
            continue
        if idx and idx < window_start:
            continue
        recent_followers.append({"did": did, "handle": f.get("handle"), "indexedAt": f.get("indexedAt")})

    if not recent_followers:
        return {"attributed": 0, "followers_checked": len(followers)}

    attributed_count = 0
    for sub in recent_followers:
        did = sub["did"]
        # Find earliest matching record
        candidates: list[tuple[datetime, str, dict]] = []
        for uri, rec in data.items():
            posted = _parse_iso(rec.get("posted_at", ""))
            if not posted or posted < window_start:
                continue
            m = rec.get("metrics", {})
            if did in (m.get("liker_dids") or []) or did in (m.get("replier_dids") or []):
                candidates.append((posted, uri, rec))

        if not candidates:
            continue
        candidates.sort(key=lambda x: x[0])  # earliest first
        _, _, winner = candidates[0]
        winner["metrics"]["attributed_followers"].append(
            {"did": did, "handle": sub.get("handle"), "indexedAt": sub.get("indexedAt"), "attribution": "did_match"}
        )
        winner["metrics"]["follows_attributed"] = len(winner["metrics"]["attributed_followers"])
        attributed_count += 1
        log.info(
            "bluesky_reply_metrics: attributed @%s -> %s (%s)",
            sub.get("handle"),
            winner.get("reply_uri"),
            winner.get("pattern") or "untagged",
        )

    _save(data)
    return {"attributed": attributed_count, "followers_checked": len(followers)}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize_by_pattern(min_age_hours: float = 24.0) -> dict:
    data = _load()
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=min_age_hours)

    buckets: dict[str, dict] = {}
    for rec in data.values():
        posted = _parse_iso(rec.get("posted_at", ""))
        if not posted or posted > threshold:
            continue
        pattern = rec.get("pattern") or "untagged"
        b = buckets.setdefault(
            pattern,
            {
                "n": 0,
                "total_likes": 0,
                "total_quotes": 0,
                "author_replies": 0,
                "total_follows": 0,
                "max_likes": 0,
                "max_likes_text": "",
            },
        )
        m = rec.get("metrics", {})
        b["n"] += 1
        likes = int(m.get("likes") or 0)
        b["total_likes"] += likes
        b["total_quotes"] += int(m.get("quotes") or 0)
        if m.get("author_reply"):
            b["author_replies"] += 1
        b["total_follows"] += int(m.get("follows_attributed") or 0)
        if likes > b["max_likes"]:
            b["max_likes"] = likes
            b["max_likes_text"] = (rec.get("text") or "")[:140]

    summary = {}
    for pattern, b in buckets.items():
        n = b["n"] or 1
        summary[pattern] = {
            "n": b["n"],
            "avg_likes": round(b["total_likes"] / n, 2),
            "avg_quotes": round(b["total_quotes"] / n, 2),
            "author_reply_rate": round(b["author_replies"] / n, 2),
            "follows_attributed": b["total_follows"],
            "follows_per_reply": round(b["total_follows"] / n, 3),
            "best_example_likes": b["max_likes"],
            "best_example_text": b["max_likes_text"],
        }
    return summary


# Need this at bottom to avoid circular-ish urllib.parse import at top
import urllib.parse  # noqa: E402
