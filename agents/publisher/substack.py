"""Substack publisher — create and publish posts via Substack API.

Substack uses cookie-based auth. You need:
1. Log into Substack in browser
2. Copy the `substack.sid` cookie value
3. Add to secrets.yml under api_keys.substack

secrets.yml format:
    api_keys:
      substack:
        subdomain: "your-blog"        # your-blog.substack.com
        cookie: "s%3A..."             # substack.sid cookie value
        email: "you@email.com"        # optional, for draft notifications
"""
import base64
import json
import logging
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("publisher.substack")


def _get_substack_config() -> dict:
    """Load Substack credentials from secrets.yml."""
    from config import SECRETS_FILE
    from sub_agent import _parse_secrets_simple

    secrets = _parse_secrets_simple(SECRETS_FILE)
    cfg = secrets.get("api_keys", {}).get("substack", {})
    if isinstance(cfg, str):
        return {}
    return cfg


def _md_to_html(markdown_text: str) -> str:
    """Convert markdown to basic HTML for Substack."""
    # Use Claude to do a clean conversion
    from sub_agent import claude_think

    prompt = f"""Convert this Markdown to clean HTML suitable for a Substack newsletter post.
- Use <h2>, <h3> for headings (not <h1>, Substack uses that for title)
- Use <p> for paragraphs
- Use <blockquote> for quotes
- Use <strong>, <em> for emphasis
- Use <ul>/<ol>/<li> for lists
- Keep it clean and simple, no CSS classes or inline styles
- Output ONLY the HTML, no explanation

Markdown:
{markdown_text[:8000]}"""

    html = claude_think(prompt, timeout=60)
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


def _html_to_prosemirror(html: str) -> dict:
    """Convert simple HTML to Substack ProseMirror JSON document format."""
    import re as _re
    content = []
    # Split by top-level tags
    tag_pattern = _re.compile(r'<(h[1-6]|p|blockquote|ul|ol|hr)(?:\s[^>]*)?>(.+?)</\1>|<hr\s*/?>',
                              _re.DOTALL)

    for match in tag_pattern.finditer(html):
        if match.group(0).startswith('<hr'):
            content.append({"type": "horizontal_rule"})
            continue
        tag = match.group(1)
        inner = match.group(2).strip()

        if tag in ('h1', 'h2'):
            text_nodes = _parse_inline(inner)
            content.append({
                "type": "heading",
                "attrs": {"level": 2},
                "content": text_nodes,
            })
        elif tag == 'h3':
            text_nodes = _parse_inline(inner)
            content.append({
                "type": "heading",
                "attrs": {"level": 3},
                "content": text_nodes,
            })
        elif tag == 'blockquote':
            content.append({
                "type": "blockquote",
                "content": [{"type": "paragraph", "content": _parse_inline(inner)}],
            })
        elif tag == 'p':
            if inner == '---':
                content.append({"type": "horizontal_rule"})
            else:
                text_nodes = _parse_inline(inner)
                content.append({
                    "type": "paragraph",
                    "content": text_nodes,
                })
        # Lists: simplified — treat each <li> as a paragraph for now
        elif tag in ('ul', 'ol'):
            list_type = "bullet_list" if tag == 'ul' else "ordered_list"
            items = _re.findall(r'<li>(.*?)</li>', inner, _re.DOTALL)
            list_items = []
            for item in items:
                list_items.append({
                    "type": "list_item",
                    "content": [{"type": "paragraph", "content": _parse_inline(item.strip())}],
                })
            if list_items:
                content.append({"type": list_type, "content": list_items})

    if not content:
        # Fallback: treat entire html as a single paragraph
        content = [{"type": "paragraph", "content": [{"type": "text", "text": html[:5000]}]}]

    return {"type": "doc", "content": content}


