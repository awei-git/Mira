"""emptiness.py — Motivation pressure model for autonomous self-awakening.

The emptiness model replaces simple heartbeat polling with accumulated drive:

    emptiness_value += base_rate * Δt + question_rate * num_pending * Δt

When emptiness exceeds threshold, Mira self-awakens to think about pending
questions. External input always triggers immediately and takes priority.

State stored in: agents/shared/soul/emptiness.json
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("mira.emptiness")

_SOUL_DIR = Path(__file__).resolve().parent / "soul"
EMPTINESS_FILE = _SOUL_DIR / "emptiness.json"

# Default tuning constants
DEFAULT_THRESHOLD = 100.0        # emptiness units to trigger self-awakening
DEFAULT_BASE_RATE = 0.8          # units per minute when idle, no pending questions
DEFAULT_QUESTION_RATE = 0.4      # additional units per minute per pending question
DEFAULT_DECAY_AFTER_THINK = 70.0 # emptiness reduction after one think session
MAX_EMPTINESS = 500.0            # cap so it doesn't explode if agent is offline for days


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


def load_emptiness() -> dict:
    if EMPTINESS_FILE.exists():
        try:
            return json.loads(EMPTINESS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return _default_state()


def save_emptiness(state: dict):
    EMPTINESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    EMPTINESS_FILE.write_text(
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

def tick() -> float:
    """Advance the emptiness value based on elapsed time and pending questions.

    Call this once per agent cycle (every 30s) during idle periods.
    Returns the updated emptiness value.
    """
    state = load_emptiness()

    minutes = _elapsed_minutes(state.get("last_updated", _now_iso()))
    num_questions = len([q for q in state.get("pending_questions", []) if not q.get("resolved")])

    base_rate = state.get("base_rate", DEFAULT_BASE_RATE)
    q_rate = state.get("question_rate", DEFAULT_QUESTION_RATE)

    delta = (base_rate + q_rate * num_questions) * minutes
    new_value = min(state.get("emptiness_value", 0.0) + delta, MAX_EMPTINESS)

    state["emptiness_value"] = new_value
    state["last_updated"] = _now_iso()
    save_emptiness(state)

    log.debug("Emptiness tick: %.1f → %.1f (Δ%.1f, %d questions, %.1f min)",
              state.get("emptiness_value", 0.0), new_value, delta, num_questions, minutes)
    return new_value


def check_threshold() -> bool:
    """Returns True if emptiness has crossed the threshold and self-awakening is due."""
    state = load_emptiness()
    value = state.get("emptiness_value", 0.0)
    threshold = state.get("threshold", DEFAULT_THRESHOLD)
    has_questions = any(not q.get("resolved") for q in state.get("pending_questions", []))
    return value >= threshold and has_questions


def after_think():
    """Call after a think session to reduce emptiness."""
    state = load_emptiness()
    decay = state.get("decay_after_think", DEFAULT_DECAY_AFTER_THINK)
    state["emptiness_value"] = max(0.0, state.get("emptiness_value", 0.0) - decay)
    state["last_think_at"] = _now_iso()
    state["stats"]["total_self_awakenings"] = state["stats"].get("total_self_awakenings", 0) + 1
    save_emptiness(state)
    log.info("Emptiness after think: %.1f (decayed %.1f)", state["emptiness_value"], decay)


def on_external_input():
    """Call when external input arrives to reset the emptiness clock.

    External input takes full priority — we don't self-awaken when there's
    a real conversation happening.
    """
    state = load_emptiness()
    # Partial reset: external stimulus is satisfying, but unresolved questions remain
    state["emptiness_value"] = max(0.0, state.get("emptiness_value", 0.0) * 0.3)
    state["last_updated"] = _now_iso()
    save_emptiness(state)


# ---------------------------------------------------------------------------
# Question management
# ---------------------------------------------------------------------------

def add_question(text: str, priority: float = 5.0, source: str = "") -> str:
    """Add a pending question to the queue. Returns the question ID."""
    state = load_emptiness()

    # Dedup by text (fuzzy: same first 60 chars)
    key = text[:60].strip().lower()
    for q in state.get("pending_questions", []):
        if q.get("text", "")[:60].strip().lower() == key:
            # Bump priority if higher
            if priority > q.get("priority", 0):
                q["priority"] = priority
            save_emptiness(state)
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
    save_emptiness(state)
    log.info("Added question [%s] p=%.1f: %s", q_id, priority, text[:80])
    return q_id


def get_active_questions(limit: int = 5) -> list[dict]:
    """Return unresolved questions sorted by priority (descending)."""
    state = load_emptiness()
    active = [q for q in state.get("pending_questions", []) if not q.get("resolved")]
    active.sort(key=lambda q: q.get("priority", 0), reverse=True)
    return active[:limit]


def mark_thought(q_id: str):
    """Record that a question was thought about (but not resolved)."""
    state = load_emptiness()
    for q in state.get("pending_questions", []):
        if q["id"] == q_id:
            q["last_thought_at"] = _now_iso()
            q["thought_count"] = q.get("thought_count", 0) + 1
            break
    save_emptiness(state)


def resolve_question(q_id: str):
    """Mark a question as resolved."""
    state = load_emptiness()
    for q in state.get("pending_questions", []):
        if q["id"] == q_id:
            q["resolved"] = True
            q["resolved_at"] = _now_iso()
            state["stats"]["total_questions_resolved"] = \
                state["stats"].get("total_questions_resolved", 0) + 1
            break
    save_emptiness(state)


def get_status_str() -> str:
    """Return a one-line status string for logging/display."""
    state = load_emptiness()
    value = state.get("emptiness_value", 0.0)
    threshold = state.get("threshold", DEFAULT_THRESHOLD)
    active = [q for q in state.get("pending_questions", []) if not q.get("resolved")]
    return f"emptiness={value:.1f}/{threshold:.0f}, {len(active)} questions pending"
