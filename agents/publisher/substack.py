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

    # Step 1: Create draft
    base_url = f"https://{subdomain}.substack.com"
    draft_url = f"{base_url}/api/v1/drafts"

    draft_payload = {
        "draft_title": title,
        "draft_subtitle": subtitle or "",
        "draft_body": json.dumps({"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "placeholder"}]}
        ]}),
        "draft_bylines": [],
        "type": "newsletter",
    }

    headers = {
        "Content-Type": "application/json",
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0",
    }

    try:
        # Create the draft
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

        # Step 2: Update draft with actual HTML content
        update_url = f"{base_url}/api/v1/drafts/{draft_id}"
        update_payload = {
            "draft_title": title,
            "draft_subtitle": subtitle or "",
            "draft_body_html": body_html,
        }

        req = urllib.request.Request(
            update_url,
            data=json.dumps(update_payload).encode("utf-8"),
            headers=headers,
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()

        # Step 3: Publish the draft
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
