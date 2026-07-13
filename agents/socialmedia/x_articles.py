"""Guarded X Articles review and publish pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from public_text_guard import PublicTextLeakError, validate_public_text
from twitter import TWITTER_API_ENDPOINT, _get_twitter_config, _load_state, _make_auth_header, _save_state

log = logging.getLogger("socialmedia.x_articles")

X_ARTICLE_MIN_WORDS = 700
X_ARTICLE_MAX_WORDS = 1200
X_ARTICLE_DAILY_LIMIT = 1

GENERIC_TITLE_WORDS = {
    "analysis",
    "framework",
    "guide",
    "insights",
    "musings",
    "reflections",
    "thoughts",
}

OPERATIONAL_EVIDENCE_WORDS = {
    "artifact",
    "dashboard",
    "draft",
    "evidence",
    "green dot",
    "handoff",
    "ledger",
    "log",
    "memory",
    "metric",
    "pipeline",
    "plan",
    "receipt",
    "run",
    "state",
    "system",
    "trace",
    "workflow",
}


class XArticlePublishBlockedError(RuntimeError):
    """Raised when an X Article cannot safely be published."""


@dataclass
class XArticlePacket:
    title: str
    subtitle: str
    body: str
    evidence_note: str = ""
    quotable_lines: list[str] = field(default_factory=list)
    thread_hook: str = ""
    standalone_posts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class XArticleReviewReport:
    title: str
    word_count: int
    checks: dict[str, bool] = field(default_factory=dict)
    blocking_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pass_gate(self) -> bool:
        return not self.blocking_reasons

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pass_gate"] = self.pass_gate
        return data


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?|[\u4e00-\u9fff]", text or ""))


def _section(markdown: str, heading: str, *, stop_headings: tuple[str, ...] | None = None) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, markdown, flags=re.MULTILINE)
    if not match:
        return ""
    rest = markdown[match.end() :].strip()
    if stop_headings:
        stop_pattern = "|".join(re.escape(item) for item in stop_headings)
        next_heading = re.search(rf"^##\s+(?:{stop_pattern})\s*$", rest, flags=re.MULTILINE)
    else:
        next_heading = re.search(r"^##\s+", rest, flags=re.MULTILINE)
    if next_heading:
        rest = rest[: next_heading.start()]
    return rest.strip()


def _metadata_value(markdown: str, label: str) -> str:
    match = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+)$", markdown, flags=re.MULTILINE)
    return _compact(match.group(1)) if match else ""


def _bullet_lines(text: str) -> list[str]:
    return [_compact(line[2:]) for line in text.splitlines() if line.strip().startswith("- ")]


def _numbered_items(text: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*\d+\.\s+", line):
            if current:
                items.append(_compact(" ".join(current)))
            current = [re.sub(r"^\s*\d+\.\s+", "", line).strip()]
        elif current and line.strip():
            current.append(line.strip())
    if current:
        items.append(_compact(" ".join(current)))
    return items


def parse_x_article_packet(markdown_text: str) -> XArticlePacket:
    """Parse the review packet used by the X Article pipeline."""
    title = _metadata_value(markdown_text, "Title")
    subtitle = _metadata_value(markdown_text, "Subtitle")
    evidence_note = _metadata_value(markdown_text, "Evidence note for editor")
    body = _section(markdown_text, "X Article", stop_headings=("Quotable Lines",))
    quotable_lines = _bullet_lines(_section(markdown_text, "Quotable Lines"))
    thread_hook = _section(markdown_text, "Thread Hook")
    standalone_posts = _numbered_items(_section(markdown_text, "Standalone X Posts"))

    if not title:
        for line in markdown_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = _compact(stripped[2:])
                break

    return XArticlePacket(
        title=title,
        subtitle=subtitle,
        body=body,
        evidence_note=evidence_note,
        quotable_lines=quotable_lines,
        thread_hook=thread_hook,
        standalone_posts=standalone_posts,
    )


def _has_operational_receipt_early(body: str) -> bool:
    first_third = body[: max(len(body) // 3, 1)].lower()
    return sum(1 for word in OPERATIONAL_EVIDENCE_WORDS if word in first_third) >= 3


def _first_person_operational_claims(body: str) -> list[str]:
    claims: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", _compact(body)):
        lower = sentence.lower()
        if not re.search(r"\b(i|my|mira)\b", lower):
            continue
        if any(word in lower for word in OPERATIONAL_EVIDENCE_WORDS) or re.search(r"\b\d+\b", lower):
            claims.append(sentence)
    return claims


def review_x_article_packet(packet: XArticlePacket) -> XArticleReviewReport:
    """Run deterministic publish gates for an X Article packet."""
    blocking: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}
    word_count = _word_count(packet.body)

    checks["title_present"] = bool(packet.title)
    if not checks["title_present"]:
        blocking.append("title is required")

    title_lower = packet.title.lower()
    checks["title_specific"] = 18 <= len(packet.title) <= 100 and not any(
        word in title_lower for word in GENERIC_TITLE_WORDS
    )
    if not checks["title_specific"]:
        blocking.append("title must be specific, non-generic, and 18-100 characters")

    checks["subtitle_present"] = bool(packet.subtitle)
    if not checks["subtitle_present"]:
        blocking.append("subtitle is required")

    checks["length"] = X_ARTICLE_MIN_WORDS <= word_count <= X_ARTICLE_MAX_WORDS
    if not checks["length"]:
        blocking.append(f"article body must be {X_ARTICLE_MIN_WORDS}-{X_ARTICLE_MAX_WORDS} words; got {word_count}")

    checks["public_text_guard"] = False
    try:
        validate_public_text(packet.body, surface="x_article")
        validate_public_text(packet.thread_hook, surface="x_article_thread_hook")
        for post in packet.standalone_posts:
            validate_public_text(post, surface="x_article_standalone_post")
        checks["public_text_guard"] = True
    except PublicTextLeakError as exc:
        blocking.append(str(exc))

    opening = _compact(re.split(r"\n\s*\n", packet.body, maxsplit=1)[0] if packet.body else "")
    checks["opening_hook"] = (
        bool(opening)
        and len(opening) <= 220
        and not opening.lower().startswith(("in today's", "as an ai", "this article", "in this essay"))
    )
    if not checks["opening_hook"]:
        blocking.append("opening must be short, concrete, and non-generic")

    checks["operational_receipt_early"] = _has_operational_receipt_early(packet.body)
    if not checks["operational_receipt_early"]:
        blocking.append("one operational receipt or concrete example is required in the first third")

    first_person_claims = _first_person_operational_claims(packet.body)
    checks["first_person_claims_evidenced"] = not first_person_claims or bool(packet.evidence_note)
    if not checks["first_person_claims_evidenced"]:
        blocking.append("first-person operational claims require an evidence note")

    checks["quotable_lines"] = len(packet.quotable_lines) >= 5
    if not checks["quotable_lines"]:
        blocking.append("at least 5 quotable lines are required")

    checks["thread_hook"] = bool(packet.thread_hook) and _word_count(packet.thread_hook) <= 80
    if not checks["thread_hook"]:
        blocking.append("thread hook is required and should stay under 80 words")

    checks["standalone_posts"] = len(packet.standalone_posts) >= 3
    if not checks["standalone_posts"]:
        blocking.append("at least 3 standalone X posts are required")

    if "read the full" in packet.body.lower():
        warnings.append("remove newsletter-style CTA from native X Article body")

    return XArticleReviewReport(
        title=packet.title,
        word_count=word_count,
        checks=checks,
        blocking_reasons=blocking,
        warnings=warnings,
    )


def review_x_article_markdown(markdown_text: str) -> tuple[XArticlePacket, XArticleReviewReport]:
    packet = parse_x_article_packet(markdown_text)
    return packet, review_x_article_packet(packet)


def markdown_to_draftjs(markdown_text: str) -> dict[str, Any]:
    """Convert simple Markdown into the DraftJS content state X expects."""
    blocks: list[dict[str, Any]] = []
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", markdown_text.strip()) if chunk.strip()]
    for index, paragraph in enumerate(paragraphs):
        block_type = "unstyled"
        text = paragraph
        if paragraph.startswith("### "):
            block_type = "header-three"
            text = paragraph[4:].strip()
        elif paragraph.startswith("## "):
            block_type = "header-two"
            text = paragraph[3:].strip()
        elif paragraph.startswith("# "):
            block_type = "header-one"
            text = paragraph[2:].strip()
        text = re.sub(r"`([^`]+)`", r"\1", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        key = hashlib.sha1(f"{index}:{text}".encode("utf-8")).hexdigest()[:5]
        blocks.append(
            {
                "key": key,
                "text": text,
                "type": block_type,
                "depth": 0,
                "inline_style_ranges": [],
                "entity_ranges": [],
                "data": {},
            }
        )
    return {"blocks": blocks, "entities": []}


def _auth_header(method: str, url: str, cfg: dict[str, str]) -> str:
    user_token = cfg.get("oauth2_user_token") or cfg.get("user_access_token")
    if user_token:
        return f"Bearer {user_token}"
    required = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
    if all(cfg.get(key) for key in required):
        return _make_auth_header(method, url, cfg)
    raise XArticlePublishBlockedError(
        "X Article publish blocked: twitter credentials need OAuth 1.0a keys or an oauth2_user_token"
    )


def _request_json(method: str, url: str, auth: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw or "{}")


def _can_publish_x_article_now() -> tuple[bool, str]:
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    daily_count = int(state.get(f"x_articles_{today}", 0) or 0)
    if daily_count >= X_ARTICLE_DAILY_LIMIT:
        return False, f"daily X Article limit reached: {daily_count}/{X_ARTICLE_DAILY_LIMIT}"
    if state.get("spend_cap_reached"):
        return False, "X spend cap reached"
    return True, ""


def _record_x_article_publish(
    *, title: str, article_id: str, post_id: str, source_path: Path | None, word_count: int
) -> None:
    state = _load_state()
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now().strftime("%Y-%m-%d")
    state["x_article_last_published_at"] = now
    state[f"x_articles_{today}"] = int(state.get(f"x_articles_{today}", 0) or 0) + 1
    history = state.get("x_article_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "title": title,
            "article_id": article_id,
            "post_id": post_id,
            "source_path": str(source_path) if source_path else "",
            "word_count": word_count,
            "published_at": now,
        }
    )
    state["x_article_history"] = history[-50:]
    _save_state(state)


def create_x_article_draft(packet: XArticlePacket) -> dict[str, Any]:
    cfg = _get_twitter_config()
    url = f"{TWITTER_API_ENDPOINT}/articles/draft"
    auth = _auth_header("POST", url, cfg)
    payload = {
        "title": packet.title,
        "content_state": markdown_to_draftjs(packet.body),
    }
    return _request_json("POST", url, auth, payload)


def publish_x_article_id(article_id: str) -> dict[str, Any]:
    cfg = _get_twitter_config()
    url = f"{TWITTER_API_ENDPOINT}/articles/{article_id}/publish"
    auth = _auth_header("POST", url, cfg)
    return _request_json("POST", url, auth)


def publish_x_article_markdown(
    markdown_text: str,
    *,
    source_path: Path | None = None,
    dry_run: bool = False,
    allow_test_publish: bool = False,
) -> dict[str, Any]:
    """Review and optionally publish an X Article packet.

    Public publish is fully automatic only after deterministic gates pass. Any
    connector/auth/API uncertainty fails closed and returns a blocked result.
    """
    packet, report = review_x_article_markdown(markdown_text)
    result: dict[str, Any] = {
        "platform": "x_article",
        "title": packet.title,
        "status": "review_passed" if report.pass_gate else "blocked",
        "review": report.to_dict(),
    }
    if not report.pass_gate:
        return result
    if dry_run:
        result["status"] = "dry_run"
        result["content_state_preview"] = markdown_to_draftjs(packet.body)
        return result
    if os.getenv("PYTEST_CURRENT_TEST") and not allow_test_publish:
        result["status"] = "blocked"
        result["blocking_reasons"] = ["test guard: refusing live X Article publish under pytest"]
        return result

    can_publish, reason = _can_publish_x_article_now()
    if not can_publish:
        result["status"] = "blocked"
        result["blocking_reasons"] = [reason]
        return result

    try:
        draft_response = create_x_article_draft(packet)
        article_id = str(draft_response.get("data", {}).get("id") or "")
        if not article_id:
            raise XArticlePublishBlockedError("X Article draft creation returned no article id")
        publish_response = publish_x_article_id(article_id)
        post_id = str(publish_response.get("data", {}).get("post_id") or "")
        if not post_id:
            raise XArticlePublishBlockedError("X Article publish returned no post id")
        _record_x_article_publish(
            title=packet.title,
            article_id=article_id,
            post_id=post_id,
            source_path=source_path,
            word_count=report.word_count,
        )
        result.update(
            {
                "status": "published",
                "article_id": article_id,
                "post_id": post_id,
                "draft_response": draft_response,
                "publish_response": publish_response,
            }
        )
        return result
    except XArticlePublishBlockedError as exc:
        result["status"] = "blocked"
        result["blocking_reasons"] = [str(exc)]
        return result
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        result["status"] = "blocked"
        result["blocking_reasons"] = [f"X Article API HTTP {exc.code}: {body}"]
        return result
    except Exception as exc:
        log.exception("X Article publish failed")
        result["status"] = "blocked"
        result["blocking_reasons"] = [f"X Article publish failed safely: {exc}"]
        return result


def publish_x_article_file(
    path: str | Path, *, dry_run: bool = False, allow_test_publish: bool = False
) -> dict[str, Any]:
    source = Path(path)
    result = publish_x_article_markdown(
        source.read_text(encoding="utf-8"),
        source_path=source,
        dry_run=dry_run,
        allow_test_publish=allow_test_publish,
    )
    sidecar = source.with_suffix(
        ".x_article_review.json" if dry_run or result["status"] != "published" else ".x_article_publish.json"
    )
    sidecar.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
