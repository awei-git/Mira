"""Apple Notes read/write bridge via JXA and AppleScript."""
import hashlib
import json
import logging
import re
import subprocess

from config import NOTES_INBOX_FOLDER, NOTES_SYNC_STATE

log = logging.getLogger("mira")

# ---------------------------------------------------------------------------
# HTML → plain text (reused from writing pipeline)
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Convert Apple Notes HTML body to plain text."""
    # Remove the first <div>...</div> (title duplication)
    html = re.sub(r"^<div>.*?</div>\s*", "", html, count=1)
    text = re.sub(r"</div>\s*<div>", "\n", html)
    text = re.sub(r"</?div>", "\n", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<li>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&nbsp;", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Read notes from a folder
# ---------------------------------------------------------------------------

JXA_FETCH = """
'use strict';
const Notes = Application('Notes');
const folderName = '__FOLDER__';
let folder = null;
const folders = Notes.folders();
for (let i = 0; i < folders.length; i++) {
    if (folders[i].name() === folderName) { folder = folders[i]; break; }
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


def fetch_notes(folder: str = NOTES_INBOX_FOLDER) -> list[dict]:
    """Fetch all notes from a Notes folder. Returns list of {id, name, body, modification_date}."""
    script = JXA_FETCH.replace("__FOLDER__", folder)
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("JXA timed out reading folder '%s'", folder)
        return []

    if result.returncode != 0:
        log.error("JXA failed reading '%s': %s", folder, result.stderr[:300])
        return []

    stdout = result.stdout.strip()
    if not stdout:
        return []

    try:
        notes = json.loads(stdout)
    except json.JSONDecodeError as e:
        log.error("Failed to parse JXA output: %s", e)
        return []

    for note in notes:
        note["body"] = html_to_text(note["body"])

    return notes


# ---------------------------------------------------------------------------
# Create a note in a folder
# ---------------------------------------------------------------------------

def _escape_applescript(s: str) -> str:
    """Escape a string for use in AppleScript."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def create_note(folder: str, title: str, body: str) -> bool:
    """Create a new note in the specified Apple Notes folder.

    Body should be plain text — we wrap it in basic HTML.
    Returns True on success.
    """
    # Convert plain text to basic HTML
    html_lines = []
    for line in body.split("\n"):
        if line.strip():
            html_lines.append(f"<div>{line}</div>")
        else:
            html_lines.append("<div><br></div>")
    html_body = "".join(html_lines)

    escaped_title = _escape_applescript(title)
    escaped_body = _escape_applescript(html_body)

    applescript = f'''
tell application "Notes"
    tell account "iCloud"
        if not (exists folder "{_escape_applescript(folder)}") then
            make new folder with properties {{name:"{_escape_applescript(folder)}"}}
        end if
        tell folder "{_escape_applescript(folder)}"
            make new note with properties {{name:"{escaped_title}", body:"{escaped_body}"}}
        end tell
    end tell
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            log.error("Failed to create note '%s': %s", title, result.stderr[:300])
            return False
        log.info("Created note '%s' in '%s'", title, folder)
        return True
    except subprocess.TimeoutExpired:
        log.error("Timed out creating note '%s'", title)
        return False


# ---------------------------------------------------------------------------
# Inbox sync (detect new/changed requests)
# ---------------------------------------------------------------------------

def load_sync_state() -> dict:
    if NOTES_SYNC_STATE.exists():
        try:
            return json.loads(NOTES_SYNC_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_sync_state(state: dict):
    NOTES_SYNC_STATE.parent.mkdir(parents=True, exist_ok=True)
    NOTES_SYNC_STATE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _parse_status(body: str) -> str:
    """Extract Status value from note body. Returns lowercase status or ''."""
    for line in body.split("\n"):
        m = re.match(r"^\s*[Ss]tatus[:\uff1a]\s*(\w+)", line)
        if m:
            return m.group(1).strip().lower()
    return ""


def check_inbox() -> list[dict]:
    """Check for new or changed notes in the Mira inbox.

    Returns list of {id, title, body, status} for notes that need processing.

    Status rules:
      - (no status) or "new": new request → process it
      - "wip": agent is working on it → skip
      - "update": user edited/added comments → re-process with updated body
      - "done": user is finished → skip (for writing projects, check_writing_responses handles this)
      - "processed": fully done → skip
    """
    notes = fetch_notes(NOTES_INBOX_FOLDER)
    if not notes:
        return []

    sync = load_sync_state()
    new_requests = []

    for note in notes:
        nid = note["id"]
        title = note["name"].strip()
        body = note["body"].strip()

        if not body:
            continue

        status = _parse_status(body)

        # Skip notes the agent is working on or already finished
        if status in ("wip", "processed"):
            continue

        # "done" notes are handled by check_writing_responses, skip here
        if status == "done":
            continue

        c_hash = content_hash(body)

        if nid in sync and sync[nid].get("content_hash") == c_hash:
            continue  # unchanged

        # "update" status means user explicitly edited — always re-process
        # No status or "new" — treat as new if content changed
        new_requests.append({
            "id": nid,
            "title": title,
            "body": body,
            "status": status,
        })

        sync[nid] = {
            "title": title,
            "content_hash": c_hash,
            "mod_date": note["modification_date"],
        }

    save_sync_state(sync)
    return new_requests
