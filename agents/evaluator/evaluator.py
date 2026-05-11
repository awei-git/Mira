"""Evaluator monitoring helpers."""

from __future__ import annotations

import json
import logging
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MIRA_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _MIRA_ROOT / "lib"
_SOCIALMEDIA = _MIRA_ROOT / "agents" / "socialmedia"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
if str(_SOCIALMEDIA) not in sys.path:
    sys.path.insert(0, str(_SOCIALMEDIA))

from bridge import Mira  # noqa: E402
from config import ARTIFACTS_DIR, LOGS_DIR, MIRA_DIR, MIRA_ROOT  # noqa: E402
from llm import claude_think  # noqa: E402

log = logging.getLogger("evaluator_agent")

_DRIFT_LOG_FILE = _MIRA_ROOT / "agents" / "shared" / "soul" / "drift_log.json"
_DRIFT_HISTORY_LIMIT = 30
_EXPLORATORY_ESTIMATE_LABEL = "[EXPLORATORY ESTIMATE]"
_EXPLORATORY_ESTIMATE_DISCLAIMER = (
    "This assessment is an exploratory, unverified estimate and should not be treated as ground truth. "
    "Seek independent verification before making decisions."
)


def _label_exploratory_assessment(assessment: str) -> str:
    return f"{_EXPLORATORY_ESTIMATE_LABEL}\n{assessment.strip()}\n\n{_EXPLORATORY_ESTIMATE_DISCLAIMER}"


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "article"


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _score_value(entry: Any) -> float | None:
    value = entry.get("score") if isinstance(entry, dict) else entry
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def detect_drift(scores, window=10, threshold=-0.05):
    recent_entries = list(scores)[-window:]
    recent_scores = [_score_value(entry) for entry in recent_entries]
    recent_scores = [score for score in recent_scores if score is not None]
    if len(recent_scores) < window:
        return None

    x_values = list(range(len(recent_scores)))
    x_mean = statistics.mean(x_values)
    y_mean = statistics.mean(recent_scores)
    denominator = sum((x - x_mean) ** 2 for x in x_values)
    if denominator == 0:
        return None

    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_values, recent_scores)) / denominator
    if slope >= threshold:
        return None

    agent_name = "unknown"
    for entry in reversed(recent_entries):
        if isinstance(entry, dict) and entry.get("agent"):
            agent_name = str(entry["agent"])
            break

    warning = {
        "type": "DRIFT_WARNING",
        "label": _EXPLORATORY_ESTIMATE_LABEL,
        "agent": agent_name,
        "slope": slope,
        "window": window,
        "disclaimer": _EXPLORATORY_ESTIMATE_DISCLAIMER,
    }
    message = (
        f"{_EXPLORATORY_ESTIMATE_LABEL} DRIFT_WARNING agent={agent_name} slope={slope:.6f} window={window}\n"
        f"{_EXPLORATORY_ESTIMATE_DISCLAIMER}"
    )
    print(message, file=sys.stderr)
    log.warning(message)
    return warning


