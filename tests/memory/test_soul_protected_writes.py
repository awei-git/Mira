"""Every soul file declared protected must have a public update_xxx() API
that goes through _protected_write. Direct writes break the integrity
hash and trigger CRITICAL alerts on every cycle (root cause of the
2026-04-30 identity-hash incident).
"""

from __future__ import annotations

import hashlib
import importlib

import pytest

from memory import soul, soul_io


def test_protected_files_have_public_update_api():
    """Every entry in _PROTECTED_FILES needs an update_<name>() function."""
    missing = []
    for name in soul_io._PROTECTED_FILES:
        api_name = f"update_{name}"
        if not hasattr(soul, api_name):
            missing.append(api_name)
    assert not missing, (
        f"protected files without update API: {missing}. "
        "Add a wrapper in lib/memory/soul.py that calls _protected_write."
    )


def test_update_identity_routes_through_protected_write(tmp_path, monkeypatch):
    """update_identity must update both file content and stored hash."""
    identity_file = tmp_path / "identity.md"
    worldview_file = tmp_path / "worldview.md"
    identity_file.write_text("seed identity", encoding="utf-8")
    worldview_file.write_text("seed worldview", encoding="utf-8")

    monkeypatch.setattr(soul_io, "IDENTITY_FILE", identity_file)
    monkeypatch.setattr(soul_io, "WORLDVIEW_FILE", worldview_file)
    monkeypatch.setattr(soul_io, "_HASH_FILE", tmp_path / ".soul_hashes.json")
    monkeypatch.setattr(soul_io, "_BACKUP_DIR", tmp_path / ".backups")
    monkeypatch.setattr(
        soul_io,
        "_PROTECTED_FILES",
        {
            "identity": identity_file,
            "worldview": worldview_file,
        },
    )
    monkeypatch.setattr(soul, "IDENTITY_FILE", identity_file)
    monkeypatch.setattr(soul_io, "CHANGELOG_FILE", tmp_path / "changelog.md")

    soul_io._save_hashes()
    assert soul_io.verify_soul_integrity() == []

    soul.update_identity("new identity content", reason="test update")

    assert identity_file.read_text(encoding="utf-8") == "new identity content"
    assert soul_io.verify_soul_integrity() == [], "after update_identity the hash file must reflect the new content"
    backups = list((tmp_path / ".backups").glob("identity_*.md"))
    assert backups, "update_identity must rotate a backup of the prior content"
    assert backups[0].read_text(encoding="utf-8") == "seed identity"


def test_direct_write_is_detected_as_violation(tmp_path, monkeypatch):
    """Bypassing the API must trigger an integrity violation, not pass silently."""
    identity_file = tmp_path / "identity.md"
    worldview_file = tmp_path / "worldview.md"
    identity_file.write_text("baseline", encoding="utf-8")
    worldview_file.write_text("worldview baseline", encoding="utf-8")

    monkeypatch.setattr(soul_io, "IDENTITY_FILE", identity_file)
    monkeypatch.setattr(soul_io, "WORLDVIEW_FILE", worldview_file)
    monkeypatch.setattr(soul_io, "_HASH_FILE", tmp_path / ".soul_hashes.json")
    monkeypatch.setattr(soul_io, "_BACKUP_DIR", tmp_path / ".backups")
    monkeypatch.setattr(
        soul_io,
        "_PROTECTED_FILES",
        {
            "identity": identity_file,
            "worldview": worldview_file,
        },
    )

    soul_io._save_hashes()
    identity_file.write_text("tampered", encoding="utf-8")  # bypass

    violations = soul_io.verify_soul_integrity()
    assert "identity" in violations
