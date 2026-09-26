"""Map the app-maintained content projection to the existing soul dictionary."""

from pathlib import Path

from content_worker.files import active_snapshot, safe_file

IDENTITY_FILES = ("SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md", "MEMORY.md")


def load_identity(shared_root):
    snapshot = active_snapshot(shared_root)
    texts = {name: safe_file(snapshot, "identity/" + name).read_text(encoding="utf-8") for name in IDENTITY_FILES}
    return {
        "identity": texts["SOUL.md"] + "\n\n" + texts["IDENTITY.md"],
        "worldview": texts["AGENTS.md"],
        "memory": texts["MEMORY.md"],
        "interests": texts["USER.md"],
        "skills": "",
    }
