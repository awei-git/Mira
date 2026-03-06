"""Manage the agent's soul: identity, memory, interests, skills."""
import json
import logging
from datetime import datetime
from pathlib import Path

from config import (
    IDENTITY_FILE, MEMORY_FILE, INTERESTS_FILE,
    SKILLS_DIR, SKILLS_INDEX, SKILLS_FILE, MAX_MEMORY_LINES,
    PLAYGROUND_ROOT,
)

log = logging.getLogger("mira")


def load_soul() -> dict:
    """Load the full soul context. Returns dict with identity, memory, interests, skills."""
    return {
        "identity": _read_or_default(IDENTITY_FILE, "No identity defined yet."),
        "memory": _read_or_default(MEMORY_FILE, "No memories yet."),
        "interests": _read_or_default(INTERESTS_FILE, "No interests defined yet."),
        "skills": load_skills_summary(),
    }


def format_soul(soul: dict) -> str:
    """Format the full soul as a string for injection into prompts."""
    parts = [
        "# My Identity\n",
        soul["identity"],
        "\n\n# My Memory\n",
        soul["memory"],
        "\n\n# My Current Interests\n",
        soul["interests"],
    ]
    if soul["skills"]:
        parts.append("\n\n# My Skills\n")
        parts.append(soul["skills"])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def append_memory(entry: str):
    """Append a timestamped entry to memory. Enforces MAX_MEMORY_LINES."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- [{ts}] {entry}\n"

    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8")
    else:
        text = "# Memory\n\n"

    text += line

    # Enforce line limit — trim oldest entries if over MAX_MEMORY_LINES
    lines = text.split("\n")
    if len(lines) > MAX_MEMORY_LINES:
        header = lines[:2]
        entries = lines[2:]
        trimmed = entries[-(MAX_MEMORY_LINES - 2):]
        text = "\n".join(header + trimmed)
        log.info("Memory trimmed to %d lines", MAX_MEMORY_LINES)

    MEMORY_FILE.write_text(text, encoding="utf-8")
    log.info("Memory +: %s", entry[:80])


def update_memory(new_content: str):
    """Replace memory file with new content (used by reflect mode)."""
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(new_content, encoding="utf-8")
    log.info("Memory updated (%d lines)", new_content.count("\n"))


def get_memory_size() -> int:
    """Return line count of memory file."""
    if not MEMORY_FILE.exists():
        return 0
    return MEMORY_FILE.read_text(encoding="utf-8").count("\n")


# ---------------------------------------------------------------------------
# Interests
# ---------------------------------------------------------------------------

def update_interests(new_content: str):
    """Replace interests file."""
    INTERESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    INTERESTS_FILE.write_text(new_content, encoding="utf-8")
    log.info("Interests updated")


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def load_skills_summary() -> str:
    """Load a summary of all skills (names + one-liners)."""
    if not SKILLS_INDEX.exists():
        return ""
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    lines = []
    for skill in index:
        lines.append(f"- **{skill['name']}**: {skill.get('description', '')}")
    return "\n".join(lines)


def load_skill(name: str) -> str:
    """Load a specific skill file's full content."""
    # Normalize name to filename
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def save_skill(name: str, description: str, content: str):
    """Save a new skill and update the index."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "-")
    path = SKILLS_DIR / f"{slug}.md"
    path.write_text(content, encoding="utf-8")

    # Update index
    index = []
    if SKILLS_INDEX.exists():
        try:
            index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Remove old entry with same name if exists
    index = [s for s in index if s["name"] != name]
    index.append({
        "name": name,
        "description": description,
        "file": f"{slug}.md",
        "created": datetime.now().isoformat(),
    })

    SKILLS_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("Saved skill: %s", name)

    # Keep skills.md in sync
    rebuild_skills_md()

    # Sync actionable skills to CLAUDE.md for Claude Code sessions
    _sync_skills_to_claude_md()


def rebuild_skills_md():
    """Regenerate soul/skills.md from index + individual skill files."""
    if not SKILLS_INDEX.exists():
        return
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    lines = [f"# Skills ({len(index)} learned)\n"]
    for entry in index:
        name = entry["name"]
        desc = entry.get("description", "")
        added = entry.get("added") or entry.get("created", "")[:10]
        lines.append(f"## {name}")
        lines.append(f"*{desc}*  ")
        if added:
            lines.append(f"Learned: {added}  ")
        # Include the full skill content
        skill_file = SKILLS_DIR / entry.get("file", "")
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8").strip()
            lines.append("")
            lines.append(content)
        lines.append("\n---\n")

    SKILLS_FILE.write_text("\n".join(lines), encoding="utf-8")
    log.info("Rebuilt skills.md (%d skills)", len(index))


# ---------------------------------------------------------------------------
# Claude Code skill sync
# ---------------------------------------------------------------------------

# Tags that indicate a skill is actionable for Claude Code sessions
_ACTIONABLE_TAGS = {"writing", "craft", "fiction", "dialogue", "video", "editing",
                    "agents", "coding", "architecture", "tool-use", "debugging"}

# CLAUDE.md lives at MtJoy root so all Claude Code sessions in MtJoy see it
_CLAUDE_MD = PLAYGROUND_ROOT.parent / "CLAUDE.md"


def _sync_skills_to_claude_md():
    """Rebuild the skills section of MtJoy/CLAUDE.md from the skills index.

    Only promotes skills with actionable tags. Keeps existing non-skill
    content in CLAUDE.md intact.
    """
    if not SKILLS_INDEX.exists():
        return
    try:
        index = json.loads(SKILLS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    # Filter: only skills with at least one actionable tag
    actionable = []
    for skill in index:
        tags = set(skill.get("tags", []))
        if tags & _ACTIONABLE_TAGS:
            actionable.append(skill)

    if not actionable:
        return

    # Build skills section — concise one-liners only.
    # Full skill content stays in soul/skills/ for the agent's own use.
    skill_lines = ["## Learned Skills (auto-synced from Mira agent)", ""]
    for skill in actionable:
        name = skill["name"]
        desc = skill.get("description", "")
        tags = ", ".join(skill.get("tags", []))
        skill_lines.append(f"- **{name}** [{tags}]: {desc}")
    skill_lines.append("")
    skill_lines.append(f"Full skill details: `Mira/soul/skills/`")
    skill_lines.append("")

    skills_block = "\n".join(skill_lines)

    # Read existing CLAUDE.md or start fresh
    MARKER_START = "<!-- MIRA-SKILLS-START -->"
    MARKER_END = "<!-- MIRA-SKILLS-END -->"

    if _CLAUDE_MD.exists():
        existing = _CLAUDE_MD.read_text(encoding="utf-8")
    else:
        existing = ""

    # Replace or append the skills section
    if MARKER_START in existing:
        before = existing[:existing.index(MARKER_START)]
        after_marker = existing[existing.index(MARKER_END) + len(MARKER_END):]
        new_content = f"{before}{MARKER_START}\n{skills_block}\n{MARKER_END}{after_marker}"
    else:
        if existing:
            new_content = f"{existing}\n\n{MARKER_START}\n{skills_block}\n{MARKER_END}\n"
        else:
            new_content = f"{MARKER_START}\n{skills_block}\n{MARKER_END}\n"

    _CLAUDE_MD.write_text(new_content, encoding="utf-8")
    log.info("Synced %d actionable skills to CLAUDE.md", len(actionable))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_or_default(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return default
