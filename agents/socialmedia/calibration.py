"""Human calibration prompts for Substack guard decisions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge import Mira
from config import LOGS_DIR, MIRA_DIR, MIRA_ROOT

log = logging.getLogger("mira.socialmedia.calibration")

try:
    from config import CALIBRATION_PROMPT_SAMPLE_SIZE
except ImportError:
    CALIBRATION_PROMPT_SAMPLE_SIZE = 3

_RECENT_SCAN_LIMIT = 200


def _parse_timestamp(entry: dict[str, Any]) -> datetime:
    raw = entry.get("timestamp") or entry.get("ts") or ""
    if not raw:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines()[-_RECENT_SCAN_LIMIT:]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                item["_source"] = path.name
                entries.append(item)
    except OSError as exc:
        log.debug("Calibration audit read failed for %s: %s", path, exc)
    return entries


def _audit_paths() -> list[Path]:
    roots = [MIRA_ROOT / "logs", LOGS_DIR]
    names = [
        "scaffolding_rejections.jsonl",
        "scaffolding_audit.jsonl",
        "guards.log",
        "guard_vigilance.jsonl",
    ]
    paths: list[Path] = []
    for root in roots:
        for name in names:
            path = root / name
            if path not in paths:
                paths.append(path)
    return paths


def _select_guard_entries(sample_size: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _audit_paths():
        entries.extend(_load_jsonl(path))

    entries = [
        item
        for item in entries
        if item.get("guard_name") or item.get("guard") or item.get("first_100_chars") or item.get("content_prefix")
    ]
    entries.sort(key=_parse_timestamp, reverse=True)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in entries:
        key = (
            str(item.get("task_id") or item.get("content_hash") or item.get("timestamp") or item.get("ts")),
            str(item.get("guard_name") or item.get("guard") or ""),
            str(item.get("first_100_chars") or item.get("content_prefix") or "")[:80],
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= sample_size:
            break
    return selected


def _format_entry(index: int, entry: dict[str, Any]) -> str:
    timestamp = _parse_timestamp(entry).strftime("%Y-%m-%d")
    guard = entry.get("guard_name") or entry.get("guard") or "guard"
    decision = entry.get("severity") or entry.get("result") or "recorded"
    reason = entry.get("trigger_reason") or entry.get("reason") or ""
    title = entry.get("task_id") or entry.get("title") or entry.get("agent") or ""
    snippet = (entry.get("first_100_chars") or entry.get("content_prefix") or "").strip()
    snippet = " ".join(snippet.split())[:280]

    lines = [f"{index}. {timestamp} - {guard} / {decision}"]
    if title:
        lines.append(f"   Item: {title}")
    if reason:
        lines.append(f"   Reason: {reason}")
    if snippet:
        lines.append(f"   Snippet: {snippet}")
    return "\n".join(lines)


def format_calibration_prompt(entries: list[dict[str, Any]]) -> str:
    body = "\n\n".join(_format_entry(i, entry) for i, entry in enumerate(entries, start=1))
    return (
        "Weekly guard calibration.\n\n"
        "Please rate these 1-5 for quality and whether the guard call felt right. "
        "Add any short note if something feels off.\n\n"
        f"{body}\n\n"
        "Reply here; I will not change any automated guard behavior from this."
    )


def send_guard_calibration_prompt(user_id: str = "ang", sample_size: int | None = None) -> bool:
    size = sample_size or CALIBRATION_PROMPT_SAMPLE_SIZE
    entries = _select_guard_entries(max(1, int(size)))
    if not entries:
        log.info("Guard calibration: no recent audit entries found")
        return False

    now = datetime.now()
    item_id = f"guard_calibration_{now.strftime('%G_W%V')}"
    bridge = Mira(MIRA_DIR, user_id=user_id)
    if bridge.item_exists(item_id):
        log.info("Guard calibration prompt already exists for %s", now.strftime("%G-W%V"))
        return False

    item = bridge.create_discussion(
        item_id,
        f"Weekly guard calibration {now.strftime('%G-W%V')}",
        format_calibration_prompt(entries),
        sender="agent",
        tags=["mira", "guard", "calibration", "substack"],
    )
    item["calibration_entries"] = entries
    bridge._write_item(item)
    bridge._update_manifest(item)
    log.info("Guard calibration prompt posted with %d entries", len(entries))
    return True
