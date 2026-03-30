"""Write health_summary.json to bridge for iOS app to read directly.

This is the bridge between PostgreSQL health data and the iOS app.
Called by the daily health check and after every metric insertion.
"""
import json
import logging
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

log = logging.getLogger("health_summary")


def write_summary_to_bridge(store, bridge_dir: Path, person_id: str):
    """Write a health_summary.json with latest metrics, trends, and notes.

    The iOS app reads this file directly for dashboard cards and charts.
    """
    summary = build_summary(store, person_id)

    health_dir = bridge_dir / "users" / person_id / "health"
    health_dir.mkdir(parents=True, exist_ok=True)
    out_file = health_dir / "health_summary.json"

    try:
        tmp = out_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        tmp.rename(out_file)
        log.info("Health summary written for %s (%d metrics)", person_id, len(summary.get("latest", {})))
    except OSError as e:
        log.error("Failed to write health summary: %s", e)


def write_all_summaries(store, bridge_dir: Path):
    """Write summaries for all users with health data."""
    users_dir = bridge_dir / "users"
    if not users_dir.exists():
        return
    for user_dir in users_dir.iterdir():
        if user_dir.is_dir():
            write_summary_to_bridge(store, bridge_dir, user_dir.name)


def build_summary(store, person_id: str) -> dict:
    """Build a complete health summary dict for one person."""
    now = datetime.now(timezone.utc)

    # Latest values for each metric type
    latest = {}
    metric_types = store.get_all_metric_types(person_id)
    for mtype in metric_types:
        val = store.get_latest_metric(person_id, mtype)
        if val:
            latest[mtype] = {
                "value": val["value"],
                "unit": val.get("unit", ""),
                "date": val["date"].isoformat() if hasattr(val["date"], "isoformat") else str(val["date"]),
            }

    # Trends: last 30 days for key metrics
    trends = {}
    for mtype in ["weight", "sleep_hours", "sleep_score", "heart_rate",
                   "body_fat", "hrv", "steps", "blood_oxygen",
                   "active_minutes", "active_calories", "stress_high",
                   "recovery_high", "readiness_score", "activity_score",
                   "sedentary_hours", "workout", "resilience_level"]:
        data = store.get_recent_metrics(person_id, mtype, days=30)
        if data:
            points = []
            for d in reversed(data):  # oldest first for charting
                points.append({
                    "value": d["value"],
                    "date": d["date"].isoformat() if hasattr(d["date"], "isoformat") else str(d["date"]),
                })
            trends[mtype] = points

    # 7-day stats for key metrics
    stats = {}
    for mtype in metric_types:
        s = store.get_metric_stats(person_id, mtype, days=7)
        if s and s.get("count", 0) > 0:
            stats[mtype] = {
                "avg": round(float(s["avg"]), 2) if s["avg"] else None,
                "min": round(float(s["min"]), 2) if s["min"] else None,
                "max": round(float(s["max"]), 2) if s["max"] else None,
                "count": s["count"],
            }

    # Recent notes
    notes = store.get_recent_notes(person_id, days=7)
    notes_list = [{
        "date": str(n["date"]),
        "category": n["category"],
        "content": n["content"],
    } for n in notes]

    return {
        "person_id": person_id,
        "updated_at": now.isoformat(),
        "latest": latest,
        "trends": trends,
        "stats_7d": stats,
        "notes": notes_list,
    }
