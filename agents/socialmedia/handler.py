"""Publisher agent — publish content to external platforms.

Supports: Substack (articles), with planned support for Instagram, Threads, etc.

Usage from task_worker:
    from handler import handle as publish_handle
    publish_handle(workspace, task_id, content, sender, thread_id)
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from config import ARTIFACTS_DIR, WRITINGS_OUTPUT_DIR, SUBSTACK_PUBLISHING_DISABLED, MIRA_ROOT
from publish.preflight import preflight_check
from llm import claude_think

log = logging.getLogger("publisher")

# ---------------------------------------------------------------------------
# Content guard — block publishing error messages (CLAUDE.md: 发布前必须确认内容)
# ---------------------------------------------------------------------------

# Keywords that indicate the "content" is actually an error message, not real content
_ERROR_KEYWORDS = [
    "找不到", "错误", "失败", "exception", "traceback", "error",
    "failed", "not found", "无法", "没有找到", "cannot", "unable",
]
# Real articles are long; anything shorter than this is almost certainly not publishable
_MIN_PUBLISH_CHARS = 200
_PREFLIGHT_CACHE = ".socialmedia_preflight.json"


def _content_looks_like_error(text: str) -> tuple[bool, str]:
    """Return (True, reason) if text looks like an error message, not publishable content.

    This is the code-level enforcement of CLAUDE.md rule:
    'Substack 发布前必须确认内容 — 如果内容看起来是错误信息或过短，强制拒绝发布'
    """
    stripped = text.strip()
    if len(stripped) < _MIN_PUBLISH_CHARS:
        return True, f"内容过短（{len(stripped)} 字符，最少需要 {_MIN_PUBLISH_CHARS}）"
    lower = stripped.lower()
    for kw in _ERROR_KEYWORDS:
        if kw in lower:
            # Only flag if the error keyword appears early (first 20% of content)
            # to avoid false positives for articles that discuss errors
            early_section = lower[: max(200, len(lower) // 5)]
            if kw in early_section:
                return True, f"内容包含错误关键词「{kw}」，疑似上一步的错误信息"
    return False, ""


# Platform registry — add new platforms here
PLATFORMS = {
    "substack": {
        "name": "Substack",
        "content_types": ["article", "essay", "blog", "newsletter"],
    },
    "substack_note": {
        "name": "Substack Notes",
        "content_types": ["note", "notes", "short"],
    },
    # Future:
    # "instagram": {"name": "Instagram", "content_types": ["photo", "reel"]},
    # "threads":   {"name": "Threads",   "content_types": ["text", "photo"]},
}


def handle(workspace: Path, task_id: str, content: str,
           sender: str, thread_id: str, **kwargs) -> str | None:
    """Handle a publish request. Returns summary or None on failure."""

    # Guard: Substack publishing disabled
    if SUBSTACK_PUBLISHING_DISABLED:
        msg = "Substack 发布已被禁用（config.yml: publishing.substack_disabled=true）。如需重新启用，请修改 config.yml。"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg

    # Step 1: Figure out what to publish and where
    cached = _load_preflight_cache(workspace)
    if cached:
        plan = cached.get("plan", {})
        article_text = cached.get("article_text", "")
    else:
        plan = _plan_publish(content)
        article_text = ""
    if not plan:
        return None

    platform = plan.get("platform", "substack")
    source = plan.get("source", "")
    title = plan.get("title", "")
    subtitle = plan.get("subtitle", "")

    log.info("Publishing to %s: title='%s' source='%s'", platform, title, source)

    # Step 2: Find the content to publish
    if not article_text:
        article_text = _resolve_content(source, content)
    if not article_text:
        msg = f"找不到要发布的内容: {source}"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg

    # Step 2b: Content guard — HARD block if content looks like an error message.
    # This is the code-level enforcement of CLAUDE.md: 发布前必须确认内容.
    # Guards against pipeline errors (e.g., podcast agent returns error string,
    # which gets chained to publish agent and published verbatim).
    is_error, error_reason = _content_looks_like_error(article_text)
    if is_error:
        msg = (f"🚫 发布被拒绝：{error_reason}。\n"
               f"内容预览（前 150 字符）：{article_text[:150]!r}\n\n"
               f"请检查上一步是否成功完成，确认内容正确后再重试。")
        log.error("PUBLISH BLOCKED (content guard): %s | preview: %s",
                  error_reason, article_text[:100])
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return None  # None → task_worker marks as status="error"

    # Step 3: Dispatch to platform
    if platform == "substack":
        # Full autonomy mode (2026-04-07): publish directly without user approval.
        # Safety net: content guard above already blocked error-shaped payloads;
        # publish_to_substack() also enforces preflight + cooldown.
        from substack import publish_to_substack
        log.info("Auto-publishing manual request '%s' to Substack", title)
        result = publish_to_substack(
            title=title,
            subtitle=subtitle,
            article_text=article_text,
            workspace=workspace,
        )
    elif platform == "substack_note":
        result = _handle_note(content, article_text, workspace)
    else:
        result = f"平台 '{platform}' 暂不支持"

    actual_result = result[len("NEEDS_APPROVAL:"):] if result.startswith("NEEDS_APPROVAL:") else result
    (workspace / "output.md").write_text(actual_result, encoding="utf-8")
    return result


def preflight(workspace: Path, task_id: str, content: str,
              sender: str, thread_id: str, **kwargs) -> tuple[bool, str]:
    """Execution preflight for publish actions before side effects happen."""
    plan = _plan_publish(content)
    if not plan:
        return False, "PREFLIGHT BLOCKED [publish]: could not determine publish target"

    platform = plan.get("platform", "substack")
    title = plan.get("title", "") or "untitled"
    source = plan.get("source", "")
    article_text = _resolve_content(source, content)
    if not article_text:
        return False, f"PREFLIGHT BLOCKED [publish]: 找不到要发布的内容: {source}"

    is_error, error_reason = _content_looks_like_error(article_text)
    if is_error:
        return False, f"PREFLIGHT BLOCKED [publish]: {error_reason}"

    action_type = "broadcast" if platform == "substack_note" else "publish"
    result = preflight_check(
        action_type,
        {
            "instruction": content,
            "title": title,
            "content": article_text,
            "platform": platform,
            "channel": platform,
        },
    )
    if result.passed:
        _write_preflight_cache(workspace, plan, article_text)
        return True, ""
    return False, result.summary()


def _write_preflight_cache(workspace: Path, plan: dict, article_text: str) -> None:
    cache_file = workspace / _PREFLIGHT_CACHE
    cache_file.write_text(
        json.dumps({"plan": plan, "article_text": article_text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_preflight_cache(workspace: Path) -> dict | None:
    cache_file = workspace / _PREFLIGHT_CACHE
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        cache_file.unlink()
    except OSError:
        pass
    return data


def _handle_note(content: str, inline_text: str | None,
                 workspace: Path) -> str:
    """Handle a Substack Notes publish request.

    Supports:
    - Posting a specific Note text
    - Backfilling Notes for all past articles
    - Posting a Note for a specific article
    """
    from notes import post_note, backfill_notes_for_articles

    # Check if this is a backfill request
    backfill_keywords = ["之前", "过去", "所有", "backfill", "all", "past",
                         "以前的文章", "历史"]
    is_backfill = any(kw in content.lower() for kw in backfill_keywords)

    if is_backfill:
        results = backfill_notes_for_articles(dry_run=False)
        lines = ["## Notes 补发结果\n"]
        for r in results:
            status = "已发布" if r["posted"] else "跳过"
            lines.append(f"- [{status}] {r['title']}")
            if r.get("note_text"):
                lines.append(f"  Note: {r['note_text'][:100]}...")
        if not results:
            lines.append("所有文章都已有 Notes，无需补发。")
        return "\n".join(lines)

    # Otherwise post the inline text as a Note
    if inline_text and len(inline_text) > 10:
        result = post_note(inline_text)
        if result:
            return f"已发布 Note (id={result.get('id')}): {inline_text[:100]}"
        return "Note 发布失败"

    return "未找到要发布的 Note 内容"


def _plan_publish(content: str) -> dict | None:
    """Use LLM to extract publish intent: platform, source file, title."""
    prompt = f"""Extract the publishing intent from this message. Return ONLY valid JSON.

