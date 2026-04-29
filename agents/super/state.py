"""State management and session context for the Mira super agent.

Handles persistent state (agent_state.json), file locking, per-user state
namespaces, and rolling session context (short-term memory across cycles).
"""

import fcntl
import json
import logging
import signal
from datetime import datetime, timedelta
from pathlib import Path

from config import STATE_FILE, SESSION_FILE

log = logging.getLogger("mira")

# ---------------------------------------------------------------------------
# Graceful shutdown — SIGTERM sets flag, current operation finishes cleanly
# ---------------------------------------------------------------------------
_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    log.info("SIGTERM received — will shut down after current operation")


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def should_shutdown() -> bool:
    """Check if shutdown was requested. Call between operations."""
    return _shutdown_requested


# ---------------------------------------------------------------------------
# State management (tracks when we last ran each mode)
# ---------------------------------------------------------------------------

_LEGACY_USER_STATE_EXACT_KEYS = {
    "last_reflect",
    "last_skill_study",
    "last_spark_check",
    "spark_memory_lines",
    "last_comment_check",
    "last_growth_cycle",
    "last_notes_cycle",
}

_LEGACY_USER_STATE_PREFIXES = (
    "journal_",
    "skill_study_",
    "sparks_",
    "spontaneous_idea_",
)


def _load_state_raw() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _locked_state_write(update_fn):
    lock_file = STATE_FILE.with_suffix(".lock")
    try:
        with open(lock_file, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                state = _load_state_raw()
                new_state = update_fn(state)
                STATE_FILE.write_text(json.dumps(new_state, indent=2, ensure_ascii=False), encoding="utf-8")
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except BlockingIOError:
        log.warning("State file locked by another process, skipping save")


def _is_legacy_user_state_key(key: str) -> bool:
    return key in _LEGACY_USER_STATE_EXACT_KEYS or any(key.startswith(prefix) for prefix in _LEGACY_USER_STATE_PREFIXES)


def load_state(user_id: str | None = None) -> dict:
    state = _load_state_raw()
    if not user_id:
        return state

    users = state.get("users", {})
    if isinstance(users, dict):
        user_state = users.get(user_id)
        if isinstance(user_state, dict):
            return dict(user_state)

    # Backward compatibility: first per-user read can still see migrated keys
    # from the old flat state file until that user writes its own namespace.
    if user_id != "ang":
        return {}
    return {key: value for key, value in state.items() if _is_legacy_user_state_key(key)}


def save_state(state: dict, user_id: str | None = None):
    if not user_id:
        _locked_state_write(lambda _old_state: state)
        return

    def _update(raw_state: dict) -> dict:
        users = raw_state.get("users")
        if not isinstance(users, dict):
            users = {}
        users[user_id] = state
        raw_state["users"] = users
        return raw_state

    _locked_state_write(_update)


# ---------------------------------------------------------------------------
# Session context — rolling short-term memory across cycles (Level 1)
# ---------------------------------------------------------------------------

_SESSION_FILE = SESSION_FILE
_SESSION_MAX_ENTRIES = 40  # ~20 minutes of context at 30s cycles


def load_session_context() -> list[dict]:
    """Load recent session context entries. Each entry is one cycle's decisions."""
    if not _SESSION_FILE.exists():
        return []
    try:
        data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_session_context(entries: list[dict]):
    """Save session context, keeping only the most recent entries."""
    trimmed = entries[-_SESSION_MAX_ENTRIES:]
    try:
        _SESSION_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("Failed to save session context: %s", e)


def session_record(action: str, detail: str = "", **extra) -> dict:
    """Create a session context entry."""
    entry = {
        "ts": datetime.now().isoformat(),
        "action": action,
    }
    if detail:
        entry["detail"] = detail
    entry.update(extra)
    return entry


def session_has_recent(action: str, hours: float = 1.0, ctx: list[dict] | None = None) -> dict | None:
    """Check if a specific action was recorded recently. Returns the entry or None."""
    if ctx is None:
        ctx = load_session_context()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    for entry in reversed(ctx):
        if entry.get("ts", "") < cutoff:
            break
        if entry.get("action") == action:
            return entry
    return None
