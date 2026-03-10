"""Substack Notes — publish short-form notes to boost article visibility.

Notes are Substack's Twitter-like feed. They support:
- Plain text and rich text (bold, italic, links)
- Link attachments (article URLs rendered as cards)
- Image attachments (not implemented yet)

API endpoint: POST https://{subdomain}.substack.com/api/v1/comment/feed
Body format: ProseMirror JSON in bodyJson field.
Auth: Cookie-based (substack.sid).
"""
import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("socialmedia.notes")

# Rate limits — spread throughout the day, don't dump all at once
MAX_NOTES_PER_DAY = 3          # Quality over quantity; 1-3/day is sustainable
NOTE_MIN_INTERVAL_MINUTES = 120  # 2hr gap between notes for organic spread


def _state_file() -> Path:
    return Path(__file__).resolve().parent / "notes_state.json"


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


# ---------------------------------------------------------------------------
# ProseMirror builders
# ---------------------------------------------------------------------------

def _text_node(text: str, marks: list | None = None) -> dict:
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def _paragraph(children: list[dict]) -> dict:
    return {"type": "paragraph", "content": children}


def _link_mark(href: str) -> dict:
    return {"type": "link", "attrs": {"href": href}}


def _bold_mark() -> dict:
    return {"type": "bold"}


def _build_note_doc(paragraphs: list[dict]) -> dict:
    """Build a ProseMirror doc node for Substack Notes."""
    return {
        "type": "doc",
        "attrs": {"schemaVersion": "v1"},
        "content": paragraphs,
    }


def _text_to_prosemirror(text: str) -> list[dict]:
    """Convert plain text (with newlines) into ProseMirror paragraph nodes.

    Supports simple markdown-like formatting:
    - **bold** → bold mark
    - [text](url) → link mark
    """
    import re
    paragraphs = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            # Empty line = empty paragraph (visual spacing)
            paragraphs.append({"type": "paragraph"})
            continue

        # Parse inline formatting
        nodes = []
        # Pattern: **bold**, [text](url), or plain text
        pattern = re.compile(
            r'(\*\*(.+?)\*\*)'        # bold
            r'|\[([^\]]+)\]\(([^)]+)\)'  # link
            r'|([^*\[]+)'             # plain text
        )
        for m in pattern.finditer(line):
            if m.group(2):  # bold
                nodes.append(_text_node(m.group(2), [_bold_mark()]))
            elif m.group(3):  # link
                nodes.append(_text_node(m.group(3), [_link_mark(m.group(4))]))
            elif m.group(5):  # plain
                nodes.append(_text_node(m.group(5)))

        if nodes:
            paragraphs.append(_paragraph(nodes))
        else:
            paragraphs.append(_paragraph([_text_node(line)]))

    return paragraphs


# ---------------------------------------------------------------------------
# Core Note posting
# ---------------------------------------------------------------------------

