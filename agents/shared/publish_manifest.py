"""Publishing manifest — single source of truth for article pipeline state.

Tracks each article from approval through publish → podcast → complete.
Lives at WRITINGS_OUTPUT_DIR / "publish_manifest.json" (on iCloud).
"""
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import PUBLISH_MAX_RETRIES, PUBLISH_RETRY_BACKOFF

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
        entry.setdefault("timestamps", {})["last_error"] = _utc_iso()

    # Special: allow clearing last_error timestamp
    if fields.get("_clear_last_error"):
        entry.get("timestamps", {}).pop("last_error", None)
        del entry["_clear_last_error"]

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
        # Also check for retryable failed entries at the previous status
        retryable = [
            a for a in manifest.get("articles", {}).values()
            if a.get("status") == prev_status and a.get("error") and should_retry(a)
        ]
        if retryable:
            retryable.sort(key=lambda a: a.get("timestamps", {}).get("last_error", ""))
            candidate = retryable[0]
            if prepare_retry(candidate["slug"]):
                log.info("Retrying '%s' at status '%s' (attempt %d)",
                         candidate["slug"], prev_status,
                         candidate.get("retry_count", 0) + 1)
                # Re-load after prepare_retry mutated the manifest
                manifest = load_manifest()
                return manifest.get("articles", {}).get(candidate["slug"])
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


# ---------------------------------------------------------------------------
# Post-condition validation — called before advancing pipeline status
# ---------------------------------------------------------------------------

def validate_step(slug: str, status: str, **kwargs) -> tuple[bool, str]:
    """Validate that a pipeline step actually succeeded.

    Called before advancing manifest status. Returns (passed, error_message).
    kwargs contains step-specific data (url, mp3_path, feed_url, etc.)
    """
    validators = {
        "published": _validate_published,
        "podcast_en": _validate_podcast,
        "podcast_zh": _validate_podcast,
    }
    validator = validators.get(status)
    if not validator:
        return True, ""
    try:
        return validator(slug, **kwargs)
    except Exception as e:
        return False, f"Validation error: {e}"


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

MAX_RETRIES = PUBLISH_MAX_RETRIES
RETRY_BACKOFF = PUBLISH_RETRY_BACKOFF  # 15min, 1hr, 4hr


def should_retry(entry: dict) -> bool:
    """Check if a failed entry should be retried now."""
    if not entry.get("error"):
        return False  # Not in error state

    retry_count = entry.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        return False  # Exhausted retries

    # Guard: don't retry if last error is identical to previous error
    # (learned from real failures — retrying the same non-transient error wastes cycles)
    if retry_count > 0 and entry.get("prev_error") and entry.get("error") == entry.get("prev_error"):
        log.warning("Skipping retry for '%s': same error repeated (%s)",
                    entry.get("slug", "?"), entry["error"][:120])
        return False

    # Check backoff timing
    last_error_ts = entry.get("timestamps", {}).get("last_error")
    if not last_error_ts:
        return True  # No timestamp, allow retry

    try:
        last_error = datetime.fromisoformat(last_error_ts.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_error).total_seconds()
        backoff = RETRY_BACKOFF[min(retry_count, len(RETRY_BACKOFF) - 1)]
        return elapsed >= backoff
    except (ValueError, TypeError):
        return True  # Can't parse, allow retry


def prepare_retry(slug: str) -> bool:
    """Clear error state and increment retry count for next attempt.

    Returns True if retry was prepared, False if max retries exceeded.
    """
    manifest = load_manifest()
    articles = manifest.get("articles", {})
    entry = articles.get(slug)
    if not entry:
        return False

    retry_count = entry.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        return False

    # Save current error as prev_error for repeated-error detection in should_retry
    update_manifest(slug,
                    prev_error=entry.get("error"),
                    error=None,
                    retry_count=retry_count + 1,
                    _clear_last_error=True)
    return True


def _validate_published(slug: str, url: str = "", title: str = "", **kw) -> tuple[bool, str]:
    """Verify published article is accessible."""
    if not url:
        return False, "No URL returned from publish"
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mira/1.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        if resp.status != 200:
            return False, f"Published URL returned {resp.status}"
        return True, ""
    except Exception as e:
        return False, f"Cannot reach published URL: {e}"


def _validate_podcast(slug: str, mp3_path: str = "", expected_min_seconds: int = 1500, **kw) -> tuple[bool, str]:
    """Verify podcast episode is valid audio."""
    path = Path(mp3_path) if mp3_path else None
    if not path or not path.exists():
        return False, f"MP3 file not found: {mp3_path}"

    # Check file size (must be > 2MB)
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb < 2:
        return False, f"MP3 too small: {size_mb:.1f}MB (expected >2MB)"

    # Check duration with ffprobe
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10
        )
        duration = float(result.stdout.strip())
        if duration < expected_min_seconds:
            return False, f"Duration {duration:.0f}s < minimum {expected_min_seconds}s"
        return True, ""
    except Exception as e:
        return False, f"ffprobe failed: {e}"
