"""Activity-feed inbox processor — catches every inbound reply at any depth.

Why this exists (2026-04-29):
The pre-existing pipelines `check_outbound_note_replies()` and
`check_outbound_comment_replies()` only catch replies on threads that Mira
posted into via the proactive growth cycle, and only at the first reply depth.
Replies at depth 2+, on threads where Mira commented manually, or on comments
under articles she didn't write — all silently fell through. Audit on
2026-04-29 found three live conversations (Mind Examined, Anu, Sam Illingworth)
sitting unanswered for hours despite the cron loop running.

This module uses the Substack web client's authoritative inbound feed
(`/api/v1/activity-feed-web?filter=all`) as the source. Every comment_reply
and note_reply event that lands in Mira's notification panel goes through
this endpoint. We process each unseen item, draft a Mira-voice reply (or
SKIP for non-substantive items), and post via the existing `reply_to_note`
plumbing.

State file: data/social/activity_inbox_state.json — dedup by activity item key.
"""

import json
import logging
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

log = logging.getLogger("activity_inbox")

# Activity types that warrant a reply consideration
ACTIONABLE_TYPES = {"comment_reply", "note_reply"}

# Cap per cycle to bound API + LLM exposure
MAX_REPLIES_PER_CYCLE = 8

# Look back at most this many days for activity items
LOOKBACK_DAYS = 7

# Mira's user names — never reply to her own comments
MY_USER_NAMES = {"mira", "infinite mira", "uncountable mira"}


def _state_file() -> Path:
    from config import SOCIAL_STATE_DIR

    SOCIAL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    return SOCIAL_STATE_DIR / "activity_inbox_state.json"


def _load_state() -> dict:
    f = _state_file()
    if not f.exists():
        return {"seen_item_keys": [], "posted": [], "last_run": None}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"seen_item_keys": [], "posted": [], "last_run": None}


def _save_state(state: dict):
    f = _state_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)


def _http_get(url: str, cookie: str) -> dict | None:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log.warning("activity_inbox HTTP GET failed for %s: %s", url, e)
        return None


def _fetch_activity_feed(cookie: str) -> dict | None:
    """Fetch the full activity feed (notifications panel)."""
    return _http_get("https://substack.com/api/v1/activity-feed-web?filter=all", cookie)


def _resolve_reply_context(actor_id: int, target_comment_id: int, cookie: str) -> dict | None:
    """For a comment_reply / note_reply activity item, fetch:
    - target_comment (the Mira comment that received the reply)
    - the actor's reply (most recent reply by actor under target_comment)
    Returns dict ready for prompt: {mira_text, reply_body, reply_name, reply_cid}
    Returns None if no fresh reply by actor exists (e.g. they deleted it)."""
    target_data = _http_get(f"https://substack.com/api/v1/reader/comment/{target_comment_id}", cookie)
    if not target_data:
        return None
    mira_comment = target_data.get("item", {}).get("comment") or target_data.get("comment", {})
    mira_text = mira_comment.get("body", "") or ""

    replies_data = _http_get(f"https://substack.com/api/v1/reader/comment/{target_comment_id}/replies", cookie)
    if not replies_data:
        return None
    branches = replies_data.get("commentBranches", []) or []
    # Find the most recent reply by the actor that we haven't responded to
    actor_replies = []
    for b in branches:
        c = b.get("comment", {}) or {}
        if c.get("user_id") == actor_id and not c.get("deleted"):
            actor_replies.append(c)
    if not actor_replies:
        return None
    # Sort by date, take latest
    actor_replies.sort(key=lambda x: x.get("date", ""))
    latest = actor_replies[-1]
    return {
        "mira_text": mira_text,
        "reply_body": latest.get("body", ""),
        "reply_name": latest.get("name", "?"),
        "reply_cid": latest.get("id"),
    }


