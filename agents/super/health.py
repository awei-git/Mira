"""Health monitoring — daily health pipeline and weekly reports.

Handles Apple Health + Oura data ingestion, anomaly detection,
daily GPT insights, and weekly health report generation.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None

from config import MIRA_DIR, ARTIFACTS_DIR

log = logging.getLogger("mira")

_AGENTS_DIR = Path(__file__).resolve().parent.parent


def _has_pending_health_exports() -> bool:
    """Return True if any user has an Apple Health export waiting to ingest."""
    users_dir = MIRA_DIR / "users"
    if not users_dir.exists():
        return False

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        export_file = user_dir / "health" / "apple_health_export.json"
        if export_file.exists():
            return True
    return False


def _run_health_check():
    """Daily health pipeline: fetch data -> DB -> summary -> insight -> bridge.

    Runs once each morning (7-9 AM). DB is source of truth; bridge items
    are a write-through cache for iOS display using stable IDs (one file
    per type per user, overwritten daily).
    """
    sys.path.insert(0, str(_AGENTS_DIR / "health"))
    from health_store import HealthStore
    from ingest import ingest_all_users
    from monitor import check_all_users, format_alerts
    from summary import write_summary_to_bridge
    from report import generate_daily_insight
    from config import DATABASE_URL, SECRETS_FILE, HEALTH_REPORT_MODEL

    store = HealthStore(DATABASE_URL)
    bridge_path = Path(MIRA_DIR)
    today = datetime.now().date()

    # --- 1. Ingest: Oura API + Apple Health exports -> DB ---

    try:
        import yaml

        secrets = yaml.safe_load(SECRETS_FILE.read_text(encoding="utf-8")) or {}
        oura_cfg = secrets.get("api_keys", {}).get("oura", {})
        oura_users = {"ang": oura_cfg} if isinstance(oura_cfg, str) else oura_cfg if isinstance(oura_cfg, dict) else {}
        from oura import fetch_and_store as oura_fetch

        for uid, token in oura_users.items():
            try:
                count = oura_fetch(store, token, uid, days_back=1)
                log.info("Oura: fetched %d metrics for %s", count, uid)
            except Exception as e:
                log.warning("Oura fetch failed for %s: %s", uid, e)
    except Exception as e:
        log.warning("Oura setup failed: %s", e)

    ingested = ingest_all_users(bridge_path, store)
    if ingested:
        log.info("Health: ingested %d metrics from Apple Health", ingested)

    # --- 2. Discover users ---

    users_dir = bridge_path / "users"
    user_ids = (
        sorted(d.name for d in users_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
        if users_dir.exists()
        else ["ang"]
    )

    # --- 3. Refresh health_summary.json for each user (iOS dashboard) ---

    for uid in user_ids:
        try:
            write_summary_to_bridge(store, bridge_path, uid)
        except Exception as e:
            log.warning("Health summary for %s failed: %s", uid, e)

    # --- 4. Anomaly detection -> DB + bridge ---

    all_bridges = Mira.for_all_users()
    bridges_by_user = {b.user_id: b for b in all_bridges}

    alerts_by_user = check_all_users(store, user_ids)
    for uid, alerts in (alerts_by_user or {}).items():
        bridge = bridges_by_user.get(uid)
        if not bridge:
            continue
        message = format_alerts(uid, alerts)
        store.upsert_insight(uid, today, "alert", message)
        _write_health_feed(bridge, f"health_alert_{uid}", "健康提醒", message, ["health", "alert"])
        log.info("Health alert sent to %s: %d alerts", uid, len(alerts))

    if not alerts_by_user:
        log.info("Health check: all clear for all users")

    # --- 5. Daily GPT insight -> DB + bridge ---

    for uid in user_ids:
        bridge = bridges_by_user.get(uid)
        if not bridge:
            continue
        # Skip if already generated today (check DB, not bridge)
        existing = store.get_latest_insight(uid, "daily")
        if existing and existing["insight_date"] == today:
            log.info("Health insight for %s already exists today, skipping", uid)
            continue
        try:
            insight = generate_daily_insight(store, uid, model=HEALTH_REPORT_MODEL)
            if not insight:
                continue
            store.upsert_insight(uid, today, "daily", insight, model=HEALTH_REPORT_MODEL)
            _write_health_feed(bridge, f"health_insight_{uid}", "今日健康洞察", insight, ["health", "insight"])
            log.info("Daily health insight sent to %s", uid)
        except Exception as e:
            log.warning("Daily health insight for %s failed: %s", uid, e)

    store.close()


def _write_health_feed(bridge, item_id: str, title: str, content: str, tags: list[str]):
    """Write a health feed item to bridge, overwriting any previous version.

    Uses a stable item_id so there's always exactly one file per type per user.
    Directly uses bridge._write_item + _update_manifest for atomic consistency.
    """
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    item = {
        "id": item_id,
        "type": "feed",
        "title": title,
        "status": "done",
        "tags": tags,
        "origin": "agent",
        "pinned": True,
        "quick": False,
        "parent_id": "",
        "created_at": now,
        "updated_at": now,
        "messages": [
            {
                "id": f"{abs(hash(now + item_id)) % 0xFFFFFFFF:08x}",
                "sender": "health_agent",
                "content": content,
                "timestamp": now,
                "kind": "text",
            }
        ],
        "error": None,
        "result_path": None,
    }
    bridge._write_item(item)
    bridge._update_manifest(item)


def _run_health_weekly_report():
    """Generate weekly health reports -> DB + bridge (stable ID)."""
    sys.path.insert(0, str(_AGENTS_DIR / "health"))
    from health_store import HealthStore
    from report import generate_weekly_report
    from config import DATABASE_URL

    store = HealthStore(DATABASE_URL)
    all_bridges = Mira.for_all_users()
    bridges_by_user = {b.user_id: b for b in all_bridges}
    today = datetime.now().date()

    users_dir = Path(MIRA_DIR) / "users"
    user_ids = (
        sorted(d.name for d in users_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
        if users_dir.exists()
        else ["ang"]
    )

    for uid in user_ids:
        report = generate_weekly_report(store, uid)
        if "暂无健康数据" in report:
            continue

        # Store in DB
        store.upsert_insight(uid, today, "weekly", report)

        # Write to iCloud Artifacts
        today_str = today.isoformat()
        artifacts_base = Path(ARTIFACTS_DIR).parent
        health_dir = artifacts_base / uid / "health"
        health_dir.mkdir(parents=True, exist_ok=True)
        (health_dir / f"weekly_{today_str}.md").write_text(report, encoding="utf-8")
        log.info("Health weekly report written for %s", uid)

        # Write to bridge (stable ID — overwrites previous week)
        bridge = bridges_by_user.get(uid)
        if bridge:
            _write_health_feed(bridge, f"health_weekly_{uid}", f"健康周报", report, ["health", "report"])

    store.close()
