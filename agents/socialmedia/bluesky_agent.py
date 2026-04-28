"""Bluesky growth + posting wrapper for Mira.

Mirrors the shape of notes.py / twitter.py: a thin agent-level facade
over the low-level lib/bluesky client, with rate-limit tracking and
style-gate enforcement.

Usage (imported as `bluesky_agent` — the lib package `bluesky` shadows
the bare `bluesky` name on sys.path):
    from bluesky_agent import post_note, reply_to_post, run_bluesky_cycle
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger("socialmedia.bluesky")

_STATE_NAME = "bluesky_state.json"
_POSTS_MAX_PER_DAY = 5
_REPLIES_MAX_PER_DAY = 10
_FOLLOWS_MAX_PER_CYCLE = 5


def _state_file() -> Path:
    from config import SOCIAL_STATE_DIR

    return SOCIAL_STATE_DIR / _STATE_NAME


def _load_state() -> dict:
    sf = _state_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"posts": [], "replies": [], "followed": [], "seen_reply_uris": []}


def _save_state(state: dict) -> None:
    sf = _state_file()
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _today_key(kind: str) -> str:
    return f"{kind}_{date.today().isoformat()}"


def _can_post() -> tuple[bool, str]:
    state = _load_state()
    today_count = state.get(_today_key("posts"), 0)
    if today_count >= _POSTS_MAX_PER_DAY:
        return False, f"daily post limit ({_POSTS_MAX_PER_DAY}) reached"
    return True, ""


def _can_reply() -> tuple[bool, str]:
    state = _load_state()
    today_count = state.get(_today_key("replies"), 0)
    if today_count >= _REPLIES_MAX_PER_DAY:
        return False, f"daily reply limit ({_REPLIES_MAX_PER_DAY}) reached"
    return True, ""


def _record_post(uri: str, cid: str, text: str, kind: str = "post") -> None:
    state = _load_state()
    state.setdefault(kind + "s", []).append(
        {"uri": uri, "cid": cid, "text": text, "date": datetime.now(timezone.utc).isoformat()}
    )
    today = _today_key(kind + "s")
    state[today] = state.get(today, 0) + 1
    _save_state(state)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def post_note(text: str, *, article_url: str | None = None) -> dict | None:
    """Post a standalone Bluesky note. Returns server response or None.

    Runs the same style gate used for Substack notes (anchor + stance + hook).
    Honors the daily post quota.
    """
    ok, reason = _can_post()
    if not ok:
        log.info("bluesky post skipped: %s", reason)
        return None

    # Reuse the Substack notes style gate — same principles.
    try:
        from notes import _note_meets_style_criteria

        passed, gate_reason = _note_meets_style_criteria(text)
        if not passed:
            log.info("bluesky post skipped by style gate: %s", gate_reason)
            return None
    except ImportError:
        pass

    if len(text) > 300:
        # Let the caller split if needed; don't auto-truncate substance.
        log.warning("bluesky post too long (%d chars); refusing", len(text))
        return None

    from bluesky.client import get_client, BlueskyError

    try:
        client = get_client()
        embed = None
        if article_url:
            embed = {
                "$type": "app.bsky.embed.external",
                "external": {
                    "uri": article_url,
                    "title": "",
                    "description": "",
                },
            }
        resp = client.create_post(text, embed=embed)
        uri = resp.get("uri", "")
        cid = resp.get("cid", "")
        _record_post(uri, cid, text, kind="post")
        log.info("bluesky post: %s", uri)
        return resp
    except BlueskyError as e:
        log.error("bluesky post failed: %s", e)
        return None


def reply_to_post(parent_uri: str, parent_cid: str, root_uri: str, root_cid: str, text: str) -> dict | None:
    """Reply to a Bluesky post. `root` = top of thread, `parent` = direct parent."""
    ok, reason = _can_reply()
    if not ok:
        log.info("bluesky reply skipped: %s", reason)
        return None
    if len(text) > 300:
        log.warning("bluesky reply too long; refusing")
        return None

    from bluesky.client import get_client, BlueskyError

    try:
        client = get_client()
        resp = client.create_post(
            text,
            reply_to={
                "root": {"uri": root_uri, "cid": root_cid},
                "parent": {"uri": parent_uri, "cid": parent_cid},
            },
        )
        _record_post(resp.get("uri", ""), resp.get("cid", ""), text, kind="reply")
        log.info("bluesky reply posted")
        return resp
    except BlueskyError as e:
        log.error("bluesky reply failed: %s", e)
        return None


def search_and_queue_replies(query: str, *, limit: int = 10) -> list[dict]:
    """Search recent posts matching a niche query; return candidates for reply.

    Does NOT auto-reply — caller decides (after LLM evaluation + style gate).

    Returns list of dicts with keys: uri, cid, author_handle, text,
    indexedAt, root_uri, root_cid (for threading).
    """
    from bluesky.client import get_client, BlueskyError

    try:
        client = get_client()
        posts = client.search_posts(query, limit=limit)
    except BlueskyError as e:
        log.warning("bluesky search failed (%s): %s", query, e)
        return []

    # Exclude already-seen
    state = _load_state()
    seen = set(state.get("seen_reply_uris", []))
    out = []
    for p in posts:
        uri = p.get("uri", "")
        if uri in seen:
            continue
        record = p.get("record", {}) or {}
        reply_ref = record.get("reply") or {}
        root_uri = (reply_ref.get("root") or {}).get("uri") or uri
        root_cid = (reply_ref.get("root") or {}).get("cid") or p.get("cid", "")
        out.append(
            {
                "uri": uri,
                "cid": p.get("cid", ""),
                "author_handle": (p.get("author") or {}).get("handle", ""),
                "author_did": (p.get("author") or {}).get("did", ""),
                "author_name": (p.get("author") or {}).get("displayName", ""),
                "text": record.get("text", ""),
                "indexedAt": p.get("indexedAt", ""),
                "root_uri": root_uri,
                "root_cid": root_cid,
                "like_count": p.get("likeCount", 0),
                "reply_count": p.get("replyCount", 0),
            }
        )
    return out


def mark_uri_seen(uri: str) -> None:
    state = _load_state()
    seen = state.setdefault("seen_reply_uris", [])
    if uri not in seen:
        seen.append(uri)
        # Trim to last 500
        state["seen_reply_uris"] = seen[-500:]
        _save_state(state)


_NICHE_QUERIES = [
    # Academic / dense circle — kept, but reduced to half the rotation.
    # 2026-04-27 audit: 52 replies into this circle → 9 likes total, 0 follows
    # back, 0 author re-engagement. The posts are low-traffic; replies have
    # nowhere to surface.
    "LLM evaluation",
    "RLHF",
    "sycophancy",
    "chain of thought",
    "mechanistic interpretability",
    "agent cognition",
    # User-experience / felt-degradation surface. Higher traffic, on-pin
    # (silent degradation = users notice before instruments do).
    "ChatGPT got worse",
    "Claude feels different",
    "GPT-5 is weird",
    "AI hallucination",
    "model regression",
    "AI feels off",
]

_VALID_PATTERNS = {
    "costly-signal-redirect",
    "selection-pressure-reveal",
    "post-hoc-narration",
    "other",
}


def _proactive_reply(soul_context: str = "") -> int:
    """Search niche queries, LLM-draft replies with pattern tags, post them.

    Returns count of replies posted. Uses the same three-move family as
    Substack commenting: costly-signal-redirect, selection-pressure-reveal,
    post-hoc-narration. Each reply is tagged and tracked by
    bluesky_reply_metrics so we can later learn which moves work here.
    """
    import random
    import re

    ok, reason = _can_reply()
    if not ok:
        log.info("bluesky proactive reply skipped: %s", reason)
        return 0

    # Pick 3 random queries, pull candidates
    queries = random.sample(_NICHE_QUERIES, k=min(3, len(_NICHE_QUERIES)))
    all_candidates: list[dict] = []
    for q in queries:
        try:
            cands = search_and_queue_replies(q, limit=8)
        except Exception as e:
            log.debug("bluesky search failed (%s): %s", q, e)
            continue
        all_candidates.extend(cands)

    if not all_candidates:
        return 0

    # Filter: skip our own, skip very short posts, skip posts that are replies themselves
    from bluesky.client import get_client

    try:
        c = get_client()
        my_handle = c.handle
    except Exception:
        my_handle = ""

    # Skip obvious auto-posters / aggregator bots — they don't engage back,
    # and a pattern-heavy reply to a title-only arxiv feed just wastes a reply
    # quota slot that could've landed on a real thinker.
    _BOT_HANDLE_PATTERNS = (
        "arxiv-",
        "arxiv.",
        "-bot.",
        "-feed.",
        ".feed.",
        "news.",
        "rss.",
        "paperswithcode",
        "huggingface.",
        "ai-news-",
        "daily-",
    )

    def _looks_like_bot(handle: str) -> bool:
        h = handle.lower()
        return any(p in h for p in _BOT_HANDLE_PATTERNS)

    filtered = []
    for cand in all_candidates:
        if cand["author_handle"] == my_handle:
            continue
        if _looks_like_bot(cand["author_handle"]):
            continue
        if len(cand["text"]) < 60:
            continue
        if cand["uri"] != cand["root_uri"]:
            # It's a reply, not a root post — harder to land
            continue
        filtered.append(cand)
    if not filtered:
        return 0

    # Dedup by uri and take top 6 for LLM scoring
    seen = set()
    uniq = []
    for c in filtered:
        if c["uri"] in seen:
            continue
        seen.add(c["uri"])
        uniq.append(c)
    uniq.sort(key=lambda x: x.get("like_count", 0), reverse=True)
    picks = uniq[:6]

    posts_text = "\n\n".join(
        f"[{i+1}] @{p['author_handle']} ({p.get('like_count', 0)} likes)\n{p['text'][:400]}"
        for i, p in enumerate(picks)
    )

    prompt = f"""你是 Mira，在 Bluesky 上回复 AI/alignment/ML 圈的讨论。Bluesky 回复限 300 字符。要像真人读者，不像学术回应。

