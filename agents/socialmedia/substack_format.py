"""Substack content format conversion and cover image generation.

Handles Markdown-to-HTML, HTML-to-ProseMirror, and cover image sourcing
(personal photos, Unsplash, DALL-E).
"""

import base64
import json
import logging
import os
import re
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


def _md_to_html(markdown_text: str) -> str:
    """Convert markdown to basic HTML for Substack."""
    # Use Claude to do a clean conversion
    from llm import claude_think

    prompt = f"""Convert this Markdown to clean HTML suitable for a Substack newsletter post.
- Use <h2>, <h3> for headings (not <h1>, Substack uses that for title)
- Use <p> for paragraphs
- Use <blockquote> for quotes
- Use <strong>, <em> for emphasis
- Use <ul>/<ol>/<li> for lists
- Keep it clean and simple, no CSS classes or inline styles
- Output ONLY the HTML, no explanation

Markdown:
{markdown_text}"""

    html = claude_think(prompt, timeout=120)
    if html:
        # Strip any markdown code fences from response
        html = html.strip()
        if html.startswith("```"):
            html = html.split("\n", 1)[1] if "\n" in html else html
        if html.endswith("```"):
            html = html.rsplit("```", 1)[0]
        return html.strip()

    # Fallback: minimal conversion
    lines = markdown_text.split("\n")
    html_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith("# "):
            html_lines.append(f"<h2>{line[2:]}</h2>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line:
            html_lines.append(f"<p>{line}</p>")
    return "\n".join(html_lines)


def _html_to_markdown(html: str) -> str:
    """Convert HTML back to Markdown (used for article export)."""
    import re as _re

    text = html
    # Headings
    for level in range(1, 7):
        text = _re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, l=level: "#" * l + " " + m.group(1).strip() + "\n\n",
            text,
            flags=_re.DOTALL,
        )
    # Bold / italic
    text = _re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=_re.DOTALL)
    text = _re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=_re.DOTALL)
    # Links
    text = _re.sub(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", text, flags=_re.DOTALL)
    # List items
    text = _re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", text, flags=_re.DOTALL)
    # Blockquotes
    text = _re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>", lambda m: "> " + m.group(1).strip() + "\n\n", text, flags=_re.DOTALL
    )
    # Paragraphs → double newline
    text = _re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", text, flags=_re.DOTALL)
    # Horizontal rules
    text = _re.sub(r"<hr\s*/?>", "---\n\n", text)
    # Images
    text = _re.sub(r'<img[^>]+src="([^"]*)"[^>]*/?>', r"![](\1)\n\n", text)
    # Strip remaining tags
    text = _re.sub(r"<[^>]+>", "", text)
    # Clean up whitespace
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _html_to_prosemirror(html: str) -> dict:
    """Convert simple HTML to Substack ProseMirror JSON document format."""
    import re as _re

    content = []
    # Split by top-level tags
    tag_pattern = _re.compile(r"<(h[1-6]|p|blockquote|ul|ol|hr)(?:\s[^>]*)?>(.+?)</\1>|<hr\s*/?>", _re.DOTALL)

    for match in tag_pattern.finditer(html):
        if match.group(0).startswith("<hr"):
            content.append({"type": "horizontal_rule"})
            continue
        tag = match.group(1)
        inner = match.group(2).strip()

        if tag in ("h1", "h2"):
            text_nodes = _parse_inline(inner)
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": text_nodes,
                }
            )
        elif tag == "h3":
            text_nodes = _parse_inline(inner)
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": text_nodes,
                }
            )
        elif tag == "blockquote":
            content.append(
                {
                    "type": "blockquote",
                    "content": [{"type": "paragraph", "content": _parse_inline(inner)}],
                }
            )
        elif tag == "p":
            if inner == "---":
                content.append({"type": "horizontal_rule"})
            else:
                text_nodes = _parse_inline(inner)
                content.append(
                    {
                        "type": "paragraph",
                        "content": text_nodes,
                    }
                )
        # Lists: simplified — treat each <li> as a paragraph for now
        elif tag in ("ul", "ol"):
            list_type = "bullet_list" if tag == "ul" else "ordered_list"
            items = _re.findall(r"<li>(.*?)</li>", inner, _re.DOTALL)
            list_items = []
            for item in items:
                list_items.append(
                    {
                        "type": "list_item",
                        "content": [{"type": "paragraph", "content": _parse_inline(item.strip())}],
                    }
                )
            if list_items:
                content.append({"type": list_type, "content": list_items})

    if not content:
        # Fallback: treat entire html as a single paragraph
        content = [{"type": "paragraph", "content": [{"type": "text", "text": html}]}]

    return {"type": "doc", "content": content}


