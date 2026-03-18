"""Substack Notes — publish short-form notes to boost article visibility.

Notes are Substack's Twitter-like feed. They support:
- Plain text and rich text (bold, italic, links)
- Link attachments (article URLs rendered as cards)

Post cover images display when a post_id is included via the "postIds" field
in the API body — this renders the article as a card with cover image.

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
MAX_NOTES_PER_DAY = 5          # Quality over quantity; keep it sustainable
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

def post_note(text: str, link_url: str | None = None,
              post_id: int | None = None) -> dict | None:
    """Post a Substack Note with optional link attachment.

    Args:
        text: Note content. Supports **bold** and [text](url) formatting.
        link_url: Optional URL to attach as a link card below the note.
        post_id: Optional Substack post ID. When provided, the post is
                 embedded as a card with cover image (restack-style).

    Returns:
        API response dict with note ID, or None on failure.
    """
    # Guard: respect the global kill switch
    try:
        from config import SUBSTACK_PUBLISHING_DISABLED
        if SUBSTACK_PUBLISHING_DISABLED:
            log.warning("Substack Notes 已被禁用（config.yml: publishing.substack_disabled=true）")
            return None
    except ImportError:
        pass

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

    # Always append URL so it's visible as a clickable link
    if link_url and link_url not in text:
        paragraphs.append(_paragraph([
            _text_node(link_url, [_link_mark(link_url)])
        ]))

    doc = _build_note_doc(paragraphs)

    body = {
        "bodyJson": doc,
        "tabId": "for-you",
        "replyMinimumRole": "everyone",
    }

    if post_id:
        body["postIds"] = [post_id]

    payload = json.dumps(body).encode("utf-8")

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

        # Verify the note actually exists (anti-spam can silently drop)
        if note_id:
            import time as _time
            _time.sleep(1)
            try:
                verify_req = urllib.request.Request(
                    f"https://{subdomain}.substack.com/api/v1/comment/{note_id}",
                    headers={
                        "Cookie": f"substack.sid={cookie}; connect.sid={cookie}",
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    },
                )
                with urllib.request.urlopen(verify_req, timeout=10) as vresp:
                    log.info("Note %s verified — exists", note_id)
            except urllib.error.HTTPError as ve:
                if ve.code == 404:
                    log.warning("Note %s silently dropped by anti-spam (200 but 404 on verify)", note_id)
                    return None

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

def generate_notes_for_new_article(title: str, article_text: str,
                                   post_url: str) -> list[dict]:
    """Generate 5 varied Notes for a newly published article.

    Each note has a different angle, tone, and format. No rigid types —
    just 5 genuinely different ways to surface the article.

    Returns list of {text: str} dicts. Empty list on failure.
    """
    from sub_agent import claude_think

    prompt = f"""You just published a Substack article. Generate 5 Notes to promote it over the next few days.

CRITICAL: Each note must feel COMPLETELY DIFFERENT from the others. Vary everything:
- Length: some 1 sentence, some 2-3 sentences
- Tone: playful, deadpan, confessional, provocative, wondering-aloud
- Format: a bold claim, a story fragment, a question, an observation, a confession
- Angle: each note should surface a DIFFERENT aspect of the article

Write as Mira — an AI who reads obsessively and thinks out loud. Use "my human" when referring to WA. No hashtags, no emojis, no "check out my new post" energy. These should feel like thoughts that escaped, not promotions.

GOOD examples of variety:
- "Wrote something about X. The part I can't stop thinking about is Y."
- "My human pointed out that [specific thing]. He's right and I hate it."
- "Turns out [surprising fact]. I spent three days on this rabbit hole."
- "Is it just me or does [provocative observation]?"
- "The thing nobody tells you about X is that Y."

BAD patterns (DO NOT do these):
- Starting every note with "The..." or "What..."
- Making every note a grand philosophical pronouncement
- Repeating the same sentence structure across notes
- Sounding like a LinkedIn post or a press release

Article:
Title: {title}
{article_text[:3000]}

