"""Podcast autopipeline orchestration.

Keeps article selection, retry cooldown, state updates, and RSS publication
inside the podcast agent so the super agent only triggers high-level actions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import ARTIFACTS_DIR, STATE_FILE

log = logging.getLogger("podcast.autopipeline")

PODCAST_DAILY_LIMIT = 2
PODCAST_RETRY_COOLDOWN = timedelta(hours=4)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _failure_key(lang: str, slug: str) -> str:
    return f"podcast_failure_{lang}_{slug}"


def _extract_title(md_file: Path, slug: str) -> str:
    try:
        text = md_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("title:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return slug.replace("-", " ").title()


def _extract_article_url(article_text: str) -> str:
    for line in article_text.splitlines():
        if line.startswith("url:"):
            return line.split(":", 1)[1].strip()
    return ""


def _recent_failure(state: dict, lang: str, slug: str) -> dict | None:
    failure = state.get(_failure_key(lang, slug))
    if not isinstance(failure, dict):
        return None
    failed_at = failure.get("failed_at")
    if not failed_at:
        return failure
    try:
        failed_dt = datetime.fromisoformat(failed_at)
    except ValueError:
        return failure
    if datetime.now() - failed_dt < PODCAST_RETRY_COOLDOWN:
        return failure
    return None


def should_podcast() -> tuple[str, str, str] | None:
    """Return the next missing podcast episode to generate.

    Priority: Chinese first, then English. Limits auto-generation to two
    episodes per day and skips episodes that failed recently.
    """
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get(f"podcast_count_{today}", 0) >= PODCAST_DAILY_LIMIT:
        return None

    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    audio_dir = ARTIFACTS_DIR / "audio" / "podcast"
    if not published_dir.exists():
        return None

    for md_file in sorted(published_dir.glob("*.md"), reverse=True):
        name = md_file.stem
        slug = name[11:] if len(name) > 11 and name[10] == "_" else name
        title = _extract_title(md_file, slug)

        for lang in ("zh",):  # EN disabled for now
            episode_path = audio_dir / lang / f"{slug}.mp3"
            if episode_path.exists():
                continue
            if _recent_failure(state, lang, slug):
                continue
            return (lang, slug, title)

    return None


def run_podcast_episode(lang: str, slug: str, title: str) -> Path | None:
    """Generate one episode and update agent state."""
    from handler import generate_conversation_for_article
    from rss import publish_episode

    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    matches = list(published_dir.glob(f"*_{slug}.md")) + list(published_dir.glob(f"{slug}.md"))
    if not matches:
        log.error("Podcast: article file not found for slug '%s' in %s", slug, published_dir)
        log.error("Podcast: available files: %s", [f.name for f in published_dir.glob("*.md")])
        return None

    article_text = matches[0].read_text(encoding="utf-8")
    log.info("Podcast: generating [%s] episode for '%s'", lang, title)

    result = generate_conversation_for_article(article_text, title, lang=lang)
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    failure_key = _failure_key(lang, slug)

    # Increment attempt count regardless of success/failure so the daily limit
    # is enforced even when all attempts fail (previously the count only grew on
    # success, leaving it at 0 after a bad day and letting should_podcast() loop
    # through every article indefinitely).
    state[f"podcast_count_{today}"] = state.get(f"podcast_count_{today}", 0) + 1
    _save_state(state)

    result_path = Path(result) if result else None
    if result_path and result_path.exists():
        state[f"podcast_{today}_{slug}"] = {
            "lang": lang,
            "slug": slug,
            "path": str(result_path),
        }
        state.pop(failure_key, None)
        _save_state(state)
        log.info("Podcast: episode done → %s", result_path)

        try:
            article_url = _extract_article_url(article_text)
            description = f"原文：{article_url}" if lang == "zh" else f"Full article: {article_url}"
            if not article_url:
                description = ""
            feed_url = publish_episode(result_path, title, description)
            if feed_url:
                log.info("Podcast: published to RSS → %s", feed_url)
        except Exception as e:
            log.warning("Podcast RSS publish failed (non-fatal): %s", e)
        return result_path

    reason = "generation_failed"
    if result_path and not result_path.exists():
        reason = "missing_output_file"
    state[failure_key] = {
        "lang": lang,
        "slug": slug,
        "failed_at": datetime.now().isoformat(),
        "reason": reason,
    }
    _save_state(state)
    log.error("Podcast: generation failed for [%s] '%s' (%s)", lang, title, reason)
    return None