Message: {content[:500]}

Return JSON with:
- "platform": one of {list(PLATFORMS.keys())} (default "substack")
  Use "substack_note" if the message is about posting Notes, short-form content,
  or backfilling Notes for existing articles.
  Use "substack" for full articles/essays.
- "source": file path or project name mentioned (e.g. "自由意志" or a path), or "" if not specified
- "title": article title to use, or "" to auto-detect
- "subtitle": subtitle if mentioned, or ""

Example: {{"platform": "substack", "source": "自由意志", "title": "On Free Will", "subtitle": ""}}
Example: {{"platform": "substack_note", "source": "", "title": "", "subtitle": ""}}"""

    result = claude_think(prompt, timeout=90, tier="light")
    if not result:
        return None

    match = re.search(r'\{.*?\}', result, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"platform": "substack", "source": "", "title": "", "subtitle": ""}


_MIN_ARTICLE_BYTES = 3000  # stubs are <500 bytes; real revised articles are >>3000


def _find_article_in_project(project_dir: Path) -> str | None:
    """Find the publishable article in a writing project directory."""
    # final.md is the gold standard
    final = project_dir / "final.md"
    if final.exists():
        return final.read_text(encoding="utf-8")
    # draft_r2.md+ are the actual revised articles written by Claude
    drafts_dir = project_dir / "drafts"
    if drafts_dir.exists():
        candidates = [
            f for f in sorted(drafts_dir.glob("draft_r[2-9].md"), reverse=True)
            if f.stat().st_size >= _MIN_ARTICLE_BYTES
        ]
        if candidates:
            return candidates[0].read_text(encoding="utf-8")
        # R*_revised.md as fallback
        rev_candidates = sorted(drafts_dir.glob("R*_revised.md"), reverse=True)
        if rev_candidates:
            return rev_candidates[0].read_text(encoding="utf-8")
    return None


def _resolve_content(source: str, original_msg: str) -> str | None:
    """Find the article content to publish — search writings, artifacts, or use inline."""

    # Check if source is a direct file path (absolute)
    if source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")

    writings_dir = ARTIFACTS_DIR / "writings"

    # If source looks like a relative path (e.g. "drafts/draft_r2.md"),
    # search for it inside project directories
    if source and "/" in source:
        file_name = source.rsplit("/", 1)[-1]
        if writings_dir.exists():
            for candidate in writings_dir.iterdir():
                if not candidate.is_dir() or candidate.name.startswith("_"):
                    continue
                target = candidate / source
                if target.exists():
                    return target.read_text(encoding="utf-8")
                # Also try just the filename under drafts/
                target2 = candidate / "drafts" / file_name
                if target2.exists() and target2.stat().st_size >= _MIN_ARTICLE_BYTES:
                    return target2.read_text(encoding="utf-8")

    # Search in writings output by project name
    if source and writings_dir.exists():
        # Normalize: strip path separators in case source is a fragment
        search_term = source.replace("/", " ").replace("_", "-").lower()
        for candidate in writings_dir.iterdir():
            if not candidate.is_dir() or candidate.name.startswith("_"):
                continue
            if source.lower() in candidate.name.lower() or search_term in candidate.name.lower():
                article = _find_article_in_project(candidate)
                if article:
                    return article

    # Check for chained output from previous agent step
    separator = "--- 上一步的输出 ---"
    if separator in original_msg:
        return original_msg.split(separator, 1)[1].strip()

    # Check if content is inline (message contains the article itself)
    if len(original_msg) > 500:
        return original_msg

    return None


# ---------------------------------------------------------------------------
# Post-publish pipeline — hardcoded correct sequence
# ---------------------------------------------------------------------------

def post_publish_pipeline(slug: str, title: str, article_text: str):
    """Hardcoded post-publish pipeline. No guessing allowed.

    Correct sequence after publishing an article to Substack:
    1. Generate podcast (conversation mode, BOTH zh and en)
    2. Notify user to listen and confirm before RSS publish
    3. Notes promotion is already queued by publish_to_substack()

    This function handles step 1-2. Step 3 is automatic.
    """
    import sys
    from pathlib import Path
    podcast_dir = str(Path(__file__).resolve().parent.parent / "podcast")
    shared_dir = str(Path(__file__).resolve().parent.parent .parent / "lib")
    if podcast_dir not in sys.path:
        sys.path.insert(0, podcast_dir)
    if shared_dir not in sys.path:
        sys.path.insert(0, shared_dir)

    from handler import generate_conversation_for_article  # podcast handler, NOT this file
    from config import ARTIFACTS_DIR

    results = {}

    # Generate BOTH languages
    for lang in ["en", "zh"]:
        log.info("Post-publish: generating %s podcast for '%s'", lang, title)
        try:
            result = generate_conversation_for_article(
                article_text=article_text,
                title=title,
                lang=lang,
            )
            results[lang] = result
            log.info("Post-publish: %s podcast → %s", lang, result)
        except Exception as e:
            log.error("Post-publish: %s podcast failed: %s", lang, e)
            results[lang] = None

    # Notify user — do NOT auto-publish to RSS
    summary_lines = ["Podcast 已生成，等待试听确认："]
    for lang, path in results.items():
        status = f"✅ {path}" if path else "❌ 生成失败"
        summary_lines.append(f"  {lang.upper()}: {status}")
    summary_lines.append(f"\n确认后回复 'publish podcast {slug}' 发布到 RSS。")

    log.info("\n".join(summary_lines))
    return results
