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
from datetime import datetime
from typing import Iterator

from schemas.trajectory import TrajectoryRecord

from . import config as _cfg
from .trajectory_recorder import TrajectoryRecorder

log = logging.getLogger("mira.evolution.trace")


def workflow_trace(
    name: str,
    *,
    agent: str = "super",
    budget_seconds: float | None = None,
    user_id: str = "",
):
    """Drop-in convenience for wrapping a `do_X` workflow entry point.

    Builds a unique task_id from `name` + timestamp + optional user, so
    every cycle of the workflow becomes one trajectory. Usage:

        def do_journal(user_id="ang"):
            with workflow_trace("journal", user_id=user_id) as trace:
                trace.add_user("daily journal cycle")
                # ...existing body...
                trace.mark_completed(outcome_verified=True)

    The returned context manager is the same as `trace_task` — same
    flag-gating, same soft-fail semantics.
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"_{user_id}" if user_id else ""
    task_id = f"{name}_{ts}{suffix}"
    return trace_task(task_id=task_id, agent=agent, budget_seconds=budget_seconds)


def traced(name: str, *, agent: str = "super", budget_seconds: float | None = None):
    """Decorator form — easiest wiring for existing `do_X` functions.

    Automatically picks up `user_id` kwarg (default "ang") so the task_id
    is disambiguated per user when the agent runs multi-tenant.

    Marks `completed=True` and `outcome_verified` based on whether the
    wrapped function returned without exception; sets `crashed=True`
    if it raised (trace_task handles that automatically via __exit__).

    Zero-overhead when `ENABLE_TRAJECTORY_V2` is off.
    """

    def _decorator(fn):
        from functools import wraps

        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = kwargs.get("user_id", "") or (args[0] if args and isinstance(args[0], str) else "")
            with workflow_trace(name, agent=agent, budget_seconds=budget_seconds, user_id=uid) as trace:
                trace.add_user(f"{name} cycle")
                result = fn(*args, **kwargs)
                trace.mark_completed(outcome_verified=True)
                return result

        return wrapper

    return _decorator


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
                # Phase 2 — also index turns for FTS5 recall. We index the
                # *uncompressed* record so the raw exchange stays searchable;
                # the summarized middle would make phrase recall useless.
                try:
                    from memory.session_index import index_trajectory

                    index_trajectory(record)
                except Exception as idx_err:
                    log.debug("session_index.index_trajectory skipped: %s", idx_err)
            except Exception as e:
                log.warning("trace_task finalize failed (task=%s): %s", task_id, e)
