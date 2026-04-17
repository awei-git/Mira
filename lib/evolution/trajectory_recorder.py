"""TrajectoryRecorder — assemble a TrajectoryRecord during a task's life.

Usage pattern in task_worker (once Phase 1 wiring lands):

    rec = TrajectoryRecorder(task_id, agent, model="claude-opus-4-7")
    rec.add_user(prompt)
    # ... each LLM response / tool call / tool result:
    rec.add_assistant(response_text, tool_name=..., tool_args=...)
    rec.record_tool(tool_name, success=True)
    rec.add_tool_result(tool_name, output_preview, success=True)
    trajectory = rec.finalize(completed=True)

Persistence is split in two:
- Per-task trace: `<workspace>/trajectory.jsonl` (one JSON line with the
  full record) — useful for debugging.
- Global aggregate: `data/soul/trajectories.jsonl` (one line per task,
  after compression) — consumed by reflect + rewards.

The recorder never raises on persistence failure; missing or broken
storage degrades silently so the main task path never crashes because
of telemetry.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from schemas.trajectory import TrajectoryRecord, Turn

from .config import TRAJECTORY_FILE, TOOL_STATS_FILE  # noqa: F401  (for discovery)

log = logging.getLogger("mira.evolution.recorder")


_TOOL_RESULT_PREVIEW_CHARS = 800


def _truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [+{len(text) - limit} chars]"


class TrajectoryRecorder:
    """Stateful builder for a single task's TrajectoryRecord.

    Not thread-safe — one recorder per task_worker process.
    """

    def __init__(
        self,
        task_id: str,
        agent: str,
        *,
        model: str = "",
        prompt_index: int = 0,
    ) -> None:
        self._record = TrajectoryRecord(
            task_id=task_id,
            agent=agent,
            model=model,
            prompt_index=prompt_index,
            timestamp=datetime.now(timezone.utc),
        )

    # ---- turn builders --------------------------------------------------

    def add_system(self, content: str) -> None:
        self._record.add_turn(Turn(role="system", content=content))

    def add_user(self, content: str) -> None:
        self._record.add_turn(Turn(role="human", content=content))

    def add_assistant(
        self,
        content: str,
        *,
        tool_name: str | None = None,
        tool_args: dict | None = None,
    ) -> None:
        self._record.add_turn(
            Turn(
                role="assistant",
                content=content,
                tool_name=tool_name,
                tool_args=tool_args,
            )
        )

    def add_tool_result(
        self,
        tool_name: str,
        output: str,
        *,
        success: bool,
    ) -> None:
        self._record.add_turn(
            Turn(
                role="tool",
                content="",
                tool_name=tool_name,
                tool_result_preview=_truncate(output, _TOOL_RESULT_PREVIEW_CHARS),
                tool_success=success,
            )
        )

    def record_tool(self, name: str, *, success: bool) -> None:
        """Record a tool invocation for stats aggregation."""
        self._record.record_tool(name, success)

    def bump_api_calls(self, n: int = 1) -> None:
        self._record.api_calls += n

    # ---- finalization ---------------------------------------------------

    def finalize(
        self,
        *,
        completed: bool,
        partial: bool = False,
        crashed: bool = False,
    ) -> TrajectoryRecord:
        self._record.completed = completed
        self._record.partial = partial
        self._record.crashed = crashed
        return self._record


# -----------------------------------------------------------------------
# Persistence helpers — separate from the builder so tests can unit them
# -----------------------------------------------------------------------


def persist_per_task(workspace: Path, trajectory: TrajectoryRecord) -> Path | None:
    """Write the full trajectory to `<workspace>/trajectory.jsonl`.

    Returns the path on success, None on failure (never raises).
    """
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        path = workspace / "trajectory.jsonl"
        path.write_text(trajectory.as_jsonl_line() + "\n", encoding="utf-8")
        return path
    except OSError as e:
        log.warning("per-task trajectory write failed (%s): %s", workspace, e)
        return None


def append_to_global(trajectory: TrajectoryRecord) -> Path | None:
    """Append the (already-compressed) trajectory to the global JSONL.

    Returns the path on success, None on failure (never raises).
    """
    try:
        TRAJECTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAJECTORY_FILE, "a", encoding="utf-8") as f:
            f.write(trajectory.as_jsonl_line() + "\n")
        return TRAJECTORY_FILE
    except OSError as e:
        log.warning("global trajectory append failed: %s", e)
        return None


def load_trajectory_jsonl(path: Path) -> TrajectoryRecord | None:
    """Load a single-record JSONL back into a TrajectoryRecord (for tests/debug)."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    first = text.splitlines()[0]
    try:
        data = json.loads(first)
    except json.JSONDecodeError:
        return None
    try:
        return TrajectoryRecord.model_validate(data)
    except Exception as e:  # pydantic ValidationError and friends
        log.warning("trajectory deserialize failed: %s", e)
        return None
