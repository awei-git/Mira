"""Helpers for per-user soul/state paths with legacy fallbacks."""

from __future__ import annotations

from pathlib import Path

from config import JOURNAL_DIR, MIRA_DIR, READING_NOTES_DIR

DEFAULT_USER_ID = "ang"


def normalize_user_id(user_id: str | None = None) -> str:
    return user_id or DEFAULT_USER_ID


def user_soul_dir(user_id: str | None = None) -> Path:
    uid = normalize_user_id(user_id)
    if uid == DEFAULT_USER_ID:
        return JOURNAL_DIR.parent
    return MIRA_DIR / "users" / uid / "soul"


def user_journal_dir(user_id: str | None = None) -> Path:
    uid = normalize_user_id(user_id)
    if uid == DEFAULT_USER_ID:
        return JOURNAL_DIR
    return user_soul_dir(uid) / "journal"


def user_reading_notes_dir(user_id: str | None = None) -> Path:
    uid = normalize_user_id(user_id)
    if uid == DEFAULT_USER_ID:
        return READING_NOTES_DIR
    return user_soul_dir(uid) / "reading_notes"


def user_state_dir(user_id: str | None = None) -> Path:
    uid = normalize_user_id(user_id)
    return MIRA_DIR / "users" / uid / "state"


def user_soul_question_history_file(user_id: str | None = None) -> Path:
    return user_state_dir(user_id) / "soul_questions_history.json"


def artifact_name_for_user(filename: str, user_id: str | None = None) -> str:
    uid = normalize_user_id(user_id)
    if uid == DEFAULT_USER_ID:
        return filename
    path = Path(filename)
    return f"{path.stem}_{uid}{path.suffix}"
