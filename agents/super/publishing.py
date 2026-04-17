"""Publishing pipeline — auto-publish approved articles and trigger podcasts.

Handles the publish -> podcast_en -> podcast_zh -> complete lifecycle,
weekly podcast rate limiting, and stuck pipeline detection.
"""

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None

from state import load_state, save_state
from runtime.dispatcher import _dispatch_background

log = logging.getLogger("mira")

_AGENTS_DIR = Path(__file__).resolve().parent.parent


def _check_pending_publish():
    """Auto-publish approved articles from the manifest."""
    from publish.manifest import get_next_pending, update_manifest, validate_step

    entry = get_next_pending("published")  # finds status="approved"
    if not entry:
        # Legacy fallback: check agent_state.json (one release cycle)
        state = load_state()
        legacy = state.get("pending_publish")
        if legacy:
            # Migrate to manifest
            slug = legacy.get("item_id", "unknown").replace("autowrite_", "").replace("_", "-")
            final_md = legacy.get("final_md", "final.md")
            workspace = legacy.get("workspace", "")
            if not Path(final_md).is_absolute() and workspace:
                final_md = str(Path(workspace) / final_md)
            update_manifest(
                slug,
                title=legacy.get("title", slug),
                status="approved",
                workspace=workspace,
                final_md=final_md,
                item_id=legacy.get("item_id", ""),
                auto_podcast=legacy.get("auto_podcast", True),
            )
            del state["pending_publish"]
            save_state(state)
            log.info("Migrated legacy pending_publish '%s' to manifest", slug)
            entry = get_next_pending("published")
        if not entry:
            return

    try:
        sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
        from substack import publish_to_substack

        final = Path(entry["final_md"])
        if not final.exists():
            update_manifest(entry["slug"], error=f"final_md not found: {final}")
            return

        workspace = Path(entry.get("workspace", final.parent))
        content = final.read_text(encoding="utf-8")
        result = publish_to_substack(
            title=entry["title"],
            subtitle=entry.get("subtitle", ""),
            article_text=content,
            workspace=workspace,
        )

        if "发布被拦截" in result or "cooldown" in result.lower():
            log.info("Publish cooldown active for '%s': %s", entry["slug"], result[:80])
            return  # still in cooldown, try next cycle

        # Published successfully
        post_url = ""
        for part in result.split():
            if "substack.com" in part:
                post_url = part
                break

        # Post-condition: verify the published URL is reachable
        passed, verify_err = validate_step(entry["slug"], "published", url=post_url, title=entry["title"])
        if not passed:
            try:
                from ops.failure_log import record_failure

                record_failure(
                    pipeline="publish",
                    step="substack_publish",
                    slug=entry["slug"],
                    error_type="verification_failed",
                    error_message=verify_err,
                    expected_output=f"Accessible article at {post_url}",
                    actual_output=verify_err,
                )
            except Exception as e:
                log.debug("Failed to record publish verification failure: %s", e)
            log.warning("Publish verification failed for '%s': %s", entry["title"], verify_err)
            # Don't fail hard — URL may take time to propagate

        update_manifest(entry["slug"], status="published", substack_url=post_url)
        log.info("Auto-published '%s': %s", entry["title"], result[:100])

        # Update item status
        bridge = Mira()
        item_id = entry.get("item_id")
        if item_id:
            bridge.update_status(item_id, "done", agent_message=f"已发布到 Substack: {result[:200]}")

        # Queue notes for the new article
        from notes import queue_notes_for_article

        if post_url:
            queue_notes_for_article(entry["title"], content[:3000], post_url)

        # Tweet about the new article
        try:
            sys.path.insert(0, str(_AGENTS_DIR / "socialmedia"))
            from twitter import tweet_for_article

            tweet_result = tweet_for_article(entry["title"], entry.get("subtitle", ""), post_url, soul_context="")
            if tweet_result:
                log.info("Tweeted about '%s'", entry["title"])
        except Exception as tw_e:
            log.warning("Twitter promotion failed for '%s': %s", entry["slug"], tw_e)

    except Exception as e:
        update_manifest(entry["slug"], error=str(e))
        log.warning("Pending publish failed for '%s': %s", entry["slug"], e)


