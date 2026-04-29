"""
Sync app feeds → browsable markdown in Mira-bridge/artifacts/apps/.

Reads feeds/apps/*.json (v2 protocol), generates human-readable markdown
files that the Mira iOS Library tab can browse.

Usage:
    from agents.shared.app_sync import sync_app_artifacts
    sync_app_artifacts()  # call periodically or on demand

Output structure:
    Mira-bridge/artifacts/apps/
        masterminds/
            _status.md              ← progress overview
            深空回声-构思阶段总结.md  ← phase reports
            深空回声-世界与角色总结.md
        tetra/
            _status.md
            Daily-Market-Report-2026-03-08.md
            Trade-Recommendations-2026-03-08.md
"""

import json
import logging
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from agents.shared.config import ARTIFACTS_DIR, FEEDS_DIR

logger = logging.getLogger("mira.app_sync")

APPS_FEED_DIR = FEEDS_DIR / "apps"
APPS_ARTIFACT_DIR = ARTIFACTS_DIR / "apps"


def _safe_filename(title: str) -> str:
    """Convert title to safe filename."""
    # Replace slashes and special chars
    name = re.sub(r'[/\\:*?"<>|]', "-", title)
    name = re.sub(r"\s+", "-", name.strip())
    return name[:80] or "untitled"


def _format_progress(item: dict[str, Any]) -> str:
    """Format a progress item as markdown."""
    stage = item.get("stage", {})
    lines = [
        f"# {item.get('title', '?')}",
        "",
        f"**Status:** {item.get('status', '?')}",
        f"**Stage:** {stage.get('label', '?')}（{stage.get('current', '?')}/{stage.get('total', '?')}）",
        "",
    ]
    highlights = item.get("highlights", [])
    if highlights:
        lines.append("## Highlights")
        for h in highlights:
            lines.append(f"- {h}")
        lines.append("")
    blockers = item.get("blockers", [])
    if blockers:
        lines.append("## Blockers")
        for b in blockers:
            lines.append(f"- ⚠ {b}")
        lines.append("")
    lines.append(f"_Updated: {item.get('updatedAt', '?')}_")
    return "\n".join(lines)


def _format_status_page(app: str, outputs: list[dict[str, Any]]) -> str:
    """Generate the _status.md overview page for an app."""
    progress_items = [o for o in outputs if o.get("type") == "progress"]
    reports = [o for o in outputs if o.get("type") == "report"]
    dives = [o for o in outputs if o.get("type") == "deep_dive"]
    alerts = [o for o in outputs if o.get("type") == "alert"]

    lines = [f"# {app}", ""]

    # Progress
    for p in progress_items:
        stage = p.get("stage", {})
        status = p.get("status", "?")
        lines.append(
            f"**{p.get('title', '?')}** [{status}] "
            f"— {stage.get('label', '?')}（{stage.get('current', '?')}/{stage.get('total', '?')}）"
        )
        for h in p.get("highlights", []):
            lines.append(f"  - {h}")
    lines.append("")

    # Alerts
    if alerts:
        lines.append("## Alerts")
        for a in alerts:
            sev = a.get("severity", "info")
            icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "🔵")
            lines.append(f"{icon} {a.get('message', '')}")
        lines.append("")

    # Reports index
    if reports:
        lines.append("## Reports")
        for r in reports:
            fname = _safe_filename(r.get("title", "?"))
            lines.append(f"- [{r.get('title', '?')}]({fname}.md) ({r.get('period', '')})")
        lines.append("")

    # Deep dives index
    if dives:
        lines.append("## Deep Dives")
        for d in dives:
            fname = _safe_filename(d.get("title", "?"))
            lines.append(f"- [{d.get('title', '?')}]({fname}.md) — {d.get('topic', '')}")
        lines.append("")

    return "\n".join(lines)


def sync_app_artifacts() -> int:
    """Read all app feeds and generate browsable markdown artifacts.

    Returns number of files written.
    """
    if not APPS_FEED_DIR.exists():
        return 0

    files_written = 0

    for feed_path in APPS_FEED_DIR.glob("*.json"):
        try:
            data = json.loads(feed_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read %s: %s", feed_path.name, e)
            continue

        if "app" not in data or "outputs" not in data:
            continue

        app = data["app"]
        outputs = data["outputs"]
        app_dir = APPS_ARTIFACT_DIR / app
        app_dir.mkdir(parents=True, exist_ok=True)

        # Write _status.md
        status_md = _format_status_page(app, outputs)
        (app_dir / "_status.md").write_text(status_md, encoding="utf-8")
        files_written += 1

        # Write individual reports
        for item in outputs:
            itype = item.get("type")
            title = item.get("title", "untitled")
            fname = _safe_filename(title) + ".md"

            if itype == "progress":
                content = _format_progress(item)
            elif itype == "report":
                content = item.get("content", "")
                if not content.startswith("#"):
                    content = f"# {title}\n\n{content}"
            elif itype == "deep_dive":
                header = f"# {title}\n\n**Topic:** {item.get('topic', '')}\n\n"
                content = header + item.get("content", "")
            elif itype == "alert":
                # Alerts go in _status.md only
                continue
            else:
                continue

            (app_dir / fname).write_text(content, encoding="utf-8")
            files_written += 1

        logger.info("Synced %s: %d outputs → %s", app, len(outputs), app_dir)

    return files_written