def _parse_inline(html_text: str) -> list:
    """Parse inline HTML (bold, italic, links) into ProseMirror text nodes."""
    import re as _re
    nodes = []
    # Simple pattern: process <strong>, <em>, <a>, and plain text
    parts = _re.split(r'(<strong>.*?</strong>|<em>.*?</em>|<a\s+href="[^"]*">.*?</a>)',
                       html_text, flags=_re.DOTALL)
    for part in parts:
        if not part:
            continue
        m_strong = _re.match(r'<strong>(.*?)</strong>', part, _re.DOTALL)
        m_em = _re.match(r'<em>(.*?)</em>', part, _re.DOTALL)
        m_a = _re.match(r'<a\s+href="([^"]*)">(.*?)</a>', part, _re.DOTALL)
        if m_strong:
            # Strip any nested tags for simplicity
            text = _re.sub(r'<[^>]+>', '', m_strong.group(1))
            nodes.append({"type": "text", "marks": [{"type": "bold"}], "text": text})
        elif m_em:
            text = _re.sub(r'<[^>]+>', '', m_em.group(1))
            nodes.append({"type": "text", "marks": [{"type": "italic"}], "text": text})
        elif m_a:
            href, text = m_a.group(1), _re.sub(r'<[^>]+>', '', m_a.group(2))
            nodes.append({
                "type": "text",
                "marks": [{"type": "link", "attrs": {"href": href}}],
                "text": text,
            })
        else:
            # Strip remaining tags, keep text
            text = _re.sub(r'<[^>]+>', '', part)
            if text:
                nodes.append({"type": "text", "text": text})
    if not nodes:
        nodes = [{"type": "text", "text": " "}]
    return nodes


def _get_cover_image(title: str, article_text: str, workspace: Path) -> str | None:
    """Get a cover image for the article. Tries Unsplash first, then DALL-E.

    Returns local file path or None.
    """
    from sub_agent import claude_think

    # Step 1: Ask Claude for 2-3 search keywords
    keyword_prompt = f"""Given this article title and excerpt, suggest 2-3 evocative search keywords
for finding a cover photo on Unsplash. Think abstract, atmospheric, editorial.
NOT literal — find the visual metaphor.

Title: {title}
Excerpt: {article_text[:800]}

Output ONLY the keywords separated by spaces. Example: "reflection mirror glass"
No explanation."""

    keywords = claude_think(keyword_prompt, timeout=20)
    if keywords:
        keywords = keywords.strip().strip('"').strip("'")
        log.info("Cover image search: %s", keywords)

        # Try Unsplash (no API key needed for source.unsplash.com redirect)
        path = _fetch_unsplash(keywords, workspace)
        if path:
            return path

    # Fallback: DALL-E
    return _generate_dalle_image(title, article_text, workspace)


