#!/usr/bin/env python3
"""Canonical writing shim with legacy compatibility commands."""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_writing_dir = Path(__file__).resolve().parent

_shared_dir = str(_writing_dir.parent / "shared")
if _shared_dir not in sys.path:
    sys.path.insert(0, _shared_dir)
_super_dir = str(_writing_dir.parent / "super")
if _super_dir not in sys.path:
    sys.path.insert(0, _super_dir)

from legacy_writing import IDEAS_DIR, advance_idea, parse_idea


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )


log = logging.getLogger("writing-pipeline")


def cmd_run():
    """Compatibility wrapper for legacy callers of writing_agent.cmd_run()."""
    log.warning("writing_agent.cmd_run is deprecated; delegating to canonical pipeline")
    return _run_canonical_pipeline()


def cmd_status():
    """Show canonical writing_workflow projects and any remaining legacy ideas."""
    canonical_projects = _iter_canonical_projects()
    if canonical_projects:
        print(f"\n{'Project':<35} {'Phase':<18} {'Version':<8} {'Updated'}")
        print("-" * 85)
        for project_dir, project in canonical_projects:
            phase = project.get("phase", "-")
            version = project.get("version", "-")
            updated = project.get("updated_at", "-")
            print(f"{project_dir.name:<35} {phase:<18} {version!s:<8} {updated}")
        print()
    else:
        print("No canonical writing_workflow projects found.")

    IDEAS_DIR.mkdir(exist_ok=True)
    idea_files = sorted(
        path for path in IDEAS_DIR.glob("*.md") if not path.name.startswith("_")
    )

    if not idea_files:
        return

    print("Legacy idea files still present:")
    for idea_path in idea_files:
        idea = parse_idea(idea_path)
        state = idea.get("state", "new")
        print(f"  {idea['slug']}: {state}")


def _cmd_iterate_legacy(slug: str):
    """Legacy idea-file iterator kept for backward compatibility."""
    idea_path = IDEAS_DIR / f"{slug}.md"

    if not idea_path.exists():
        for path in IDEAS_DIR.glob("*.md"):
            if path.name.startswith("_"):
                continue
            idea = parse_idea(path)
            if idea.get("project_dir", "").endswith(slug):
                idea_path = path
                break

    if not idea_path.exists():
        available = [path.stem for path in IDEAS_DIR.glob("*.md") if not path.name.startswith("_")]
        print(f"[legacy] No idea file found for '{slug}'")
        print(f"[legacy] Available: {available}")
        return

    idea = parse_idea(idea_path)
    state = idea.get("state", "new")
    print(f"[legacy] Current state: {state}")

    if advance_idea(idea):
        idea = parse_idea(idea_path)
        print(f"[legacy] Advanced to: {idea.get('state', 'unknown')}")
    else:
        print("[legacy] No progress made (already done, error, or awaiting feedback)")


def cmd_iterate(slug: str):
    """Advance canonical projects when available; otherwise fall back to legacy ideas."""
    project_match = _find_canonical_project(slug)
    if project_match:
        project_dir, project = project_match
        phase = project.get("phase", "unknown")
        print(f"Canonical project {project_dir.name}: phase={phase}")
        if phase == "plan_ready":
            _, advance_project = _get_canonical_writing_ops()
            advance_project(project_dir)
            refreshed = {
                workspace: latest_project
                for workspace, latest_project in _iter_canonical_projects()
            }
            latest = refreshed.get(project_dir, project)
            print(f"Advanced to: {latest.get('phase', 'unknown')}")
        elif phase == "draft_ready":
            print("Canonical project is waiting for feedback; not advancing automatically.")
        else:
            print("Canonical project is not in an advanceable phase.")
        return

    print(f"No canonical project found for '{slug}', falling back to legacy idea files.")
    _cmd_iterate_legacy(slug)


def _cmd_new_legacy():
    """Show template for creating legacy idea files."""
    template_path = IDEAS_DIR / "_template.md"

    if template_path.exists():
        print("\nCopy the template to create a new idea:")
        print(f"  cp '{template_path}' '{IDEAS_DIR}/my-idea.md'")
        print("\nThen edit the file to fill in your idea.")
    else:
        print(f"Template not found at {template_path}")

    IDEAS_DIR.mkdir(exist_ok=True)
    existing = [path.stem for path in IDEAS_DIR.glob("*.md") if not path.name.startswith("_")]
    if existing:
        print(f"\nExisting ideas: {existing}")