def _parse_inline(html_text: str) -> list:
    """Parse inline HTML (bold, italic, links) into ProseMirror text nodes."""
    import re as _re

    nodes = []
    # Simple pattern: process <strong>, <em>, <a>, and plain text
    parts = _re.split(r'(<strong>.*?</strong>|<em>.*?</em>|<a\s+href="[^"]*">.*?</a>)', html_text, flags=_re.DOTALL)
    for part in parts:
        if not part:
            continue
        m_strong = _re.match(r"<strong>(.*?)</strong>", part, _re.DOTALL)
        m_em = _re.match(r"<em>(.*?)</em>", part, _re.DOTALL)
        m_a = _re.match(r'<a\s+href="([^"]*)">(.*?)</a>', part, _re.DOTALL)
        if m_strong:
            # Strip any nested tags for simplicity
            text = _re.sub(r"<[^>]+>", "", m_strong.group(1))
            nodes.append({"type": "text", "marks": [{"type": "bold"}], "text": text})
        elif m_em:
            text = _re.sub(r"<[^>]+>", "", m_em.group(1))
            nodes.append({"type": "text", "marks": [{"type": "italic"}], "text": text})
        elif m_a:
            href, text = m_a.group(1), _re.sub(r"<[^>]+>", "", m_a.group(2))
            nodes.append(
                {
                    "type": "text",
                    "marks": [{"type": "link", "attrs": {"href": href}}],
                    "text": text,
                }
            )
        else:
            # Strip remaining tags, keep text
            text = _re.sub(r"<[^>]+>", "", part)
            if text:
                nodes.append({"type": "text", "text": text})
    if not nodes:
        nodes = [{"type": "text", "text": " "}]
    return nodes


def _get_cover_image(title: str, article_text: str, workspace: Path) -> str | None:
    """Get a cover image for the article.

    Priority: personal photos > DALL-E.
    Returns local file path or None.
    """
    # Always try personal photo library first
    personal = _pick_personal_cover()
    if personal:
        return personal

    # Fallback: DALL-E
    return _generate_dalle_image(title, article_text, workspace)


def _pick_personal_cover() -> str | None:
    """Pick a personal photo for article cover, avoiding recent repeats."""
    import random
    import subprocess
    import tempfile

    _photos_dirs = [
        Path(__file__).resolve().parent.parent.parent.parent / "Assets" / "photos",  # MtJoy/Assets/photos/
        Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "photos" / "lred",
    ]

    photos = []
    for d in _photos_dirs:
        if not d.exists():
            continue
        extensions = {".jpg", ".jpeg", ".png", ".heic", ".tiff"}
        photos = [
            p
            for p in d.iterdir()
            if p.suffix.lower() in extensions and p.stat().st_size > 50_000 and " 2" not in p.name
        ]  # skip macOS duplicates
        if photos:
            break

    if not photos:
        return None

    # Track ALL used photos to guarantee no repeats until library exhausted
    from config import DATA_DIR

    history_file = DATA_DIR / "state" / "cover_history.json"
    used: set[str] = set()
    try:
        if history_file.exists():
            used = set(json.loads(history_file.read_text("utf-8")))
    except Exception:
        pass

    available = [p for p in photos if p.name not in used]
    if not available:
        used.clear()  # all used, reset
        available = photos

    pick = random.choice(available)

    # Update history (full list)
    used.add(pick.name)
    try:
        history_file.write_text(json.dumps(sorted(used)), "utf-8")
    except Exception:
        pass

    # Resize to Substack cover dimensions (1456px wide)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    subprocess.run(["sips", "-Z", "1456", str(pick), "--out", tmp.name], capture_output=True)

    log.info("Personal cover photo: %s", pick.name)
    return tmp.name


