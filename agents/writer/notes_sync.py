"""Sync Apple Notes → markdown idea files.

Reads notes from the "写作想法" folder in Apple Notes via JXA (osascript),
parses the simple label format, and generates/updates markdown idea files
that the writing pipeline processes.
"""

import hashlib
import json
import logging
import re
import subprocess
import unicodedata
from pathlib import Path

# Load writing-specific config by file path (avoid collision with agent/config.py)
import importlib.util as _ilu
_wcfg_path = Path(__file__).resolve().parent / "writer_config.py"
_spec = _ilu.spec_from_file_location("writing_config", _wcfg_path)
_wcfg = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_wcfg)

IDEAS_DIR = _wcfg.IDEAS_DIR
NOTES_FOLDER_NAME = _wcfg.NOTES_FOLDER_NAME
NOTES_SYNC_STATE = _wcfg.NOTES_SYNC_STATE

log = logging.getLogger("writing-pipeline")

# ---------------------------------------------------------------------------
# Note status (user adds "Status: done/wip/update" as a metadata line)
# ---------------------------------------------------------------------------
# Status: done   — ready to process through the pipeline
# Status: wip    — work in progress, skip
# Status: update — content updated, re-sync and re-process
# (Apple Notes swallows #hashtags as native tag objects, so we use plain text)

VALID_STATUSES = {"done", "wip", "update"}


def parse_note_status(body: str) -> tuple[str, str]:
    """Extract a Status label from the metadata lines at the top of the note.

    Looks for "Status: done", "Status: wip", or "Status: update" (case-insensitive).
    Also accepts Chinese colon "：".

    Returns (status, body_without_status_line). Status is one of "done", "wip",
    "update", or "" if not found.
    """
    lines = body.split("\n")
    status = ""
    remaining = []
    for line in lines:
        m = re.match(r"^\s*[Ss]tatus[:\uff1a]\s*(\w+)", line)
        if m and not status:  # take the first match only
            val = m.group(1).strip().lower()
            if val in VALID_STATUSES:
                status = val
                continue  # strip this line from the body
        remaining.append(line)
    return status, "\n".join(remaining)


# ---------------------------------------------------------------------------
# HTML → plain text (Apple Notes stores content as HTML)
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Convert Apple Notes HTML body to plain text with proper line breaks.

    Apple Notes uses <div> for paragraphs and <br> for line breaks.
    The first <div> is usually the note title (we skip it since we get name separately).
    """
    # Remove the first <div>...</div> (it's the title, duplicated from note.name())
    html = re.sub(r"^<div>.*?</div>\s*", "", html, count=1)

    # Replace </div><div> boundaries with newlines
    text = re.sub(r"</div>\s*<div>", "\n", html)
    # Replace remaining <div> and </div>
    text = re.sub(r"</?div>", "\n", text)
    # <br> → newline
    text = re.sub(r"<br\s*/?>", "\n", text)
    # <li> items → bullet points
    text = re.sub(r"<li>", "- ", text)
    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&nbsp;", " ")
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# JXA interface
# ---------------------------------------------------------------------------

JXA_SCRIPT = """
'use strict';
const Notes = Application('Notes');
const folderName = '__FOLDER__';

// Find the folder
let folder = null;
const folders = Notes.folders();
for (let i = 0; i < folders.length; i++) {
    if (folders[i].name() === folderName) {
        folder = folders[i];
        break;
    }
}

if (!folder) {
    JSON.stringify([]);
} else {
    const notes = folder.notes();
    const result = [];
    for (let i = 0; i < notes.length; i++) {
        const n = notes[i];
        result.push({
            id: n.id(),
            name: n.name(),
            body: n.body(),
            modification_date: n.modificationDate().toISOString()
        });
    }
    JSON.stringify(result);
}
""".strip()


# JXA script to reset a note's Status from "update" back to "done"
JXA_RESET_STATUS = """
'use strict';
const Notes = Application('Notes');
const noteId = '__NOTE_ID__';

