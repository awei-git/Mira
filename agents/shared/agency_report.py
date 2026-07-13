"""Autonomous agency report — collect actions taken without a direct user request.

Used by the daily journal to surface scheduled/proactive work so the user
can see where the agent acted independently (explore, reflect, skill learning,
writing advancement, etc.).
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("mira")

_STATE_KEY_LABELS: dict[str, str] = {
    "zhesi": "Philosophy reflection (zhesi)",
    "soul_question": "Daily soul question for self-development",
    "research_log": "Autonomous research log entry",
    "last_reflect": "Weekly memory reflection and consolidation",
    "last_research_cycle": "Advance research queue (autonomous research-build loop)",
    "last_spark_check": "Spark check and social content posting",
    "last_comment_check": "Check and respond to publication comments",
    "last_growth_cycle": "Substack/social growth cycle",
    "last_notes_cycle": "Substack Notes posting cycle",
    "last_idle_think": "Idle thinking and spark generation",
    "last_self_evolve": "Self-improvement and skill evolution",
    "last_skill_study": "Autonomous skill study session",
    "last_book_review": "Book review pipeline run",
    "last_zhesi": "Philosophy reflection (zhesi)",
    "last_analyst": "Market analysis run",
    "last_daily_photo": "Daily photo editing session",
    "last_podcast": "Podcast episode production",
    "last_autowrite": "Autonomous writing pipeline run",
    "last_autowrite_check": "Check for autonomous writing opportunities",
    "last_soul_question": "Daily soul question for self-development",
    "last_daily_report": "Daily report generation",
    "last_research_log": "Autonomous research log entry",
    "explore": "Fetch and summarize feeds (explore briefing)",
}

_DATE_SUFFIX_RE = re.compile(r"_\d{4}-\d{2}-\d{2}(_actor)?$")
_ACTOR_SUFFIX_RE = re.compile(r"_actor$")


def _parse_ts(ts_str: str) -> datetime | None:
    if not isinstance(ts_str, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (ValueError, AttributeError):
        return None


def _label_for_state_key(key: str) -> str | None:
    """Return a human-readable label for a state key, or None if not a scheduled job."""
    if _ACTOR_SUFFIX_RE.search(key):
        return None

    bare = _DATE_SUFFIX_RE.sub("", key)

    if bare in _STATE_KEY_LABELS:
        return _STATE_KEY_LABELS[bare]

    for prefix, label in _STATE_KEY_LABELS.items():
        if bare == prefix or bare.startswith(prefix + "_"):
            return label

    if bare.startswith("last_"):
        job_name = bare[len("last_") :]
        return f"Scheduled job: {job_name.replace('_', ' ')}"

    return None


def _scheduled_actions_from_state(state: dict, since: datetime) -> list[dict]:
    actions = []
    seen_labels: set[str] = set()

    for key, value in state.items():
        if _ACTOR_SUFFIX_RE.search(key):
            continue
        bare = _DATE_SUFFIX_RE.sub("", key)
        if bare == "journal":
            continue

        ts = _parse_ts(value) if isinstance(value, str) else None
        if ts is None or ts < since:
            continue

        label = _label_for_state_key(key)
        if not label or label in seen_labels:
            continue

        seen_labels.add(label)
        actions.append(
            {
                "source": "scheduled_job",
                "kind": bare,
                "description": label,
                "ts": ts.isoformat(),
            }
        )

    return actions


def _scan_task_workspaces(since: datetime, tasks_dir: Path) -> list[dict]:
    """Scan task workspaces for agent-initiated (non-user) tasks since `since`."""
    actions = []
    if not tasks_dir.exists():
        return actions

    for workspace in tasks_dir.iterdir():
        if not workspace.is_dir():
            continue
        try:
            mtime = workspace.stat().st_mtime
            workspace_ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        except OSError:
            continue

        if workspace_ts < since:
            continue

        message_file = workspace / "message.json"
        if not message_file.exists():
            continue
        try:
            msg = json.loads(message_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender") or "").strip().lower()
        if sender == "user":
            continue

        content = str(msg.get("content") or "").strip()[:120]
        if not content:
            continue

        agent = str(msg.get("routing_agent") or msg.get("agent") or "").strip()
        actions.append(
            {
                "source": "agent_task",
                "task_id": str(msg.get("id") or workspace.name),
                "sender": sender,
                "agent": agent,
                "description": content,
                "ts": workspace_ts.isoformat(),
            }
        )

    return sorted(actions, key=lambda x: x["ts"])


def get_autonomous_actions_since(last_run: datetime | None, user_id: str = "default") -> list[dict]:
    """Return autonomous actions taken since `last_run`.

    Each action dict has: source, kind (or task_id), description, ts.
    Covers scheduled background jobs (from state) and agent-initiated tasks
    (from task workspaces where sender != 'user').
    """
    if last_run is None:
        last_run = datetime.now(timezone.utc) - timedelta(hours=25)

    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    actions: list[dict] = []

    try:
        from state import load_state

        state = load_state(user_id=user_id)
        actions.extend(_scheduled_actions_from_state(state, last_run))
    except Exception as exc:
        log.debug("agency_report: state load failed: %s", exc)

    try:
        from task_manager import TASKS_DIR

        actions.extend(_scan_task_workspaces(last_run, TASKS_DIR))
    except Exception as exc:
        log.debug("agency_report: workspace scan failed: %s", exc)

    return sorted(actions, key=lambda x: x.get("ts", ""))


def format_autonomous_agency_report(actions: list[dict]) -> str:
    """Format the autonomous actions list as a human-readable markdown section."""
    lines = ["## Autonomous Agency Report", ""]

    if not actions:
        lines.append("No autonomous actions recorded since the last journal.")
        return "\n".join(lines)

    lines.append("Actions taken without a direct user request:\n")
    for action in actions:
        desc = str(action.get("description") or "").strip()
        ts = str(action.get("ts") or "")[:16].replace("T", " ")
        source = action.get("source", "")

        if source == "scheduled_job":
            lines.append(f"- **{ts}** — {desc}")
        elif source == "agent_task":
            agent = str(action.get("agent") or action.get("sender") or "").strip()
            tag = f"[{agent}] " if agent else ""
            lines.append(f"- **{ts}** — {tag}{desc}")

    return "\n".join(lines)
