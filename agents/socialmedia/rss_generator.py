"""Generate an RSS feed for Mira artifact markdown files."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.etree import ElementTree as ET


DEFAULT_ARTIFACTS_DIR = Path.home() / "Sandbox" / "Mira" / "Mira-Artifacts"
FEED_FILENAME = "rss.xml"
MARKDOWN_SUFFIXES = {".md", ".markdown"}

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def _artifacts_dir() -> Path:
    try:
        from config import ARTIFACTS_DIR

        return Path(ARTIFACTS_DIR).expanduser()
    except Exception:
        return DEFAULT_ARTIFACTS_DIR


def _markdown_files(artifacts_dir: Path) -> list[Path]:
    files: list[Path] = []
    for subdir in ("writings", "briefings"):
        root = artifacts_dir / subdir
        if not root.exists():
            continue
        files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES)
    return sorted(files, key=lambda path: _created_timestamp(path), reverse=True)


def _created_timestamp(path: Path) -> float:
    stat = path.stat()
    return float(getattr(stat, "st_birthtime", stat.st_ctime))


def _pub_date(path: Path) -> str:
    created = datetime.fromtimestamp(_created_timestamp(path), tz=timezone.utc)
    return format_datetime(created, usegmt=True)


def _first_heading(text: str) -> str | None:
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return match.group(1).strip()
    return None


def _item_title(path: Path, text: str) -> str:
    return _first_heading(text) or path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def _item_description(path: Path, text: str, artifacts_dir: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and stripped != "---":
            return stripped[:500]
    return str(path.relative_to(artifacts_dir))


def _file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def generate_rss() -> Path:
    """Build Mira's artifact RSS feed and return the written file path."""
    artifacts_dir = _artifacts_dir()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    rss_path = artifacts_dir / FEED_FILENAME

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Mira Artifacts"
    ET.SubElement(channel, "link").text = _file_uri(artifacts_dir)
    ET.SubElement(channel, "description").text = "Mira writings and briefings"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc), usegmt=True)
    ET.SubElement(channel, "generator").text = "Mira RSS Generator"

    for path in _markdown_files(artifacts_dir):
        text = path.read_text(encoding="utf-8", errors="replace")
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = _item_title(path, text)
        link = _file_uri(path)
        ET.SubElement(item, "link").text = link
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = link
        ET.SubElement(item, "pubDate").text = _pub_date(path)
        ET.SubElement(item, "description").text = _item_description(path, text, artifacts_dir)

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")
    tree.write(rss_path, encoding="utf-8", xml_declaration=True)
    return rss_path


if __name__ == "__main__":
    print(generate_rss())
