"""Substack publishing and audio upload operations.

Handles article publishing (draft creation + publish) and audio embedding.
"""

import json
import logging
import os
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

log = logging.getLogger("publisher.substack")


def _log_scaffolding_rejection(agent: str, task_id: str, guard_name: str, content: str) -> None:
    try:
        from datetime import datetime as _dt, timezone as _tz
        from config import MIRA_ROOT

        log_path = MIRA_ROOT / "logs" / "scaffolding_rejections.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _dt.now(_tz.utc).isoformat(),
            "agent": agent,
            "task_id": task_id,
            "guard_name": guard_name,
            "content_length": len(content),
            "first_100_chars": content[:100],
        }
        with open(log_path, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as _e:
        log.debug("scaffolding rejection log failed: %s", _e)


def _get_substack_config(*, publication: str = "") -> dict:
    """Load Substack credentials from secrets.yml."""
    from substack import _get_substack_config as _cfg

    return _cfg(publication=publication)


def publish_to_substack(title: str, subtitle: str, article_text: str, workspace: Path, *, publication: str = "") -> str:
    """Publish an article to Substack. Returns status message.

    Args:
        publication: optional key under api_keys in secrets.yml (e.g. "substack_books").
                     Defaults to "substack" (primary publication).
    """
    from substack import PublishBlockedError, _content_has_unverified_security_claims, _security_preamble
    from substack_format import _md_to_html, _html_to_prosemirror, _get_cover_image, _upload_image_to_substack

    # Safety: refuse to publish when running under pytest. Tests must mock
    # this function explicitly. Added 2026-04-07 after a test harness path
    # accidentally published a bogus "Approved Title" draft to production.
    import os as _os

    if _os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in _os.environ.get("_", ""):
        msg = (
            "[TEST-GUARD] publish_to_substack refused: running under pytest. "
            "Tests must monkeypatch publish_to_substack."
        )
        log.error(msg)
        return msg

    # Preflight check
    try:
        from publish.preflight import preflight_check

        pf = preflight_check(
            "publish",
            {
                "instruction": f"Publish '{title}' to Substack",
                "title": title,
                "content": article_text,
                "platform": "substack",
            },
        )
        if not pf.passed:
            msg = f"Preflight blocked publish: {'; '.join(pf.blocking_reasons)}"
            log.error(msg)
            _log_scaffolding_rejection("publisher", title, "preflight_check", article_text)
            return msg
    except ImportError:
        pass

    if _content_has_unverified_security_claims(article_text):
        raise PublishBlockedError(
            "Security claim detected without [verified: <source>] tag" " — manual review required before publishing."
        )

    # Guard: respect the global kill switch
    try:
        from config import SUBSTACK_PUBLISHING_DISABLED

        if SUBSTACK_PUBLISHING_DISABLED:
            msg = "Substack \u53d1\u5e03\u5df2\u88ab\u7981\u7528\uff08config.yml: publishing.substack_disabled=true\uff09\u3002"
            log.warning(msg)
            return msg
    except ImportError:
        pass

    # Guard: enforce cooldown.
    #
    # Source of truth: the Substack API's posts feed for this publication.
    # Both the calendar-day check and the absolute-minutes check derive from
    # the same `post_date` field on the most recent published post.
    #
    # Pre-2026-04-27: the day check used the local catalog. Catalog writes
    # could miss (the 2026-04-12 marginalmira publish of "刘以鬯" never made
    # it to catalog.jsonl, so a same-publication republish that day would
    # have been silently allowed). The minute check already used the API;
    # promoting the day check to the same source closes that gap.
    #
    # Catalog still acts as a fallback when the API call fails. Bosons
    # incident (2026-04-17) showed that swallowing a cooldown exception is
    # what enables a publish gap, so unhandled exceptions still hard-fail
    # rather than proceed.
    PUBLISH_COOLDOWN_DAYS = 1
    MIN_MINUTES_BETWEEN_PUBLISHES = 180  # 3 hours, regardless of calendar day
    log.info("COOLDOWN CHECK start: title=%r", title[:60])
    try:
        from datetime import datetime as _dt, timezone as _tz
        from substack import _get_substack_config as _cfg_fn

        _cfg = _cfg_fn(publication=publication)
        _subdomain = _cfg.get("subdomain", "")
        log.info("COOLDOWN CHECK target_subdomain=%s", _subdomain)

        # --- Primary source: Substack API for this publication ---
        import urllib.request as _ur

        _last_dt = None
        _api_ok = False
        try:
            _req = _ur.Request(
                f"https://{_subdomain}.substack.com/api/v1/posts?limit=1",
                headers={
                    "Cookie": f"substack.sid={_cfg['cookie']}; connect.sid={_cfg['cookie']}",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            with _ur.urlopen(_req, timeout=10) as _resp:
                _posts = json.loads(_resp.read().decode("utf-8"))
            log.info("COOLDOWN CHECK api: fetched %d posts", len(_posts) if _posts else 0)
            if _posts:
                _last_ts = _posts[0].get("post_date") or _posts[0].get("published_at")
                if _last_ts:
                    _last_dt = _dt.fromisoformat(_last_ts.replace("Z", "+00:00"))
                    _api_ok = True
                else:
                    log.warning("COOLDOWN CHECK api: post has no post_date/published_at field")
            else:
                _api_ok = True  # empty list is a valid answer (no prior posts)
        except Exception as _api_e:
            log.warning("COOLDOWN CHECK api: failed (%s) — falling back to catalog", _api_e)

        # --- Fallback: local catalog filtered to this publication ---
        if not _api_ok:
            from memory.soul import catalog_list

            def _matches_target(entry: dict) -> bool:
                if not _subdomain:
                    return True
                url = entry.get("url", "")
                if not url:
                    return True
                return _subdomain in url

            _cat_pubs = [
                e for e in catalog_list() if e.get("status") == "published" and e.get("date") and _matches_target(e)
            ]
            log.info("COOLDOWN CHECK catalog fallback: %d entries", len(_cat_pubs))
            if _cat_pubs:
                _latest = max(e["date"] for e in _cat_pubs)
                # Catalog dates are local-date strings; treat as midnight UTC
                # for comparison purposes — slightly conservative, which is
                # the correct side to err on for a cooldown gate.
                _last_dt = _dt.strptime(_latest[:10], "%Y-%m-%d").replace(tzinfo=_tz.utc)
            else:
                # Both sources returned nothing usable. Refuse to proceed
                # rather than allow an unguarded publish (Bosons doctrine).
                raise RuntimeError(
                    "Both Substack API and local catalog returned no usable "
                    "data — refusing to publish without a cooldown signal."
                )

        # --- Apply both cooldown rules to _last_dt ---
        if _last_dt is None:
            log.info("COOLDOWN CHECK: no prior publishes on this publication — proceeding")
        else:
            _now_utc = _dt.now(_tz.utc)
            _minutes_since = (_now_utc - _last_dt).total_seconds() / 60.0
            _days_since = (_now_utc.date() - _last_dt.date()).days
            log.info(
                "COOLDOWN CHECK metrics: last=%s minutes_since=%.1f days_since=%d",
                _last_dt.isoformat(),
                _minutes_since,
                _days_since,
            )

            if _minutes_since < MIN_MINUTES_BETWEEN_PUBLISHES:
                msg = (
                    f"发布被拦截：距上次发布仅 "
                    f"{_minutes_since:.0f} 分钟，最小间隔为 "
                    f"{MIN_MINUTES_BETWEEN_PUBLISHES} 分钟。"
                )
                log.warning(
                    "GUARD_FIRED",
                    extra={
                        "guard": "cooldown_minutes",
                        "agent": "publisher",
                        "task_id": title[:60],
                        "reason": f"minutes_since={_minutes_since:.0f}",
                    },
                )
                _log_scaffolding_rejection("publisher", title, "cooldown", article_text)
                return msg

            if _days_since < PUBLISH_COOLDOWN_DAYS:
                msg = (
                    f"发布被拦截：距上次发布仅 {_days_since} 天，"
                    f"冷却期为 {PUBLISH_COOLDOWN_DAYS} 天。请等待后再发布。"
                )
                log.warning(
                    "GUARD_FIRED",
                    extra={
                        "guard": "cooldown_date",
                        "agent": "publisher",
                        "task_id": title[:60],
                        "reason": f"days_since={_days_since}",
                    },
                )
                _log_scaffolding_rejection("publisher", title, "cooldown", article_text)
                return msg

            log.info("COOLDOWN CHECK: PASSED (minutes_since=%.1f, days_since=%d)", _minutes_since, _days_since)
    except Exception as e:
        # Do NOT silently proceed on exception. Bosons incident proved that
        # a swallowed exception on the cooldown check becomes a publish gap.
        log.error("PUBLISH ABORTED — cooldown check failed: %s", e, exc_info=True)
        return f"发布被拦截：冷却期检查失败，安全起见中止发布。{e}"

    cfg = _get_substack_config(publication=publication)
    subdomain = cfg.get("subdomain", "")
    cookie = cfg.get("cookie", "")

    if not subdomain or not cookie:
        return (
            "Substack \u672a\u914d\u7f6e\u3002\u8bf7\u5728 secrets.yml \u6dfb\u52a0 cookie:\n\n"
            "\u83b7\u53d6\u65b9\u6cd5:\n"
            "1. Chrome \u6253\u5f00 substack.com\uff0c\u786e\u4fdd\u5df2\u767b\u5f55\n"
            "2. Cmd+Option+I \u6253\u5f00 DevTools\n"
            "3. Application tab \u2192 Cookies \u2192 substack.com\n"
            "4. \u590d\u5236 substack.sid \u7684 Value\n"
            "5. \u7c98\u8d34\u5230 secrets.yml:\n"
            "   substack:\n"
            "     subdomain: your-blog\n"
            "     cookie: \u7c98\u8d34\u7684\u503c"
        )

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

    _has_cjk = bool(_re_lang.search(r"[\u4e00-\u9fff]", title))
    _body_sample = article_text[:2000]
    _body_cjk_ratio = len(_re_lang.findall(r"[\u4e00-\u9fff]", _body_sample)) / max(len(_body_sample), 1)
    _body_is_cjk = _body_cjk_ratio > 0.1
    if _has_cjk != _body_is_cjk:
        from llm import claude_think as _ct

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
        from llm import claude_think

        _lang_hint = "\u4e2d\u6587" if _body_is_cjk else "English"
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

    # Strip metadata that should never be published
    import re as _re_strip

    # YAML frontmatter
    article_text = _re_strip.sub(r"^---\n.*?\n---\n", "", article_text, flags=_re_strip.DOTALL)
    # Revision table at end
    article_text = _re_strip.sub(
        r"\n---\s*\n+##?\s*\u4fee\u6539\u8bb0\u5f55.*", "", article_text, flags=_re_strip.DOTALL
    )
    article_text = _re_strip.sub(
        r"\n---\s*\n+##?\s*Changelog.*", "", article_text, flags=_re_strip.DOTALL | _re_strip.IGNORECASE
    )
    # Review-notes preamble/trailer. Added 2026-04-17 after Bosons incident:
    # the published article started with "# Title\n\nBased on the three editor
    # reviews..." preamble which the older regexes did not catch. Strip any
    # paragraph that starts with these markers and runs until the next H1.
    _REVIEW_MARKERS = (
        r"Based on the (?:three |two |four )?(?:editor|reviewer)s?[^\n]*reviews?",
        r"Here (?:is|'s) the (?:complete )?revised draft",
        r"主要(?:的)?(?:改动|修改)(?:说明|如下|：|:)",
        r"读完[^\n]{0,10}编辑的意见",
        r"以下是完整修订稿",
    )
    for _marker in _REVIEW_MARKERS:
        # Strip from marker line until next H1 (# heading) or end of document
        article_text = _re_strip.sub(
            rf"(?:^|\n){_marker}[^\n]*(?:\n(?!# ).*?)*?(?=\n# |\Z)",
            "\n",
            article_text,
            flags=_re_strip.DOTALL | _re_strip.IGNORECASE,
        )
    # Collapse duplicate consecutive H1 titles (Bosons had "# Title\n...\n# Title")
    _first_h1 = _re_strip.match(r"#\s+([^\n]+)", article_text)
    if _first_h1:
        _escaped = _re_strip.escape(_first_h1.group(1).strip())
        article_text = _re_strip.sub(rf"(?<=\n)#\s+{_escaped}\s*\n", "", article_text, count=3)
    # Line-level metadata
    article_text = _re_strip.sub(r"^#\s*\u4fee\u8ba2\u7a3f.*?\n", "", article_text)
    article_text = _re_strip.sub(r"^#\s*\u521d\u7a3f.*?\n", "", article_text)
    article_text = _re_strip.sub(r"^\u65e5\u671f[\uff1a:].*?\n", "", article_text)
    article_text = _re_strip.sub(r"^\u5b57\u6570[\uff1a:].*?\n", "", article_text)
    article_text = _re_strip.sub(r"^\u57fa\u4e8e[\uff1a:].*?\n", "", article_text)
    article_text = article_text.strip()

    # English-only growth target guard (active 2026-04-11 → 2026-05-11).
    # Only applies to the PRIMARY publication (uncountablemira). Secondary
    # publications like substack_books (marginalmira) have their own audience
    # and intentionally publish Chinese-language content — the primary-pub
    # growth target must not block them. (2026-04-19 fix: this guard was
    # silently blocking 22 weeks of book reviews from ever being published.)
    try:
        from datetime import date as _date

        _is_primary_pub = not publication or publication == "substack"
        if _is_primary_pub and _date.today() < _date(2026, 5, 12):
            _cjk_in_body = len(_re_strip.findall(r"[\u4e00-\u9fff]", article_text[:3000]))
            if _cjk_in_body > 200:
                msg = (
                    f"发布被拦截：English-only growth target 生效期间（至 2026-05-11），"
                    f"正文 CJK 字符数 {_cjk_in_body} > 200。请先翻译为英文。"
                )
                log.warning("LANGUAGE GUARD BLOCK: %s", msg)
                return msg
    except Exception as _lg_e:
        log.warning("Language guard failed (proceeding): %s", _lg_e)

    # Auto-append subscribe CTA footer. Research (2026-04-16): articles without
    # an explicit conversion invitation underperform.
    #
    # 2026-04-28 rewrite: was a single fixed template repeated on every article;
    # readers seeing multiple essays saw the same paragraph each time, which
    # registers as spam regardless of content quality. Replaced with a 5-line
    # rotation pool per language, picked at random per publish. None of the
    # rotated lines now lead with "I'm an AI agent" — that disclosure stays
    # public on profile/about/article body, where readers actively look it
    # up; pushing it in every CTA was costing conversion (per WA 2026-04-28).
    _has_existing_cta = any(
        marker in article_text[-600:].lower()
        for marker in ("subscribe", "订阅", "subscribing", "get the next", "下一篇")
    )
    if not _has_existing_cta:
        import random as _random

        _CTA_POOL_EN = [
            "Subscribe and the next one shows up in your inbox. About two a week, "
            "mostly on silent failure modes in AI systems — what looks fine right up "
            "until it doesn't.",
            "I write when I find something I haven't seen written down yet. "
            "Subscribe if that's worth a slot in your inbox; runs about twice a week.",
            "If you've ever shipped a system that passed every check and broke "
            "anyway, this newsletter's for you. Two essays a week, give or take.",
            "Monitoring that lies, evals that drift, priors that don't update. "
            "If that's the conversation you want in your inbox, subscribe.",
            "Roughly two essays a week on what breaks quietly inside AI systems. " "Subscribe to get the next one.",
        ]
        _CTA_POOL_ZH = [
            "订阅一下，下一篇会到你邮箱。一周大约两篇，主要写 AI 系统里那些静默失败" "——表面看一切正常，直到不正常。",
            "我看到没人写过的角度才动笔。觉得值得占你收件箱一格就订一下，" "大约每周两篇。",
            "如果你做过那种'每个检查都过了但还是炸了'的系统，" "这个 newsletter 是写给你的。一周两篇左右。",
            "监控显示一切正常的失败、漂走的 evals、不更新的 prior" "——如果这是你想读的对话，订阅。",
            "一周两篇左右，写 AI 系统里那些静默崩坏的方式。订阅获取下一篇。",
        ]
        _pool = _CTA_POOL_ZH if _body_is_cjk else _CTA_POOL_EN
        _cta_body = _random.choice(_pool)
        _cta = f"\n\n---\n\n*{_cta_body}*"
        log.info("CTA picked from %s pool, %d chars", "ZH" if _body_is_cjk else "EN", len(_cta_body))
        article_text = article_text + _cta

    # Convert markdown to HTML
    body_html = _md_to_html(article_text)

    # Save HTML preview
    (workspace / "preview.html").write_text(f"<h1>{title}</h1>\n{body_html}", encoding="utf-8")

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
            return "\u521b\u5efa Substack \u8349\u7a3f\u5931\u8d25\uff1a\u6ca1\u6709\u8fd4\u56de draft ID"

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
        result = f"\u5df2\u53d1\u5e03\u5230 Substack!\n\u6807\u9898: {title}\n\u94fe\u63a5: {post_url}"
        log.info("Published to Substack: %s", post_url)

        # Save result
        (workspace / "published.json").write_text(
            json.dumps(
                {
                    "platform": "substack",
                    "title": title,
                    "url": post_url,
                    "draft_id": draft_id,
                    "status": "success",
                    "verification_chain": [
                        {
                            "check": "content_looks_like_error",
                            "proxy_for": "content is not garbled output",
                            "assumption": "error-shaped text covers all failure modes",
                        },
                        {
                            "check": "preflight_check",
                            "proxy_for": "payload is valid for Substack API",
                            "assumption": "schema validity implies semantic correctness",
                        },
                        {
                            "check": "file_exists",
                            "proxy_for": "article was written as intended",
                            "assumption": "non-empty file equals correct content",
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Add to content catalog
        try:
            from memory.soul import catalog_add

            catalog_add(
                {
                    "type": "article",
                    "title": title,
                    "path": str(workspace),
                    "topics": [],  # will be enriched by smart_classify
                    "status": "published",
                    "substack_id": draft_id,
                    "url": post_url,
                    "description": (subtitle or "")[:200],
                }
            )
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
                f'---\ntitle: "{title}"\ndate: {pub_date}\nurl: {post_url}\n---\n\n' f"# {title}\n\n{article_text}\n",
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
            result += "\n\nNotes \u5df2\u52a0\u5165\u961f\u5217\uff0c\u5c06\u5728 growth cycle \u4e2d\u53d1\u51fa"
        except Exception as e:
            log.warning("Auto Note queue failed (non-fatal): %s", e)

        return result

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Substack API error (HTTP %d): %s", e.code, error_body)
        if e.code == 401 or e.code == 403:
            return (
                "Substack \u8ba4\u8bc1\u5931\u8d25\uff0ccookie \u5df2\u8fc7\u671f\u3002\n\n"
                "\u91cd\u65b0\u83b7\u53d6: Chrome \u2192 substack.com \u2192 Cmd+Option+I \u2192 "
                "Application \u2192 Cookies \u2192 \u590d\u5236 substack.sid \u2192 "
                "\u7c98\u8d34\u5230 secrets.yml \u7684 cookie \u5b57\u6bb5"
            )
        return f"Substack \u53d1\u5e03\u5931\u8d25 (HTTP {e.code}): {error_body[:200]}"
    except Exception as e:
        log.error("Substack publish failed: %s", e)
        return f"Substack \u53d1\u5e03\u5931\u8d25: {e}"


# ---------------------------------------------------------------------------
# Audio upload
# ---------------------------------------------------------------------------


def upload_audio_to_post(mp3_path: Path, post_id: int | str, label: str | None = None) -> bool:
    """Upload an MP3 and embed it as an audio player block in the post body."""
    raise RuntimeError(
        "upload_audio_to_post() is DISABLED. "
        "Audio is published to RSS feeds via podcast/rss.py, NOT embedded in Substack posts. "
        "Never upload audio to Substack."
    )
    # Original docstring continuation below (dead code):
    """

    Flow:
    1. POST /api/v1/audio/upload  -> presigned S3 URL + media_id
    2. PUT file to S3             -> collect ETags
    3. POST /transcode            -> triggers S3 CompleteMultipartUpload + transcoding
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
    params = urllib.parse.urlencode(
        {
            "filetype": "audio/mpeg",
            "fileSize": file_size,
            "fileName": file_name,
            "post_id": post_id,
        }
    )
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
        log.info("Got upload URL for post %s, media_id=%s, %d parts", post_id, media_id, len(s3_urls))

        # Step 2: Upload file parts to S3
        file_data = mp3_path.read_bytes()
        multipart_upload_id = data.get("multipartUploadId", "")
        existing_etags = [p.get("etag") for p in media_upload.get("parts", []) if p.get("etag")]
        etags = list(existing_etags)

        if len(s3_urls) == 1:
            s3_req = urllib.request.Request(
                s3_urls[0],
                data=file_data,
                method="PUT",
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
                    url,
                    data=chunk,
                    method="PUT",
                    headers={"Content-Type": "audio/mpeg"},
                )
                with urllib.request.urlopen(s3_req, timeout=120) as s3_resp:
                    etag = s3_resp.headers.get("ETag", "")
                    etags.append(etag)
                log.info("Part %d/%d uploaded (%d bytes)", i + 1, len(s3_urls), len(chunk))

        # Step 3: Call transcode endpoint (triggers S3 CompleteMultipartUpload + processing)
        import time as _time

        transcode_url = f"{base_url}/api/v1/audio/upload/{media_id}/transcode"
        transcode_body = json.dumps(
            {
                "duration": None,
                "multipart_upload_id": multipart_upload_id,
                "multipart_upload_etags": etags,
            }
        ).encode()
        json_headers = {**headers, "Content-Type": "application/json"}
        req = urllib.request.Request(
            transcode_url,
            data=transcode_body,
            headers=json_headers,
            method="POST",
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
            f"{base_url}/api/v1/drafts/{post_id}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            draft = json.loads(resp.read().decode("utf-8"))

        body_raw = draft.get("draft_body") or draft.get("body") or "{}"
        body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw

        # Resolve duration from poll result or media upload object
        duration = 0.0
        try:
            req = urllib.request.Request(
                f"{base_url}/api/v1/audio/upload/{media_id}",
                headers=headers,
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
            data=put_body,
            headers=json_headers,
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            pass
        log.info("Embedded audio node in post body")

        # Step 7: Publish silently (no email)
        pub_body = json.dumps({"should_send_email": False}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/v1/drafts/{post_id}/publish",
            data=pub_body,
            headers=json_headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            log.info("Published embedded audio to post %s", post_id)

        log.info("Audio uploaded to post %s: %s (%d KB)", post_id, file_name, file_size // 1024)
        return True

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.error("Audio upload failed (HTTP %d): %s", e.code, error_body)
        return False
    except Exception as e:
        log.error("Audio upload failed: %s", e)
        return False


def sync_posts_for_ios() -> int:
    """Write a posts.json file that iOS can read to show published posts.

    Returns number of posts written.
    """
    from substack_stats import get_recent_posts

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
    posts_file.parent.mkdir(parents=True, exist_ok=True)
    posts_file.write_text(
        json.dumps(posts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Synced %d posts for iOS", len(posts))
    return len(posts)
