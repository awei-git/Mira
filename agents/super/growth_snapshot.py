"""Daily growth snapshot — one line per day in growth_metrics.jsonl.

Fetches subscriber/follower counts from the public profile page and counts
notes/articles posted today. Idempotent within a day: a second invocation
overwrites the existing entry rather than duplicating.
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

_super_dir = Path(__file__).resolve().parent
_lib_dir = _super_dir.parent.parent / "lib"
_socialmedia_dir = _super_dir.parent / "socialmedia"
for _p in (str(_lib_dir), str(_super_dir), str(_socialmedia_dir)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import CATALOG_FILE, SOCIAL_STATE_DIR, SOUL_DIR
from state import load_state, save_state

log = logging.getLogger("growth-snapshot")

GROWTH_METRICS_FILE = SOUL_DIR / "growth_metrics.jsonl"
PROFILE_URL = "https://substack.com/@uncountablemira"


def _fetch_profile_counts() -> tuple[int | None, int | None]:
    """Fetch subscriberCount and followerCount from the public profile page."""
    import urllib.request

    try:
        from substack import _get_substack_config
    except Exception:
        _get_substack_config = lambda: {}

    cfg = _get_substack_config()
    cookie = cfg.get("cookie", "")

    headers = {"User-Agent": "Mozilla/5.0"}
    if cookie:
        headers["Cookie"] = f"substack.sid={cookie}; connect.sid={cookie}"

    req = urllib.request.Request(PROFILE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.error("Profile fetch failed: %s", e)
        return None, None

    # The HTML embeds JSON with escaped slashes; the verified regex relaxes
    # the gap between key name and digits to absorb escapes/quotes.
    def _grab(key: str) -> int | None:
        m = re.search(rf"\b{key}[^\d]{{0,5}}(\d+)", html)
        return int(m.group(1)) if m else None

    return _grab("subscriberCount"), _grab("followerCount")


def _count_articles_today(today: str) -> int:
    """Count articles in catalog.jsonl with status=published and date=today."""
    if not CATALOG_FILE.exists():
        return 0
    count = 0
    with CATALOG_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("type") == "article" and row.get("status") == "published" and row.get("date") == today:
                count += 1
    return count


def _count_notes_today(today: str) -> int:
    """Read today's notes count from socialmedia notes_state.json."""
    state_file = SOCIAL_STATE_DIR / "notes_state.json"
    if not state_file.exists():
        return 0
    try:
        st = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    return int(st.get(f"notes_{today}", 0) or 0)


def _read_existing_lines() -> list[dict]:
    if not GROWTH_METRICS_FILE.exists():
        return []
    rows = []
    for line in GROWTH_METRICS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_rows(rows: list[dict]):
    with GROWTH_METRICS_FILE.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_snapshot() -> bool:
    """Take one growth snapshot for today. Idempotent."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y-%m-%dT%H:%M")

    state = load_state()
    if state.get(f"growth_snapshot_{today}"):
        log.info("growth_snapshot already done for %s — skipping", today)
        return True

    subs, follows = _fetch_profile_counts()
    notes_today = _count_notes_today(today)
    articles_today = _count_articles_today(today)

    row = {
        "date": today,
        "ts": ts,
        "subscribers": subs,
        "followers": follows,
        "notes_posted_today": notes_today,
        "comments_posted_today": None,
        "articles_posted_today": articles_today,
        "notable_engagement": "",
    }

    rows = _read_existing_lines()
    # Replace any existing same-date data row (preserve schema header)
    kept = []
    for r in rows:
        if r.get("_schema"):
            kept.append(r)
            continue
        if r.get("date") == today:
            continue
        kept.append(r)
    kept.append(row)
    _write_rows(kept)

    state[f"growth_snapshot_{today}"] = ts
    save_state(state)
    log.info(
        "growth_snapshot wrote %s subs=%s follows=%s notes=%s articles=%s",
        today,
        subs,
        follows,
        notes_today,
        articles_today,
    )
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_snapshot()