def cmd_new():
    """Point users to the canonical workflow while keeping legacy guidance available."""
    print("Canonical writing does not use idea-file scaffolds anymore.")
    print("Use the main agent path or `core.py write-from-plan` for new canonical projects.")
    _cmd_new_legacy()


def cmd_sync():
    """Sync Apple Notes for the legacy idea-file workflow only."""
    print("Apple Notes sync is legacy-only and not part of the canonical writing workflow.")
    from notes_sync import sync_notes

    synced = sync_notes()
    if synced:
        print(f"[legacy] Synced {len(synced)} notes: {synced}")
    else:
        print("[legacy] No changes from Apple Notes")


def _get_canonical_writing_ops():
    """Return the canonical writing workflow functions."""
    from writing_workflow import advance_project, check_writing_responses

    return check_writing_responses, advance_project


def _run_canonical_pipeline() -> int:
    """Scheduler shim: advance canonical writing_workflow projects only."""
    check_writing_responses, advance_project = _get_canonical_writing_ops()
    advanced = 0
    for response in check_writing_responses():
        phase = response["project"].get("phase", "")
        if phase == "plan_ready":
            advance_project(response["workspace"])
            advanced += 1
    log.info("Canonical writing pipeline advanced %d project(s)", advanced)
    return advanced


def _get_canonical_autowrite_runner():
    """Return the canonical autonomous-writing entry point."""
    from workflows.writing import run_autowrite_pipeline

    return run_autowrite_pipeline


def _run_canonical_autowrite(
    title: str,
    writing_type: str,
    idea_content: str,
    task_id: str = "",
):
    """Compatibility shim for the old `writing_agent.py auto` command."""
    task_id = task_id or f"autowrite_{datetime.now().strftime('%Y-%m-%d')}"
    log.warning("writing_agent.py auto is deprecated; delegating to canonical autowrite")
    runner = _get_canonical_autowrite_runner()
    runner(task_id, title, writing_type, idea_content)


def _iter_canonical_projects():
    """Return canonical writing_workflow projects from the shared workspace."""
    from config import WORKSPACE_DIR

    projects = []
    for project_dir in sorted(WORKSPACE_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        project_file = project_dir / "project.json"
        if not project_file.exists():
            continue
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Skipping canonical project %s: %s", project_dir, exc)
            continue
        projects.append((project_dir, project))
    return projects


def _find_canonical_project(slug: str):
    """Find a canonical writing_workflow project by directory name or title."""
    for project_dir, project in _iter_canonical_projects():
        if project_dir.name == slug or project.get("title") == slug:
            return project_dir, project
    return None


def cmd_auto(title: str, writing_type: str, idea_content: str):
    """Compatibility wrapper for legacy callers of writing_agent.cmd_auto()."""
    log.warning("writing_agent.cmd_auto is deprecated; delegating to canonical autowrite")
    return _run_canonical_autowrite(title, writing_type, idea_content)


USAGE = """Usage: writing_agent.py <command> [args]

Commands:
    run                 Canonical writing_workflow scheduler shim
    status              Show canonical projects and remaining legacy ideas
    iterate <slug>      Canonical-first manual advance with legacy fallback
    sync                Sync Apple Notes -> legacy idea files
    new                 Show guidance for canonical writing and legacy templates
    auto                Canonical autowrite shim (deprecated command)
"""


def main():
    setup_logging()

    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    command = sys.argv[1]

    args = sys.argv[2:]
    flags = {}
    index = 0
    while index < len(args):
        if args[index].startswith("--") and index + 1 < len(args):
            flags[args[index][2:]] = args[index + 1]
            index += 2
        else:
            index += 1

    if command == "run":
        _run_canonical_pipeline()
    elif command == "status":
        cmd_status()
    elif command == "sync":
        cmd_sync()
    elif command == "new":
        cmd_new()
    elif command == "iterate":
        if len(sys.argv) < 3:
            print("Usage: writing_agent.py iterate <idea-slug>")
            sys.exit(1)
        cmd_iterate(sys.argv[2])
    elif command == "auto":
        title = flags.get("title", "Untitled")
        writing_type = flags.get("type", "essay")
        idea = flags.get("idea", "")
        task_id = flags.get("task-id", "")
        _run_canonical_autowrite(title, writing_type, idea, task_id=task_id)
    else:
        print(f"Unknown command: {command}")
        print(USAGE)
        sys.exit(1)


if __name__ == "__main__":
    main()
