"""Per-comment metric tracking for growth-loop learning.

Each outbound comment on another publication creates one record.
A background poll fetches likes (reactions) and replies, detects whether
the post author replied, and attributes new followers who were observed
engaging on the same thread. Records close after MAX_AGE_DAYS.

The goal is to answer: which commenting patterns actually produce
author replies / reader likes / follows, rather than relying on
"this one felt good."

Storage: DATA_DIR/social/comment_metrics.json — dict keyed by
comment_id (str). Not JSONL because polling rewrites records in place.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("publisher.substack")

MAX_AGE_DAYS = 14
MIN_POLL_INTERVAL_MINUTES = 60


def _metrics_file() -> Path:
    from config import SOCIAL_STATE_DIR

    return SOCIAL_STATE_DIR / "comment_metrics.json"


def _load() -> dict:
    f = _metrics_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("comment_metrics.json unreadable — starting fresh")
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


# ---------------------------------------------------------------------------
# Record creation (called from growth.record_comment)
# ---------------------------------------------------------------------------


def record_new_comment(
    comment_id: int,
    post_url: str,
    text: str,
    pattern: str | None = None,
    post_id: int | None = None,
    subscriber_baseline: int | None = None,
) -> None:
    """Create a metric record for a newly posted outbound comment.

    pattern: optional tag, e.g. "costly-signal-redirect". Null means
    the comment wasn't tagged — still tracked, but summarize_by_pattern()
    lumps it into an "untagged" bucket.

    post_id: pre-resolved post id. If None, poll_open_records() will
    resolve it lazily (costs one extra request per stale record).
    """
    if not comment_id:
        return

    data = _load()
    key = str(comment_id)
    if key in data:
        # Idempotent: never overwrite an existing record
        return

    # Resolve subscriber baseline if not provided
    if subscriber_baseline is None:
        try:
            from substack_stats import fetch_subscriber_snapshot

            snap = fetch_subscriber_snapshot()
            subscriber_baseline = int(snap.get("total", 0)) if snap else None
        except Exception as e:
            log.debug("subscriber baseline fetch failed: %s", e)
            subscriber_baseline = None

    data[key] = {
        "comment_id": comment_id,
        "post_id": post_id,
        "post_url": post_url,
        "posted_at": _now_iso(),
        "text": text,
        "pattern": pattern,
        "subscriber_baseline": subscriber_baseline,
        "metrics": {
            "likes": 0,
            "reactor_names": [],
            "author_reply": False,
            "author_reply_text": None,
            "other_replies": 0,
            "reply_user_ids": [],
            "reply_names": [],
            "follows_attributed": 0,
            "attributed_followers": [],
        },
        "last_polled_at": None,
        "poll_count": 0,
        "closed": False,
        "closed_reason": None,
    }
    _save(data)
    log.info("comment_metrics: recorded %s (pattern=%s)", key, pattern or "untagged")


# ---------------------------------------------------------------------------
# Polling (fetch likes + replies + author_reply for open records)
# ---------------------------------------------------------------------------


def _fetch_post_comments(subdomain: str, post_id: int, cookie: str) -> list | None:
    """Fetch the full comment tree for a post on another publication."""
    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/post/{post_id}/comments"
            f"?token=&all_comments=true&sort=newest_first",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        if isinstance(raw, dict):
            return raw.get("comments", [])
        if isinstance(raw, list):
            return raw
        return []
    except urllib.error.HTTPError as e:
        log.debug("comment fetch HTTP %d for %s/%s", e.code, subdomain, post_id)
        return None
    except Exception as e:
        log.debug("comment fetch failed for %s/%s: %s", subdomain, post_id, e)
        return None


def _find_comment_in_tree(tree: list, target_id: int) -> dict | None:
    for c in tree:
        if not isinstance(c, dict):
            continue
        if c.get("id") == target_id:
            return c
        if c.get("children"):
            found = _find_comment_in_tree(c["children"], target_id)
            if found:
                return found
    return None


def _extract_reply_features(my_comment: dict) -> dict:
    """Walk children of my comment and summarize reply features."""
    author_reply = False
    author_reply_text = None
    other_replies = 0
    reply_user_ids: list[int] = []
    reply_names: list[str] = []

    def walk(children: list) -> None:
        nonlocal author_reply, author_reply_text, other_replies
        for c in children:
            if not isinstance(c, dict):
                continue
            meta = c.get("metadata") or {}
            is_author = bool(meta.get("is_author"))
            uid = c.get("user_id")
            name = c.get("name") or ""
            if uid:
                reply_user_ids.append(uid)
            if name:
                reply_names.append(name)
            if is_author and not author_reply:
                author_reply = True
                author_reply_text = (c.get("body") or "")[:400]
            if not is_author:
                other_replies += 1
            # Recurse — deep threads still count
            if c.get("children"):
                walk(c["children"])

    walk(my_comment.get("children") or [])
    return {
        "author_reply": author_reply,
        "author_reply_text": author_reply_text,
        "other_replies": other_replies,
        "reply_user_ids": sorted(set(reply_user_ids)),
        "reply_names": sorted(set(reply_names)),
    }


def _resolve_post_id_lazy(post_url: str, cookie: str) -> int | None:
    from substack_comments import _resolve_post_id

    parsed = urllib.parse.urlparse(post_url)
    return _resolve_post_id(f"{parsed.scheme}://{parsed.netloc}", parsed.path, cookie)


def poll_open_records(limit: int = 30, rate_limit_sleep: float = 3.0) -> dict:
    """Update metrics for open records.

    - Skip records younger than MIN_POLL_INTERVAL_MINUTES (unless never polled)
    - Close records older than MAX_AGE_DAYS
    - Sleep between fetches to avoid 429s

    Returns counters: polled, updated, closed, errored.
    """
    from substack import _get_substack_config

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        log.warning("comment_metrics.poll: no cookie configured")
        return {"polled": 0, "updated": 0, "closed": 0, "errored": 0}

    data = _load()
    now = datetime.now(timezone.utc)
    counters = {"polled": 0, "updated": 0, "closed": 0, "errored": 0}

    # Sort by least-recently-polled so we rotate coverage
    def sort_key(item: tuple[str, dict]) -> datetime:
        rec = item[1]
        last = rec.get("last_polled_at")
        if not last:
            return datetime.min.replace(tzinfo=timezone.utc)
        dt = _parse_iso(last)
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    open_items = sorted(
        [(k, v) for k, v in data.items() if not v.get("closed")],
        key=sort_key,
    )

    for key, rec in open_items[:limit]:
        posted = _parse_iso(rec.get("posted_at", ""))
        if posted and (now - posted) > timedelta(days=MAX_AGE_DAYS):
            rec["closed"] = True
            rec["closed_reason"] = "max_age_reached"
            counters["closed"] += 1
            continue

        last_polled = _parse_iso(rec.get("last_polled_at") or "")
        if last_polled and (now - last_polled) < timedelta(minutes=MIN_POLL_INTERVAL_MINUTES):
            continue

        post_url = rec.get("post_url", "")
        parsed = urllib.parse.urlparse(post_url)
        host = parsed.netloc
        if not host.endswith(".substack.com"):
            rec["closed"] = True
            rec["closed_reason"] = "non_substack_host"
            counters["closed"] += 1
            continue
        subdomain = host.replace(".substack.com", "")

        post_id = rec.get("post_id")
        if not post_id:
            post_id = _resolve_post_id_lazy(post_url, cookie)
            if post_id:
                rec["post_id"] = post_id
            else:
                counters["errored"] += 1
                continue

        tree = _fetch_post_comments(subdomain, post_id, cookie)
        counters["polled"] += 1
        time.sleep(rate_limit_sleep)
        if tree is None:
            counters["errored"] += 1
            continue

        mine = _find_comment_in_tree(tree, rec["comment_id"])
        if mine is None:
            # Could be deleted, moderated, or API quirk — don't close yet,
            # but note it
            rec["last_polled_at"] = _now_iso()
            rec["poll_count"] = rec.get("poll_count", 0) + 1
            continue

        old_metrics = dict(rec["metrics"])
        new_likes = int(mine.get("reaction_count") or 0)
        rec["metrics"]["likes"] = new_likes
        rec["metrics"]["reactor_names"] = list(mine.get("reactor_names") or [])

        reply_features = _extract_reply_features(mine)
        for k, v in reply_features.items():
            rec["metrics"][k] = v

        rec["last_polled_at"] = _now_iso()
        rec["poll_count"] = rec.get("poll_count", 0) + 1

        if rec["metrics"] != old_metrics:
            counters["updated"] += 1

    _save(data)
    log.info(
        "comment_metrics.poll: polled=%d updated=%d closed=%d errored=%d",
        counters["polled"],
        counters["updated"],
        counters["closed"],
        counters["errored"],
    )
    return counters


# ---------------------------------------------------------------------------
# Follow attribution — cross-reference new subscribers with engaged threads
# ---------------------------------------------------------------------------


def attribute_follows(lookback_days: int = 14) -> dict:
    """For each new subscriber in the lookback window, see if they appear
    in any comment thread Mira engaged on within that window. Attribute
    to the comment they engaged with whose posting time is closest to
    (but before) their signup.

    Uses reactor_names and reply_names. Matches are case-insensitive
    exact matches on name. Name collisions are unavoidable without
    user_id on reactors — accept the noise and label attribution as
    probabilistic.

    Returns summary counters.
    """
    try:
        from substack_stats import fetch_subscriber_snapshot
    except Exception as e:
        log.warning("attribute_follows: stats module unavailable: %s", e)
        return {}

    snap = fetch_subscriber_snapshot()
    subscribers = snap.get("subscribers") or [] if snap else []
    if not subscribers:
        return {"attributed": 0, "new_followers_in_window": 0}

    data = _load()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=lookback_days)

    # Index new subscribers by lowercase name
    recent_subs: list[dict] = []
    for s in subscribers:
        signup = _parse_iso(s.get("signup_at") or "")
        if not signup or signup < window_start:
            continue
        if not s.get("name"):
            continue
        recent_subs.append({**s, "_signup_dt": signup, "_name_lc": s["name"].lower()})

    if not recent_subs:
        return {"attributed": 0, "new_followers_in_window": 0}

    # Build already-attributed set across all records so we don't double-count
    already: set[int] = set()
    for rec in data.values():
        for fid in rec.get("metrics", {}).get("attributed_followers", []):
            if isinstance(fid, dict) and fid.get("user_id"):
                already.add(int(fid["user_id"]))

    attributed_count = 0

    for sub in recent_subs:
        if sub.get("user_id") and int(sub["user_id"]) in already:
            continue

        # Candidate records: posted before signup, within lookback window
        candidates: list[tuple[datetime, dict]] = []
        for rec in data.values():
            posted = _parse_iso(rec.get("posted_at", ""))
            if not posted or posted > sub["_signup_dt"] or posted < window_start:
                continue
            names_lc = {n.lower() for n in (rec["metrics"].get("reactor_names") or [])}
            names_lc |= {n.lower() for n in (rec["metrics"].get("reply_names") or [])}
            if sub["_name_lc"] in names_lc:
                candidates.append((posted, rec))

        if not candidates:
            continue

        # Attribute to the most recent matching comment before signup
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, winner = candidates[0]
        winner["metrics"]["attributed_followers"].append(
            {
                "user_id": sub.get("user_id"),
                "name": sub.get("name"),
                "signup_at": sub.get("signup_at"),
                "attribution": "name_match",
            }
        )
        winner["metrics"]["follows_attributed"] = len(winner["metrics"]["attributed_followers"])
        attributed_count += 1
        log.info(
            "attribute_follows: '%s' -> comment %s (%s)",
            sub.get("name"),
            winner.get("comment_id"),
            winner.get("pattern") or "untagged",
        )

    _save(data)
    return {
        "attributed": attributed_count,
        "new_followers_in_window": len(recent_subs),
    }


# ---------------------------------------------------------------------------
# Aggregation / readout
# ---------------------------------------------------------------------------


def summarize_by_pattern(min_age_hours: float = 24.0) -> dict:
    """Aggregate metrics by pattern tag.

    Only includes records older than min_age_hours — fresh comments
    haven't had time to accrue signal. Returns dict keyed by pattern.
    """
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
            "author_reply_rate": round(b["author_replies"] / n, 2),
            "follows_attributed": b["total_follows"],
            "follows_per_comment": round(b["total_follows"] / n, 3),
            "best_example_likes": b["max_likes"],
            "best_example_text": b["max_likes_text"],
        }
    return summary


def format_summary(summary: dict) -> str:
    """Human-readable summary for logs / notes inbox / Mira reports."""
    if not summary:
        return "comment_metrics: no records old enough to summarize yet."
    lines = ["comment_metrics — per-pattern readout:"]
    rows = sorted(summary.items(), key=lambda kv: (-kv[1]["author_reply_rate"], -kv[1]["avg_likes"]))
    for pattern, s in rows:
        lines.append(
            f"  [{pattern}] n={s['n']} avg_likes={s['avg_likes']} "
            f"author_reply={int(s['author_reply_rate']*100)}% "
            f"follows={s['follows_attributed']} ({s['follows_per_comment']}/comment)"
        )
        if s["best_example_text"]:
            lines.append(f"    best ({s['best_example_likes']} likes): {s['best_example_text']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backfill from existing comment_history (one-shot migration)
# ---------------------------------------------------------------------------


def backfill_from_history(limit: int | None = None) -> int:
    """Seed comment_metrics from growth_state.comment_history for comments
    that don't already have a metric record. pattern=None for all backfilled
    records. Useful on first deploy so we immediately have something to poll.
    """
    from config import SOCIAL_STATE_DIR

    gs = SOCIAL_STATE_DIR / "growth_state.json"
    if not gs.exists():
        return 0
    state = json.loads(gs.read_text(encoding="utf-8"))
    history = state.get("comment_history", [])
    data = _load()
    added = 0
    for entry in history[-limit:] if limit else history:
        cid = entry.get("id")
        if not cid or str(cid) in data:
            continue
        data[str(cid)] = {
            "comment_id": cid,
            "post_id": None,
            "post_url": entry.get("url", ""),
            "posted_at": entry.get("date") or _now_iso(),
            "text": entry.get("text", ""),
            "pattern": None,
            "subscriber_baseline": None,
            "metrics": {
                "likes": 0,
                "reactor_names": [],
                "author_reply": False,
                "author_reply_text": None,
                "other_replies": 0,
                "reply_user_ids": [],
                "reply_names": [],
                "follows_attributed": 0,
                "attributed_followers": [],
            },
            "last_polled_at": None,
            "poll_count": 0,
            "closed": False,
            "closed_reason": None,
            "backfilled": True,
        }
        added += 1
    _save(data)
    log.info("comment_metrics.backfill: added %d records", added)
    return added
