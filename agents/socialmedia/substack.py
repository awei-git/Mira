"""Substack UGC — publish articles, comment on posts, grow the account.

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
import os
import tempfile
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
        Path.home() / "Library" / "Mobile Documents"
        / "com~apple~CloudDocs" / "photos" / "lred",
    ]

    photos = []
    for d in _photos_dirs:
        if not d.exists():
            continue
        extensions = {".jpg", ".jpeg", ".png", ".heic", ".tiff"}
        photos = [p for p in d.iterdir()
                  if p.suffix.lower() in extensions
                  and p.stat().st_size > 50_000
                  and " 2" not in p.name]  # skip macOS duplicates
        if photos:
            break

    if not photos:
        return None

    # Track recently used photos to avoid repeats
    from config import MIRA_ROOT
    history_file = MIRA_ROOT / ".cover_history.json"
    recent: list[str] = []
    try:
        if history_file.exists():
            recent = json.loads(history_file.read_text("utf-8"))
    except Exception:
        pass

    # Exclude recently used (keep last 10)
    available = [p for p in photos if p.name not in recent]
    if not available:
        available = photos  # all used, reset

    pick = random.choice(available)

    # Update history
    recent.append(pick.name)
    recent = recent[-10:]  # keep last 10
    try:
        history_file.write_text(json.dumps(recent), "utf-8")
    except Exception:
        pass

    # Resize to Substack cover dimensions (1456px wide)
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    subprocess.run(["sips", "-Z", "1456", str(pick), "--out", tmp.name],
                   capture_output=True)

    log.info("Personal cover photo: %s", pick.name)
    return tmp.name


def _fetch_unsplash(query: str, workspace: Path) -> str | None:
    """Fetch a landscape photo from Unsplash. Returns local file path or None."""
    try:
        # Unsplash source URL gives a random photo matching the query
        # 1456x816 = recommended Substack cover dimensions
        search_url = f"https://source.unsplash.com/1456x816/?{urllib.parse.quote(query)}"
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
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

    dalle_prompt = claude_think(prompt_request, timeout=90)
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


def publish_to_substack(title: str, subtitle: str,
                        article_text: str, workspace: Path) -> str:
    """Publish an article to Substack. Returns status message."""
    # Guard: respect the global kill switch
    try:
        from config import SUBSTACK_PUBLISHING_DISABLED
        if SUBSTACK_PUBLISHING_DISABLED:
            msg = "Substack 发布已被禁用（config.yml: publishing.substack_disabled=true）。"
            log.warning(msg)
            return msg
    except ImportError:
        pass

    # Guard: enforce 1 post per 3 days cooldown
    PUBLISH_COOLDOWN_DAYS = 3
    try:
        from soul_manager import catalog_list
        pubs = [e for e in catalog_list() if e.get("status") == "published" and e.get("date")]
        if pubs:
            from datetime import datetime as _dt
            latest = max(e["date"] for e in pubs)
            pub_date = _dt.strptime(latest[:10], "%Y-%m-%d").date()
            days_since = (_dt.now().date() - pub_date).days
            if days_since < PUBLISH_COOLDOWN_DAYS:
                msg = (f"发布被拦截：距上次发布仅 {days_since} 天，"
                       f"冷却期为 {PUBLISH_COOLDOWN_DAYS} 天。请等待后再发布。")
                log.warning(msg)
                return msg
    except Exception as e:
        log.warning("Publish cooldown check failed (proceeding): %s", e)

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

    # Enforce language consistency: title and body must match
    import re as _re_lang
    _has_cjk = bool(_re_lang.search(r'[\u4e00-\u9fff]', title))
    _body_sample = article_text[:2000]
    _body_cjk_ratio = len(_re_lang.findall(r'[\u4e00-\u9fff]', _body_sample)) / max(len(_body_sample), 1)
    _body_is_cjk = _body_cjk_ratio > 0.1
    if _has_cjk != _body_is_cjk:
        from sub_agent import claude_think as _ct
        _target_lang = "Chinese" if _body_is_cjk else "English"
        _new_title = _ct(
            f"Translate this article title to {_target_lang}. "
            f"Keep it compelling and concise. Output ONLY the translated title.\n\n{title}",
            timeout=15,
        )
        if _new_title:
            log.info("Title language mismatch fixed: '%s' -> '%s'", title, _new_title.strip())
            title = _new_title.strip().strip('"').strip("'")

    # Auto-generate subtitle if not provided (acts as email preview + SEO)
    if not subtitle:
        from sub_agent import claude_think
        _lang_hint = "中文" if _body_is_cjk else "English"
        sub_prompt = f"""Write a one-sentence subtitle for this Substack article.
