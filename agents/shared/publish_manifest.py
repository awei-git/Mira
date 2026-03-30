"""Publishing manifest — single source of truth for article pipeline state.

Tracks each article from approval through publish → podcast → complete.
Lives at WRITINGS_OUTPUT_DIR / "publish_manifest.json" (on iCloud).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mira")

_manifest_path: Path | None = None


def _get_path() -> Path:
    global _manifest_path
    if _manifest_path is None:
        from config import WRITINGS_OUTPUT_DIR
        _manifest_path = WRITINGS_OUTPUT_DIR / "publish_manifest.json"
    return _manifest_path


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest() -> dict:
    """Load the manifest. Returns empty structure if missing or corrupt."""
    p = _get_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Manifest corrupt, starting fresh: %s", e)
    return {"articles": {}}


def _save(data: dict):
    """Atomic write."""
    p = _get_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.rename(p)


# Status progression: approved → published → podcast_en → podcast_zh → complete
_STATUS_ORDER = ["approved", "published", "podcast_en", "podcast_zh", "complete"]


def update_manifest(slug: str, **fields) -> dict:
    """Update an article entry. Creates it if new.

    Special fields:
      status  — also sets timestamps[status] = now
      error   — logged as warning
    """
    manifest = load_manifest()
    articles = manifest.setdefault("articles", {})
    entry = articles.setdefault(slug, {
        "slug": slug,
        "timestamps": {},
    })

    # Set timestamp for status transitions
    if "status" in fields:
        entry.setdefault("timestamps", {})[fields["status"]] = _utc_iso()
        # Clear error on successful step
        entry.pop("error", None)

    entry.update(fields)

    if "error" in fields and fields["error"]:
        log.warning("Publish pipeline error for '%s': %s", slug, fields["error"])

    _save(manifest)
    return entry


def get_next_pending(target_status: str) -> dict | None:
    """Get the first article ready for `target_status`.

    E.g. get_next_pending("published") returns articles with status="approved".
    Returns None if nothing pending.
    """
    if target_status not in _STATUS_ORDER:
        return None
    prev_idx = _STATUS_ORDER.index(target_status) - 1
    if prev_idx < 0:
        return None
    prev_status = _STATUS_ORDER[prev_idx]

    manifest = load_manifest()
    candidates = [
        a for a in manifest.get("articles", {}).values()
        if a.get("status") == prev_status and not a.get("error")
    ]
    if not candidates:
        return None
    # FIFO by timestamp of the previous status
    candidates.sort(key=lambda a: a.get("timestamps", {}).get(prev_status, ""))
    return candidates[0]


def get_stuck_articles(timeout_minutes: int = 120) -> list[dict]:
    """Find articles stuck in a non-terminal status for too long."""
    manifest = load_manifest()
    stuck = []
    now = datetime.now(timezone.utc)
    for entry in manifest.get("articles", {}).values():
        status = entry.get("status", "")
        if status in ("complete", ""):
            continue
        ts_str = entry.get("timestamps", {}).get(status)
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if (now - ts).total_seconds() > timeout_minutes * 60:
                stuck.append(entry)
        except (ValueError, TypeError):
            continue
    return stuck