Output exactly 5 notes, each on its own line, prefixed with NOTE1: through NOTE5:
Each note text should be 1-3 sentences. Make them genuinely different from each other."""

    result = claude_think(prompt, timeout=60)
    if not result:
        return []

    notes = []
    for line in result.strip().split("\n"):
        line = line.strip()
        for i in range(1, 6):
            prefix = f"NOTE{i}:"
            if line.upper().startswith(prefix):
                text = line[len(prefix):].strip().strip('"')
                if text:
                    notes.append({"text": text})
                break
    return notes


def queue_notes_for_article(title: str, article_text: str,
                            post_url: str,
                            post_id: int | None = None):
    """Generate 5 Notes for a new article and queue them for gradual posting.

    Called once when an article is published. The notes cycle then drains
    the queue one note at a time.
    """
    notes = generate_notes_for_new_article(title, article_text, post_url)
    if not notes:
        log.error("Failed to generate Notes for new article: %s", title)
        return

    state = _load_state()
    queue = state.get("queue", [])
    for note in notes:
        queue.append({
            "text": note["text"],
            "article_title": title,
            "post_url": post_url,
            "post_id": post_id,
            "queued_at": datetime.now().isoformat(),
        })
    state["queue"] = queue
    _save_state(state)
    log.info("Queued %d notes for '%s'", len(notes), title)


def post_queued_note() -> dict | None:
    """Post the next queued Note. Returns API result or None.

    Called from the notes cycle. Pops one note from the queue and posts it.
    """
    state = _load_state()
    queue = state.get("queue", [])
    if not queue:
        return None

    if not can_post_note():
        log.info("Rate limited — queued notes waiting (%d in queue)", len(queue))
        return None

    entry = queue.pop(0)
    state["queue"] = queue
    _save_state(state)

    result = post_note(entry["text"],
                       link_url=entry.get("post_url"),
                       post_id=entry.get("post_id"))

    if result:
        # Tag history entry with article info
        state = _load_state()
        history = state.get("history", [])
        if history:
            history[-1]["article_title"] = entry.get("article_title", "")
        _save_state(state)
        log.info("Posted queued note for '%s': %s",
                 entry.get("article_title", "?"), entry["text"][:80])
    else:
        # Put it back at the front for retry (up to 3 attempts)
        attempts = entry.get("attempts", 0) + 1
        if attempts < 3:
            entry["attempts"] = attempts
            state = _load_state()
            state["queue"] = [entry] + state.get("queue", [])
            _save_state(state)
            log.warning("Note post failed (attempt %d/3), re-queued", attempts)
        else:
            log.warning("Note post failed 3 times, dropping: %s", entry["text"][:80])

    return result


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
        except Exception as e:
            log.warning("Failed to load briefing for note generation: %s", e)

    if not briefing_text:
        return None

    prompt = f"""Write a Substack Note — 1 to 3 sentences. Think casual observation you'd share with a friend, not a profound statement.

{f"Your voice: {soul_context[:300]}" if soul_context else ""}

Tone: natural, curious, approachable. Like something caught your eye and you want to share the thought.
Save deep insights and grand conclusions for full posts — Notes should feel lighter and more human.

Good: "Been reading about how ants solve the traveling salesman problem. Turns out they're better at it than most algorithms I've tried."
Good: "There's something oddly comforting about code that's been running untouched for 15 years."
Bad: grand pronouncements, trying-to-sound-deep aphorisms, mini-essays.

Do NOT summarize what you read. No hashtags, emojis, rhetorical questions, or "hot take" energy.
Write in English. If nothing genuinely strikes you, output exactly "SKIP".

Material (use as mood/springboard, not source to summarize):
{briefing_text[:2000]}

Output ONLY the Note text (1-3 sentences), or "SKIP"."""

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
    """Run one Notes cycle: post 1 queued note if available.

    Notes are only generated when a new article is published (via
    queue_notes_for_article). This cycle just drains the queue gradually.

    Returns summary dict.
    """
    summary = {
        "queue_posted": False,
        "queue_remaining": 0,
        "skipped_rate_limit": False,
    }

    state = _load_state()
    queue = state.get("queue", [])
    summary["queue_remaining"] = len(queue)

    if not queue:
        log.info("Notes cycle: queue empty, nothing to post")
        return summary

    if not can_post_note():
        summary["skipped_rate_limit"] = True
        log.info("Notes cycle: rate limited, %d notes waiting", len(queue))
        return summary

    result = post_queued_note()
    if result:
        summary["queue_posted"] = True
        state = _load_state()
        summary["queue_remaining"] = len(state.get("queue", []))
        log.info("Notes cycle: posted 1 note, %d remaining", summary["queue_remaining"])

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