def _fetch_unsplash(query: str, workspace: Path) -> str | None:
    """Fetch a landscape photo from Unsplash. Returns local file path or None."""
    try:
        # Unsplash source URL gives a random photo matching the query
        # 1456x816 = recommended Substack cover dimensions
        search_url = f"https://source.unsplash.com/1456x816/?{urllib.parse.quote(query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            image_bytes = resp.read()
            final_url = resp.url  # after redirect

        if len(image_bytes) < 5000:  # too small = error page
            log.warning("Unsplash returned too-small response, skipping")
            return None

        cover_path = workspace / "cover.jpg"
        cover_path.write_bytes(image_bytes)
        log.info("Unsplash cover saved: %s (%d KB, from %s)",
                 cover_path.name, len(image_bytes) // 1024, final_url[:80])

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
    from sub_agent import _get_api_key, claude_think

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

    dalle_prompt = claude_think(prompt_request, timeout=30)
    if not dalle_prompt:
        return None

    dalle_prompt = dalle_prompt.strip()
    log.info("DALL-E prompt: %s", dalle_prompt[:100])

    try:
        payload = json.dumps({
            "model": "dall-e-3",
            "prompt": dalle_prompt,
            "n": 1,
            "size": "1792x1024",
            "quality": "standard",
            "response_format": "b64_json",
        }).encode("utf-8")

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
                "User-Agent": "Mozilla/5.0",
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


def publish_to_substack(title: str, subtitle: str,
                        article_text: str, workspace: Path) -> str:
    """Publish an article to Substack. Returns status message."""
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")

    if not subdomain or not cookie:
        return ("Substack 未配置。请在 secrets.yml 添加 cookie:\n\n"
                "获取方法:\n"
                "1. Chrome 打开 substack.com，确保已登录\n"
                "2. Cmd+Option+I 打开 DevTools\n"
                "3. Application tab → Cookies → substack.com\n"
                "4. 复制 substack.sid 的 Value\n"
                "5. 粘贴到 secrets.yml:\n"
                "   substack:\n"
                "     subdomain: your-blog\n"
                "     cookie: 粘贴的值")

    # Auto-detect title from article if not provided
    if not title:
        lines = article_text.strip().split("\n")
        for line in lines:
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break
        if not title:
            title = lines[0][:60] if lines else "Untitled"

    # Auto-generate subtitle if not provided (acts as email preview + SEO)
    if not subtitle:
        from sub_agent import claude_think
        sub_prompt = f"""Write a one-sentence subtitle for this Substack article.
It should be compelling, specific, and under 120 characters.
It will appear as the email preview text and meta description.

Title: {title}
First 500 chars: {article_text[:500]}

Output ONLY the subtitle, nothing else."""
        subtitle = claude_think(sub_prompt, timeout=20) or ""
        subtitle = subtitle.strip().strip('"').strip("'")[:140]
        if subtitle:
            log.info("Auto-generated subtitle: %s", subtitle)

    # Convert markdown to HTML
    body_html = _md_to_html(article_text)

    # Save HTML preview
    (workspace / "preview.html").write_text(
        f"<h1>{title}</h1>\n{body_html}", encoding="utf-8"
    )

    # Generate and upload cover image
    cover_url = None
    cover_path = _get_cover_image(title, article_text, workspace)
    if cover_path:
        cover_url = _upload_image_to_substack(cover_path, subdomain, cookie)

    # Step 1: Create draft with actual content
    base_url = f"https://{subdomain}.substack.com"
    draft_url = f"{base_url}/api/v1/drafts"

    # Build ProseMirror doc from HTML paragraphs
    doc_content = _html_to_prosemirror(body_html)

    draft_payload = {
        "draft_title": title,
        "draft_subtitle": subtitle or "",
        "draft_body": json.dumps(doc_content),
        "draft_bylines": [],
        "type": "newsletter",
    }
    if cover_url:
        draft_payload["cover_image"] = cover_url

    headers = {
        "Content-Type": "application/json",
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        # Create the draft with full content
        req = urllib.request.Request(
            draft_url,
            data=json.dumps(draft_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            draft_data = json.loads(resp.read().decode("utf-8"))
            draft_id = draft_data.get("id")

        if not draft_id:
            return "创建 Substack 草稿失败：没有返回 draft ID"

        log.info("Created Substack draft: id=%s", draft_id)

        # Step 2: Publish the draft
        publish_url = f"{base_url}/api/v1/drafts/{draft_id}/publish"
        publish_payload = {
            "send": True,  # Send to email subscribers
        }

        req = urllib.request.Request(
            publish_url,
            data=json.dumps(publish_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            pub_data = json.loads(resp.read().decode("utf-8"))

        post_url = pub_data.get("canonical_url", f"{base_url}/p/{draft_id}")
        result = f"已发布到 Substack!\n标题: {title}\n链接: {post_url}"
        log.info("Published to Substack: %s", post_url)

        # Save result
        (workspace / "published.json").write_text(
            json.dumps({"platform": "substack", "title": title,
                        "url": post_url, "draft_id": draft_id},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Substack API error (HTTP %d): %s", e.code, error_body)
        if e.code == 401 or e.code == 403:
            return ("Substack 认证失败，cookie 已过期。\n\n"
                    "重新获取: Chrome → substack.com → Cmd+Option+I → "
                    "Application → Cookies → 复制 substack.sid → "
                    "粘贴到 secrets.yml 的 cookie 字段")
        return f"Substack 发布失败 (HTTP {e.code}): {error_body[:200]}"
    except Exception as e:
        log.error("Substack publish failed: %s", e)
        return f"Substack 发布失败: {e}"