const allNotes = Notes.notes();
let targetNote = null;
for (let i = 0; i < allNotes.length; i++) {
    if (allNotes[i].id() === noteId) {
        targetNote = allNotes[i];
        break;
    }
}

if (targetNote) {
    let body = targetNote.body();
    body = body.replace(/Status[:\\uff1a]\\s*update/i, 'Status: done');
    targetNote.body = body;
    'ok';
} else {
    'not_found';
}
""".strip()


def reset_note_status(note_id: str) -> bool:
    """Reset Apple Note status from 'update' back to 'done' via JXA.

    Called after processing a Status: update note so the trigger becomes
    one-shot — user can set 'update' again next time without toggling.

    Returns True if the reset succeeded, False otherwise.
    """
    script = JXA_RESET_STATUS.replace("__NOTE_ID__", note_id)
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            log.info("Reset note %s status to 'done'", note_id[:20])
            return True
        else:
            log.warning("Failed to reset note status: %s", result.stderr[:200])
            return False
    except Exception as e:
        log.warning("Could not reset note status: %s", e)
        return False


def fetch_notes() -> list[dict]:
    """Fetch all notes from the 写作想法 folder via JXA.

    Returns list of {id, name, body, modification_date}.
    """
    script = JXA_SCRIPT.replace("__FOLDER__", NOTES_FOLDER_NAME)

    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("JXA timed out reading Apple Notes")
        return []

    if result.returncode != 0:
        log.error("JXA failed (exit %d): %s", result.returncode, result.stderr[:300])
        return []

    stdout = result.stdout.strip()
    if not stdout:
        log.info("No notes found in '%s' folder", NOTES_FOLDER_NAME)
        return []

    try:
        notes = json.loads(stdout)
    except json.JSONDecodeError as e:
        log.error("Failed to parse JXA output as JSON: %s", e)
        return []

    # Convert HTML bodies to plain text
    for note in notes:
        note["body"] = html_to_text(note["body"])

    log.info("Fetched %d notes from '%s'", len(notes), NOTES_FOLDER_NAME)
    return notes


# ---------------------------------------------------------------------------
# Note body parser
# ---------------------------------------------------------------------------

# Metadata labels at the top of the note (key: value)
META_LABELS = {
    "类型": "type",
    "语言": "language",
    "平台": "platform",
    "字数": "target_words",
}

# Section headers that delimit content blocks
SECTION_HEADERS = ["主题", "要点", "备注", "反馈"]

# Map section headers to markdown fields
SECTION_MAP = {
    "主题": "theme",
    "要点": "key_points",
    "备注": "notes",
    "反馈": "feedback",
}

META_DEFAULTS = {
    "type": "essay",
    "language": "中文",
    "platform": "Substack",
    "target_words": "3000",
}


def parse_note_body(body: str) -> dict:
    """Parse a note with simple Chinese labels into a structured dict.

    Returns {type, language, platform, target_words, theme, key_points, notes, feedback}.
    """
    result = dict(META_DEFAULTS)
    lines = body.split("\n")

    # Phase 1: parse metadata lines at the top (before first section header)
    content_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Check if this line is a section header
        if stripped in SECTION_HEADERS:
            content_start = i
            break
        # Check for metadata: "key: value" or "key：value"
        meta_match = re.match(r"^(.+?)[:\uff1a]\s*(.+)$", stripped)
        if meta_match:
            label = meta_match.group(1).strip()
            value = meta_match.group(2).strip()
            if label in META_LABELS:
                result[META_LABELS[label]] = value
        content_start = i + 1

    # Phase 2: split remaining lines by section headers
    current_section = None
    sections = {}

    for line in lines[content_start:]:
        stripped = line.strip()
        if stripped in SECTION_HEADERS:
            current_section = SECTION_MAP[stripped]
            sections[current_section] = []
        elif current_section is not None:
            sections[current_section].append(line)

    # Join section content and strip
    for key in SECTION_MAP.values():
        content = "\n".join(sections.get(key, [])).strip()
        result[key] = content

    return result


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def title_to_slug(title: str) -> str:
    """Convert a title (possibly Chinese) to a filesystem-safe slug.

    Tries pypinyin if available, otherwise uses ASCII transliteration + hash.
    """
    # Try pypinyin for proper Chinese → pinyin conversion
    try:
        from pypinyin import lazy_pinyin
        parts = lazy_pinyin(title)
        slug = "-".join(parts)
    except ImportError:
        # Fallback: keep ASCII, replace non-ASCII with short hash
        ascii_parts = []
        non_ascii_chunk = []
        for ch in title:
            if ch.isascii() and ch.isalnum():
                if non_ascii_chunk:
                    chunk = "".join(non_ascii_chunk)
                    h = hashlib.md5(chunk.encode()).hexdigest()[:4]
                    ascii_parts.append(h)
                    non_ascii_chunk = []
                ascii_parts.append(ch.lower())
            elif ch.isascii() and (ch == " " or ch == "-" or ch == "_"):
                if non_ascii_chunk:
                    chunk = "".join(non_ascii_chunk)
                    h = hashlib.md5(chunk.encode()).hexdigest()[:4]
                    ascii_parts.append(h)
                    non_ascii_chunk = []
                ascii_parts.append("-")
            elif not ch.isascii():
                non_ascii_chunk.append(ch)

        if non_ascii_chunk:
            chunk = "".join(non_ascii_chunk)
            h = hashlib.md5(chunk.encode()).hexdigest()[:4]
            ascii_parts.append(h)

        slug = "-".join(ascii_parts) if ascii_parts else hashlib.md5(title.encode()).hexdigest()[:8]

    # Normalize: lowercase, collapse dashes, strip edges
    slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")

    return slug or "untitled"


# ---------------------------------------------------------------------------
# Sync state management
# ---------------------------------------------------------------------------

def load_sync_state() -> dict:
    """Load sync state from .notes_sync.json."""
    if NOTES_SYNC_STATE.exists():
        try:
            return json.loads(NOTES_SYNC_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load sync state: %s", e)
    return {}


def save_sync_state(state: dict):
    """Save sync state to .notes_sync.json."""
    NOTES_SYNC_STATE.parent.mkdir(parents=True, exist_ok=True)
    NOTES_SYNC_STATE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def content_hash(text: str) -> str:
    """Short hash of text for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def generate_idea_markdown(title: str, parsed: dict) -> str:
    """Generate markdown matching _template.md format."""
    lines = [
        f"# {title}",
        "",
        f"- **type**: {parsed.get('type', 'essay')}",
        f"- **language**: {parsed.get('language', '中文')}",
        f"- **platform**: {parsed.get('platform', 'Substack')}",
        f"- **target_words**: {parsed.get('target_words', '3000')}",
        "- **deadline**:",
        "",
        "## Theme",
        "",
        parsed.get("theme", ""),
        "",
        "## Key Points",
        "",
        parsed.get("key_points", ""),
        "",
        "## Notes",
        "",
        parsed.get("notes", ""),
        "",
        "## Feedback",
        "",
        parsed.get("feedback", ""),
        "",
        "---",
        "<!-- AUTO-MANAGED BELOW — DO NOT EDIT -->",
        "## Status",
        "",
        "- **state**: new",
        "- **project_dir**:",
        "- **created**:",
        "- **scaffolded**:",
        "- **round_1_draft**:",
        "- **round_1_critique**:",
        "- **round_1_revision**:",
        "- **feedback_detected**:",
        "- **round_2_draft**:",
        "- **round_2_critique**:",
        "- **round_2_revision**:",
        "- **current_round**: 0",
        "- **idea_hash**:",
        "- **last_error**:",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Update existing markdown (preserve Status section)
# ---------------------------------------------------------------------------

def _reset_idea_state(idea_path: Path):
    """Reset the pipeline state fields so the idea gets re-processed.

    Called when Status: update forces a re-sync. Sets state to 'restart'
    and clears timestamps/errors so the pipeline will re-scaffold.
    """
    text = idea_path.read_text(encoding="utf-8")
    resets = {
        "state": "restart",
        "last_error": "",
        "scaffolded": "",
        "round_1_draft": "",
        "round_1_critique": "",
        "round_1_revision": "",
        "feedback_detected": "",
        "round_2_draft": "",
        "round_2_critique": "",
        "round_2_revision": "",
        "current_round": "0",
    }
    for key, value in resets.items():
        pattern = rf"(^[ \t]*-[ \t]*\*\*{re.escape(key)}\*\*:[ \t]*)(.*)$"
        replacement = rf"\g<1>{value}"
        text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
    idea_path.write_text(text, encoding="utf-8")
    log.info("Reset pipeline state for %s to 'restart'", idea_path.name)


def update_idea_content(idea_path: Path, title: str, parsed: dict):
    """Update the content sections of an existing idea file, preserving Status."""
    text = idea_path.read_text(encoding="utf-8")

    # Split at the auto-managed marker
    marker = "<!-- AUTO-MANAGED BELOW"
    parts = text.split(marker)
    if len(parts) < 2:
        log.warning("No AUTO-MANAGED marker in %s, rewriting fully", idea_path)
        idea_path.write_text(generate_idea_markdown(title, parsed), encoding="utf-8")
        return

    # Rebuild the content section above the marker
    new_above = "\n".join([
        f"# {title}",
        "",
        f"- **type**: {parsed.get('type', 'essay')}",
        f"- **language**: {parsed.get('language', '中文')}",
        f"- **platform**: {parsed.get('platform', 'Substack')}",
        f"- **target_words**: {parsed.get('target_words', '3000')}",
        "- **deadline**:",
        "",
        "## Theme",
        "",
        parsed.get("theme", ""),
        "",
        "## Key Points",
        "",
        parsed.get("key_points", ""),
        "",
        "## Notes",
        "",
        parsed.get("notes", ""),
        "",
        "## Feedback",
        "",
        parsed.get("feedback", ""),
        "",
        "---",
    ])

    new_text = new_above + "\n" + marker + parts[1]
    idea_path.write_text(new_text, encoding="utf-8")
    log.info("Updated content in %s", idea_path.name)


def update_idea_feedback(idea_path: Path, feedback: str):
    """Update just the Feedback section, preserving everything else."""
    text = idea_path.read_text(encoding="utf-8")

    # Replace content between ## Feedback and ---/AUTO-MANAGED
    new_text = re.sub(
        r"(## Feedback\s*\n).*?(\n---\n)",
        rf"\g<1>\n{feedback}\n\g<2>",
        text,
        flags=re.DOTALL,
    )
    idea_path.write_text(new_text, encoding="utf-8")
    log.info("Updated feedback in %s", idea_path.name)


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def find_unique_slug(slug: str, note_id: str, sync_state: dict) -> str:
    """Ensure slug is unique — append -2, -3 etc. if needed."""
    # Check if another note already owns this slug
    existing_ids = {
        v["slug"]: nid for nid, v in sync_state.items()
    }

    candidate = slug
    counter = 2
    while candidate in existing_ids and existing_ids[candidate] != note_id:
        candidate = f"{slug}-{counter}"
        counter += 1

    # Also check filesystem for manually created files
    while (IDEAS_DIR / f"{candidate}.md").exists():
        # File exists — is it ours (from this note_id)?
        if note_id in sync_state and sync_state[note_id].get("slug") == candidate:
            break
        candidate = f"{slug}-{counter}"
        counter += 1

    return candidate


def sync_notes() -> list[str]:
    """Sync Apple Notes → markdown idea files. Returns list of updated slugs."""
    notes = fetch_notes()
    if not notes:
        return []

    sync_state = load_sync_state()
    updated = []

    for note in notes:
        note_id = note["id"]
        title = note["name"].strip()
        body = note["body"].strip()

        # Extract status label (Status: done/wip/update)
        status, body = parse_note_status(body)

        # Skip notes without a status or marked wip
        if not status or status == "wip":
            if status == "wip":
                log.info("Skipping note '%s' — Status: wip", title)
            else:
                log.info("Skipping note '%s' — no Status line (add 'Status: done')", title)
            continue

        # Parse note content (tag already stripped from body)
        parsed = parse_note_body(body)

        # Require at least a theme
        if not parsed.get("theme"):
            log.warning("Skipping note '%s' — no 主题 section", title)
            continue

        # Calculate hashes for change detection
        # Content = everything except feedback
        feedback_text = parsed.get("feedback", "")
        body_without_feedback = re.sub(
            r"反馈\s*\n.*",
            "",
            body,
            flags=re.DOTALL,
        ).strip()
        c_hash = content_hash(body_without_feedback)
        f_hash = content_hash(feedback_text) if feedback_text else ""

        if note_id in sync_state:
            # Existing note — check for changes
            entry = sync_state[note_id]
            slug = entry["slug"]
            idea_path = IDEAS_DIR / f"{slug}.md"

            # "update" forces a re-sync regardless of hash — but only once.
            # After processing, we set update_synced=True so we don't loop.
            # Clear the flag when user switches back to "done".
            force_update = (status == "update")
            if status != "update":
                entry.pop("update_synced", None)
            if force_update and entry.get("update_synced") and entry.get("content_hash") == c_hash:
                # Already handled this update with same content — skip
                force_update = False

            if force_update or entry.get("content_hash") != c_hash:
                # Content changed (or forced) → update markdown content
                reason = "#update tag" if force_update else "content changed"
                log.info("Note '%s' %s, updating %s", title, reason, slug)
                if idea_path.exists():
                    update_idea_content(idea_path, title, parsed)
                    # If force_update, reset pipeline state so it re-processes
                    if force_update:
                        _reset_idea_state(idea_path)
                else:
                    idea_path.write_text(
                        generate_idea_markdown(title, parsed),
                        encoding="utf-8",
                    )
                entry["content_hash"] = c_hash
                entry["feedback_hash"] = f_hash
                entry["mod_date"] = note["modification_date"]
                if force_update:
                    if reset_note_status(note_id):
                        entry["update_synced"] = True
                    # If reset fails, update_synced stays unset so
                    # next run will retry the reset.
                updated.append(slug)

            elif f_hash and entry.get("feedback_hash") != f_hash:
                # Only feedback changed
                log.info("Note '%s' feedback changed, updating %s", title, slug)
                if idea_path.exists():
                    update_idea_feedback(idea_path, feedback_text)
                else:
                    idea_path.write_text(
                        generate_idea_markdown(title, parsed),
                        encoding="utf-8",
                    )
                entry["feedback_hash"] = f_hash
                entry["mod_date"] = note["modification_date"]
                updated.append(slug)

            else:
                # No changes
                pass

        else:
            # New note with done or update → create markdown
            slug = title_to_slug(title)
            slug = find_unique_slug(slug, note_id, sync_state)

            log.info("New note '%s' [Status: %s] → %s.md", title, status, slug)
            idea_path = IDEAS_DIR / f"{slug}.md"
            idea_path.write_text(
                generate_idea_markdown(title, parsed),
                encoding="utf-8",
            )

            sync_state[note_id] = {
                "slug": slug,
                "mod_date": note["modification_date"],
                "content_hash": c_hash,
                "feedback_hash": f_hash,
            }
            updated.append(slug)

    # Check for deleted notes (notes in state but not in fetched list)
    fetched_ids = {n["id"] for n in notes}
    for note_id in list(sync_state.keys()):
        if note_id not in fetched_ids:
            slug = sync_state[note_id]["slug"]
            log.warning(
                "Note '%s' no longer in '%s' folder (keeping markdown)",
                slug,
                NOTES_FOLDER_NAME,
            )

    save_sync_state(sync_state)
    return updated
