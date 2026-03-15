#!/usr/bin/env python3
"""One-time backfill: populate catalog.jsonl from existing artifacts
and archive recent conversations as episodes.

Run: python backfill_catalog.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

# Add shared to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ARTIFACTS_DIR, MIRA_BRIDGE_DIR, EPISODES_DIR
from soul_manager import catalog_add, save_episode

WRITINGS_DIR = ARTIFACTS_DIR / "writings"
PUBLISHED_DIR = WRITINGS_DIR / "_published"
RESEARCH_DIR = ARTIFACTS_DIR / "research"
AUDIO_DIR = ARTIFACTS_DIR / "audio"
TASKS_DIR = MIRA_BRIDGE_DIR / "tasks"


def backfill_published_articles():
    """Catalog all published Substack articles."""
    if not PUBLISHED_DIR.exists():
        return 0
    count = 0
    for path in sorted(PUBLISHED_DIR.glob("*.md")):
        date_str = path.stem[:10]
        slug = path.stem[11:] if len(path.stem) > 11 else path.stem
        title = slug.replace("-", " ").title()
        # Try to extract real title from file content
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break
        catalog_add({
            "type": "article",
            "title": title,
            "date": date_str,
            "path": str(path),
            "topics": [],
            "status": "published",
        })
        count += 1
    print(f"  Published articles: {count}")
    return count


def backfill_writing_projects():
    """Catalog writing projects (drafts with project.json)."""
    if not WRITINGS_DIR.exists():
        return 0
    count = 0
    for d in WRITINGS_DIR.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        project_file = d / "project.json"
        final_file = d / "final.md"
        if not project_file.exists() and not final_file.exists():
            continue
        title = d.name.replace("-", " ").title()
        status = "draft"
        if project_file.exists():
            try:
                proj = json.loads(project_file.read_text(encoding="utf-8"))
                title = proj.get("title", title)
                status = "done" if proj.get("phase") == "done" else "draft"
            except (json.JSONDecodeError, OSError):
                pass
        # Try final.md for title
        if final_file.exists():
            for line in final_file.read_text(encoding="utf-8").splitlines()[:5]:
                if line.strip().startswith("# "):
                    title = line.strip()[2:].strip()
                    break
        catalog_add({
            "type": "essay",
            "title": title,
            "path": str(d),
            "topics": [],
            "status": status,
        })
        count += 1
    print(f"  Writing projects: {count}")
    return count


def backfill_research():
    """Catalog research outputs."""
    if not RESEARCH_DIR.exists():
        return 0
    count = 0
    for path in sorted(list(RESEARCH_DIR.glob("*.md")) + list(RESEARCH_DIR.glob("*.txt"))):
        title = path.stem.replace("_", " ").replace("-", " ").title()
        # Try to get title from content
        content = path.read_text(encoding="utf-8")
        for line in content.splitlines()[:5]:
            if line.strip().startswith("# "):
                title = line.strip()[2:].strip()
                break
        catalog_add({
            "type": "research",
            "title": title,
            "path": str(path),
            "topics": [],
            "status": "done",
        })
        count += 1
    # Research subdirectories
    for d in RESEARCH_DIR.iterdir():
        if not d.is_dir():
            continue
        output = d / "output.md"
        if not output.exists():
            continue
        title = d.name.replace("_", " ").replace("-", " ").title()
        catalog_add({
            "type": "research",
            "title": title,
            "path": str(d),
            "topics": [],
            "status": "done",
        })
        count += 1
    print(f"  Research: {count}")
    return count


def backfill_audio():
    """Catalog audio content (podcasts, voiceover)."""
    if not AUDIO_DIR.exists():
        return 0
    count = 0
    podcast_dir = AUDIO_DIR / "podcast"
    if podcast_dir.exists():
        for lang_dir in podcast_dir.iterdir():
            if not lang_dir.is_dir():
                continue
            for ep_dir in lang_dir.iterdir():
                if not ep_dir.is_dir():
                    continue
                title = ep_dir.name.replace("-", " ").replace("_", " ").title()
                catalog_add({
                    "type": "podcast",
                    "title": title,
                    "path": str(ep_dir),
                    "topics": [],
                    "status": "done",
                })
                count += 1
    for mp3 in AUDIO_DIR.glob("*.mp3"):
        catalog_add({
            "type": "audio",
            "title": mp3.stem.replace("-", " ").replace("_", " ").title(),
            "path": str(mp3),
            "topics": [],
            "status": "done",
        })
        count += 1
    print(f"  Audio: {count}")
    return count


def backfill_episodes():
    """Archive recent meaningful conversations as episodes."""
    if not TASKS_DIR.exists():
        return 0
    count = 0
    for task_file in sorted(TASKS_DIR.glob("task_*.json")):
        if task_file.name.endswith(".status.json") or task_file.name.endswith(".reply.json"):
            continue
        try:
            task = json.loads(task_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        messages = task.get("messages", [])
        # Only archive conversations with at least 2 substantive messages
        substantive = [m for m in messages
                      if not m.get("content", "").startswith('{"type":')
                      and len(m.get("content", "")) > 10]
        if len(substantive) < 2:
            continue
        task_id = task.get("id", task_file.stem)
        title = task.get("title", task_id)
        tags = task.get("tags", [])
        save_episode(task_id, title, messages, tags=tags)
        count += 1
    print(f"  Episodes: {count}")
    return count


def main():
    print("Backfilling content catalog and episodes...")
    total = 0
    total += backfill_published_articles()
    total += backfill_writing_projects()
    total += backfill_research()
    total += backfill_audio()
    print(f"\nCatalog: {total} entries added")

    ep_count = backfill_episodes()
    print(f"Episodes: {ep_count} conversations archived")

    print("\nDone! Run 'python memory_index.py rebuild' to index everything.")


if __name__ == "__main__":
    main()
