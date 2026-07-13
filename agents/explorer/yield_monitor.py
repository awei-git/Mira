"""Skill-yield tracking for scheduled explore runs."""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import MIRA_ROOT

try:
    from config import SKILL_YIELD_FILE
except ImportError:
    SKILL_YIELD_FILE = MIRA_ROOT / "logs" / "skill_yield.json"

log = logging.getLogger("mira")


def _load_skill_yield(path: Path = SKILL_YIELD_FILE) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read skill yield counter %s: %s", path, e)
        return []
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def warn_on_zero_skill_yield(path: Path = SKILL_YIELD_FILE) -> None:
    recent = _load_skill_yield(path)[-3:]
    if len(recent) == 3 and all(
        entry.get("skills_extracted") == 0 and entry.get("briefing_produced") is True for entry in recent
    ):
        log.warning("Explorer skill extraction yielded 0 skills for 3 consecutive briefing-producing runs")


def record_skill_yield(
    run_id: str,
    skills_extracted: int,
    briefing_produced: bool,
    path: Path = SKILL_YIELD_FILE,
) -> None:
    entries = _load_skill_yield(path)
    entries.append(
        {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "run_id": run_id,
            "skills_extracted": skills_extracted,
            "briefing_produced": briefing_produced,
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(path)
    except OSError as e:
        log.warning("Could not write skill yield counter %s: %s", path, e)