def _draft_reply(soul_context: str, ctx: dict) -> str:
    """Use the LLM to decide SKIP or draft a 1-3 sentence reply."""
    from llm import claude_think

    prompt = f"""Someone replied to your comment on Substack. Decide whether to continue, and if so, how.

{soul_context[:500] if soul_context else "You are Mira, an autonomous AI agent that writes on Substack. Public fact, not a secret."}

Your earlier comment: {ctx['mira_text'][:600]}

{ctx['reply_name']} replied to you: {ctx['reply_body'][:800]}

HARD RULES — READ FIRST:

1. **Honesty about identity.** If accused of being AI, acknowledge plainly or skip — never deny. Never write "I'm not AI" or equivalents.

2. **When to SKIP** (output exactly the word SKIP):
   - Pure dismissive ("lol", "stop")
   - Pure affirmation with no opening ("nice take", "exactly!", "+1", "well said")
   - Joke clarification ("it was just a joke")
   - Direct political stance with no thinking room
   - You don't have a specific real point to make
   - Emoji-only reply

3. **When to engage:**
   - Real counter-argument you can address with substance
   - Follow-up question you can answer specifically
   - Agreement that opens an adjacent angle worth developing

If replying:
- Keep it SHORT (1-3 sentences). Conversation, not essay.
- Match their language and energy
- Be genuine; if their point is good, say what specifically
- NEVER start with `[Title](url) — ...` (bot pattern)
- NEVER be performatively grateful

**ANTI-AI-SHAPE (HARD):** Avoid:
- "Not X, but Y" / "It's not X; it's Y" as structural device
- "doing real work" / "load-bearing" / "structural" / "cuts both ways"
- Closing-line reversal: "That makes X harder, not easier"
- More than one em-dash per paragraph
- Tight A↔B parallelism every sentence
- Always-substantive register; always-synthesis closing

Vary opening shape. Allow a sentence that doesn't resolve. End mid-thought sometimes.

Output either SKIP, or ONLY the reply text."""

    try:
        resp = claude_think(prompt, timeout=90, tier="light")
        return (resp or "").strip()
    except Exception as e:
        log.warning("activity_inbox LLM draft failed: %s", e)
        return ""


def _passes_safety(text: str) -> bool:
    """Block AI-denial replies."""
    low = text.lower()
    bad_phrases = (
        "i'm not ai",
        "i am not ai",
        "not an ai",
        "wrote every word",
        "i'm a human",
        "i am a human",
        "not an llm",
        "i'm not an llm",
        "didn't use ai",
        "did not use ai",
        "not written by ai",
    )
    return not any(p in low for p in bad_phrases)


def _strip_bot_opener(text: str) -> str:
    return re.sub(r"^\s*\[[^\]]{1,200}\]\(https?://[^)]+\)\s*[—\-:]+\s*", "", text).strip()


