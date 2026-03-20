"""
App Integration Protocol v2 — read structured outputs from connected apps.

Architecture: registry-based.
  1. Mira reads feeds/apps/registry.json to discover apps
  2. Each app entry has root path + status file path
  3. Mira resolves paths, reads each app's status.json from the app's own directory
  4. For report outputs with "path" fields, reads the file directly from the app dir

Usage:
    from agents.shared.app_feeds import read_app_feeds, format_app_digest
    from agents.shared.app_feeds import get_outputs, get_output, read_report_content

    feeds = read_app_feeds()                          # all feeds
    digest = format_app_digest(feeds)                 # markdown summary
    reports = get_outputs("masterminds", "report")    # all reports from an app
    item = get_output("masterminds", "深空回声/bible") # specific output by id
    content = read_report_content("masterminds", "深空回声/bible")  # read file
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from config import FEEDS_DIR

logger = logging.getLogger("mira.app_feeds")

REGISTRY_FILE = FEEDS_DIR / "apps" / "registry.json"

# Type aliases
OutputItem = dict[str, Any]
AppFeed = dict[str, Any]


def _load_registry() -> dict[str, Any]:
    """Load the app registry. Returns {"apps": {...}} or empty."""
    if not REGISTRY_FILE.exists():
        logger.debug("No registry file at %s", REGISTRY_FILE)
        return {}
    try:
        return json.loads(REGISTRY_FILE.read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read registry: %s", e)
        return {}


def _resolve_app_root(app_entry: dict[str, Any]) -> Optional[Path]:
    """Resolve an app's root directory from registry entry.

    Paths in registry.json are relative to the Mira project root.
    e.g. "../MasterMinds" → MtJoy/MasterMinds (one level up from Mira/)
    """
    root_rel = app_entry.get("root", "")
    if not root_rel:
        return None
    # FEEDS_DIR = Mira/feeds, so FEEDS_DIR.parent = Mira/
    mira_root = FEEDS_DIR.parent
    resolved = (mira_root / root_rel).resolve()
    if resolved.exists():
        return resolved
    logger.debug("App root not found: %s (resolved to %s)", root_rel, resolved)
    return None


def read_app_feeds(max_age_hours: float = 48) -> list[AppFeed]:
    """Read all app status feeds via registry.

    Reads registry.json, resolves each app's root, reads its status.json.
    """
    registry = _load_registry()
    apps = registry.get("apps", {})
    if not apps:
        return []

    feeds: list[AppFeed] = []
    now = datetime.now(timezone.utc)

    for app_name, entry in apps.items():
        app_root = _resolve_app_root(entry)
        if not app_root:
            continue

        status_rel = entry.get("status", "status.json")
        status_path = app_root / status_rel

        if not status_path.exists():
            logger.debug("Status file not found for %s: %s", app_name, status_path)
            continue

        try:
            data = json.loads(status_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read status for %s: %s", app_name, e)
            continue

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

        # Attach resolved root for file reading
        data["_root"] = str(app_root)
        feeds.append(data)

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


def read_report_content(app: str, output_id: str) -> Optional[str]:
    """Read the actual file content for a report/deep_dive output.

    Resolves the 'path' field relative to the app's root directory.
    """
    for feed in read_app_feeds():
        if feed["app"] != app:
            continue
        app_root = feed.get("_root")
        if not app_root:
            return None
        for item in feed.get("outputs", []):
            if item.get("id") != output_id:
                continue
            rel_path = item.get("path")
            if not rel_path:
                # Fall back to inline content
                return item.get("content")
            full_path = Path(app_root) / rel_path
            if full_path.exists():
                try:
                    return full_path.read_text("utf-8")
                except OSError as e:
                    logger.warning("Failed to read %s: %s", full_path, e)
            return None
    return None


def format_app_digest(feeds: Optional[list[AppFeed]] = None) -> str:
    """Format all app feeds into a markdown digest.

    Groups outputs by app, shows progress items as status lines,
    lists reports and deep dives with previews.
    For reports with path references, reads first 100 chars from the file.
    """
    if feeds is None:
        feeds = read_app_feeds()
    if not feeds:
        return ""

    sections: list[str] = []

    for feed in feeds:
        app = feed["app"]
        app_root = feed.get("_root")
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
                # Try to get preview from file or inline content
                preview = ""
                rel_path = r.get("path")
                if rel_path and app_root:
                    full_path = Path(app_root) / rel_path
                    if full_path.exists():
                        try:
                            text = full_path.read_text("utf-8")
                            preview = text[:100].replace("\n", " ").strip()
                        except (OSError, UnicodeDecodeError):
                            pass
                if not preview:
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


def sync_mira_status() -> None:
    """Write Mira's own status.json following the same protocol.

    Scans artifacts/ for briefings, writing projects, and research.
    """
    from config import MIRA_ROOT, ARTIFACTS_DIR, BRIEFINGS_DIR, WRITINGS_OUTPUT_DIR, RESEARCH_DIR

    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now.strftime("%Y-%m-%d")

    outputs: list[dict[str, Any]] = []

    # Progress: agent heartbeat
    outputs.append({
        "type": "progress",
        "id": "agent",
        "title": "Mira Agent",
        "updatedAt": now_str,
        "status": "active",
        "stage": {"current": 1, "total": 1, "label": "Running"},
        "highlights": [],
    })

    # Briefings (recent 7 days)
    if BRIEFINGS_DIR.exists():
        briefings = sorted(BRIEFINGS_DIR.glob("*.md"), reverse=True)[:7]
        for b in briefings:
            rel = str(b.relative_to(ARTIFACTS_DIR))
            stat = b.stat()
            outputs.append({
                "type": "report",
                "id": f"briefing/{b.stem}",
                "title": f"Briefing — {b.stem}",
                "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "period": "daily",
                "path": rel,
                "size": stat.st_size,
                "parent": "agent",
            })

    # Writing projects
    if WRITINGS_OUTPUT_DIR.exists():
        for proj_dir in sorted(WRITINGS_OUTPUT_DIR.iterdir()):
            if not proj_dir.is_dir():
                continue
            idea_file = proj_dir / "idea.md"
            if not idea_file.exists():
                continue
            stat = proj_dir.stat()
            # Count files as rough progress indicator
            files = list(proj_dir.glob("*.md"))
            outputs.append({
                "type": "progress",
                "id": f"writing/{proj_dir.name}",
                "title": proj_dir.name,
                "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "active",
                "stage": {"current": len(files), "total": len(files), "label": f"{len(files)} files"},
                "highlights": [],
            })

    # Research (recent)
    if RESEARCH_DIR.exists():
        research_files = sorted(RESEARCH_DIR.glob("**/*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        for r in research_files:
            rel = str(r.relative_to(ARTIFACTS_DIR))
            stat = r.stat()
            outputs.append({
                "type": "deep_dive",
                "id": f"research/{r.stem}",
                "title": r.stem.replace("_", " ").replace("-", " ").title(),
                "updatedAt": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "topic": r.stem,
                "content": "",
                "path": rel,
            })

    feed = {
        "app": "mira",
        "version": 2,
        "updatedAt": now_str,
        "outputs": outputs,
    }

    status_path = MIRA_ROOT / "output" / "status.json"
    try:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(feed, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.rename(status_path)
        logger.info("Mira status written: %s", status_path)
    except Exception as e:
        logger.warning("Failed to write Mira status: %s", e)