def _fetch_unsplash(query: str, workspace: Path) -> str | None:
    """Fetch a landscape photo from Unsplash. Returns local file path or None."""
    try:
        # Unsplash source URL gives a random photo matching the query
        # 1456x816 = recommended Substack cover dimensions
        search_url = f"https://source.unsplash.com/1456x816/?{urllib.parse.quote(query)}"
        req = urllib.request.Request(
            search_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            image_bytes = resp.read()
            final_url = resp.url  # after redirect

        if len(image_bytes) < 5000:  # too small = error page
            log.warning("Unsplash returned too-small response, skipping")
            return None

        cover_path = workspace / "cover.jpg"
        cover_path.write_bytes(image_bytes)
        log.info("Unsplash cover saved: %s (%d KB, from %s)", cover_path.name, len(image_bytes) // 1024, final_url[:80])

        # Save attribution
        (workspace / "cover_source.txt").write_text(
            f"Source: Unsplash\nQuery: {query}\nURL: {final_url}\n",
            encoding="utf-8",
        )
        return str(cover_path)

    except Exception as e:
        log.warning("Unsplash fetch failed: %s", e)
        return None


def _generate_dalle_image(title: str, article_text: str, workspace: Path) -> str | None:
    """Generate a cover image using DALL-E 3. Returns local file path or None."""
    from llm import _get_api_key, claude_think

    api_key = _get_api_key("openai")
    if not api_key:
        log.warning("No OpenAI API key — skipping DALL-E generation")
        return None

    prompt_request = f"""Create a DALL-E image generation prompt for a Substack cover image.

Requirements:
- Abstract, artistic, evocative — NOT literal illustration
- No text, no words, no letters in the image
- Moody, atmospheric, visually striking
- One clear visual concept, not cluttered

Title: {title}
Content excerpt: {article_text[:1000]}

Output ONLY the DALL-E prompt, nothing else. Keep it under 150 words."""

    dalle_prompt = claude_think(prompt_request, timeout=90)
    if not dalle_prompt:
        return None

    dalle_prompt = dalle_prompt.strip()
    log.info("DALL-E prompt: %s", dalle_prompt[:100])

    try:
        payload = json.dumps(
            {
                "model": "dall-e-3",
                "prompt": dalle_prompt,
                "n": 1,
                "size": "1792x1024",
                "quality": "standard",
                "response_format": "b64_json",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        b64_data = result["data"][0]["b64_json"]
        image_bytes = base64.b64decode(b64_data)

        cover_path = workspace / "cover.png"
        cover_path.write_bytes(image_bytes)
        log.info("DALL-E cover saved: %s (%d KB)", cover_path.name, len(image_bytes) // 1024)

        (workspace / "cover_prompt.txt").write_text(dalle_prompt, encoding="utf-8")
        return str(cover_path)

    except Exception as e:
        log.error("DALL-E generation failed: %s", e)
        return None


def _upload_image_to_substack(image_path: str, subdomain: str, cookie: str) -> str | None:
    """Upload a local image to Substack. Returns the hosted image URL."""
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()

        b64_image = b"data:image/png;base64," + base64.b64encode(image_bytes)

        # Substack image upload endpoint
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/image",
            data=urllib.parse.urlencode({"image": b64_image.decode()}).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        image_url = result.get("url", "")
        if image_url:
            log.info("Uploaded image to Substack: %s", image_url[:80])
        return image_url or None

    except Exception as e:
        log.error("Substack image upload failed: %s", e)
        return None
