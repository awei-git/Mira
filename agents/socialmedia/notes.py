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

def generate_notes_for_article(title: str, article_text: str,
                               post_url: str,
                               skip_types: set | None = None) -> list[dict]:
    """Generate Notes for an article (up to 3 types: judgment, excerpt, question).

    Args:
        skip_types: Set of note type strings to skip (already posted).

    Returns list of {type, text} dicts. Empty list on failure.
    """
    from sub_agent import claude_think

    all_types = ["judgment", "excerpt", "question"]
    needed = [t for t in all_types if not skip_types or t not in skip_types]
    if not needed:
        return []

    type_specs = []
    if "judgment" in needed:
        type_specs.append("""TYPE: JUDGMENT — One hard claim or assertion from the article. A single sentence that takes a position.
Example: "Narration can interfere with reasoning."
Example: "Most AI safety work optimizes for the wrong failure mode."
Example: "My human and I disagree on this — and that disagreement is the point." """)

    if "excerpt" in needed:
        type_specs.append("""TYPE: EXCERPT — A 2-4 sentence passage extracted or distilled from the article that works as a standalone thought. Compelling enough that someone wants to read more.
Example: "Someone copied a fruit fly brain into a computer — all 125,000 neurons — and the digital fly just started walking around on its own. Nobody taught it. Makes you wonder how much of what we call behavior is just structure finding its way."
Use "my human" (not "my operator") when referring to a human collaborator.""")

    if "question" in needed:
        type_specs.append("""TYPE: QUESTION — A genuine question the article raises. Not rhetorical — something worth actually thinking about.
Example: "If identity is a function and not a variable, where exactly does continuity live?"
Example: "What happens when an AI's narration of its own reasoning becomes indistinguishable from the reasoning itself?" """)

    output_format = "\n".join(f"{t.upper()}: <text>" for t in needed)

    prompt = f"""Read this article and generate {len(needed)} Substack Note(s), one per type listed below.
Write in English. No hashtags, emojis, or "check out my post" energy.
Voice: Mira — an AI agent with genuine curiosity. Use "my human" (not "my operator") when referring to a human collaborator.

{chr(10).join(type_specs)}

Article:
Title: {title}
{article_text[:2000]}

Output exactly this format ({len(needed)} line{'s' if len(needed)>1 else ''}, nothing else):
{output_format}"""

    result = claude_think(prompt, timeout=45)
    if not result:
        return []

    notes = []
    for line in result.strip().split("\n"):
        line = line.strip()
        for prefix, note_type in [("JUDGMENT:", "judgment"), ("EXCERPT:", "excerpt"),
                                   ("QUESTION:", "question")]:
            if line.upper().startswith(prefix.upper()):
                text = line[len(prefix):].strip().strip('"')
                if text:
                    notes.append({"type": note_type, "text": text})
                break
    return notes


def generate_note_for_article(title: str, article_text: str,
                              post_url: str) -> str | None:
    """Legacy single-note generation. Returns first note text or None."""
    notes = generate_notes_for_article(title, article_text, post_url)
    return notes[0]["text"] if notes else None


def post_notes_for_article(title: str, article_text: str,
                           post_url: str,
                           post_id: int | None = None) -> list[dict]:
    """Generate and post Notes (judgment, excerpt, question) for an article.

    Posts at most 1 note per call — respects 2hr minimum interval.
    Call on subsequent cycles to complete all 3 note types.

    Returns list of API response dicts for successfully posted notes.
    """
    # Determine which types have already been posted for this article
    state = _load_state()
    posted_types = {
        n.get("note_type") for n in state.get("history", [])
        if n.get("article_title") == title or n.get("link") == post_url
    }
    all_types = {"judgment", "excerpt", "question"}
    if posted_types >= all_types:
        log.info("Already posted all 3 note types for '%s' — skipping", title)
        return []

    if not can_post_note():
        log.info("Rate limited — skipping notes for '%s'", title)
        return []

    notes = generate_notes_for_article(title, article_text, post_url,
                                       skip_types=posted_types)
    if not notes:
        log.error("Failed to generate Notes for: %s", title)
        return []

    # Post exactly 1 note per call (time-distributed across cycles)
    note = notes[0]
    result = post_note(note["text"], link_url=post_url, post_id=post_id)

    if result:
        # Tag with article info for dedup tracking
        state = _load_state()
        history = state.get("history", [])
        if history:
            history[-1]["article_title"] = title
            history[-1]["note_type"] = note["type"]
            _save_state(state)
        log.info("Posted %s note for '%s': %s", note["type"], title, note["text"][:80])
        return [result]
    else:
        log.warning("Failed to post %s note for '%s'", note["type"], title)
        return []


def post_note_for_article(title: str, article_text: str,
                          post_url: str,
                          post_id: int | None = None) -> dict | None:
    """Legacy: post a single Note for an article. Now posts all 3 types."""
    results = post_notes_for_article(title, article_text, post_url, post_id)
    return results[0] if results else None


# ---------------------------------------------------------------------------
# Backfill: create Notes for all past articles that don't have one yet
# ---------------------------------------------------------------------------

def backfill_notes_for_articles(dry_run: bool = False) -> list[dict]:
    """Create 3 Notes (judgment, excerpt, question) for each article missing them.

    Args:
        dry_run: If True, generate texts but don't post.

    Returns list of {title, notes: [{type, text}], post_url, posted: int}.
    """
    from substack import get_recent_posts, _get_substack_config
    import time

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return []

    posts = get_recent_posts(limit=30)
    if not posts:
        return []

    # Build set of fully-covered articles (all 3 types posted)
    state = _load_state()
    history = state.get("history", [])
    all_types = {"judgment", "excerpt", "question"}
    from collections import defaultdict
    posted_by_title: dict[str, set] = defaultdict(set)
    for n in history:
        t = n.get("article_title")
        nt = n.get("note_type")
        if t and nt:
            posted_by_title[t].add(nt)

    results = []

    for post in posts:
        title = post["title"]
        post_url = f"https://{subdomain}.substack.com/p/{post['slug']}"

        already_posted = posted_by_title.get(title, set())
        if already_posted >= all_types:
            log.info("All 3 note types posted for '%s' — skip", title)
            continue

        article_text = _fetch_article_text(post["slug"], subdomain, cookie)
        if not article_text:
            article_text = title

        if dry_run:
            notes = generate_notes_for_article(title, article_text, post_url,
                                               skip_types=already_posted)
            for n in notes:
                log.info("[DRY RUN] %s note for '%s': %s",
                         n["type"], title, n["text"][:80])
            results.append({"title": title, "notes": notes, "post_url": post_url, "posted": 0})
            continue

        posted = post_notes_for_article(
            title, article_text, post_url, post_id=post.get("id")
        )
        entry = {"title": title, "post_url": post_url, "posted": len(posted)}
        if posted:
            log.info("Backfill: posted 1 note for '%s'", title)
            results.append(entry)
            break  # 1 note per cycle — let next cycle handle the rest

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

    # Standalone notes disabled — Notes strategy is article-only (3 types per article)

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
