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
import json
import logging
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

    # Convert markdown to HTML
    body_html = _md_to_html(article_text)

    # Save HTML preview
    (workspace / "preview.html").write_text(
        f"<h1>{title}</h1>\n{body_html}", encoding="utf-8"
    )

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
