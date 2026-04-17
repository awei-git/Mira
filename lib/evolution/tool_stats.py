"""Aggregate per-tool success/failure counters across trajectories.

Storage: `data/soul/tool_stats.json`, a flat dict of
`{tool_name: {"count": int, "success": int, "failure": int}}`.

Writes are atomic (tmp-file + replace) so the agent never reads a
half-written file if it crashes mid-update.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from schemas.trajectory import ToolStat, TrajectoryRecord

from .config import TOOL_STATS_FILE

log = logging.getLogger("mira.evolution.tool_stats")


def load_tool_stats(path: Path | None = None) -> dict[str, ToolStat]:
    """Load the global tool_stats dict. Missing file → empty dict."""
    target = path or TOOL_STATS_FILE
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("tool_stats load failed (%s): %s — treating as empty", target, e)
        return {}
    stats: dict[str, ToolStat] = {}
    for name, row in raw.items():
        try:
            stats[name] = ToolStat(
                name=name,
                count=int(row.get("count", 0)),
                success=int(row.get("success", 0)),
                failure=int(row.get("failure", 0)),
            )
        except Exception as e:
            log.warning("tool_stats row skipped (%s): %s", name, e)
    return stats


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_tool_stats(stats: dict[str, ToolStat], path: Path | None = None) -> None:
    target = path or TOOL_STATS_FILE
    serialized = {name: stat.model_dump(exclude={"name"}) for name, stat in stats.items()}
    _atomic_write(target, json.dumps(serialized, indent=2, sort_keys=True, ensure_ascii=False))


def merge_into_global(trajectory: TrajectoryRecord, path: Path | None = None) -> dict[str, ToolStat]:
    """Merge this trajectory's per-tool counters into the global file.

    Returns the updated full dict. Never raises; logs and no-ops on IO
    failure.
    """
    try:
        current = load_tool_stats(path)
        for name, stat in trajectory.tool_stats.items():
            acc = current.get(name) or ToolStat(name=name)
            acc.count += stat.count
            acc.success += stat.success
            acc.failure += stat.failure
            current[name] = acc
        save_tool_stats(current, path)
        return current
    except Exception as e:
        log.warning("tool_stats merge failed: %s", e)
        return {}


def success_rate_snapshot(stats: dict[str, ToolStat]) -> dict[str, float]:
    """Return {tool_name: success_rate} for quick inspection / reward input."""
    return {name: stat.success_rate for name, stat in stats.items() if stat.count > 0}
