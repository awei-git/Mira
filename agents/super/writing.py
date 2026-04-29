"""Writing pipeline — advance canonical writing_workflow projects.

Handles writer selection logging and auto-advancement of projects
through the plan/write/review phases.
"""

import json
import logging
from datetime import datetime

from config import LOGS_DIR, STALE_PROJECT_DAYS
from writing_workflow import check_writing_responses, advance_project

log = logging.getLogger("mira")


def _log_writer_selection(considered: list, selected: list, skipped: list, rationale: str):
    """Append a structured selection-rationale entry to writer_selection.jsonl."""
    entry = {
        "ts": datetime.now().isoformat(),
        "considered": considered,
        "selected": selected,
        "skipped": skipped,
        "rationale": rationale,
    }
    log_file = LOGS_DIR / "writer_selection.jsonl"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Failed to write writer_selection log: %s", e)


def _run_canonical_writing_pipeline() -> int:
    """Advance canonical writing_workflow projects that are ready to move."""
    now = datetime.now()
    advanced = 0
    responses = check_writing_responses()
    considered = [resp["project"].get("title", resp["workspace"].name) for resp in responses]
    selected = []
    skipped = []

    for resp in responses:
        phase = resp["project"].get("phase", "")
        title = resp["project"].get("title", "")
        last_advanced_str = resp["project"].get("last_advanced_at") or resp["project"].get("updated", "")
        if last_advanced_str:
            try:
                last_advanced = datetime.fromisoformat(last_advanced_str)
                days_since = (now - last_advanced).days
                if days_since > STALE_PROJECT_DAYS:
                    log.warning(
                        "STALE PROJECT: '%s' has not been advanced in %d days (phase: %s)",
                        title,
                        days_since,
                        phase,
                    )
            except (ValueError, TypeError):
                pass
        if phase == "plan_ready":
            log.info("Auto-advancing canonical writing project: %s", title)
            advance_project(resp["workspace"])
            advanced += 1
            selected.append(title)
        elif phase == "draft_ready":
            log.info("Writing project awaiting user feedback: %s", title)
            skipped.append((title, "draft_ready: awaiting user feedback"))

    if responses:
        rationale = (
            f"Advanced {len(selected)} plan_ready project(s); " f"{len(skipped)} held in draft_ready awaiting feedback."
        )
        _log_writer_selection(considered, selected, skipped, rationale)

    return advanced