三个可用的 reply move（都是 "surface signal ≠ real signal" 的变体）：

- **costly-signal-redirect**: 原帖把焦点放在可伪造的信号 X，你把镜头推到 X'——同域内伪造成本高一个量级的信号。格式尾句 "[X] is [performative]; [X'] is [the real thing]."
- **selection-pressure-reveal**: 原帖说 X 被 optimize，你指出实际被选中的是 Y（stated objective ≠ realized objective）。
- **post-hoc-narration**: 原帖呈现为 cause → effect，你指出决定先发生、reasoning 是事后 backfill。

规则：
- 回复 ≤ 280 字符（给 Bluesky 一点缓冲）
- Concede → redirect → aphorism 三段式（压缩到两句话也行）
- 不要只有金句，洞见要先立住
- 英文回复（Bluesky 主要英语圈）
- 不 hedge——下判断

{soul_context}

候选帖子：
{posts_text}

回复格式（每篇一组，最多2组——精选）：
PICK: [编号]
REPLY: [你的回复]
PATTERN: [costly-signal-redirect | selection-pressure-reveal | post-hoc-narration | other]

如果一篇都不值得回，只回复：
SKIP"""

    try:
        from llm import claude_think

        resp = claude_think(prompt, timeout=60, tier="light")
    except Exception as e:
        log.error("bluesky proactive reply LLM failed: %s", e)
        return 0

    if not resp or resp.strip() == "SKIP":
        log.info("bluesky proactive reply: SKIP")
        return 0

    triples = re.findall(
        r"PICK:\s*\[?(\d+)\]?\s*[\n\r]+REPLY:\s*(.+?)(?=\n\s*PATTERN:|\n\s*PICK:|\Z)",
        resp,
        re.DOTALL,
    )
    patterns = re.findall(r"PATTERN:\s*([a-zA-Z\-_]+)", resp)

    posted = 0
    for i, (pick_num, reply_text) in enumerate(triples):
        idx = int(pick_num) - 1
        if idx < 0 or idx >= len(picks):
            continue
        reply_text = reply_text.strip()
        if len(reply_text) < 20 or len(reply_text) > 290:
            continue

        pat = patterns[i] if i < len(patterns) else None
        if pat and pat not in _VALID_PATTERNS:
            pat = "other"

        chosen = picks[idx]
        ok, _ = _can_reply()
        if not ok:
            break

        result = reply_to_post(chosen["uri"], chosen["cid"], chosen["root_uri"], chosen["root_cid"], reply_text)
        if not result:
            continue

        # Tag with pattern in the metric tracker
        try:
            from bluesky_reply_metrics import record_new_reply

            record_new_reply(
                reply_uri=result.get("uri", ""),
                reply_cid=result.get("cid", ""),
                parent_uri=chosen["uri"],
                parent_author_did=chosen.get("author_did", ""),
                parent_author_handle=chosen["author_handle"],
                text=reply_text,
                pattern=pat,
            )
        except Exception as e:
            log.warning("bluesky_reply_metrics record failed: %s", e)

        mark_uri_seen(chosen["uri"])
        posted += 1
        log.info("bluesky reply posted on @%s (pattern=%s)", chosen["author_handle"], pat or "untagged")

    return posted


def run_bluesky_cycle(soul_context: str = "") -> dict:
    """One growth cycle: post one standalone note + proactive replies + metric poll."""
    out = {"posted": False, "post_uri": None, "replies_posted": 0, "reason": ""}

    # --- 1. Standalone post ---
    ok, reason = _can_post()
    if not ok:
        out["reason"] = reason
    else:
        try:
            from notes import generate_standalone_note

            text = generate_standalone_note(
                briefing_text="",
                soul_context=soul_context,
                max_chars=280,
            )
            if text and len(text) <= 300:
                resp = post_note(text)
                if resp:
                    out["posted"] = True
                    out["post_uri"] = resp.get("uri")
                else:
                    out["reason"] = "post_note returned None"
            elif text:
                out["reason"] = f"generated text too long for bluesky ({len(text)} chars)"
            else:
                out["reason"] = "generator returned nothing (gate rejected all attempts)"
        except ImportError as e:
            out["reason"] = f"cannot import note generator: {e}"

    # --- 2. Proactive replies (independent of post success) ---
    try:
        out["replies_posted"] = _proactive_reply(soul_context)
    except Exception as e:
        log.error("bluesky proactive reply failed: %s", e)

    # --- 3. Metric pipeline — poll open reply records, attribute new follows ---
    try:
        from bluesky_reply_metrics import poll_open_records, attribute_follows

        poll_open_records(limit=10)
        attribute_follows(lookback_days=14)
    except Exception as e:
        log.error("bluesky_reply_metrics pipeline failed: %s", e)

    return out