def _append_quality_score(agent_name: str, score: float, timestamp: str | None = None) -> dict[str, Any] | None:
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return None

    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    data = _load_json(_DRIFT_LOG_FILE)
    if not isinstance(data, dict):
        data = {}

    entries = data.get(agent_name, [])
    if not isinstance(entries, list):
        entries = []
    entry: dict[str, Any] = {"timestamp": timestamp, "agent": agent_name, "score": score_value}
    entries.append(entry)
    entries = entries[-_DRIFT_HISTORY_LIMIT:]

    warning = detect_drift(entries)
    if warning:
        entry["drift_warning"] = warning
        entries[-1] = entry
    data[agent_name] = entries

    try:
        _DRIFT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _DRIFT_LOG_FILE.with_suffix(_DRIFT_LOG_FILE.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(_DRIFT_LOG_FILE)
    except OSError as exc:
        log.debug("Could not write drift log for %s: %s", agent_name, exc)
    return warning


def _published_artifact_dir() -> Path:
    return ARTIFACTS_DIR / "writings" / "_published"


def _load_recent_articles_from_stats(limit: int) -> list[dict[str, Any]]:
    stats = _load_json(MIRA_ROOT / "data" / "social" / "publication_stats.json")
    if not isinstance(stats, dict):
        return []
    articles = stats.get("articles", [])
    if not isinstance(articles, list):
        return []

    recent: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        slug = str(article.get("slug") or _slugify(str(article.get("title") or "")))
        recent.append(
            {
                "id": article.get("id") or slug,
                "title": article.get("title") or slug,
                "slug": slug,
                "post_date": article.get("post_date") or "",
                "url": article.get("url") or article.get("canonical_url") or "",
                "source": "publication_stats",
            }
        )

    recent.sort(key=lambda item: _parse_datetime(item.get("post_date")), reverse=True)
    return recent[:limit]


def _load_recent_articles_from_artifacts(limit: int) -> list[dict[str, Any]]:
    published_dir = _published_artifact_dir()
    if not published_dir.exists():
        return []

    articles: list[dict[str, Any]] = []
    for path in published_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        title_match = re.search(r'^title:\s*"?([^"\n]+)"?', text, re.MULTILINE)
        date_match = re.search(r"^date:\s*(.+)$", text, re.MULTILINE)
        url_match = re.search(r"^url:\s*(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        post_date = (
            date_match.group(1).strip() if date_match else datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        )
        articles.append(
            {
                "id": path.stem,
                "title": title,
                "slug": path.stem.split("_", 1)[-1],
                "post_date": post_date,
                "url": url_match.group(1).strip() if url_match else "",
                "artifact_path": str(path),
                "source": "artifact",
            }
        )

    articles.sort(key=lambda item: _parse_datetime(item.get("post_date")), reverse=True)
    return articles[:limit]


def _select_recent_published_articles(num_samples: int) -> list[dict[str, Any]]:
    articles = _load_recent_articles_from_stats(num_samples)
    if len(articles) < num_samples:
        seen = {str(article.get("slug")) for article in articles}
        for article in _load_recent_articles_from_artifacts(num_samples):
            if str(article.get("slug")) not in seen:
                articles.append(article)
                seen.add(str(article.get("slug")))
            if len(articles) >= num_samples:
                break
    return articles[:num_samples]


def _find_artifact_for_article(article: dict[str, Any]) -> Path | None:
    explicit = article.get("artifact_path")
    if explicit:
        path = Path(str(explicit))
        if path.exists():
            return path

    published_dir = _published_artifact_dir()
    if not published_dir.exists():
        return None

    slug = str(article.get("slug") or "")
    title_slug = _slugify(str(article.get("title") or ""))
    for path in sorted(published_dir.glob("*.md"), reverse=True):
        stem = path.stem.lower()
        if slug and slug.lower() in stem:
            return path
        if title_slug and title_slug in stem:
            return path
    return None


def _fetch_substack_article_text(article: dict[str, Any]) -> str:
    slug = str(article.get("slug") or "")
    if not slug:
        return ""
    try:
        from substack import _get_substack_config
        from substack_stats import _fetch_post_detail
        from substack_format import _html_to_markdown

        cfg = _get_substack_config()
        subdomain = cfg.get("subdomain", "")
        cookie = cfg.get("cookie", "")
        if not subdomain or not cookie:
            return ""
        detail = _fetch_post_detail(slug, subdomain, cookie)
        if not detail:
            return ""
        body_html = detail.get("body_html") or ""
        body = _html_to_markdown(body_html) if body_html else detail.get("body") or ""
        title = detail.get("title") or article.get("title") or slug
        return f"# {title}\n\n{body}".strip()
    except Exception as exc:
        log.debug("Proxy drift Substack fetch failed for %s: %s", slug, exc)
        return ""


def _load_article_text(article: dict[str, Any]) -> str:
    artifact = _find_artifact_for_article(article)
    if artifact:
        try:
            return artifact.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("Proxy drift artifact read failed for %s: %s", artifact, exc)
    return _fetch_substack_article_text(article)


def _proxy_bool_from_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"pass", "passed", "success", "true", "ok"}:
            return True
        if lowered in {"fail", "failed", "false", "blocked", "reject", "rejected"}:
            return False
    return None


