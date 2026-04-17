"""Score persistence — load, save, record events, prune history."""

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, date, timedelta
from pathlib import Path

from .dimensions import ALL_SUBDIMS, EMA_ALPHA, HISTORY_KEEP_DAYS

log = logging.getLogger("evaluator")

SELF_ASSESSED_WEIGHT = 0.5
_VALID_EVIDENCE_TYPES = ("log_verified", "user_confirmed", "external_benchmark", "self_assessed")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
from config import SOUL_DIR as _SOUL_DIR

_SOUL_DIR  # imported from config
SCORES_FILE = _SOUL_DIR / "scores.json"

# ---------------------------------------------------------------------------
# Score storage
# ---------------------------------------------------------------------------


def _default_scores() -> dict:
    return {
        "version": 1,
        "current": {},  # "dim.subdim" -> float
        "history": [],  # daily snapshots
        "predictions": [],
        "skill_usage": {},  # skill_name -> [last_date, count]
        "weakness_scores": {},  # "agent.metric" -> {value, evidence_type, last_updated, last_non_self_assessed}
        "meta": {
            "last_evaluated": None,
            "total_evaluations": 0,
            "rubric_version": 1,
        },
    }


def load_scores() -> dict:
    """Load scores.json, return default if missing."""
    if SCORES_FILE.exists():
        try:
            return json.loads(SCORES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load scores: %s", e)
    return _default_scores()


def save_scores(scores: dict):
    """Atomic write scores.json with file lock."""
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = SCORES_FILE.with_suffix(".json.lock")
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(dir=SCORES_FILE.parent, suffix=".tmp", prefix=".scores_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(scores, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, SCORES_FILE)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def update_weakness_score(metric: str, value: float, evidence_type: str = "self_assessed") -> float:
    """Update a per-agent weakness/performance score with evidence type tracking.

    evidence_type must be one of: 'log_verified', 'user_confirmed',
    'external_benchmark', 'self_assessed'.

    Self-assessed updates blend at SELF_ASSESSED_WEIGHT so they move the score
    half as far as grounded updates.  Emits a warning if a metric has been
    stable for >7 days with only self_assessed evidence.

    Returns the effective (stored) score.
    """
    if evidence_type not in _VALID_EVIDENCE_TYPES:
        log.warning("Unknown evidence_type '%s' for metric %s", evidence_type, metric)
        evidence_type = "self_assessed"

    data = load_scores()
    weakness_scores = data.setdefault("weakness_scores", {})
    today = datetime.now().strftime("%Y-%m-%d")

    prior_entry = weakness_scores.get(metric)
    prior_value = prior_entry["value"] if prior_entry else value

    if evidence_type == "self_assessed":
        effective = round(prior_value * (1 - SELF_ASSESSED_WEIGHT) + value * SELF_ASSESSED_WEIGHT, 3)
    else:
        effective = round(float(value), 3)

    if evidence_type == "self_assessed" and prior_entry:
        last_non_sa = prior_entry.get("last_non_self_assessed")
        if last_non_sa:
            days_stale = (datetime.now() - datetime.fromisoformat(last_non_sa)).days
            if days_stale > 7 and abs(effective - prior_value) < 0.01:
                log.warning(
                    "WARNING: %s score stable for %dd with only self_assessed evidence " "— may be trust-compressed.",
                    metric,
                    days_stale,
                )

    weakness_scores[metric] = {
        "value": effective,
        "evidence_type": evidence_type,
        "last_updated": today,
        "last_non_self_assessed": (
            today
            if evidence_type != "self_assessed"
            else (prior_entry.get("last_non_self_assessed") if prior_entry else None)
        ),
    }
    save_scores(data)
    return effective


def record_event(event_type: str, scores: dict[str, float], metadata: dict | None = None):
    """Record scoring event and update EMA for affected sub-dimensions.

    event_type: "journal", "task_complete", "reflect", "explore",
                "publish", "growth", "standalone"
    scores: {"dimension.subdim": float_score}
    metadata: optional context
    """
    data = load_scores()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Update EMA for each scored dimension
    for key, val in scores.items():
        if key not in ALL_SUBDIMS:
            log.warning("Unknown sub-dimension: %s", key)
            continue
        val = max(0.0, min(10.0, float(val)))
        old = data["current"].get(key)
        if old is None:
            data["current"][key] = val
        else:
            data["current"][key] = round(EMA_ALPHA * val + (1 - EMA_ALPHA) * old, 2)

    # Append to today's history
    today_entry = None
    for entry in data["history"]:
        if entry["date"] == today:
            today_entry = entry
            break
    if not today_entry:
        today_entry = {"date": today, "scores": {}, "events": []}
        data["history"].append(today_entry)

    today_entry["events"].append(
        {
            "type": event_type,
            "time": now.strftime("%H:%M"),
            "scores": {k: round(v, 2) for k, v in scores.items()},
            **({"meta": metadata} if metadata else {}),
        }
    )
    # Update today's snapshot with latest current scores
    today_entry["scores"] = dict(data["current"])

    data["meta"]["last_evaluated"] = now.isoformat()
    data["meta"]["total_evaluations"] = data["meta"].get("total_evaluations", 0) + 1

    save_scores(data)
    log.info("Recorded %s evaluation: %s", event_type, {k: round(v, 1) for k, v in scores.items()})


# ---------------------------------------------------------------------------
# History maintenance
# ---------------------------------------------------------------------------


def prune_history(keep_days: int = HISTORY_KEEP_DAYS):
    """Remove history entries older than keep_days."""
    data = load_scores()
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    data["history"] = [e for e in data.get("history", []) if e["date"] >= cutoff]

    # Also prune resolved predictions older than 90 days
    data["predictions"] = [
        p for p in data.get("predictions", []) if not p.get("resolved") or p.get("made", "") >= cutoff
    ]
    save_scores(data)
