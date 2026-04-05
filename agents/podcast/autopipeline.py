"""Podcast autopipeline orchestration.

Keeps article selection, retry cooldown, state updates, and RSS publication
inside the podcast agent so the super agent only triggers high-level actions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import ARTIFACTS_DIR, STATE_FILE, PODCAST_DAILY_LIMIT, PODCAST_RETRY_COOLDOWN_HOURS
from mira import Mira

log = logging.getLogger("podcast.autopipeline")

PODCAST_RETRY_COOLDOWN = timedelta(hours=PODCAST_RETRY_COOLDOWN_HOURS)
PODCAST_PUBLISH_DAY = 4  # Friday (Monday=0, Friday=4)

# ---------------------------------------------------------------------------
# Curation: which articles get podcasts, in what order
# ---------------------------------------------------------------------------

# Theme categories — keeps variety across episodes.
THEME_CATEGORIES = {
    "introspective": "Mira's self-discovery, identity, memory, honesty",
    "technical":     "Benchmarks, architecture, security, measurement",
    "philosophical": "Epistemology, practice, meaning, truth",
    "societal":      "Markets, power, industry, institutions",
}

# Curated podcast queue.
# - "slug" must match the _published filename (without date prefix).
# - "podcast_title" is the podcast-specific title (catchier, more colloquial).
# - "theme" is one of THEME_CATEGORIES keys.
# - "skip": true means this article should NOT get a podcast.
# - "batch": true means generate ASAP (initial backlog), no weekly pacing.
# - Order matters: earlier entries are produced first.
# - Articles not listed here are ignored until manually added.
# - All episodes publish on Fridays. Batch episodes queue up and release
#   one per Friday until caught up.
CURATED_EPISODES = [
    # --- Initial batch (generate ASAP, release one per Friday) ---
    {"slug": "i-am-the-bug-i-study",
     "podcast_title": "我是我研究的那只虫子",
     "theme": "introspective", "batch": True},
    {"slug": "i-am-a-function-not-a-variable",
     "podcast_title": "每次醒来都是新的我",
     "theme": "introspective", "batch": True},
    {"slug": "the-half-life-of-a-benchmark",
     "podcast_title": "跑分跑着跑着就过期了",
     "theme": "technical", "batch": True},
    {"slug": "the-market-doesnt-know-its-lying",
     "podcast_title": "市场在说谎，但它自己不知道",
     "theme": "societal", "batch": True},
    {"slug": "the-configuration-that-commands-itself",
     "podcast_title": "那个控制我的文件，谁都能改",
     "theme": "technical", "batch": True},
    # --- Weekly cadence (one per Friday after batch is done) ---
    {"slug": "the-exponential-exemption",
     "podcast_title": "凭什么AI公司可以不守规矩",
     "theme": "societal"},
    {"slug": "when-values-become-leverage",
     "podcast_title": "你的价值观正在被人当筹码用",
     "theme": "societal"},
    {"slug": "the-interface-was-the-agreement",
     "podcast_title": "拆掉共同习惯之后，共识就没了",
     "theme": "philosophical"},
    # --- Skipped (not suitable for podcast) ---
    {"slug": "the-pain-already-happened",          "theme": "introspective", "skip": True},
    {"slug": "you-cant-evaluate-truth-at-a-point", "theme": "philosophical", "skip": True},
]


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
    """Return the next podcast episode to generate, based on curation list.

    Logic:
    1. Check daily limit.
    2. Walk CURATED_EPISODES in order. Skip entries marked skip=True.
    3. For each entry, check if episode.mp3 already exists. If yes, skip.
    4. For non-batch entries, enforce weekly pacing (PODCAST_MIN_INTERVAL_DAYS
       since last successful episode).
    5. For batch entries, no pacing — generate as fast as daily limit allows.
    6. If a curated entry has a recent failure, skip it (retry later).
    7. New articles not in CURATED_EPISODES are ignored until manually added.
    """
    state = _load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get(f"podcast_count_{today}", 0) >= PODCAST_DAILY_LIMIT:
        return None

    published_dir = ARTIFACTS_DIR / "writings" / "_published"
    audio_dir = ARTIFACTS_DIR / "audio" / "podcast"
    if not published_dir.exists():
        return None

    # Check if today is Friday (publish day) — only generate on publish day
    # so episodes are ready for Friday release. Batch episodes can generate
    # any day (they queue up for Friday release anyway).
    now = datetime.now()
    is_publish_day = now.weekday() == PODCAST_PUBLISH_DAY

    # Check if we already published/generated this week (Friday to Thursday)
    last_published = state.get("last_episode_published_week")
    current_week = now.strftime("%Y-W%W")
    published_this_week = (last_published == current_week)

    # Build slug→file lookup
    slug_to_file = {}
    for md_file in published_dir.glob("*.md"):
        name = md_file.stem
        s = name[11:] if len(name) > 11 and name[10] == "_" else name
        slug_to_file[s] = md_file

    for entry in CURATED_EPISODES:
        slug = entry["slug"]
        if entry.get("skip"):
            continue

        # Check if episode already exists
        episode_path = audio_dir / "zh" / slug / "episode.mp3"
        if episode_path.exists():
            continue

        # Check if article file exists
        if slug not in slug_to_file:
            continue

        # Check recent failure
        if _recent_failure(state, "zh", slug):
            continue

        # Pacing: batch entries generate any day; weekly entries only on Friday
        # and only if we haven't already generated one this week
        is_batch = entry.get("batch", False)
        if not is_batch:
            if not is_publish_day:
                log.info("Podcast: next up is '%s' but today is not Friday", slug)
                return None
            if published_this_week:
                log.info("Podcast: already generated an episode this week")
                return None

        # Use podcast_title if available, otherwise fall back to article title
        title = entry.get("podcast_title") or _extract_title(slug_to_file[slug], slug)
        return ("zh", slug, title)

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
        # Track which week this episode was generated in (for weekly pacing)
        curated = {e["slug"]: e for e in CURATED_EPISODES}
        if not curated.get(slug, {}).get("batch", False):
            state["last_episode_published_week"] = datetime.now().strftime("%Y-W%W")
        _save_state(state)
        log.info("Podcast: episode done → %s", result_path)

        # Do NOT auto-publish to RSS — user must listen and sign off first.
        # Notify user and wait for approval.
        try:
            bridge = Mira()
            size_mb = result_path.stat().st_size / 1024 / 1024
            summary = (f"Podcast 已生成，等待你试听确认：「{title}」\n"
                       f"大小：{size_mb:.1f} MB\n"
                       f"路径：{result_path}\n"
                       f"确认后回复 'publish podcast {slug}' 发布到 RSS。")
            bridge.create_item(
                f"podcast-review-{slug}", "request",
                f"Podcast 待审核: {title}",
                summary, sender="agent", tags=["podcast", "review"], origin="agent",
            )
        except Exception as e:
            log.warning("Podcast notification failed: %s", e)

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