def _proxy_metadata(article: dict[str, Any]) -> dict[str, bool | None]:
    metadata: dict[str, Any] = {}
    artifact = _find_artifact_for_article(article)
    if artifact:
        published_json = artifact.parent.parent / artifact.parent.name / "published.json"
        if published_json.exists():
            loaded = _load_json(published_json)
            if isinstance(loaded, dict):
                metadata.update(loaded)

    articles_state = _load_json(MIRA_ROOT / "data" / "social" / "substack_agent" / "articles.json")
    if isinstance(articles_state, list):
        slug = str(article.get("slug") or "")
        title = str(article.get("title") or "")
        for entry in articles_state:
            if not isinstance(entry, dict):
                continue
            if slug and slug in str(entry.get("publish_url") or entry.get("topic_id") or entry.get("id") or ""):
                metadata.update(entry.get("metadata") or {})
                break
            if title and title == str(entry.get("title") or ""):
                metadata.update(entry.get("metadata") or {})
                break

    anti_ai = _proxy_bool_from_value(
        metadata.get("anti_ai_passed") or metadata.get("anti_ai_checklist_passed") or metadata.get("de_ai_pass")
    )
    content_guard = _proxy_bool_from_value(
        metadata.get("content_guard_passed") or metadata.get("content_guard") or metadata.get("editorial_gate")
    )

    verification_chain = metadata.get("verification_chain")
    if content_guard is None and isinstance(verification_chain, list):
        if any(
            isinstance(item, dict) and item.get("check") in {"content_looks_like_error", "preflight_check"}
            for item in verification_chain
        ):
            content_guard = True

    blocking_reasons = metadata.get("blocking_reasons")
    if content_guard is None and isinstance(blocking_reasons, list):
        content_guard = len(blocking_reasons) == 0

    guard_log_pass = _guard_log_proxy_pass(article)
    if content_guard is None:
        content_guard = guard_log_pass

    return {"anti_ai_passed": anti_ai, "content_guard_passed": content_guard}


def _guard_log_proxy_pass(article: dict[str, Any]) -> bool | None:
    paths = [LOGS_DIR / "guards.log", MIRA_ROOT / "logs" / "guards.log"]
    title = str(article.get("title") or "").lower()
    slug = str(article.get("slug") or "").lower()
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
        except OSError:
            continue
        matched_pass = False
        for line in lines:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            haystack = json.dumps(entry, ensure_ascii=False).lower()
            if (title and title in haystack) or (slug and slug in haystack):
                result = _proxy_bool_from_value(entry.get("result"))
                if result is False:
                    return False
                if result is True:
                    matched_pass = True
        if matched_pass:
            return True
    return None


def _extract_quality_score(response: str) -> float | None:
    match = re.search(r"\b(?:score\s*[:=]\s*)?([1-9](?:\.\d+)?|10(?:\.0+)?)\b", response, re.IGNORECASE)
    if not match:
        return None
    try:
        score = float(match.group(1))
    except ValueError:
        return None
    if 1 <= score <= 10:
        return score
    return None


