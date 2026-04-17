"""Daily task contracts and self-repair — detect and retry failed daily tasks.

Each scheduled task declares what "done" means via a verify function.
The self-repair loop checks contracts and retries tasks that failed
verification, with 30-minute cooldown between retries.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from bridge import Mira
except (ImportError, ModuleNotFoundError):
    Mira = None

from config import MIRA_DIR, ARTIFACTS_DIR
from state import load_state, save_state
from runtime.dispatcher import _dispatch_background, _is_bg_running

log = logging.getLogger("mira")


# ---------------------------------------------------------------------------
# Task contracts: every scheduled task declares what "done" means
# ---------------------------------------------------------------------------
# Each entry: state_key_prefix -> {
#   "dispatch": (bg_name, [cmd_args]),
#   "window": (earliest_hour, latest_hour),  # when to schedule + retry
#   "verify": callable(state, today) -> bool,  # did it actually succeed?
# }
# The verify function checks for real output, not just a state flag.
# A task that set its state flag but produced no output is NOT done.


def _verify_state_key(prefix):
    """Simple verifier: state key exists for today."""

    def check(state, today):
        return bool(state.get(f"{prefix}_{today}"))

    return check


def _verify_analyst(slot):
    """Analyst verifier: state key + briefing file exists."""

    def check(state, today):
        key = f"analyst_{today}_{slot}"
        if not state.get(key):
            return False
        briefing = (
            ARTIFACTS_DIR / "briefings" / f"{today}_analyst_{'pre_market' if slot == '0700' else 'post_market'}.md"
        )
        return briefing.exists()

    return check


def _verify_journal(state, today):
    """Journal verifier: journal file exists in soul/journal/."""
    if not state.get(f"journal_{today}"):
        return False
    from config import SOUL_DIR

    journal_dir = SOUL_DIR / "journal"
    return any(journal_dir.glob(f"{today}*.md"))


def _verify_reflect(state, today):
    """Weekly reflect: just check state key (output goes to worldview/interests)."""
    return bool(state.get("last_reflect") and state["last_reflect"][:10] >= today)


def _verify_self_evolve(state, today):
    """Self-evolve verifier: state key set + at least one proposal file exists."""
    if not state.get(f"self_evolve_{today}"):
        return False
    proposals_dir = Path(__file__).resolve().parent / "proposals"
    return any(proposals_dir.glob(f"{today}_*.json"))


_DAILY_TASK_CONTRACTS = {
    "zhesi": {
        "dispatch": ("zhesi", ["zhesi"]),
        "window": (9, 22),
        "verify": _verify_state_key("zhesi"),
        "label": "每日哲思",
    },
    "soul_question": {
        "dispatch": ("soul-question", ["soul-question"]),
        "window": (10, 22),
        "verify": _verify_state_key("soul_question"),
        "label": "灵魂提问",
    },
    "daily_photo": {
        "dispatch": ("daily-photo", ["daily-photo"]),
        "window": (7, 20),
        "verify": _verify_state_key("daily_photo"),
        "label": "每日修图",
    },
    "journal": {
        "dispatch": ("journal", ["journal"]),
        "window": (21, 23),
        "verify": _verify_journal,
        "label": "日记",
    },
    "analyst_pre": {
        "dispatch": ("analyst-0700", ["analyst", "--slot", "0700"]),
        "window": (7, 12),
        "verify": _verify_analyst("0700"),
        "label": "盘前分析",
    },
    "analyst_post": {
        "dispatch": ("analyst-1800", ["analyst", "--slot", "1800"]),
        "window": (18, 22),
        "verify": _verify_analyst("1800"),
        "label": "盘后分析",
    },
    "self_evolve": {
        "dispatch": ("self-evolve", ["self-evolve"]),
        "window": (13, 16),
        "verify": _verify_self_evolve,
        "label": "自我进化",
    },
}


def _self_repair_daily_tasks():
    """Check all task contracts. Retry any that failed verification."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    hour = now.hour
    state = load_state()

    for task_id, contract in _DAILY_TASK_CONTRACTS.items():
        earliest, latest = contract["window"]
        if hour < earliest or hour > latest:
            continue

        # Run the verify function — checks real output, not just flags
        if contract["verify"](state, today):
            continue  # genuinely done

        bg_name, cmd_args = contract["dispatch"]

        # Skip if currently running
        if _is_bg_running(bg_name):
            continue

        # 30-minute cooldown between retries
        retry_key = f"_retry_{task_id}_{today}"
        last_retry = state.get(retry_key, "")
        if last_retry:
            try:
                if (now - datetime.fromisoformat(last_retry)).total_seconds() < 1800:
                    continue
            except ValueError:
                pass

        log.warning("Self-repair: %s (%s) not verified, retrying", task_id, contract["label"])
        state[retry_key] = now.isoformat()
        save_state(state)
        _dispatch_background(
            bg_name,
            [
                sys.executable,
                str(Path(__file__).resolve().parent / "core.py"),
                *cmd_args,
            ],
        )


def _daily_task_status_report():
    """At 23:05, send a feed item with verified task completion status."""
    now = datetime.now()
    if now.hour != 23 or now.minute < 5:
        return
    today = now.strftime("%Y-%m-%d")
    today_compact = today.replace("-", "")
    state = load_state()

    report_key = f"task_status_report_{today}"
    if state.get(report_key):
        return

    lines = []
    all_ok = True
    for task_id, contract in _DAILY_TASK_CONTRACTS.items():
        verified = contract["verify"](state, today)
        status = "done" if verified else "MISSED"
        if not verified:
            all_ok = False
        # Look up actor provenance from state (try common key patterns)
        actor_key = f"{task_id}_{today}_actor"
        actor = state.get(actor_key, "")
        actor_suffix = f" [actor: {actor}]" if actor else ""
        lines.append(f"- {contract['label']} ({task_id}): {status}{actor_suffix}")

    if all_ok:
        summary = "今日任务全部完成（已验证产出）。\n\n" + "\n".join(lines)
    else:
        summary = "有任务未完成或产出验证失败：\n\n" + "\n".join(lines)

    try:
        bridge = Mira(MIRA_DIR)
        bridge.create_item(
            item_id=f"task_report_{today_compact}",
            item_type="feed",
            title=f"Daily Status: {today}",
            first_message=summary,
            sender="agent",
            tags=["status", "daily"],
            origin="agent",
        )
    except Exception as e:
        log.warning("Failed to create task status report: %s", e)

    state[report_key] = now.isoformat()
    save_state(state)
    log.info("Daily task status report sent")
