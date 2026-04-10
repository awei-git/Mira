"""emptiness.py — Motivation pressure model for autonomous self-awakening.

The emptiness model replaces simple heartbeat polling with accumulated drive:

    emptiness_value += base_rate * Δt + question_rate * num_pending * Δt

When emptiness exceeds threshold, Mira self-awakens to think about pending
questions. External input always triggers immediately and takes priority.

State stored per user in: bridge/users/{user_id}/state/emptiness.json
Legacy fallback: agents/shared/soul/emptiness.json
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import MIRA_DIR

log = logging.getLogger("mira.emptiness")

from config import SOUL_DIR as _SOUL_DIR; _SOUL_DIR  # imported from config
EMPTINESS_FILE = _SOUL_DIR / "emptiness.json"

# Default tuning constants
DEFAULT_THRESHOLD = 150.0        # emptiness units to trigger question-mode self-awakening
CONNECTION_THRESHOLD = 80.0      # lower threshold for connection-mode thinking
DEFAULT_BASE_RATE = 5.0          # units per minute when idle — targets ~30min cycle
DEFAULT_QUESTION_RATE = 0.5      # additional units per minute per pending question
DEFAULT_DECAY_AFTER_THINK = 500.0 # emptiness reduction after one think — ensures full 30min cooldown
MAX_EMPTINESS = 500.0            # cap so it doesn't explode if agent is offline for days
MAX_CONTINUATION = 5             # max rounds of continuing same thought chain


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _elapsed_minutes(since: str) -> float:
    try:
        delta = datetime.now(timezone.utc) - _parse_dt(since)
        return max(0.0, delta.total_seconds() / 60.0)
    except (ValueError, TypeError):
        return 0.0


def _state_file(user_id: str = "ang") -> Path:
    return MIRA_DIR / "users" / (user_id or "ang") / "state" / "emptiness.json"


def load_emptiness(user_id: str = "ang") -> dict:
    state_file = _state_file(user_id)
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    if EMPTINESS_FILE.exists():
        try:
            return json.loads(EMPTINESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return _default_state()


def save_emptiness(state: dict, user_id: str = "ang"):
    state_file = _state_file(user_id)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _default_state() -> dict:
    return {
        "emptiness_value": 0.0,
        "last_updated": _now_iso(),
        "threshold": DEFAULT_THRESHOLD,
        "base_rate": DEFAULT_BASE_RATE,
        "question_rate": DEFAULT_QUESTION_RATE,
        "decay_after_think": DEFAULT_DECAY_AFTER_THINK,
        "pending_questions": [],
        "last_think_at": None,
        "stats": {
            "total_self_awakenings": 0,
            "total_questions_resolved": 0,
        }
    }


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def tick(user_id: str = "ang") -> float:
    """Advance the emptiness value based on elapsed time and pending questions.

    Call this once per agent cycle (every 30s) during idle periods.
    Returns the updated emptiness value.
    """
    state = load_emptiness(user_id=user_id)

    minutes = _elapsed_minutes(state.get("last_updated", _now_iso()))
    num_questions = len([q for q in state.get("pending_questions", []) if not q.get("resolved")])

    base_rate = state.get("base_rate", DEFAULT_BASE_RATE)
    q_rate = state.get("question_rate", DEFAULT_QUESTION_RATE)

    delta = (base_rate + q_rate * num_questions) * minutes
    new_value = min(state.get("emptiness_value", 0.0) + delta, MAX_EMPTINESS)

    state["emptiness_value"] = new_value
    state["last_updated"] = _now_iso()
    save_emptiness(state, user_id=user_id)

    log.debug("Emptiness tick: %.1f → %.1f (Δ%.1f, %d questions, %.1f min)",
              state.get("emptiness_value", 0.0), new_value, delta, num_questions, minutes)
    return new_value


def check_threshold(user_id: str = "ang") -> bool:
    """Returns True if emptiness has crossed the threshold and thinking is due.

    Three modes (checked in order):
    - question: emptiness >= 100, pending questions exist → think about top question
    - connection: emptiness >= 50, thought_stream has recent entries → find connections
    - auto_question: emptiness >= 100, no questions but thought_stream > 20 → generate questions
    """
    state = load_emptiness(user_id=user_id)
    value = state.get("emptiness_value", 0.0)
    threshold = state.get("threshold", DEFAULT_THRESHOLD)
    has_questions = any(not q.get("resolved") for q in state.get("pending_questions", []))

    if value >= threshold and has_questions:
        return True
    if value >= CONNECTION_THRESHOLD:
        return True  # connection or auto-question mode will be selected in get_think_mode()
    return False


def get_think_mode(user_id: str = "ang") -> str | None:
    """Determine which thinking mode to use based on current state.

    Returns: "question", "connection", "auto_question", or None.
    """
    state = load_emptiness(user_id=user_id)
    value = state.get("emptiness_value", 0.0)
    threshold = state.get("threshold", DEFAULT_THRESHOLD)
    has_questions = any(not q.get("resolved") for q in state.get("pending_questions", []))

    # Check for active thought continuation
    continuation = state.get("thought_continuation")
    if continuation and continuation.get("continuation_count", 0) < MAX_CONTINUATION:
        return "continuation"

    if value >= threshold and has_questions:
        return "question"

    if value >= CONNECTION_THRESHOLD:
        # Check if thought_stream has enough entries for connection/auto-question
        try:
            from memory_store import get_store
            stats = get_store().get_stats(user_id=user_id)
            thought_count = stats.get("thought_stream", 0)
        except Exception:
            thought_count = 0

        if thought_count >= 20 and not has_questions:
            return "auto_question"
        if thought_count >= 3:
            return "connection"

    return None


def after_think(user_id: str = "ang"):
    """Call after a think session to reduce emptiness."""
    state = load_emptiness(user_id=user_id)
    decay = state.get("decay_after_think", DEFAULT_DECAY_AFTER_THINK)
    state["emptiness_value"] = max(0.0, state.get("emptiness_value", 0.0) - decay)
    state["last_think_at"] = _now_iso()
    state["stats"]["total_self_awakenings"] = state["stats"].get("total_self_awakenings", 0) + 1
    save_emptiness(state, user_id=user_id)
    log.info("Emptiness after think: %.1f (decayed %.1f)", state["emptiness_value"], decay)


def on_external_input(user_id: str = "ang"):
    """Call when external input arrives to reset the emptiness clock.

    External input takes full priority — we don't self-awaken when there's
    a real conversation happening.
    """
    state = load_emptiness(user_id=user_id)
    # Partial reset: external stimulus is satisfying, but unresolved questions remain
    state["emptiness_value"] = max(0.0, state.get("emptiness_value", 0.0) * 0.3)
    state["last_updated"] = _now_iso()
    save_emptiness(state, user_id=user_id)


# ---------------------------------------------------------------------------
# Question management
# ---------------------------------------------------------------------------

def add_question(text: str, priority: float = 5.0, source: str = "",
                 user_id: str = "ang") -> str:
    """Add a pending question to the queue. Returns the question ID."""
    state = load_emptiness(user_id=user_id)

    # Dedup by text (fuzzy: same first 60 chars)
    key = text[:60].strip().lower()
    for q in state.get("pending_questions", []):
        if q.get("text", "")[:60].strip().lower() == key:
            # Bump priority if higher
            if priority > q.get("priority", 0):
                q["priority"] = priority
            save_emptiness(state, user_id=user_id)
            return q["id"]

    import uuid
    q_id = "q_" + uuid.uuid4().hex[:8]
    question = {
        "id": q_id,
        "text": text,
        "priority": priority,
        "source": source,
        "added_at": _now_iso(),
        "last_thought_at": None,
        "resolved": False,
        "thought_count": 0,
    }
    state.setdefault("pending_questions", []).append(question)
    save_emptiness(state, user_id=user_id)
    log.info("Added question [%s] p=%.1f: %s", q_id, priority, text[:80])
    return q_id


def get_active_questions(limit: int = 5, user_id: str = "ang") -> list[dict]:
    """Return unresolved questions sorted by priority (descending)."""
    state = load_emptiness(user_id=user_id)
    active = [q for q in state.get("pending_questions", []) if not q.get("resolved")]
    active.sort(key=lambda q: q.get("priority", 0), reverse=True)
    return active[:limit]


def mark_thought(q_id: str, user_id: str = "ang"):
    """Record that a question was thought about (but not resolved)."""
    state = load_emptiness(user_id=user_id)
    for q in state.get("pending_questions", []):
        if q["id"] == q_id:
            q["last_thought_at"] = _now_iso()
            q["thought_count"] = q.get("thought_count", 0) + 1
            break
    save_emptiness(state, user_id=user_id)


def resolve_question(q_id: str, user_id: str = "ang"):
    """Mark a question as resolved."""
    state = load_emptiness(user_id=user_id)
    for q in state.get("pending_questions", []):
        if q["id"] == q_id:
            q["resolved"] = True
            q["resolved_at"] = _now_iso()
            state["stats"]["total_questions_resolved"] = \
                state["stats"].get("total_questions_resolved", 0) + 1
            break
    save_emptiness(state, user_id=user_id)


def start_continuation(thought_id: int, preview: str = "", user_id: str = "ang"):
    """Start tracking a thought chain for cross-cycle continuation."""
    state = load_emptiness(user_id=user_id)
    state["thought_continuation"] = {
        "active_thread_id": thought_id,
        "last_output_preview": preview[:200],
        "continuation_count": 1,
        "started_at": _now_iso(),
    }
    save_emptiness(state, user_id=user_id)


def advance_continuation(thought_id: int, preview: str = "", user_id: str = "ang"):
    """Advance the continuation counter after a thinking round."""
    state = load_emptiness(user_id=user_id)
    cont = state.get("thought_continuation", {})
    cont["active_thread_id"] = thought_id
    cont["last_output_preview"] = preview[:200]
    cont["continuation_count"] = cont.get("continuation_count", 0) + 1
    state["thought_continuation"] = cont
    save_emptiness(state, user_id=user_id)


def end_continuation(user_id: str = "ang"):
    """End the current thought continuation (crystallized or max rounds reached)."""
    state = load_emptiness(user_id=user_id)
    state.pop("thought_continuation", None)
    save_emptiness(state, user_id=user_id)


def get_continuation(user_id: str = "ang") -> dict | None:
    """Get the current thought continuation state, or None."""
    state = load_emptiness(user_id=user_id)
    cont = state.get("thought_continuation")
    if cont and cont.get("continuation_count", 0) < MAX_CONTINUATION:
        return cont
    if cont and cont.get("continuation_count", 0) >= MAX_CONTINUATION:
        end_continuation(user_id=user_id)
    return None


def passes_quality_gate(thought_text: str) -> bool:
    """Check if an idle-think output connects to at least one existing thread.

    Reads memory.md, worldview.md, and recent reading notes, then checks
    whether the thought references any concept or term found in those files.
    Standalone thoughts with no connection are filtered out to reduce noise.
    """
    from pathlib import Path
    from config import SOUL_DIR as _soul_dir; _soul = _soul_dir
    reference_text = ""

    # Load memory.md
    mem_file = _soul / "memory.md"
    if mem_file.exists():
        try:
            reference_text += mem_file.read_text(encoding="utf-8")[:3000]
        except OSError:
            pass

    # Load worldview.md
    wv_file = _soul / "worldview.md"
    if wv_file.exists():
        try:
            reference_text += "\n" + wv_file.read_text(encoding="utf-8")[:2000]
        except OSError:
            pass

    # Load recent reading notes (last 7 days)
    rn_dir = _soul / "reading_notes"
    if rn_dir.exists():
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        for p in sorted(rn_dir.glob("*.md"), reverse=True)[:10]:
            try:
                reference_text += "\n" + p.read_text(encoding="utf-8")[:500]
            except OSError:
                pass

    if not reference_text:
        # No reference material — can't filter, let it through
        return True

    # Extract meaningful terms from the thought (words 4+ chars, lowercased)
    import re
    thought_words = set(w.lower() for w in re.findall(r'[a-zA-Z\u4e00-\u9fff]{4,}', thought_text))
    ref_lower = reference_text.lower()

    # Check if at least one meaningful term from the thought appears in reference material
    matches = sum(1 for w in thought_words if w in ref_lower)
    connected = matches >= 2  # at least 2 overlapping terms
    if not connected:
        log.info("Quality gate: thought filtered (only %d term overlaps with memory/worldview)", matches)
    return connected


def get_status_str(user_id: str = "ang") -> str:
    """Return a one-line status string for logging/display."""
    state = load_emptiness(user_id=user_id)
    value = state.get("emptiness_value", 0.0)
    threshold = state.get("threshold", DEFAULT_THRESHOLD)
    active = [q for q in state.get("pending_questions", []) if not q.get("resolved")]
    mode = get_think_mode(user_id=user_id)
    cont = state.get("thought_continuation")
    parts = [f"emptiness={value:.1f}/{threshold:.0f}", f"{len(active)} questions"]
    if mode:
        parts.append(f"mode={mode}")
    if cont:
        parts.append(f"cont={cont.get('continuation_count', 0)}/{MAX_CONTINUATION}")
    return ", ".join(parts)
