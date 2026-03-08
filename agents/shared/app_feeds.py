"""
App Integration Protocol v2 — read structured outputs from connected apps.

Each app writes a JSON file to feeds/apps/{app-name}.json.
See feeds/apps/PROTOCOL.md for the full spec.

Usage:
    from agents.shared.app_feeds import read_app_feeds, format_app_digest
    from agents.shared.app_feeds import get_outputs, get_output

    feeds = read_app_feeds()                          # all feeds
    digest = format_app_digest(feeds)                 # markdown summary
    reports = get_outputs("masterminds", "report")    # all reports from an app
    item = get_output("masterminds", "深空回声/bible") # specific output by id
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from agents.shared.config import FEEDS_DIR

logger = logging.getLogger("mira.app_feeds")

APPS_DIR = FEEDS_DIR / "apps"

# Type aliases
OutputItem = dict[str, Any]
AppFeed = dict[str, Any]


def read_app_feeds(max_age_hours: float = 48) -> list[AppFeed]:
    """Read all app feeds, optionally filtering stale ones."""
    feeds: list[AppFeed] = []
    if not APPS_DIR.exists():
        return feeds

    now = datetime.now(timezone.utc)
    for path in sorted(APPS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text("utf-8"))
            if "app" not in data or "outputs" not in data:
                continue
            # Skip stale
            if max_age_hours > 0:
                try:
                    updated = datetime.fromisoformat(
                        data["updatedAt"].replace("Z", "+00:00")
                    )
                    if (now - updated).total_seconds() / 3600 > max_age_hours:
                        continue
                except (ValueError, KeyError):
                    pass
            feeds.append(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read app feed %s: %s", path.name, e)

    return feeds


def get_outputs(
    app: str,
    output_type: Optional[str] = None,
    parent: Optional[str] = None,
) -> list[OutputItem]:
    """Get outputs from a specific app, optionally filtered by type and parent."""
    for feed in read_app_feeds():
        if feed["app"] != app:
            continue
        items = feed.get("outputs", [])
        if output_type:
            items = [i for i in items if i.get("type") == output_type]
        if parent:
            items = [i for i in items if i.get("parent") == parent]
        return items
    return []


def get_output(app: str, output_id: str) -> Optional[OutputItem]:
    """Get a specific output by app and id."""
    for item in get_outputs(app):
        if item.get("id") == output_id:
            return item
    return None


def format_app_digest(feeds: Optional[list[AppFeed]] = None) -> str:
    """Format all app feeds into a markdown digest.

    Groups outputs by app, shows progress items as status lines,
    lists reports and deep dives as expandable sections.
    """
    if feeds is None:
        feeds = read_app_feeds()
    if not feeds:
        return ""

    sections: list[str] = []

    for feed in feeds:
        app = feed["app"]
        outputs = feed.get("outputs", [])
        if not outputs:
            continue

        lines: list[str] = [f"### {app}"]

        # Progress items first
        for item in outputs:
            if item.get("type") != "progress":
                continue
            stage = item.get("stage", {})
            status = item.get("status", "?")
            stage_str = f"{stage.get('label', '?')}（{stage.get('current', '?')}/{stage.get('total', '?')}）"
            lines.append(f"**{item.get('title', '?')}** [{status}] — {stage_str}")
            for h in item.get("highlights", []):
                lines.append(f"  - {h}")
            for b in item.get("blockers", []):
                lines.append(f"  - ⚠ {b}")

        # Reports
        reports = [i for i in outputs if i.get("type") == "report"]
        if reports:
            lines.append("")
            for r in reports:
                period = r.get("period", "")
                title = r.get("title", "?")
                content = r.get("content", "")
                preview = content[:100].replace("\n", " ").strip()
                lines.append(f"📄 **{title}** ({period}) — {preview}…")

        # Deep dives
        dives = [i for i in outputs if i.get("type") == "deep_dive"]
        if dives:
            lines.append("")
            for d in dives:
                lines.append(f"🔍 **{d.get('title', '?')}** — {d.get('topic', '')}")

        # Alerts
        alerts = [i for i in outputs if i.get("type") == "alert"]
        for a in alerts:
            sev = a.get("severity", "info")
            icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "🔵")
            lines.append(f"{icon} {a.get('message', '')}")

        sections.append("\n".join(lines))

    if not sections:
        return ""

    return "## App Status\n\n" + "\n\n".join(sections)
