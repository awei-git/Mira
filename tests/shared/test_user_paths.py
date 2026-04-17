from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SHARED = _HERE.parent


def test_user_paths_keep_ang_legacy_locations():
    import user_paths

    assert user_paths.user_journal_dir("ang").name == "journal"
    assert user_paths.user_reading_notes_dir("ang").name == "reading_notes"
    assert user_paths.artifact_name_for_user("2026-04-05_journal.md", "ang") == "2026-04-05_journal.md"


def test_user_paths_namespace_non_default_users():
    import user_paths

    journal_dir = user_paths.user_journal_dir("liquan")
    notes_dir = user_paths.user_reading_notes_dir("liquan")

    assert journal_dir.parts[-4:] == ("users", "liquan", "soul", "journal")
    assert notes_dir.parts[-4:] == ("users", "liquan", "soul", "reading_notes")
    assert user_paths.artifact_name_for_user("2026-04-05_journal.md", "liquan") == "2026-04-05_journal_liquan.md"
