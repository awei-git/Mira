"""Feed health monitoring for explorer source coverage."""

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path

from config import FEEDS_DIR

log = logging.getLogger("mira")

FEED_STATS_FILE = FEEDS_DIR / "feed_stats.json"
ROLLING_WINDOW = 10
MIN_BASELINE_SAMPLES = 5
MAX_SAMPLES_PER_FEED = 200
SILENCE_INTERVAL_MULTIPLIER = 3


def _load_stats(path: Path) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read feed stats %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}

    stats: dict[str, list[dict]] = {}
    for feed_name, entries in data.items():
        if not isinstance(feed_name, str) or not isinstance(entries, list):
            continue
        clean_entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                item_count = max(0, int(entry.get("item_count", 0)))
            except (TypeError, ValueError):
                continue
            timestamp = entry.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                clean_entries.append({"timestamp": timestamp, "item_count": item_count})
        if clean_entries:
            stats[feed_name] = clean_entries[-MAX_SAMPLES_PER_FEED:]
    return stats


def _write_stats(path: Path, stats: dict[str, list[dict]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _coerce_timestamp(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timestamp_text(value) -> str:
    return _coerce_timestamp(value).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return _coerce_timestamp(value)
    except (TypeError, ValueError):
        return None


def _mean_interval_seconds(entries: list[dict]) -> float | None:
    timestamps = [_parse_timestamp(entry["timestamp"]) for entry in entries]
    timestamps = sorted(ts for ts in timestamps if ts is not None)
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(timestamps, timestamps[1:])
        if (right - left).total_seconds() > 0
    ]
    if len(intervals) < 2:
        return None
    return statistics.mean(intervals)


def update_feed_stats(feed_name, item_count, timestamp) -> None:
    """Append one item-count sample for a feed to the JSON stats file."""
    name = str(feed_name).strip()
    if not name:
        return
    try:
        count = max(0, int(item_count))
    except (TypeError, ValueError):
        count = 0

    stats = _load_stats(FEED_STATS_FILE)
    entries = stats.setdefault(name, [])
    entries.append({"timestamp": _timestamp_text(timestamp), "item_count": count})
    stats[name] = entries[-MAX_SAMPLES_PER_FEED:]
    try:
        _write_stats(FEED_STATS_FILE, stats)
    except OSError as e:
        log.warning("Could not write feed stats %s: %s", FEED_STATS_FILE, e)


def check_feed_health(stats_path) -> list[dict]:
    """Return feed health warnings based on rolling item counts and silence gaps."""
    path = Path(stats_path)
    stats = _load_stats(path)
    now = datetime.now(timezone.utc)
    alerts = []

    for feed_name, entries in stats.items():
        parsed_entries = []
        for entry in entries:
            ts = _parse_timestamp(entry["timestamp"])
            if ts is not None:
                parsed_entries.append({"timestamp": ts, "item_count": entry["item_count"]})
        parsed_entries.sort(key=lambda entry: entry["timestamp"])
        if len(parsed_entries) < MIN_BASELINE_SAMPLES + 1:
            continue

        latest = parsed_entries[-1]
        baseline = parsed_entries[-(ROLLING_WINDOW + 1) : -1]
        if len(baseline) < MIN_BASELINE_SAMPLES:
            continue

        counts = [entry["item_count"] for entry in baseline]
        mean_count = statistics.mean(counts)
        if mean_count <= 0:
            continue

        active_ratio = sum(1 for count in counts if count > 0) / len(counts)
        if active_ratio < 0.5:
            continue

        stddev_count = statistics.pstdev(counts) if len(counts) > 1 else 0.0
        threshold = max(0.0, mean_count - (2 * stddev_count))
        current_count = latest["item_count"]
        if current_count < threshold:
            alerts.append(
                {
                    "feed": feed_name,
                    "type": "low_count",
                    "current_count": current_count,
                    "mean_count": round(mean_count, 2),
                    "stddev_count": round(stddev_count, 2),
                    "threshold": round(threshold, 2),
                    "message": (
                        f"{feed_name} produced {current_count} items; rolling baseline is "
                        f"{mean_count:.2f} +/- {stddev_count:.2f}"
                    ),
                }
            )

        expected_interval = _mean_interval_seconds(baseline + [latest])
        if expected_interval is None:
            continue
        last_active = next((entry for entry in reversed(parsed_entries) if entry["item_count"] > 0), None)
        if last_active is None:
            continue
        silent_seconds = (now - last_active["timestamp"]).total_seconds()
        if silent_seconds > SILENCE_INTERVAL_MULTIPLIER * expected_interval:
            alerts.append(
                {
                    "feed": feed_name,
                    "type": "silent",
                    "silent_hours": round(silent_seconds / 3600, 2),
                    "expected_interval_hours": round(expected_interval / 3600, 2),
                    "message": (
                        f"{feed_name} has produced no items for {silent_seconds / 3600:.1f}h; "
                        f"expected interval is {expected_interval / 3600:.1f}h"
                    ),
                }
            )

    return alerts
