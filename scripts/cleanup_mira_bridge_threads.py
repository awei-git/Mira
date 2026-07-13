#!/usr/bin/env python3
"""Archive stale/noisy Mira bridge threads.

This intentionally targets internal operational noise and stale prompts. It
does not delete item files; it marks them archived through the bridge API so
the app can stop surfacing them while history remains recoverable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "lib"))
sys.path.insert(0, str(_ROOT / "agents" / "super"))

from bridge import Mira  # noqa: E402
from config import MIRA_DIR  # noqa: E402


@dataclass
class CleanupDecision:
    item_id: str
    action: str
    reason: str
    old_status: str
    new_status: str


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _read_items(bridge: Mira) -> list[dict]:
    items: list[dict] = []
    for path in sorted(bridge.items_dir.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _date_from_compact(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _decision(item: dict, *, now: datetime) -> CleanupDecision | None:
    item_id = str(item.get("id") or "")
    status = str(item.get("status") or "")
    if status == "archived":
        return None

    if re.match(r"^(req|mira)_liveness_[0-9a-zA-Z_-]+$", item_id) or item_id.startswith("output_stale_"):
        return CleanupDecision(item_id, "archive", "internal liveness alert noise", status, "archived")
    if re.match(r"^req_watchdog_[0-9a-f]+$", item_id):
        return CleanupDecision(item_id, "archive", "superseded watchdog alert", status, "archived")
    if re.match(r"^task_report_\d{8}$", item_id):
        return CleanupDecision(item_id, "archive", "redundant legacy daily status report", status, "archived")
    if re.match(r"^self-audit-\d{4}-\d{2}-\d{2}$", item_id):
        return CleanupDecision(item_id, "archive", "legacy queued self-audit feed", status, "archived")
    if re.match(r"^self-evolve-\d{8}$", item_id):
        return CleanupDecision(item_id, "archive", "verbose self-evolve feed moved to backlog", status, "archived")
    if re.match(r"^photo_daily_\d{8}$", item_id):
        return CleanupDecision(item_id, "archive", "photo daily job disabled", status, "archived")
    soul_match = re.match(r"^soul_question_(\d{8})$", item_id)
    if soul_match:
        item_day = _date_from_compact(soul_match.group(1))
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if item_day and item_day < today:
            return CleanupDecision(item_id, "archive", "past-day soul question prompt", status, "archived")
    if re.match(r"^autowrite_\d{4}-\d{2}-\d{2}$", item_id) and status in {"error", "failed"}:
        return CleanupDecision(item_id, "archive", "stale failed autowrite request", status, "archived")
    if item_id.startswith("timeout-") and status in {"completed", "done", "failed"}:
        return CleanupDecision(item_id, "archive", "stale task timeout alert", status, "archived")

    updated_at = _parse_ts(str(item.get("updated_at") or ""))
    age_hours = ((now - updated_at).total_seconds() / 3600) if updated_at else 9999
    if item_id.startswith("req_") and status in {"failed", "error"} and age_hours >= 72:
        return CleanupDecision(item_id, "archive", "stale failed request", status, "archived")
    if status == "verifying" and age_hours >= 24:
        return CleanupDecision(item_id, "done", "stale verifying item with no active task", status, "done")

    return None


def plan_cleanup(user_id: str = "default") -> list[CleanupDecision]:
    bridge = Mira(MIRA_DIR, user_id=user_id)
    now = datetime.now(timezone.utc)
    decisions = [d for item in _read_items(bridge) if (d := _decision(item, now=now))]
    return decisions


def apply_cleanup(
    decisions: list[CleanupDecision], *, user_id: str = "default", dry_run: bool = True
) -> list[CleanupDecision]:
    if dry_run:
        return decisions
    bridge = Mira(MIRA_DIR, user_id=user_id)
    for decision in decisions:
        bridge.update_status(decision.item_id, decision.new_status)
        if decision.new_status == "archived":
            item = bridge._read_item(decision.item_id)
            if item:
                item["pinned"] = False
                bridge._write_item(item)
                bridge._update_manifest(item)
    bridge._update_manifest()
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="default")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    decisions = plan_cleanup(user_id=args.user)
    apply_cleanup(decisions, user_id=args.user, dry_run=not args.apply)
    print(json.dumps([d.__dict__ for d in decisions], indent=2, ensure_ascii=False))
    print(f"{'applied' if args.apply else 'dry-run'} {len(decisions)} cleanup decision(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
