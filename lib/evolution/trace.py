"""Task-level trajectory capture context.

Usage in task entry points:

    from evolution.trace import trace_task

    with trace_task(task_id="t_123", agent="writer", budget_seconds=120) as trace:
        trace.add_user(prompt)
        response = claude_think(prompt, ...)
        trace.add_assistant(response)
        # ... tool calls, etc.
        trace.mark_completed(outcome_verified=True)

On exit the context manager:
1. Stamps completion/partial/crashed state based on how the block exited.
2. Runs trajectory_compressor (idempotent + LLM-guarded).
3. Appends to global data/soul/trajectories.jsonl.
4. Merges tool_stats into the global aggregate.
5. Computes v2 reward and logs it.

All of the above is a no-op unless `config.ENABLE_TRAJECTORY_V2` is True.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from schemas.trajectory import TrajectoryRecord

from . import config as _cfg
from .trajectory_recorder import TrajectoryRecorder

log = logging.getLogger("mira.evolution.trace")


class TaskTrace:
    """Public façade exposed to `with trace_task(...) as trace:`."""

    def __init__(
        self,
        task_id: str,
        agent: str,
        *,
        model: str = "",
        prompt_index: int = 0,
        budget_seconds: float | None = None,
    ) -> None:
        self._recorder = TrajectoryRecorder(
            task_id=task_id,
            agent=agent,
            model=model,
            prompt_index=prompt_index,
        )
        self._started = time.monotonic()
        self._budget = budget_seconds
        self._completed = False
        self._partial = False
        self._crashed = False
        self._outcome_verified: bool | None = None

    def add_system(self, content: str) -> None:
        self._recorder.add_system(content)

    def add_user(self, content: str) -> None:
        self._recorder.add_user(content)

    def add_assistant(
        self,
        content: str,
        *,
        tool_name: str | None = None,
        tool_args: dict | None = None,
    ) -> None:
        self._recorder.add_assistant(content, tool_name=tool_name, tool_args=tool_args)

    def add_tool_result(self, tool_name: str, output: str, *, success: bool) -> None:
        self._recorder.add_tool_result(tool_name, output, success=success)

    def record_tool(self, name: str, *, success: bool) -> None:
        self._recorder.record_tool(name, success=success)

    def bump_api_calls(self, n: int = 1) -> None:
        self._recorder.bump_api_calls(n)

    def mark_completed(self, *, outcome_verified: bool | None = None) -> None:
        self._completed = True
        self._outcome_verified = outcome_verified

    def mark_partial(self) -> None:
        self._partial = True

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._started

    def finalize(self) -> TrajectoryRecord:
        return self._recorder.finalize(
            completed=self._completed,
            partial=self._partial,
            crashed=self._crashed,
        )


@contextmanager
def trace_task(
    task_id: str,
    agent: str,
    *,
    model: str = "",
    prompt_index: int = 0,
    budget_seconds: float | None = None,
) -> Iterator[TaskTrace]:
    """Enter a trajectory-capture context for one task.

    No-op when `config.ENABLE_TRAJECTORY_V2` is False: still yields a
    TaskTrace (so callers can always invoke its methods unconditionally)
    but the finalize side-effects are skipped.
    """
    trace = TaskTrace(
        task_id=task_id,
        agent=agent,
        model=model,
        prompt_index=prompt_index,
        budget_seconds=budget_seconds,
    )
    try:
        yield trace
    except BaseException:
        trace._crashed = True
        raise
    finally:
        if _cfg.ENABLE_TRAJECTORY_V2:
            try:
                record = trace.finalize()
                from .trajectory_compressor import compress
                from .trajectory_recorder import append_to_global
                from .tool_stats import merge_into_global
                from .rewards_v2 import compute_trajectory_reward

                compressed = compress(record)
                append_to_global(compressed)
                merge_into_global(compressed)
                score, components = compute_trajectory_reward(
                    compressed,
                    outcome_verified=trace._outcome_verified,
                    elapsed_seconds=trace.elapsed_seconds,
                    budget_seconds=trace._budget,
                )
                log.debug(
                    "trace task=%s agent=%s score=%.3f components=%s elapsed=%.1fs",
                    task_id,
                    agent,
                    score,
                    components,
                    trace.elapsed_seconds,
                )
            except Exception as e:
                log.warning("trace_task finalize failed (task=%s): %s", task_id, e)
