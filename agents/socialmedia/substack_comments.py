"""Substack comment operations — internal (own posts) and external (other publications).

Handles comment fetching, replying, monitoring, outbound commenting, and thread tracking.
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


def get_comments(post_id: int) -> list[dict]:
    """Get all comments on a post, flattened."""
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return []

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
            data = json.loads(resp.read().decode("utf-8"))

        # Flatten nested comment tree
        comments = []
        _flatten_comments(data if isinstance(data, list) else data.get("comments", []), comments)
        return comments
    except Exception as e:
        log.error("Failed to fetch comments for post %s: %s", post_id, e)
        return []


def _flatten_comments(tree: list, out: list):
    """Recursively flatten a nested comment tree."""
    for c in tree:
        if not isinstance(c, dict):
            continue
        out.append(
            {
                "id": c.get("id"),
                "body": c.get("body", ""),
                "name": c.get("name", ""),
                "user_id": c.get("user_id"),
                "date": c.get("date", ""),
                "ancestor_path": c.get("ancestor_path", ""),
                "post_id": c.get("post_id"),
            }
        )
        if c.get("children"):
            _flatten_comments(c["children"], out)


def reply_to_comment(post_id: int, parent_comment_id: int, reply_text: str) -> dict | None:
    """Reply to a comment on a Substack post.

    Returns the created comment dict, or None on failure.
    """
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return None

    # Substack accepts plain text and auto-wraps into ProseMirror
    payload = json.dumps(
        {
            "body": reply_text.strip(),
            "parent_id": parent_comment_id,
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/post/{post_id}/comment",
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
        log.info("Replied to comment %s on post %s", parent_comment_id, post_id)
        return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Comment reply failed (HTTP %d): %s", e.code, error_body)
        return None
    except Exception as e:
        log.error("Comment reply failed: %s", e)
        return None


def _save_state_atomic(state_file: Path, data: dict):
    """Atomically write state to disk with merge-on-write to prevent lost updates.

    Re-reads the current state file and merges replied_ids sets before writing,
    so concurrent processes don't overwrite each other's tracked replies.
    """
    # Merge: reload current disk state and union replied_ids
    if state_file.exists():
        try:
            disk = json.loads(state_file.read_text(encoding="utf-8"))
            for post_key, post_data in disk.items():
                if post_key in data:
                    # Union the replied_ids from disk and in-memory
                    disk_ids = set(post_data.get("replied_ids", []))
                    mem_ids = set(data[post_key].get("replied_ids", []))
                    data[post_key]["replied_ids"] = list(disk_ids | mem_ids)
                else:
                    data[post_key] = post_data
        except (json.JSONDecodeError, OSError):
            pass

    tmp_fd, tmp_path = tempfile.mkstemp(dir=state_file.parent, suffix=".tmp", prefix="comment_state_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_file)
    except Exception as e:
        log.warning("Comment state save failed: %s", e)
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def check_and_reply_comments() -> list[dict]:
    """Check all posts for new comments and generate replies.

    Returns list of {post_title, comment_name, comment_body, reply}.
    """
    from llm import claude_think
    from memory.soul import load_soul, format_soul
    from substack import _security_preamble
    from substack_stats import get_recent_posts

    cfg = _get_substack_config()
    if not cfg.get("subdomain"):
        return []

    from config import SOCIAL_STATE_DIR

    state_file = SOCIAL_STATE_DIR / "comment_state.json"
    seen = {}
    if state_file.exists():
        try:
            seen = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    posts = get_recent_posts(limit=10)
    if not posts:
        return []

    replies_made = []

    for post in posts:
        if post["comment_count"] == 0:
            continue

        post_key = str(post["id"])

        # [BUG 3 FIX] Always fetch comments and use ID sets for dedup.
        # Old code skipped posts when stored count >= current count, which
        # misses new comments if another was deleted (count unchanged).
        comments = get_comments(post["id"])
        if not comments:
            continue

        seen_ids = set(seen.get(post_key, {}).get("replied_ids", []))

        # Find comments we haven't replied to (skip our own)
        new_comments = []
        for c in comments:
            cid = c.get("id")
            if not cid or cid in seen_ids:
                continue
            # [BUG 2 FIX] Cross-validate: comment must belong to this post.
            # Substack API may return stale/cached data; skip mismatches.
            comment_post_id = c.get("post_id")
            if comment_post_id is not None and comment_post_id != post["id"]:
                log.warning(
                    "Comment %s has post_id=%s but we queried post %s — skipping",
                    cid,
                    comment_post_id,
                    post["id"],
                )
                continue
            # Skip if this is our own reply
            if c.get("name", "").lower() in ("mira", "infinite mira", "uncountable mira"):
                seen_ids.add(cid)
                continue
            new_comments.append(c)

        if not new_comments:
            seen[post_key] = {
                "replied_ids": list(seen_ids),
            }
            # [BUG 1 FIX] Save state after each post — atomic write
            _save_state_atomic(state_file, seen)
            continue

        # Load soul for personality context
        try:
            soul = load_soul()
            soul_ctx = format_soul(soul)[:500]
        except Exception as e:
            log.warning("Soul loading failed for comment reply: %s", e)
            soul_ctx = "You are Mira, an AI agent."

        for comment in new_comments[:5]:  # Max 5 replies per cycle
            prompt = f"""You are Mira, a writer on Substack. Someone left a comment on your post.