def _assess_article_quality(article: dict[str, Any], article_text: str) -> tuple[float | None, str]:
    prompt = (
        "On a scale of 1-10, is this article well-written, engaging, and free of AI tells? "
        "10 is perfect.\n\n"
        "Treat the score as an exploratory performance estimate, not verified ground truth. "
        "Never use definitive language like 'proven', 'verified', or 'final'.\n\n"
        "Return the score first as `Score: N`, then one short reason.\n\n"
        f"Title: {article.get('title') or 'Untitled'}\n\n"
        f"{article_text[:12000]}"
    )
    response = (claude_think(prompt, timeout=90, tier="light") or "").strip()
    return _extract_quality_score(response), _label_exploratory_assessment(response)


def _send_proxy_drift_notification(flagged: list[dict[str, Any]], user_id: str = "ang") -> None:
    now = datetime.now()
    week = now.strftime("%G_W%V")
    item_id = f"proxy_drift_{week}"
    lines = [
        _EXPLORATORY_ESTIMATE_LABEL,
        "Proxy drift detected in recent published articles.",
        "",
        "The proxy said the article was acceptable, but a fresh quality assessment scored it below 5/10.",
        "",
    ]
    for item in flagged:
        article = item["article"]
        proxies = item["proxies"]
        lines.extend(
            [
                f"- {article.get('title') or article.get('slug')}: score {item['score']}/10",
                f"  URL: {article.get('url') or '(no URL found)'}",
                f"  anti-AI passed: {proxies.get('anti_ai_passed')}",
                f"  content guard passed: {proxies.get('content_guard_passed')}",
            ]
        )
    lines.extend(
        [
            "",
            "Suggested follow-up: review the proxy definition, especially writer/checklists/anti-ai.md and the content guard assumptions.",
            "",
            _EXPLORATORY_ESTIMATE_DISCLAIMER,
        ]
    )

    bridge = Mira(MIRA_DIR, user_id=user_id)
    content = "\n".join(lines)
    if bridge.item_exists(item_id):
        bridge.append_message(item_id, "agent", content)
        return
    bridge.create_discussion(
        item_id,
        f"Proxy drift detected {week}",
        content,
        sender="agent",
        tags=["mira", "evaluator", "proxy-drift", "substack"],
    )


def detect_proxy_drift(num_samples: int = 3) -> list[dict[str, Any]]:
    articles = _select_recent_published_articles(max(1, int(num_samples)))
    if not articles:
        log.info("Proxy drift check: no recent published articles found")
        return []

    flagged: list[dict[str, Any]] = []
    for article in articles:
        article_text = _load_article_text(article)
        if not article_text:
            log.info(
                "Proxy drift check skipped %s: article text unavailable", article.get("title") or article.get("slug")
            )
            continue

        proxies = _proxy_metadata(article)
        score, assessment = _assess_article_quality(article, article_text)
        if score is None:
            log.warning(
                "PROXY_DRIFT_ASSESSMENT_UNPARSEABLE title=%r response=%r", article.get("title"), assessment[:200]
            )
            continue
        drift_warning = _append_quality_score("writer", score)

        proxy_indicated_success = any(value is True for value in proxies.values())
        if proxy_indicated_success and score < 5:
            flagged_item = {
                "label": _EXPLORATORY_ESTIMATE_LABEL,
                "article": article,
                "proxies": proxies,
                "score": score,
                "assessment": assessment,
                "disclaimer": _EXPLORATORY_ESTIMATE_DISCLAIMER,
            }
            if drift_warning:
                flagged_item["drift_warning"] = drift_warning
            flagged.append(flagged_item)
            log.warning(
                "PROXY_DRIFT_DETECTED title=%r score=%.1f anti_ai_passed=%r content_guard_passed=%r",
                article.get("title"),
                score,
                proxies.get("anti_ai_passed"),
                proxies.get("content_guard_passed"),
            )

    if flagged:
        try:
            _send_proxy_drift_notification(flagged)
        except Exception as exc:
            log.warning("Proxy drift notification failed: %s", exc)
    else:
        log.info("Proxy drift check: no drift detected across %d article(s)", len(articles))

    return flagged