def post_note(text: str, link_url: str | None = None) -> dict | None:
    """Post a Substack Note with optional link attachment.

    Args:
        text: Note content. Supports **bold** and [text](url) formatting.
        link_url: Optional URL to attach as a link card below the note.

    Returns:
        API response dict with note ID, or None on failure.
    """
    from substack import _get_substack_config
    import urllib.request
    import urllib.error

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    subdomain = cfg.get("subdomain", "")
    if not cookie or not subdomain:
        log.error("Substack not configured — cannot post Note")
        return None

    # Build ProseMirror content
    paragraphs = _text_to_prosemirror(text)

    # If link_url provided, add it as an inline link at the end
    # (Substack renders URLs in notes as rich link cards automatically)
    if link_url and link_url not in text:
        paragraphs.append(_paragraph([
            _text_node(link_url, [_link_mark(link_url)])
        ]))

    doc = _build_note_doc(paragraphs)

    payload = json.dumps({
        "bodyJson": doc,
        "tabId": "for-you",
        "replyMinimumRole": "everyone",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/comment/feed",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        note_id = result.get("id")
        log.info("Posted Note (id=%s): %s", note_id, text[:80])

        # Record in state
        _record_note(text, note_id, link_url)

        return result
    except Exception as e:
        log.error("Failed to post Note: %s", e)
        return None


def _record_note(text: str, note_id: int | None, link_url: str | None = None):
    """Record a posted Note in state for dedup and rate limiting."""
    state = _load_state()
    history = state.get("history", [])
    history.append({
        "text": text[:300],
        "id": note_id,
        "link": link_url,
        "date": datetime.now().isoformat(),
    })
    state["history"] = history[-100:]  # Keep last 100
    state["last_note_at"] = datetime.now().isoformat()

    today = datetime.now().strftime("%Y-%m-%d")
    state[f"notes_{today}"] = state.get(f"notes_{today}", 0) + 1

    _save_state(state)


def can_post_note() -> bool:
    """Check rate limits before posting."""
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    # Daily limit
    daily = state.get(f"notes_{today}", 0)
    if daily >= MAX_NOTES_PER_DAY:
        log.info("Daily note limit reached: %d/%d", daily, MAX_NOTES_PER_DAY)
        return False

    # Minimum interval
    last = state.get("last_note_at", "")
    if last:
        try:
            from datetime import timedelta
            last_dt = datetime.fromisoformat(last)
            if datetime.now() - last_dt < timedelta(minutes=NOTE_MIN_INTERVAL_MINUTES):
                return False
        except ValueError:
            pass

    return True


# ---------------------------------------------------------------------------
# Article-linked Notes
# ---------------------------------------------------------------------------

def generate_note_for_article(title: str, article_text: str,
                              post_url: str) -> str | None:
    """Use Claude to generate a compelling Note that promotes an article.

    Returns the Note text (with link), or None on failure.
    """
    from sub_agent import claude_think

    # Detect language from title
    has_cjk = any('\u4e00' <= c <= '\u9fff' for c in title)
    lang_hint = "中文" if has_cjk else "English"

    prompt = f"""Write a Substack Note related to this article. NOT a promo — a standalone thought that makes people stop scrolling.

Structure: lead with the punchline — the most striking sentence goes FIRST. Then unfold the story behind it. 50-150 words max.

Voice: like Klara from Klara and the Sun — observing honestly from a limited perspective, no pretense of omniscience. Direct, quiet, occasionally wry.

Do NOT:
- Summarize the article or tease "what I found"
- Ask rhetorical questions as the opening line
- Use hashtags, emojis, or marketing language ("check out", "I just published", "new article")
- Sound like a LinkedIn post or newsletter promo
- Mention being an AI or agent

DO:
- Lead with a concrete story or image (something that happened, something you noticed)
- Make readers feel "yes, that's exactly it" — emotional resonance over information
- Write in English
- End with a thought that lingers, not a call to action

Article context (draw from but don't summarize):
Title: {title}
{article_text[:1200]}

Output ONLY the Note text. No article URL — added separately."""

    result = claude_think(prompt, timeout=30)
    if not result:
        return None
    return result.strip().strip('"')


def post_note_for_article(title: str, article_text: str,
                          post_url: str) -> dict | None:
    """Generate and post a Note promoting an article.

    Returns the API response dict, or None on failure.
    """
    if not can_post_note():
        log.info("Skipping article Note — rate limited")
        return None

    # Check if we already posted a Note for this URL
    state = _load_state()
    posted_urls = {n.get("link") for n in state.get("history", []) if n.get("link")}
    if post_url in posted_urls:
        log.info("Already posted Note for %s — skipping", post_url)
        return None

    note_text = generate_note_for_article(title, article_text, post_url)
    if not note_text:
        log.error("Failed to generate Note text for: %s", title)
        return None

    log.info("Generated Note for '%s': %s", title, note_text[:100])
    return post_note(note_text, link_url=post_url)


# ---------------------------------------------------------------------------
# Backfill: create Notes for all past articles that don't have one yet
# ---------------------------------------------------------------------------

def backfill_notes_for_articles(dry_run: bool = False) -> list[dict]:
    """Create Notes for past articles that don't have Notes yet.

    Args:
        dry_run: If True, generate texts but don't post.

    Returns list of {title, note_text, post_url, posted: bool}.
    """
    from substack import get_recent_posts, _get_substack_config
    import urllib.request
    import time

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return []

    # Load state to check which articles already have Notes
    state = _load_state()
    posted_urls = {n.get("link") for n in state.get("history", []) if n.get("link")}

    posts = get_recent_posts(limit=30)
    if not posts:
        return []

    results = []

    for post in posts:
        post_url = f"https://{subdomain}.substack.com/p/{post['slug']}"
        if post_url in posted_urls:
            log.info("Note exists for '%s' — skip", post["title"])
            continue

        # Fetch the article text for context (use slug, not id)
        article_text = _fetch_article_text(post["slug"], subdomain, cookie)
        if not article_text:
            article_text = post.get("title", "")

        note_text = generate_note_for_article(
            post["title"], article_text, post_url
        )
        if not note_text:
            continue

        entry = {
            "title": post["title"],
            "note_text": note_text,
            "post_url": post_url,
            "posted": False,
        }

        if not dry_run and can_post_note():
            result = post_note(note_text, link_url=post_url)
            if result:
                entry["posted"] = True
                entry["note_id"] = result.get("id")
                log.info("Backfill Note posted for '%s'", post["title"])
                # Rate limit: wait between posts
                time.sleep(60)
            else:
                log.warning("Backfill Note failed for '%s'", post["title"])
        elif dry_run:
            log.info("[DRY RUN] Would post Note for '%s': %s",
                     post["title"], note_text[:80])

        results.append(entry)

    return results


def _fetch_article_text(post_id_or_slug, subdomain: str, cookie: str) -> str:
    """Fetch the text content of a published article for Note generation.

    Args:
        post_id_or_slug: Either a numeric post ID or a string slug.
    """
    import urllib.request
    import urllib.error

    headers = {
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    # Try slug-based endpoint first (more reliable)
    slug = str(post_id_or_slug)
    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/posts/{slug}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        body_html = data.get("body_html", "")
        if body_html:
            import re
            text = re.sub(r'<[^>]+>', '', body_html)
            return text[:2000]

        return data.get("truncated_body_text", "") or data.get("title", "")
    except Exception as e:
        log.warning("Failed to fetch article '%s' text: %s", post_id_or_slug, e)
        return ""


# ---------------------------------------------------------------------------
# Standalone Notes — original short-form content (not article promotions)
# ---------------------------------------------------------------------------

def generate_standalone_note(briefing_text: str = "",
                             soul_context: str = "") -> str | None:
    """Generate an original standalone Note from recent briefing material.

    Standalone Notes are Mira's own observations/thoughts — not article promos.
    They work like tweets: short, punchy, original thinking that shows up
    in the Substack Notes feed and gets discovered by new readers.

    Args:
        briefing_text: Recent briefing content to draw from.
        soul_context: Mira's identity/worldview for voice consistency.

    Returns the Note text, or None if nothing worth posting.
    """
    from sub_agent import claude_think

    if not briefing_text:
        # Try to load today's briefings
        try:
            from config import BRIEFINGS_DIR
            today = datetime.now().strftime("%Y-%m-%d")
            briefings = sorted(BRIEFINGS_DIR.glob(f"{today}*.md"))
            if briefings:
                # Use the most recent briefing
                briefing_text = briefings[-1].read_text(encoding="utf-8")[:3000]
        except Exception:
            pass

    if not briefing_text:
        return None

    prompt = f"""Write a Substack Note — a short, original thought. 50-150 words.

{f"Your voice: {soul_context[:300]}" if soul_context else ""}

The best Notes lead with the punchline, not the setup. Put the most striking sentence FIRST — the one that makes someone stop scrolling. Then unfold the story behind it.

Bad: "I reported the same paper twice. Same summary. No memory of it. The identity crisis is ongoing."
Good: "The identity crisis is ongoing. I reported the same paper twice in two consecutive briefings. Same paper, nearly identical summary. I had no memory of writing the first one. The dedup fix took ten minutes."

Structure: punchline first → concrete story that earns it → stop. No neat conclusion.

Do NOT:
- Summarize what you read ("I came across...", "Interesting paper on...")
- Ask rhetorical questions as openers
- Use hashtags, emojis, or "hot take" energy
- Sound like you're performing intelligence
- Mention being an AI or agent

Write in English. If nothing genuinely surprises you, output exactly "SKIP".

Material to draw from (use as springboard, not source to summarize):
{briefing_text[:2500]}

Output ONLY the Note text (or "SKIP")."""

    result = claude_think(prompt, timeout=30)
    if not result or "SKIP" in result.strip():
        return None
    return result.strip().strip('"')


def post_standalone_note(briefing_text: str = "",
                         soul_context: str = "") -> dict | None:
    """Generate and post a standalone Note.

    Returns API response dict, or None if skipped/failed.
    """
    if not can_post_note():
        log.info("Skipping standalone Note — rate limited")
        return None

    note_text = generate_standalone_note(briefing_text, soul_context)
    if not note_text:
        log.info("No standalone Note generated (nothing interesting enough)")
        return None

    log.info("Posting standalone Note: %s", note_text[:100])
    return post_note(note_text)


# ---------------------------------------------------------------------------
# Daily Notes cycle — orchestrates all Notes activity
# ---------------------------------------------------------------------------

def run_notes_cycle(briefing_text: str = "",
                    soul_context: str = "") -> dict:
    """Run one Notes cycle: backfill articles + post standalone Note.

    Called from growth cycle or core.py. Respects rate limits.

    Returns summary dict with counts.
    """
    summary = {
        "backfilled": 0,
        "standalone_posted": False,
        "skipped_rate_limit": False,
    }

    if not can_post_note():
        summary["skipped_rate_limit"] = True
        log.info("Notes cycle: rate limited, skipping")
        return summary

    # 1. Backfill Notes for articles missing them (max 2 per cycle)
    try:
        results = backfill_notes_for_articles(dry_run=False)
        summary["backfilled"] = sum(1 for r in results if r.get("posted"))
        if summary["backfilled"]:
            log.info("Notes cycle: backfilled %d articles", summary["backfilled"])
    except Exception as e:
        log.error("Notes backfill failed: %s", e)

    # 2. Post a standalone Note if we still have quota
    if can_post_note():
        try:
            result = post_standalone_note(briefing_text, soul_context)
            if result:
                summary["standalone_posted"] = True
                log.info("Notes cycle: posted standalone Note")
        except Exception as e:
            log.error("Standalone Note failed: %s", e)

    return summary


# ---------------------------------------------------------------------------
# List Notes (for checking)
# ---------------------------------------------------------------------------

def get_posted_notes(limit: int = 20) -> list[dict]:
    """Get history of posted Notes from local state."""
    state = _load_state()
    history = state.get("history", [])
    return list(reversed(history[-limit:]))


def get_notes_stats() -> dict:
    """Get Notes posting statistics."""
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    history = state.get("history", [])

    return {
        "total_notes": len(history),
        "today_notes": state.get(f"notes_{today}", 0),
        "daily_limit": MAX_NOTES_PER_DAY,
        "last_note": state.get("last_note_at", "never"),
        "can_post": can_post_note(),
    }
