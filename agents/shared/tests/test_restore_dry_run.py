"""Tests for backup manifest and restore dry-run tooling."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_script(name: str, relative: str):
    root = Path(__file__).resolve().parents[3]
    path = root / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_restore_dry_run_passes_with_valid_manifest(tmp_path: Path, monkeypatch):
    backup_integrity = _load_script("backup_integrity_test", "scripts/backup_integrity.py")
    restore_dry_run = _load_script("restore_dry_run_test", "scripts/restore_dry_run.py")

    (tmp_path / "soul").mkdir()
    (tmp_path / "config.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "soul" / "identity.md").write_text("identity", encoding="utf-8")
    (tmp_path / "soul" / "memory.md").write_text("memory", encoding="utf-8")
    (tmp_path / "soul" / "worldview.md").write_text("worldview", encoding="utf-8")
    backup_integrity.write_manifest(tmp_path)

    log_path = tmp_path / "restore_drills.jsonl"
    monkeypatch.setattr(restore_dry_run, "_REPORT_LOG", log_path)

    report = restore_dry_run.run_restore_dry_run(tmp_path)

    assert report["ok"] is True
    assert report["manifest_errors"] == []
    assert report["required_errors"] == []
    assert "config.yml" in report["staged_paths"]
    assert log_path.exists()


def test_restore_dry_run_detects_hash_mismatch(tmp_path: Path, monkeypatch):
    backup_integrity = _load_script("backup_integrity_test_2", "scripts/backup_integrity.py")
    restore_dry_run = _load_script("restore_dry_run_test_2", "scripts/restore_dry_run.py")

    (tmp_path / "soul").mkdir()
    (tmp_path / "config.yml").write_text("services: {}\n", encoding="utf-8")
    (tmp_path / "soul" / "identity.md").write_text("identity", encoding="utf-8")
    (tmp_path / "soul" / "memory.md").write_text("memory", encoding="utf-8")
    (tmp_path / "soul" / "worldview.md").write_text("worldview", encoding="utf-8")
    backup_integrity.write_manifest(tmp_path)
    (tmp_path / "soul" / "memory.md").write_text("tamper", encoding="utf-8")

    monkeypatch.setattr(restore_dry_run, "_REPORT_LOG", tmp_path / "restore_drills.jsonl")

    report = restore_dry_run.run_restore_dry_run(tmp_path)

    assert report["ok"] is False
    assert any("hash mismatch: soul/memory.md" == err for err in report["manifest_errors"])
