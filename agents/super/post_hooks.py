"""Detached post-task housekeeping runner.

Invariant: a task worker writes its result.json and **exits**. Heavy
post-task work (LLM-driven knowledge extraction, failure-lesson mining,
semantic index rebuild) runs in a detached subprocess that survives the
worker's exit, so the dispatch slot frees immediately.

Why a separate process and not a thread? Daemon threads die when the
worker process exits — heavy work would silently get killed mid-run.
A detached process (`start_new_session=True`) outlives its parent.

Usage (called by `task_result._write_result`):

    spawn_post_hooks(workspace, task_id, status, summary, tags)

The spawn is fire-and-forget. The runner takes its inputs from a small
JSON file in the workspace; the parent does not block on its exit.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

_SUPER_DIR = Path(__file__).resolve().parent
_AGENTS_DIR = _SUPER_DIR.parent
_LIB_DIR = _AGENTS_DIR.parent / "lib"
for p in (str(_LIB_DIR), str(_SUPER_DIR), str(_AGENTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

log = logging.getLogger("post_hooks")

# Rebuild rate limit. Multiple finishing tasks would otherwise each kick off
# the 28k-chunk rebuild. We accept stale-by-N-minutes search results in
# exchange for not burning embedding API calls in a loop.
_REBUILD_MIN_INTERVAL_S = 30 * 60


def _rebuild_state_path() -> Path:
    from config import SOUL_DIR

    return SOUL_DIR / ".last_rebuild"


def _rebuild_lock_path() -> Path:
    from config import SOUL_DIR

    return SOUL_DIR / ".rebuild.lock"


def _maybe_rebuild_index() -> str:
    """Run the semantic index rebuild iff it has been long enough since the
    last successful rebuild AND no other rebuild is currently in flight.

    Returns one of: "ran", "skipped_recent", "skipped_locked", "failed".
    """
    state_file = _rebuild_state_path()
    lock_file = _rebuild_lock_path()

    if state_file.exists():
        try:
            last = float(state_file.read_text(encoding="utf-8").strip())
            if time.time() - last < _REBUILD_MIN_INTERVAL_S:
                return "skipped_recent"
        except (OSError, ValueError):
            pass

    # Best-effort lock: O_EXCL means a stale lock blocks future runs until
    # cleared. We mitigate by always removing in finally and by treating
    # locks older than 1h as stale.
    if lock_file.exists():
        try:
            age = time.time() - lock_file.stat().st_mtime
            if age < 3600:
                return "skipped_locked"
            log.warning("Stale rebuild lock (age=%ds), forcing through", int(age))
        except OSError:
            pass

    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        return "skipped_locked"
    except OSError as exc:
        log.warning("Could not acquire rebuild lock: %s", exc)
        return "failed"

    try:
        from memory.soul import rebuild_memory_index

        rebuild_memory_index()
        state_file.write_text(str(time.time()), encoding="utf-8")
        return "ran"
    except Exception as exc:
        log.warning("Index rebuild failed: %s", exc)
        return "failed"
    finally:
        try:
            lock_file.unlink()
        except OSError:
            pass


def _run_writeback(workspace: Path, task_id: str, tags: list[str] | None) -> bool:
    try:
        from task_result import _extract_knowledge_writeback

        _extract_knowledge_writeback(workspace, task_id, tags=tags)
        return True
    except Exception as exc:
        log.warning("writeback failed: %s", exc)
        return False


def _run_failure_lesson(task_id: str, summary: str) -> bool:
    try:
        from evaluation.self_iteration import (
            extract_failure_lesson,
            save_failure_lesson,
        )

        lesson = extract_failure_lesson(task_id, summary[:200], summary)
        if lesson:
            save_failure_lesson(lesson)
        return True
    except Exception as exc:
        log.warning("failure lesson extraction failed: %s", exc)
        return False


def _run_auto_flush(task_id: str, status: str, summary: str, tags: list[str] | None) -> bool:
    try:
        from memory.soul import auto_flush

        ctx = f"Task {task_id} ({status}): {summary[:500]}\n" f"Tags: {', '.join(tags) if tags else 'none'}"
        auto_flush(ctx)
        return True
    except Exception as exc:
        log.warning("auto_flush failed: %s", exc)
        return False


def _should_skip_v3_experience_write() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _run_v3_experience_write(task_id: str, status: str, summary: str, tags: list[str] | None) -> bool:
    if _should_skip_v3_experience_write():
        log.info("skipping v3 experience write during pytest")
        return False
    try:
        from mira.runtime import record_task_completion

        record_task_completion(task_id=task_id, status=status, summary=summary, tags=tags)
        return True
    except Exception as exc:
        log.warning("v3 experience write failed: %s", exc)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Mira post-task hook runner")
    parser.add_argument("--input", required=True, help="JSON payload file")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    workspace = Path(payload["workspace"])
    task_id = payload["task_id"]
    status = payload["status"]
    summary = payload.get("summary", "")
    tags = payload.get("tags") or []

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(workspace / "post_hooks.log", encoding="utf-8")],
    )
    log.info("post_hooks started: task=%s status=%s", task_id, status)
    started = time.time()

    # NB: each hook is best-effort and isolated by try/except. None can block
    # another. Order is by user-visible value: writeback > failure lesson >
    # flush > rebuild.
    if status == "done" and "private" not in tags:
        _run_writeback(workspace, task_id, tags=tags)

    if status == "failed":
        _run_failure_lesson(task_id, summary)

    _run_v3_experience_write(task_id, status, summary, tags=tags)
    _run_auto_flush(task_id, status, summary, tags=tags)

    rebuild_outcome = _maybe_rebuild_index()
    log.info(
        "post_hooks done: task=%s elapsed=%.1fs rebuild=%s",
        task_id,
        time.time() - started,
        rebuild_outcome,
    )
    return 0


def spawn_post_hooks(
    workspace: Path,
    task_id: str,
    status: str,
    summary: str,
    tags: list[str] | None,
) -> None:
    """Fire-and-forget: launch this module as a detached subprocess.

    Caller (the worker) is expected to return immediately so its dispatch
    slot frees. The detached child outlives the parent because of
    `start_new_session=True`.
    """
    import subprocess

    payload = {
        "workspace": str(workspace),
        "task_id": task_id,
        "status": status,
        "summary": summary or "",
        "tags": list(tags or []),
    }
    payload_file = workspace / ".post_hooks_input.json"
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    cmd = [sys.executable, str(Path(__file__).resolve()), "--input", str(payload_file)]
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log.warning("Failed to spawn post_hooks subprocess for %s: %s", task_id, exc)


if __name__ == "__main__":
    raise SystemExit(main())
