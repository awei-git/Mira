"""Layer 1: Experience Record & Replay.

Records {action, outcome, reward} after every task. Retrieves the most
relevant past experiences for injection into prompts as few-shot context.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

from .config import EXPERIENCE_DIR, REWARD_WEIGHTS

log = logging.getLogger("mira.evolution")


def record_experience(
    action: str,
    outcome: str,
    reward: dict | None = None,
    context: dict | None = None,
    agent: str = "",
    task_id: str = "",
) -> Path:
    """Record one experience after a task or action completes.

    Args:
        action: What was done ("published note about X", "replied to comment Y")
        outcome: What happened ("3 likes in 2h", "user said 'perfect'", "timeout")
        reward: Dict of signal_name -> value (e.g. {"likes": 5, "comments": 2})
        context: Relevant context (topic, style, time_of_day, etc.)
        agent: Which agent did this (writer, growth, explorer, etc.)
        task_id: Optional task ID for traceability
    """
    EXPERIENCE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    path = EXPERIENCE_DIR / f"{today}.jsonl"

    reward = reward or {}
    context = context or {}

    score = sum(reward.get(k, 0) * w for k, w in REWARD_WEIGHTS.items() if k in reward)

    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action[:500],
        "outcome": outcome[:500],
        "reward": reward,
        "score": round(score, 2),
        "context": context,
        "agent": agent,
        "task_id": task_id,
    }

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Failed to record experience: %s", e)

    return path


def load_experiences(days: int = 7) -> list[dict]:
    """Load recent experiences from JSONL files."""
    if not EXPERIENCE_DIR.exists():
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    entries = []
    for path in sorted(EXPERIENCE_DIR.glob("*.jsonl")):
        if path.stem < cutoff:
            continue
        try:
            for line in path.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as e:
            log.debug("Skipping experience file %s: %s", path.name, e)
    return entries


def get_relevant_experiences(query: str, top_k: int = 3, days: int = 14) -> str:
    """Find past experiences most relevant to a query.

    Returns formatted text ready for insertion into a system/user prompt.
    Uses keyword matching + reward score bonus for ranking.
    """
    experiences = load_experiences(days=days)
    if not experiences:
        return ""

    query_words = set(query.lower().split())

    scored = []
    for exp in experiences:
        text = (f"{exp.get('action', '')} {exp.get('outcome', '')} " f"{json.dumps(exp.get('context', {}))}").lower()
        relevance = sum(1 for w in query_words if w in text)
        reward_bonus = max(0, exp.get("score", 0)) * 0.1
        scored.append((relevance + reward_bonus, exp))

    scored.sort(key=lambda x: -x[0])
    top = [exp for score, exp in scored[:top_k] if score > 0]

    if not top:
        return ""

    lines = ["## Relevant past experiences"]
    for exp in top:
        reward_str = f" (score: {exp.get('score', 0):+.1f})" if exp.get("score") else ""
        lines.append(f"- **{exp['action']}** -> {exp['outcome']}{reward_str}")
        if exp.get("context"):
            ctx = ", ".join(f"{k}={v}" for k, v in exp["context"].items() if v)
            if ctx:
                lines.append(f"  Context: {ctx}")
    return "\n".join(lines)


def record_task_outcome(
    task_id: str,
    agent: str,
    action: str,
    status: str,
    summary: str = "",
):
    """Record a task completion/failure as experience.

    Args:
        status: "done", "failed", "timeout"
    """
    reward_map = {"done": "success", "failed": "failure", "timeout": "timeout"}
    reward_key = reward_map.get(status, "failure")

    record_experience(
        action=action[:200],
        outcome=f"{status}: {summary[:300]}" if summary else status,
        reward={reward_key: 1},
        context={"status": status},
        agent=agent,
        task_id=task_id,
    )