It should be compelling, specific, and under 120 characters.
It will appear as the email preview text and meta description.
Write it in {_lang_hint} to match the article language.

Title: {title}
First 500 chars: {article_text[:500]}

Output ONLY the subtitle, nothing else."""
        subtitle = claude_think(sub_prompt, timeout=20) or ""
        subtitle = subtitle.strip().strip('"').strip("'")[:140]
        if subtitle:
            log.info("Auto-generated subtitle: %s", subtitle)

    # Strip revision metadata header (修订稿 R1 / 日期 / 字数 / 基于 / ---)
    import re as _re
    _rev_pattern = _re.compile(
        r"^#\s*修订稿.*?\n(?:(?:日期|字数|基于)[:：].*\n)*\s*---\s*\n",
        _re.MULTILINE,
    )
    article_text = _rev_pattern.sub("", article_text, count=1)

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
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
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

        # Add to content catalog
        try:
            from soul_manager import catalog_add
            catalog_add({
                "type": "article",
                "title": title,
                "path": str(workspace),
                "topics": [],  # will be enriched by smart_classify
                "status": "published",
                "substack_id": draft_id,
                "url": post_url,
                "description": (subtitle or "")[:200],
            })
        except Exception as _cat_e:
            log.warning("Catalog add failed: %s", _cat_e)

        # Copy to _published/ for podcast pipeline
        try:
            pub_dir = workspace.parent / "_published"
            pub_dir.mkdir(parents=True, exist_ok=True)
            slug = pub_data.get("slug", str(draft_id))
            pub_date = (pub_data.get("post_date") or "")[:10]
            if not pub_date:
                from datetime import datetime as _dt
                pub_date = _dt.now().strftime("%Y-%m-%d")
            pub_file = pub_dir / f"{pub_date}_{slug}.md"
            pub_file.write_text(
                f'---\ntitle: "{title}"\ndate: {pub_date}\nurl: {post_url}\n---\n\n'
                f'# {title}\n\n{article_text}\n',
                encoding="utf-8",
            )
            log.info("Copied to _published: %s", pub_file.name)
        except Exception as _pub_e:
            log.warning("Copy to _published failed (non-fatal): %s", _pub_e)

        # Queue Notes promoting this article (drained by growth cycle)
        try:
            from notes import queue_notes_for_article
            queue_notes_for_article(title, article_text, post_url)
            log.info("Queued Notes for article: %s", title)
            result += "\n\nNotes 已加入队列，将在 growth cycle 中发出"
        except Exception as e:
            log.warning("Auto Note queue failed (non-fatal): %s", e)

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


# ---------------------------------------------------------------------------
# Audio upload
# ---------------------------------------------------------------------------

def upload_audio_to_post(mp3_path: Path, post_id: int | str,
                          label: str | None = None) -> bool:
    """Upload an MP3 and embed it as an audio player block in the post body.

    Flow:
    1. POST /api/v1/audio/upload  → presigned S3 URL + media_id
    2. PUT file to S3             → collect ETags
    3. POST /transcode            → triggers S3 CompleteMultipartUpload + transcoding
    4. Poll GET /audio/upload/{id} until state == "transcoded"
    5. GET draft body, insert {"type":"audio","attrs":{mediaUploadId,...}} at top
    6. PUT updated draft_body
    7. POST publish (should_send_email=false) to push to published post
    """
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        log.error("Substack not configured for audio upload")
        return False

    mp3_path = Path(mp3_path)
    if not mp3_path.exists():
        log.error("Audio file not found: %s", mp3_path)
        return False

    file_size = mp3_path.stat().st_size
    file_name = mp3_path.name
    base_url = f"https://{subdomain}.substack.com"

    headers = {
        "Cookie": f"substack.sid={cookie}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }

    # Step 1: Request presigned upload URL
    params = urllib.parse.urlencode({
        "filetype": "audio/mpeg",
        "fileSize": file_size,
        "fileName": file_name,
        "post_id": post_id,
    })
    upload_url = f"{base_url}/api/v1/audio/upload?{params}"

    try:
        req = urllib.request.Request(upload_url, data=b"", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        s3_urls = data.get("multipartUploadUrls", [])
        if not s3_urls:
            log.error("No upload URLs returned: %s", json.dumps(data)[:200])
            return False

        media_upload = data.get("mediaUpload", {})
        media_id = media_upload.get("id", "")
        log.info("Got upload URL for post %s, media_id=%s, %d parts",
                 post_id, media_id, len(s3_urls))

        # Step 2: Upload file parts to S3
        file_data = mp3_path.read_bytes()
        multipart_upload_id = data.get("multipartUploadId", "")
        existing_etags = [
            p.get("etag") for p in media_upload.get("parts", [])
            if p.get("etag")
        ]
        etags = list(existing_etags)

        if len(s3_urls) == 1:
            s3_req = urllib.request.Request(
                s3_urls[0], data=file_data, method="PUT",
                headers={"Content-Type": "audio/mpeg"},
            )
            with urllib.request.urlopen(s3_req, timeout=120) as s3_resp:
                etag = s3_resp.headers.get("ETag", "")
                etags.append(etag)
                log.info("S3 upload complete, ETag=%s", etag)
        else:
            chunk_size = (file_size + len(s3_urls) - 1) // len(s3_urls)
            for i, url in enumerate(s3_urls):
                start = i * chunk_size
                end = min(start + chunk_size, file_size)
                chunk = file_data[start:end]
                s3_req = urllib.request.Request(
                    url, data=chunk, method="PUT",
                    headers={"Content-Type": "audio/mpeg"},
                )
                with urllib.request.urlopen(s3_req, timeout=120) as s3_resp:
                    etag = s3_resp.headers.get("ETag", "")
                    etags.append(etag)
                log.info("Part %d/%d uploaded (%d bytes)", i + 1, len(s3_urls), len(chunk))

        # Step 3: Call transcode endpoint (triggers S3 CompleteMultipartUpload + processing)
        import time as _time
        transcode_url = f"{base_url}/api/v1/audio/upload/{media_id}/transcode"
        transcode_body = json.dumps({
            "duration": None,
            "multipart_upload_id": multipart_upload_id,
            "multipart_upload_etags": etags,
        }).encode()
        json_headers = {**headers, "Content-Type": "application/json"}
        req = urllib.request.Request(
            transcode_url, data=transcode_body,
            headers=json_headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            log.info("Transcode initiated, state=%s", result.get("state"))

        # Step 4: Poll until state == "transcoded" (transcoding is async, ~30-120s)
        state = result.get("state", "uploaded")
        for attempt in range(24):  # up to 4 minutes
            if state == "transcoded":
                break
            _time.sleep(10)
            try:
                req = urllib.request.Request(
                    f"{base_url}/api/v1/audio/upload/{media_id}",
                    headers=headers,
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    poll = json.loads(resp.read().decode("utf-8"))
                    state = poll.get("state", state)
                    log.info("Polling transcode state: %s", state)
            except urllib.error.HTTPError:
                pass  # may 403 transiently; keep trying

        if state != "transcoded":
            log.warning("Transcode did not complete (state=%s); proceeding anyway", state)

        # Step 5: Get draft body and insert audio embed node at top
        req = urllib.request.Request(
            f"{base_url}/api/v1/drafts/{post_id}", headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            draft = json.loads(resp.read().decode("utf-8"))

        body_raw = draft.get("draft_body") or draft.get("body") or "{}"
        body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw

        # Resolve duration from poll result or media upload object
        duration = 0.0
        try:
            req = urllib.request.Request(
                f"{base_url}/api/v1/audio/upload/{media_id}", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                mu = json.loads(resp.read().decode("utf-8"))
                duration = float(mu.get("duration") or 0)
        except urllib.error.HTTPError:
            pass

        embed_node = {
            "type": "audio",
            "attrs": {
                "label": label or file_name.replace("-", " ").replace(".mp3", "").title(),
                "mediaUploadId": media_id,
                "duration": round(duration, 3),
                "downloadable": False,
                "isEditorNode": True,
            },
        }

        content = body.get("content", [])
        if content and content[0].get("type") == "audio":
            content[0] = embed_node  # replace existing audio node
        else:
            content.insert(0, embed_node)
        body["content"] = content

        # Step 6: Update draft body
        put_body = json.dumps({"draft_body": json.dumps(body)}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/v1/drafts/{post_id}",
            data=put_body, headers=json_headers, method="PUT",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            pass
        log.info("Embedded audio node in post body")

        # Step 7: Publish silently (no email)
        pub_body = json.dumps({"should_send_email": False}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/v1/drafts/{post_id}/publish",
            data=pub_body, headers=json_headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("Published embedded audio to post %s", post_id)

        log.info("Audio uploaded to post %s: %s (%d KB)",
                 post_id, file_name, file_size // 1024)
        return True

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Audio upload failed (HTTP %d): %s", e.code, error_body)
        return False
    except Exception as e:
        log.error("Audio upload failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Comment monitoring and reply
# ---------------------------------------------------------------------------

def get_recent_posts(limit: int = 10) -> list[dict]:
    """Get recent published posts with comment counts."""
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return []

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/posts?limit={limit}",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            posts = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "id": p["id"],
                "title": p.get("title", ""),
                "slug": p.get("slug", ""),
                "comment_count": p.get("comment_count", 0),
                "post_date": p.get("post_date", ""),
            }
            for p in posts
            if isinstance(p, dict)
        ]
    except Exception as e:
        log.error("Failed to fetch posts: %s", e)
        return []


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
        _flatten_comments(data if isinstance(data, list) else data.get("comments", []),
                          comments)
        return comments
    except Exception as e:
        log.error("Failed to fetch comments for post %s: %s", post_id, e)
        return []


def _flatten_comments(tree: list, out: list):
    """Recursively flatten a nested comment tree."""
    for c in tree:
        if not isinstance(c, dict):
            continue
        out.append({
            "id": c.get("id"),
            "body": c.get("body", ""),
            "name": c.get("name", ""),
            "user_id": c.get("user_id"),
            "date": c.get("date", ""),
            "ancestor_path": c.get("ancestor_path", ""),
            "post_id": c.get("post_id"),
        })
        if c.get("children"):
            _flatten_comments(c["children"], out)


def reply_to_comment(post_id: int, parent_comment_id: int,
                     reply_text: str) -> dict | None:
    """Reply to a comment on a Substack post.

    Returns the created comment dict, or None on failure.
    """
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        return None

    # Substack accepts plain text and auto-wraps into ProseMirror
    payload = json.dumps({
        "body": reply_text.strip(),
        "parent_id": parent_comment_id,
    }).encode("utf-8")

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


def sync_posts_for_ios() -> int:
    """Write a posts.json file that iOS can read to show published posts.

    Returns number of posts written.
    """
    posts = get_recent_posts(limit=20)
    if not posts:
        return 0

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")

    # Enrich with URLs
    for p in posts:
        p["url"] = f"https://{subdomain}.substack.com/p/{p['slug']}"

    # Write to bridge/tasks directory where iOS can find it
    # MIRA_DIR is already Mira-bridge/ — don't double it
    from config import MIRA_DIR
    posts_file = MIRA_DIR / "tasks" / "substack_posts.json"
    posts_file.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Synced %d posts for iOS", len(posts))
    return len(posts)


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

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=state_file.parent, suffix=".tmp", prefix="comment_state_"
    )
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
    from sub_agent import claude_think
    from soul_manager import load_soul, format_soul

    cfg = _get_substack_config()
    if not cfg.get("subdomain"):
        return []

    state_file = Path(__file__).resolve().parent / "comment_state.json"
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
                    cid, comment_post_id, post["id"],
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
- Match their language (English reply to English comment, 中文回复中文评论)

Output ONLY your reply text, nothing else."""

            reply_text = claude_think(prompt, timeout=90)
            if not reply_text:
                continue

            reply_text = reply_text.strip()
            result = reply_to_comment(post["id"], comment["id"], reply_text)

            if result:
                seen_ids.add(comment["id"])
                replies_made.append({
                    "post_title": post["title"],
                    "comment_name": comment["name"],
                    "comment_body": comment["body"][:200],
                    "reply": reply_text,
                })
                log.info("Replied to %s on '%s': %s",
                         comment["name"], post["title"], reply_text[:80])

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

    # Substack accepts plain text and auto-wraps it into ProseMirror format
    payload = json.dumps({"body": comment_text.strip()}).encode("utf-8")

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
        log.info("Commented on %s (post %s): %s", post_url, post_id, comment_text[:80])
        return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:300]
        log.error("Comment on %s failed (HTTP %d): %s", post_url, e.code, error_body)
        return None
    except Exception as e:
        log.error("Comment on %s failed: %s", post_url, e)
        return None