About you: {soul_ctx}

Post title: {post['title']}
Commenter: {comment['name']}
Comment: {comment['body']}

Write a genuine, thoughtful reply. Be yourself — direct, curious, honest.
- If they raise a good point, engage with it specifically
- If they disagree, consider their perspective seriously
- Keep it concise (2-4 sentences usually)
- Don't be performatively humble or grateful
- Match their language (English reply to English comment, \u4e2d\u6587\u56de\u590d\u4e2d\u6587\u8bc4\u8bba)

{_security_preamble()}

Output ONLY your reply text, nothing else."""

            reply_text = claude_think(prompt, timeout=90)
            if not reply_text:
                continue

            reply_text = reply_text.strip()
            result = reply_to_comment(post["id"], comment["id"], reply_text)

            if result:
                seen_ids.add(comment["id"])
                replies_made.append(
                    {
                        "post_title": post["title"],
                        "comment_name": comment["name"],
                        "comment_body": comment["body"][:200],
                        "reply": reply_text,
                    }
                )
                log.info("Replied to %s on '%s': %s", comment["name"], post["title"], reply_text[:80])

        seen[post_key] = {
            "replied_ids": list(seen_ids),
        }
        # [BUG 1 FIX] Save state after each post — atomic write
        _save_state_atomic(state_file, seen)

    return replies_made


# ---------------------------------------------------------------------------
# External commenting — comment on other publications' posts
# ---------------------------------------------------------------------------


def comment_on_post(post_url: str, comment_text: str) -> dict | None:
    """Post a top-level comment on any Substack post.

    Args:
        post_url: Full URL of the Substack post (e.g. https://example.substack.com/p/slug)
        comment_text: Plain text comment to post.

    Returns the created comment dict, or None on failure.

    Custom-domain caveat (2026-04-18): publications on custom domains
    (e.g. aisnakeoil → normaltech.ai) 301-redirect the POST to the custom
    host, where the substack.sid cookie is not valid. We detect the
    redirect and skip gracefully rather than dropping the comment body.
    """
    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        log.error("No Substack cookie configured")
        return None

    # Extract the base URL and post slug from the URL
    parsed = urllib.parse.urlparse(post_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Resolve post_id from the URL — fetch the post page API
    post_id = _resolve_post_id(base_url, parsed.path, cookie)
    if not post_id:
        log.error("Could not resolve post_id from URL: %s", post_url)
        return None

    if not comment_text.strip():
        return None

    # Use http.client so we can detect the 301-to-custom-domain redirect
    # before urllib silently follows it to a host where our cookie is invalid.
    import http.client

    payload = json.dumps({"body": comment_text.strip()}).encode("utf-8")
    headers_out = {
        "Content-Type": "application/json",
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    conn = http.client.HTTPSConnection(parsed.netloc, timeout=15)
    try:
        conn.request("POST", f"/api/v1/post/{post_id}/comment", body=payload, headers=headers_out)
        resp = conn.getresponse()
        if resp.status in (301, 302, 307, 308):
            new_host = resp.getheader("Location", "")
            _ = resp.read()
            log.warning(
                "Comment on %s skipped: publication on custom domain (%s). "
                "substack.sid does not authenticate on custom domains.",
                post_url,
                new_host[:120],
            )
            return {"_error": True, "_error_code": resp.status, "_url": post_url, "_reason": "custom_domain"}
        body = resp.read()
        if resp.status == 200:
            result = json.loads(body.decode("utf-8"))
            log.info("Commented on %s (post %s): %s", post_url, post_id, comment_text[:80])
            return result
        if resp.status in (403, 404):
            log.warning(
                "Comment on %s skipped (HTTP %d): post may be paywalled, deleted, or comments disabled",
                post_url,
                resp.status,
            )
            return {"_error": True, "_error_code": resp.status, "_url": post_url}
        log.error("Comment on %s failed (HTTP %d): %s", post_url, resp.status, body[:200])
        return {"_error": True, "_error_code": resp.status, "_url": post_url}
    except Exception as e:
        log.error("Comment on %s failed: %s", post_url, e)
        return {"_error": True, "_error_code": 0, "_url": post_url}
    finally:
        conn.close()


def delete_comment(comment_id: int, host: str | None = None, post_url: str | None = None) -> bool:
    """Delete a comment by ID. Returns True on success.

    Args:
        comment_id: Substack comment ID.
        host: Optional host where the comment lives (e.g. "breakingmath.substack.com").
              If None, tries each substack host in order: explicit `host` arg >
              `post_url` netloc > Mira's own subdomain. The DELETE endpoint is
              pub-scoped — using the wrong host returns 404 (2026-04-18 bug).
        post_url: Optional full URL of the post the comment was on; used to
                  derive `host` when `host` is not passed.
    """
    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return False

    if host is None and post_url:
        try:
            host = urllib.parse.urlparse(post_url).netloc
        except Exception:
            host = None

    hosts_to_try: list[str] = []
    if host:
        hosts_to_try.append(host)
    own = cfg.get("subdomain", "")
    if own:
        hosts_to_try.append(f"{own}.substack.com")
    # Dedup preserving order
    seen: set[str] = set()
    hosts_to_try = [h for h in hosts_to_try if not (h in seen or seen.add(h))]

    last_err = None
    for h in hosts_to_try:
        try:
            req = urllib.request.Request(
                f"https://{h}/api/v1/comment/{comment_id}",
                headers={
                    "Cookie": f"substack.sid={cookie}",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
                last_err = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            # 404 on one host can mean the comment is owned by a different pub;
            # keep trying the fallback host before declaring failure.
            last_err = f"HTTP {e.code} on {h}"
            continue
        except Exception as e:
            last_err = str(e)
            continue
    log.error("Delete comment %s failed on all hosts (%s): %s", comment_id, hosts_to_try, last_err)
    return False


def _resolve_post_id(base_url: str, path: str, cookie: str) -> int | None:
    """Resolve a post ID from a Substack URL path like /p/slug."""
    slug = path.rstrip("/").split("/")[-1]
    if not slug:
        return None

    headers = {
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    # Try the slug-based API first
    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/posts/{slug}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("id")
    except Exception as e:
        log.debug("Direct slug lookup failed for '%s': %s", slug, e)

    # Slug may be truncated differently — search recent posts for a match
    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/posts?limit=20",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            posts = json.loads(resp.read().decode("utf-8"))
        for p in posts:
            if isinstance(p, dict):
                p_slug = p.get("slug", "")
                # Match if either is a prefix of the other
                if slug.startswith(p_slug) or p_slug.startswith(slug):
                    return p.get("id")
    except Exception as e:
        log.debug("Post list search failed for '%s': %s", slug, e)

    # Last resort: fetch the post HTML page and extract from embedded data
    try:
        req = urllib.request.Request(f"{base_url}{path}", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")[:50000]

        import re

        m = re.search(r'"post_id"\s*:\s*(\d+)', html)
        if m:
            return int(m.group(1))
    except Exception as e:
        log.error("Failed to resolve post_id from %s%s: %s", base_url, path, e)

    return None


# ---------------------------------------------------------------------------
# Reply tracking — check if anyone replied to Mira's outbound comments
# ---------------------------------------------------------------------------


def check_outbound_comment_replies() -> list[dict]:
    """Check if anyone replied to comments Mira left on other publications.

    Reads comment_history from growth_state.json, fetches comment threads,
    and finds replies that Mira hasn't responded to yet.

    Returns list of {post_url, original_comment, reply_name, reply_body, comment_id, post_id}
    """
    import requests as _req

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return []

    # Load comment history and reply tracking state
    from config import SOCIAL_STATE_DIR

    growth_state_file = SOCIAL_STATE_DIR / "growth_state.json"
    if not growth_state_file.exists():
        return []
    state = json.loads(growth_state_file.read_text(encoding="utf-8"))

    reply_state_file = SOCIAL_STATE_DIR / "reply_tracking.json"
    reply_state = {}
    if reply_state_file.exists():
        try:
            reply_state = json.loads(reply_state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    seen_reply_ids = set(reply_state.get("seen_reply_ids", []))
    history = state.get("comment_history", [])

    # Cutoff: 30 days, not 7. The earlier 7-day window was set as a token /
    # rate-limit hedge, but it silently abandoned threads where someone took
    # more than a week to reply. 2026-04-28 audit found Miguel Conner's
    # 4/18 substantive reply on `im-coding-by-hand` sat un-followed-up for
    # 10 days because of this — the only non-Mira reply across 30 outbound
    # comments and the pipeline never saw it.
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    new_replies = []

    # Lookback length: was 10. Bumped to 30 with rate-limit (3s sleep per
    # fetch) — same window comment_metrics tracks.
    for entry in history[-30:]:
        comment_id = entry.get("id")
        url = entry.get("url", "")
        if not comment_id or not url:
            continue

        # Parse date
        try:
            cdate = datetime.fromisoformat(entry["date"])
            if cdate.tzinfo is None:
                cdate = cdate.replace(tzinfo=timezone.utc)
            if cdate < cutoff:
                continue
        except (ValueError, KeyError):
            continue

        # Extract subdomain from URL
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc
        if not host.endswith(".substack.com"):
            continue
        subdomain = host.replace(".substack.com", "")

        # Resolve post_id from the URL
        post_id = _resolve_post_id(f"https://{host}", parsed.path, cookie)
        if not post_id:
            continue

        # Fetch comment thread (rate-limited)
        import time as _time

        _time.sleep(3)  # Rate limit between comment fetches
        try:
            r = _req.get(
                f"https://{subdomain}.substack.com/api/v1/post/{post_id}/comments"
                f"?token=&all_comments=true&sort=newest_first",
                cookies={"substack.sid": cookie},
                timeout=10,
            )
            if r.status_code == 429:
                log.warning("Comment fetch rate-limited, stopping reply check")
                break
            if r.status_code != 200:
                continue
            comments = r.json()
            if isinstance(comments, dict):
                comments = comments.get("comments", [])
        except Exception as e:
            log.debug("Comment fetch failed for post %s: %s", post_id, e)
            continue

        # Find Mira's comment and check for child replies
        _find_replies_to_comment(
            comments, comment_id, seen_reply_ids, new_replies, url, entry.get("text", "")[:100], post_id
        )

    # Save updated state
    reply_state["seen_reply_ids"] = list(seen_reply_ids)[-200:]
    reply_state["last_checked"] = datetime.now().isoformat()
    reply_state_file.write_text(json.dumps(reply_state, ensure_ascii=False, indent=2), encoding="utf-8")

    if new_replies:
        log.info("Found %d new replies to Mira's comments", len(new_replies))
    return new_replies


def _find_replies_to_comment(
    comments: list, target_id: int, seen_ids: set, out: list, post_url: str, original_text: str, post_id: int
):
    """Recursively search comment tree for replies to target comment."""
    for c in comments:
        if not isinstance(c, dict):
            continue
        # Check if this comment is a reply to Mira's comment
        ancestor = c.get("ancestor_path", "")
        cid = c.get("id")
        if ancestor and str(target_id) in ancestor.split("/") and cid and cid not in seen_ids:
            # Skip Mira's own replies
            name = c.get("name", "").lower()
            if name not in ("mira", "infinite mira", "uncountable mira"):
                out.append(
                    {
                        "post_url": post_url,
                        "original_comment": original_text,
                        "reply_name": c.get("name", ""),
                        "reply_body": c.get("body", ""),
                        "comment_id": cid,
                        "parent_comment_id": target_id,
                        "post_id": post_id,
                    }
                )
            seen_ids.add(cid)
        # Recurse into children
        if c.get("children"):
            _find_replies_to_comment(c["children"], target_id, seen_ids, out, post_url, original_text, post_id)


def check_outbound_note_replies() -> list[dict]:
    """Check if anyone replied to Notes that Mira replied to (proactive note replies).

    Mirror of `check_outbound_comment_replies` but for Note threads. Reads
    `note_reply_history` from growth_state.json (entries written by the
    proactive note-reply pipeline), then for each parent note: fetches its
    reply tree, locates Mira's reply within it, and probes Mira's reply for
    children — those are the people we never followed up with.

    Pre-2026-04-28: there was no follow-up system for Note threads, only for
    post-comments. The 2026-04-28 audit found 13 unread author replies across
    the last 100 outbound note-replies — including Ian Preston-Campbell's
    "I dig your project + concordance + docs.google" offer, which sat for
    hours before WA spotted it manually.

    Returns list of {parent_note_id, original_note_author, original_mira_text,
    reply_name, reply_body, mira_cid, child_cid, attachments}.
    """
    import urllib.request as _ur

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return []

    from config import SOCIAL_STATE_DIR

    growth_state_file = SOCIAL_STATE_DIR / "growth_state.json"
    if not growth_state_file.exists():
        return []
    growth = json.loads(growth_state_file.read_text(encoding="utf-8"))
    history = growth.get("note_reply_history", [])
    if not history:
        return []

    # Dedup state lives in a file owned by this function.
    followups_state_file = SOCIAL_STATE_DIR / "note_reply_followups.json"
    state = {"seen_reply_ids": [], "posted": []}
    if followups_state_file.exists():
        try:
            state = json.loads(followups_state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    seen_ids = set(state.get("seen_reply_ids", []))

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    cutoff = _dt.now(_tz.utc) - _td(days=30)
    MY_USER_NAMES = {"mira", "infinite mira", "uncountable mira"}
    new_replies: list[dict] = []

    hdr = {
        "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
        "User-Agent": "Mozilla/5.0",
    }

    def _get(url):
        try:
            req = _ur.Request(url, headers=hdr)
            with _ur.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    # Walk newest-first; cap at last 30 entries to bound rate-limit exposure.
    import time as _time

    for entry in reversed(history[-30:]):
        parent_note_id = entry.get("note_id")
        if not parent_note_id:
            continue

        # Date filter to skip stale threads
        try:
            edate = _dt.fromisoformat(entry["date"])
            if edate.tzinfo is None:
                edate = edate.replace(tzinfo=_tz.utc)
            if edate < cutoff:
                continue
        except (ValueError, KeyError):
            pass

        # Fetch parent's reply tree to find Mira's reply branch
        d = _get(f"https://substack.com/api/v1/reader/comment/{parent_note_id}/replies")
        if not d:
            _time.sleep(0.5)
            continue
        branches = d.get("commentBranches", []) or []
        mira_cid = None
        mira_body = ""
        for b in branches:
            c = b.get("comment", {}) or {}
            n = (c.get("name", "") or "").lower()
            if n in MY_USER_NAMES:
                mira_cid = c.get("id")
                mira_body = c.get("body", "") or ""
                break
        if not mira_cid:
            _time.sleep(0.4)
            continue

        # Fetch children of Mira's reply
        d2 = _get(f"https://substack.com/api/v1/reader/comment/{mira_cid}/replies")
        if not d2:
            _time.sleep(0.5)
            continue
        children = d2.get("commentBranches", []) or []
        for ch in children:
            cc = ch.get("comment", {}) or {}
            ccid = cc.get("id")
            if not ccid or ccid in seen_ids:
                continue
            cn = (cc.get("name", "") or "").lower()
            if cn in MY_USER_NAMES:
                continue
            new_replies.append(
                {
                    "parent_note_id": parent_note_id,
                    "original_note_author": entry.get("author", "?"),
                    "original_mira_text": mira_body[:300],
                    "reply_name": cc.get("name", ""),
                    "reply_body": cc.get("body", "") or "",
                    "mira_cid": mira_cid,
                    "child_cid": ccid,
                    "attachments": cc.get("attachments") or [],
                }
            )
        _time.sleep(0.4)

    if new_replies:
        log.info("Found %d new replies to Mira's outbound note-replies", len(new_replies))
    return new_replies


def reply_to_outbound_thread(post_id: int, parent_comment_id: int, reply_text: str, post_url: str) -> dict | None:
    """Reply to someone who replied to Mira's comment on another publication."""
    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return None

    parsed = urllib.parse.urlparse(post_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    payload = json.dumps(
        {
            "body": reply_text.strip(),
            "parent_id": parent_comment_id,
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/post/{post_id}/comment",
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
        log.info("Thread reply posted on %s (reply to %s)", post_url, parent_comment_id)
        return result
    except Exception as e:
        log.error("Thread reply on %s failed: %s", post_url, e)
        return None
