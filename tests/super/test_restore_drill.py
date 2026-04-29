"""Tests for restore drill helpers."""

from __future__ import annotations

import sys
from pathlib import Path


def test_latest_backup_dir_picks_latest_manifest(tmp_path: Path):
    import restore_drill as rd

    old = tmp_path / "2026-04-04"
    new = tmp_path / "2026-04-05"
    old.mkdir()
    new.mkdir()
    (old / "backup_manifest.json").write_text("{}", encoding="utf-8")
    (new / "backup_manifest.json").write_text("{}", encoding="utf-8")

    assert rd.latest_backup_dir(tmp_path) == new


def test_run_latest_restore_dry_run_returns_not_found_without_backup(tmp_path: Path):
    import restore_drill as rd

    report = rd.run_latest_restore_dry_run(tmp_path)

    assert report["ok"] is False
    assert report["reason"] == "backup_not_found"