def _check_pending_podcast():
    """Trigger podcast generation for published articles.

    Cost control (2026-04-07): at most 1 EN + 1 ZH episode per ISO week.
    When multiple candidates are eligible, Mira ranks them by LLM judgment
    and picks the strongest one to podcast this week.
    """
    from publish.manifest import get_next_pending, update_manifest, load_manifest

    state = load_state()
    current_week = datetime.now().strftime("%Y-W%W")
    done_en_week = state.get("podcast_en_week")
    done_zh_week = state.get("podcast_zh_week")

    def _eligible(target_status: str) -> list[dict]:
        """All manifest entries eligible for the given podcast step, with auto_podcast enabled."""
        manifest = load_manifest()
        # Use get_next_pending semantics: find entries where source_status is set.
        src_status = "published" if target_status == "podcast_en" else "podcast_en"
        out = []
        for entry in manifest.get("articles", {}).values():
            if entry.get("status") == src_status and entry.get("auto_podcast"):
                out.append(entry)
        return out

    def _pick_best(candidates: list[dict], lang: str) -> dict | None:
        """Rank candidates by Mira's judgment; return the best one."""
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Multiple candidates: ask Claude to rank.
        try:
            from llm import claude_think

            lines = [f"{i+1}. {c.get('title','')} [{c.get('slug','')}]" for i, c in enumerate(candidates)]
            prompt = (
                f"Pick the single article best suited for a {lang.upper()} podcast episode this week. "
                "Criteria: (1) thought density — has a real argument, not a list; (2) oral readability — "
                "doesn't depend on inline code/tables; (3) emotional or narrative hook; (4) "
                "prefers articles that pair well with spoken delivery.\n\n"
                "Candidates:\n" + "\n".join(lines) + "\n\nOutput ONLY the number of the pick."
            )
            resp = (claude_think(prompt, timeout=30) or "").strip()
            m = re.search(r"\d+", resp)
            if m:
                idx = int(m.group()) - 1
                if 0 <= idx < len(candidates):
                    return candidates[idx]
        except Exception as e:
            log.warning("Podcast pick ranking failed: %s", e)
        # Fallback: oldest first (FIFO)
        return candidates[0]

    # EN podcast — weekly rate limit
    if done_en_week != current_week:
        en_candidates = _eligible("podcast_en")
        entry = _pick_best(en_candidates, "en")
        if entry:
            final = Path(entry["final_md"])
            slug = entry["slug"]
            bg_name = f"podcast-en-{slug}"
            if final.exists():
                log.info(
                    "Triggering EN podcast for '%s' (week %s, %d candidates)",
                    entry["title"],
                    current_week,
                    len(en_candidates),
                )
                _dispatch_background(
                    bg_name,
                    [
                        sys.executable,
                        str(_AGENTS_DIR / "podcast" / "handler.py"),
                        "--run",
                        "conversation",
                        "--title",
                        entry["title"],
                        "--file",
                        str(final),
                        "--lang",
                        "en",
                        "--slug",
                        slug,
                    ],
                )
                # Mark the week as spent (optimistic — avoids double-dispatch
                # if dispatch succeeds but generation fails mid-week).
                state["podcast_en_week"] = current_week
                save_state(state)
            else:
                update_manifest(slug, error=f"Podcast: final_md not found: {final}")
    else:
        log.debug("EN podcast weekly quota already used for %s", current_week)

    # ZH podcast — weekly rate limit
    if done_zh_week != current_week:
        zh_candidates = _eligible("podcast_zh")
        entry_zh = _pick_best(zh_candidates, "zh")
        if entry_zh:
            final = Path(entry_zh["final_md"])
            slug = entry_zh["slug"]
            bg_name = f"podcast-zh-{slug}"
            if final.exists():
                log.info(
                    "Triggering ZH podcast for '%s' (week %s, %d candidates)",
                    entry_zh["title"],
                    current_week,
                    len(zh_candidates),
                )
                _dispatch_background(
                    bg_name,
                    [
                        sys.executable,
                        str(_AGENTS_DIR / "podcast" / "handler.py"),
                        "--run",
                        "conversation",
                        "--title",
                        entry_zh["title"],
                        "--file",
                        str(final),
                        "--lang",
                        "zh",
                        "--slug",
                        slug,
                    ],
                )
                state["podcast_zh_week"] = current_week
                save_state(state)
            else:
                update_manifest(slug, error=f"Podcast: final_md not found: {final}")
    else:
        log.debug("ZH podcast weekly quota already used for %s", current_week)

    # Check if both podcasts done → mark complete
    from publish.manifest import load_manifest

    manifest = load_manifest()
    for entry in manifest.get("articles", {}).values():
        if entry.get("status") == "podcast_zh":
            # Both podcasts done, advance to complete
            update_manifest(entry["slug"], status="complete")
            log.info("Pipeline complete for '%s'", entry.get("title", entry["slug"]))


def _sweep_publish_pipeline():
    """Check for articles stuck in the pipeline and log warnings.

    If an entry has exhausted MAX_RETRIES, notify the user via bridge.
    """
    from publish.manifest import get_stuck_articles, MAX_RETRIES
    from config import MIRA_DIR

    stuck = get_stuck_articles(timeout_minutes=120)
    for entry in stuck:
        log.warning(
            "PIPELINE STUCK: '%s' at status '%s' for >2h", entry.get("title", entry["slug"]), entry.get("status")
        )

        if entry.get("retry_count", 0) >= MAX_RETRIES:
            log.error(
                "Pipeline STUCK after %d retries: '%s' at '%s'",
                entry.get("retry_count", 0),
                entry.get("slug"),
                entry.get("status"),
            )
            try:
                from datetime import datetime, timezone

                m = Mira()
                m.create_item(
                    item_id=f"stuck_{entry['slug']}",
                    title=f"Pipeline stuck: {entry.get('title', entry['slug'])}",
                    messages=[
                        {
                            "id": f"stuck_{entry['slug']}_alert",
                            "sender": "system",
                            "content": (
                                f"Article '{entry.get('title')}' stuck at {entry['status']} "
                                f"after {entry.get('retry_count', 0)} retries. "
                                f"Last error: {entry.get('error', 'unknown')}. "
                                f"Manual intervention needed."
                            ),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                )
            except Exception as e:
                log.warning("Failed to notify about stuck pipeline: %s", e)
