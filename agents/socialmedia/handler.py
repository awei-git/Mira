"""Publisher agent — publish content to external platforms.

Supports: Substack (articles), with planned support for Instagram, Threads, etc.

Usage from task_worker:
    from handler import handle as publish_handle
    publish_handle(workspace, task_id, content, sender, thread_id)
"""
import json
import logging
import re
from pathlib import Path

from config import ARTIFACTS_DIR, WRITINGS_OUTPUT_DIR
from soul_manager import append_memory
from sub_agent import claude_think

log = logging.getLogger("publisher")

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
           sender: str, thread_id: str) -> str | None:
    """Handle a publish request. Returns summary or None on failure."""

    # Step 1: Figure out what to publish and where
    plan = _plan_publish(content)
    if not plan:
        return None

    platform = plan.get("platform", "substack")
    source = plan.get("source", "")
    title = plan.get("title", "")
    subtitle = plan.get("subtitle", "")

    log.info("Publishing to %s: title='%s' source='%s'", platform, title, source)

    # Step 2: Find the content to publish
    article_text = _resolve_content(source, content)
    if not article_text:
        msg = f"找不到要发布的内容: {source}"
        (workspace / "output.md").write_text(msg, encoding="utf-8")
        return msg

    # Step 3: Dispatch to platform
    if platform == "substack":
        from substack import publish_to_substack
        result = publish_to_substack(title, subtitle, article_text, workspace)
    elif platform == "substack_note":
        result = _handle_note(content, article_text, workspace)
    else:
        result = f"平台 '{platform}' 暂不支持"

    (workspace / "output.md").write_text(result, encoding="utf-8")
    append_memory(f"Published to {platform}: {title[:40]}")
    return result


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

    result = claude_think(prompt, timeout=30)
    if not result:
        return None

    match = re.search(r'\{.*?\}', result, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"platform": "substack", "source": "", "title": "", "subtitle": ""}


def _resolve_content(source: str, original_msg: str) -> str | None:
    """Find the article content to publish — search writings, artifacts, or use inline."""

    # Check if source is a direct file path
    if source and Path(source).exists():
        return Path(source).read_text(encoding="utf-8")

    # Search in writings output
    if source:
        for candidate in WRITINGS_OUTPUT_DIR.iterdir():
            if not candidate.is_dir():
                continue
            if source.lower() in candidate.name.lower():
                final = candidate / "final.md"
                if final.exists():
                    return final.read_text(encoding="utf-8")
                # Try any .md file
                for md in sorted(candidate.glob("*.md"), reverse=True):
                    return md.read_text(encoding="utf-8")

    # Search in artifacts/writings
    writings_dir = ARTIFACTS_DIR / "writings"
    if source and writings_dir.exists():
        for candidate in writings_dir.iterdir():
            if source.lower() in candidate.name.lower():
                final = candidate / "final.md"
                if final.exists():
                    return final.read_text(encoding="utf-8")

    # Check for chained output from previous agent step
    separator = "--- 上一步的输出 ---"
    if separator in original_msg:
        return original_msg.split(separator, 1)[1].strip()

    # Check if content is inline (message contains the article itself)
    if len(original_msg) > 500:
        return original_msg

    return None