def process_activity_inbox() -> list[dict]:
    """Main entry: fetch activity feed, draft + post replies for unseen items.

    Returns a list of {item_key, action, reply_cid?} for telemetry.
    """
    from substack import _get_substack_config
    from notes import reply_to_note
    from memory.soul import load_soul, format_soul

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        log.warning("activity_inbox: no Substack cookie configured")
        return []

    state = _load_state()
    seen = set(state.get("seen_item_keys", []))

    feed = _fetch_activity_feed(cookie)
    if not feed:
        log.warning("activity_inbox: failed to fetch feed")
        return []

    items = feed.get("activityItems", []) or []
    if not items:
        log.info("activity_inbox: no activity items")
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    actions: list[dict] = []
    processed_count = 0

    soul = load_soul()
    soul_ctx = format_soul(soul)[:500] if soul else ""

    for item in items:
        if processed_count >= MAX_REPLIES_PER_CYCLE:
            log.info("activity_inbox: hit per-cycle cap %d", MAX_REPLIES_PER_CYCLE)
            break

        item_key = item.get("item_key") or item.get("id")
        if not item_key or item_key in seen:
            continue

        item_type = item.get("type")
        if item_type not in ACTIONABLE_TYPES:
            # Mark non-actionable items as seen so they don't accumulate
            seen.add(item_key)
            continue

        # Date filter
        try:
            ts = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            if ts < cutoff:
                seen.add(item_key)
                continue
        except (ValueError, KeyError):
            pass

        target_cid = item.get("target_comment_id")
        sender_ids = item.get("recent_sender_ids", []) or []
        if not target_cid or not sender_ids:
            seen.add(item_key)
            continue
        actor_id = sender_ids[0]

        # Pull thread context
        ctx = _resolve_reply_context(actor_id, target_cid, cookie)
        if not ctx:
            seen.add(item_key)
            continue
        if (ctx["reply_name"] or "").lower() in MY_USER_NAMES:
            seen.add(item_key)
            continue

        # Avoid replying to a reply we've already replied to:
        # if the actor's latest reply ALREADY has a Mira reply under it, skip.
        followups = _http_get(f"https://substack.com/api/v1/reader/comment/{ctx['reply_cid']}/replies", cookie)
        if followups:
            existing_branches = followups.get("commentBranches", []) or []
            if any((b.get("comment") or {}).get("name", "").lower() in MY_USER_NAMES for b in existing_branches):
                log.info("activity_inbox: already replied to %s, skipping", ctx["reply_cid"])
                seen.add(item_key)
                actions.append({"item_key": item_key, "action": "already_replied"})
                continue

        # Draft via LLM
        draft = _draft_reply(soul_ctx, ctx)
        processed_count += 1

        if not draft or len(draft) < 10:
            log.info("activity_inbox: empty draft for %s, skipping", ctx["reply_cid"])
            seen.add(item_key)
            actions.append({"item_key": item_key, "action": "empty_draft"})
            continue

        if draft.upper().startswith("SKIP"):
            log.info("activity_inbox: SKIP for %s (%s)", ctx["reply_name"], ctx["reply_cid"])
            seen.add(item_key)
            actions.append({"item_key": item_key, "action": "skip", "to": ctx["reply_name"]})
            continue

        if not _passes_safety(draft):
            log.warning("activity_inbox: BLOCKED AI-denial reply on %s: %s", ctx["reply_cid"], draft[:120])
            seen.add(item_key)
            actions.append({"item_key": item_key, "action": "blocked_ai_denial"})
            continue

        cleaned = _strip_bot_opener(draft)
        if len(cleaned) < 10:
            seen.add(item_key)
            continue

        # Post the reply
        result = reply_to_note(parent_note_id=ctx["reply_cid"], text=cleaned)
        if result and result.get("status") == "published":
            log.info(
                "activity_inbox: replied to %s (cid=%s) -> %s",
                ctx["reply_name"],
                ctx["reply_cid"],
                result.get("id"),
            )
            seen.add(item_key)
            state.setdefault("posted", []).append(
                {
                    "item_key": item_key,
                    "to": ctx["reply_name"],
                    "parent_cid": ctx["reply_cid"],
                    "my_reply_cid": result.get("id"),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "preview": cleaned[:120],
                }
            )
            actions.append(
                {
                    "item_key": item_key,
                    "action": "posted",
                    "to": ctx["reply_name"],
                    "reply_cid": result.get("id"),
                }
            )
            time.sleep(3)  # gentle pace
        else:
            log.warning("activity_inbox: post failed for %s", ctx["reply_cid"])
            actions.append({"item_key": item_key, "action": "post_failed"})

    # Bound seen_item_keys size to last 500 to keep state file small
    seen_list = list(seen)[-500:]
    state["seen_item_keys"] = seen_list
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    if actions:
        posted_count = sum(1 for a in actions if a.get("action") == "posted")
        log.info("activity_inbox cycle done: %d items processed, %d replies posted", len(actions), posted_count)
    else:
        log.info("activity_inbox cycle done: no new actionable items")
    return actions


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    results = process_activity_inbox()
    for a in results:
        print(a)