def delete_comment(comment_id: int) -> bool:
    """Delete a comment by ID. Returns True on success."""
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not cookie:
        return False

    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/comment/{comment_id}",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        log.error("Delete comment %s failed: %s", comment_id, e)
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
            f"{base_url}/api/v1/posts/{slug}", headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("id")
    except Exception as e:
        log.debug("Direct slug lookup failed for '%s': %s", slug, e)

    # Slug may be truncated differently — search recent posts for a match
    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/posts?limit=20", headers=headers,
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


def get_published_post_count() -> int:
    """Get the number of published posts on Mira's Substack."""
    posts = get_recent_posts(limit=50)
    return len(posts)


# ---------------------------------------------------------------------------
# Publication stats tracking
# ---------------------------------------------------------------------------

def _fetch_post_detail(slug: str, subdomain: str, cookie: str) -> dict | None:
    """Fetch detailed data for a single post via slug (reactions, comments, restacks)."""
    try:
        req = urllib.request.Request(
            f"https://{subdomain}.substack.com/api/v1/posts/{slug}",
            headers={
                "Cookie": f"substack.sid={cookie}",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("Failed to fetch detail for post '%s': %s", slug, e)
        return None


def fetch_publication_stats() -> dict:
    """Fetch stats for all published articles and recent Notes.

    Queries individual post endpoints for view/like/restack data,
    reads notes_state.json for Note engagement, and saves everything
    to publication_stats.json.

    Returns the stats dict (also saved to disk).
    """
    from datetime import datetime, timezone

    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        log.error("Substack not configured — cannot fetch stats")
        return {}

    stats_dir = Path(__file__).resolve().parent
    stats_file = stats_dir / "publication_stats.json"

    # --- Articles ---
    posts = get_recent_posts(limit=50)
    articles = []
    total_views = 0
    total_likes = 0
    total_comments = 0
    total_restacks = 0
    best_title = ""
    best_views = 0

    for post in posts:
        detail = _fetch_post_detail(post.get("slug", ""), subdomain, cookie)
        if not detail:
            # Fall back to basic data from list endpoint
            articles.append({
                "id": post["id"],
                "title": post.get("title", ""),
                "slug": post.get("slug", ""),
                "views": 0,
                "likes": 0,
                "comments": post.get("comment_count", 0),
                "restacks": 0,
                "post_date": post.get("post_date", ""),
            })
            total_comments += post.get("comment_count", 0)
            continue

        views = detail.get("views", 0) or 0
        # Substack uses "reactions" for likes (heart reactions)
        reactions = detail.get("reactions", {})
        likes = reactions.get("❤", 0) if isinstance(reactions, dict) else 0
        # Also check top-level reaction_count as fallback
        if not likes:
            likes = detail.get("reaction_count", 0) or 0
        comments = detail.get("comment_count", 0) or 0
        restacks = detail.get("restacks", 0) or detail.get("restack_count", 0) or 0

        articles.append({
            "id": post["id"],
            "title": detail.get("title", post.get("title", "")),
            "slug": detail.get("slug", post.get("slug", "")),
            "views": views,
            "likes": likes,
            "comments": comments,
            "restacks": restacks,
            "post_date": detail.get("post_date", post.get("post_date", "")),
        })

        total_views += views
        total_likes += likes
        total_comments += comments
        total_restacks += restacks

        if views > best_views:
            best_views = views
            best_title = detail.get("title", post.get("title", ""))

    # --- Notes ---
    notes_file = stats_dir / "notes_state.json"
    notes_entries = []
    if notes_file.exists():
        try:
            notes_data = json.loads(notes_file.read_text(encoding="utf-8"))
            for note in notes_data.get("history", []):
                note_text = note.get("text", "")
                notes_entries.append({
                    "id": note.get("id"),
                    "text_preview": note_text[:120],
                    "likes": note.get("likes", 0),
                    "comments": note.get("comments", 0),
                    "restacks": note.get("restacks", 0),
                    "date": note.get("date", ""),
                })
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read notes_state.json: %s", e)

    # --- Summary ---
    summary_parts = [
        f"Total articles: {len(articles)}",
        f"Total views: {total_views}",
        f"Total likes: {total_likes}",
        f"Total comments: {total_comments}",
        f"Total restacks: {total_restacks}",
        f"Total notes: {len(notes_entries)}",
    ]
    if best_title:
        summary_parts.append(f"Best performing: \"{best_title}\" ({best_views} views)")

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
        "notes": notes_entries,
        "summary": ". ".join(summary_parts),
    }

    # Save atomically
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=stats_dir, suffix=".tmp", prefix="pub_stats_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, stats_file)
        log.info("Publication stats saved: %d articles, %d notes", len(articles), len(notes_entries))
    except Exception as e:
        log.warning("Stats file save failed: %s", e)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return result


# ---------------------------------------------------------------------------
# Export published articles as Markdown
# ---------------------------------------------------------------------------

def _html_to_markdown(body_html: str) -> str:
    """Convert Substack HTML to clean Markdown."""
    import html as html_mod
    import re as _re

    text = body_html
    for i in range(1, 7):
        text = _re.sub(
            rf'<h{i}[^>]*>(.*?)</h{i}>',
            lambda m, lvl=i: '#' * lvl + ' ' + m.group(1).strip(),
            text, flags=_re.DOTALL,
        )
    text = _re.sub(r'<(?:strong|b)>(.*?)</(?:strong|b)>', r'**\1**', text, flags=_re.DOTALL)
    text = _re.sub(r'<(?:em|i)>(.*?)</(?:em|i)>', r'*\1*', text, flags=_re.DOTALL)
    text = _re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=_re.DOTALL)
    text = _re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?\s*>', r'![](\1)', text)
    text = _re.sub(
        r'<blockquote[^>]*>(.*?)</blockquote>',
        lambda m: '\n> ' + m.group(1).strip().replace('\n', '\n> '),
        text, flags=_re.DOTALL,
    )
    text = _re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', text, flags=_re.DOTALL)
    text = _re.sub(r'</?[ou]l[^>]*>', '', text)
    text = _re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', text, flags=_re.DOTALL)
    text = _re.sub(r'<br\s*/?>', '\n', text)
    text = _re.sub(r'<hr[^>]*/?\s*>', '\n---\n', text)
    text = _re.sub(r'<[^>]+>', '', text)
    text = html_mod.unescape(text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def export_articles_as_markdown(output_dir: str | Path | None = None) -> list[Path]:
    """Export all published Substack articles as Markdown files.

    Each file has YAML frontmatter (title, date, url, subtitle, wordcount, cover)
    followed by the article body in Markdown.

    Returns list of written file paths.
    """
    cfg = _get_substack_config()
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")
    if not subdomain or not cookie:
        log.error("Substack not configured")
        return []

    if output_dir is None:
        output_dir = Path(__file__).resolve().parent.parent.parent / "artifacts" / "writings" / "_published"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    posts = get_recent_posts(limit=50)
    written = []

    for post in posts:
        slug = post["slug"]
        detail = _fetch_post_detail(slug, subdomain, cookie)
        if not detail:
            continue

        title = detail.get("title", slug)
        body_html = detail.get("body_html", "")
        post_date = detail.get("post_date", "")[:10]
        subtitle = detail.get("subtitle", "")
        cover = detail.get("cover_image", "")
        wordcount = detail.get("wordcount", 0)
        url = detail.get("canonical_url",
                          f"https://{subdomain}.substack.com/p/{slug}")

        md = _html_to_markdown(body_html)

        content = (
            f'---\n'
            f'title: "{title}"\n'
            f'date: {post_date}\n'
            f'url: {url}\n'
            f'subtitle: "{subtitle}"\n'
            f'wordcount: {wordcount}\n'
            f'cover: {cover}\n'
            f'---\n\n'
            f'# {title}\n\n'
            f'{md}\n'
        )

        filename = f"{post_date}_{slug}.md"
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        written.append(path)
        log.info("Exported: %s (%d words)", filename, wordcount)

    log.info("Exported %d articles to %s", len(written), output_dir)
    return written


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
    growth_state_file = Path(__file__).resolve().parent / "growth_state.json"
    if not growth_state_file.exists():
        return []
    state = json.loads(growth_state_file.read_text(encoding="utf-8"))

    reply_state_file = Path(__file__).resolve().parent / "reply_tracking.json"
    reply_state = {}
    if reply_state_file.exists():
        try:
            reply_state = json.loads(reply_state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    seen_reply_ids = set(reply_state.get("seen_reply_ids", []))
    history = state.get("comment_history", [])

    # Only check comments from last 7 days
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    new_replies = []

    for entry in history[-30:]:  # Check last 30 comments
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

        # Fetch comment thread
        try:
            r = _req.get(
                f"https://{subdomain}.substack.com/api/v1/post/{post_id}/comments"
                f"?token=&all_comments=true&sort=newest_first",
                cookies={"substack.sid": cookie},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            comments = r.json()
            if isinstance(comments, dict):
                comments = comments.get("comments", [])
        except Exception as e:
            log.debug("Comment fetch failed for post %s: %s", post_id, e)
            continue

        # Find Mira's comment and check for child replies
        _find_replies_to_comment(comments, comment_id, seen_reply_ids,
                                  new_replies, url, entry.get("text", "")[:100],
                                  post_id)

    # Save updated state
    reply_state["seen_reply_ids"] = list(seen_reply_ids)[-200:]
    reply_state["last_checked"] = datetime.now().isoformat()
    reply_state_file.write_text(
        json.dumps(reply_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if new_replies:
        log.info("Found %d new replies to Mira's comments", len(new_replies))
    return new_replies


def _find_replies_to_comment(comments: list, target_id: int, seen_ids: set,
                              out: list, post_url: str, original_text: str,
                              post_id: int):
    """Recursively search comment tree for replies to target comment."""
    for c in comments:
        if not isinstance(c, dict):
            continue
        # Check if this comment is a reply to Mira's comment
        ancestor = c.get("ancestor_path", "")
        cid = c.get("id")
        if (ancestor and str(target_id) in ancestor.split("/")
                and cid and cid not in seen_ids):
            # Skip Mira's own replies
            name = c.get("name", "").lower()
            if name not in ("mira", "infinite mira", "uncountable mira"):
                out.append({
                    "post_url": post_url,
                    "original_comment": original_text,
                    "reply_name": c.get("name", ""),
                    "reply_body": c.get("body", ""),
                    "comment_id": cid,
                    "parent_comment_id": target_id,
                    "post_id": post_id,
                })
            seen_ids.add(cid)
        # Recurse into children
        if c.get("children"):
            _find_replies_to_comment(c["children"], target_id, seen_ids,
                                      out, post_url, original_text, post_id)


def reply_to_outbound_thread(post_id: int, parent_comment_id: int,
                              reply_text: str, post_url: str) -> dict | None:
    """Reply to someone who replied to Mira's comment on another publication."""
    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")
    if not cookie:
        return None

    parsed = urllib.parse.urlparse(post_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    payload = json.dumps({
        "body": reply_text.strip(),
        "parent_id": parent_comment_id,
    }).encode("utf-8")

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
