"""TaskManager — non-blocking background task dispatch with PID tracking.

The super agent dispatches tasks here; sub-agents run as separate processes.
Each launchd cycle: dispatch new tasks + collect completed results.
"""

import fcntl
import json
import logging
import os
import signal
import statistics
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR, TASK_TIMEOUT, TASK_TIMEOUT_LONG, MAX_CONCURRENT_TASKS, TASK_MAX_RETRIES, MAX_SUBTASK_DEPTH
from execution.runtime_contract import derive_workflow_id, normalize_task_status

# Timeout resolution: first check registry manifest, then fall back to tag keywords
_LONG_TIMEOUT_TAGS = {
    "writing",
    "write",
    "novel",
    "essay",
    "blog",
    "research",
    "深度研究",
    "coding",
    "podcast",
    "audio",
    "tts",
    "generate",
}

# No-timeout agents (background jobs that can run indefinitely)
_BACKGROUND_TIMEOUT = 86400  # 24 hours — effectively no timeout


def _resolve_timeout(tags: list[str]) -> float:
    """Resolve timeout for a task based on registry manifests + fallback tags.

    Priority:
    1. If tags include an agent name → check registry manifest timeout_category
    2. Fall back to keyword-based tag matching
    """
    try:
        from agent_registry import get_registry

        registry = get_registry()
        for tag in tags:
            cat = registry.get_timeout_category(tag)
            if cat == "background":
                return _BACKGROUND_TIMEOUT
            elif cat == "long":
                return TASK_TIMEOUT_LONG
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass  # Registry not available, fall back to tags

    task_tags = set(tags or [])
    if task_tags & _LONG_TIMEOUT_TAGS:
        return TASK_TIMEOUT_LONG
    return TASK_TIMEOUT


log = logging.getLogger("mira")


class TaskDepthExceeded(Exception):
    pass


# Task workspaces stored locally (NOT on iCloud bridge)
from config import TASKS_DIR

STATUS_FILE = TASKS_DIR / "status.json"
HISTORY_FILE = TASKS_DIR / "history.jsonl"
TIMING_STATS_FILE = TASKS_DIR / "timing_stats.jsonl"

# Path to the worker script (same directory as this file)
WORKER_SCRIPT = Path(__file__).resolve().parent / "task_worker.py"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class TaskRecord:
    task_id: str
    workflow_id: str
    msg_id: str
    thread_id: str
    sender: str
    content_preview: str  # first 80 chars of message
    pid: int
    status: str  # dispatched | running | done | needs-input | blocked | failed | timeout
    started_at: str
    user_id: str = "ang"
    completed_at: str = ""
    workspace: str = ""
    summary: str = ""
    tags: list[str] | None = None  # task type tags for classification
    attempt_count: int = 1
    max_attempts: int = TASK_MAX_RETRIES
    failure_class: str = ""
    timeout_alerted_at: str = ""

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


# ---------------------------------------------------------------------------
# Auto-classification (placeholder — real tagging happens in task_worker via LLM)
# ---------------------------------------------------------------------------


def classify_task(content: str) -> list[str]:
    """Pre-classify task by keywords so timeout is set correctly at dispatch.

    Full smart classification still happens in task_worker post-completion,
    but this ensures long tasks get adequate timeout before they start.
    """
    tags = []
    lower = content.lower()

    _WRITING_KW = [
        "写",
        "文章",
        "essay",
        "blog",
        "write",
        "novel",
        "research",
        "深度研究",
        "rewrite",
        "改写",
        "重写",
        "稿",
        "研究",
    ]
    _CODE_KW = ["修改", "implement", "fix", "code", "refactor", "改进", "framework", "pipeline", "publish", "发布"]
    _MEDIA_KW = ["podcast", "音频", "tts", "audio", "generate", "生成", "transcript", "episode", "跑"]

    if any(kw in lower for kw in _WRITING_KW):
        tags.append("writing")
    if any(kw in lower for kw in _CODE_KW):
        tags.append("coding")
    if any(kw in lower for kw in _MEDIA_KW):
        tags.append("podcast")

    return tags


class TaskManager:
    """Manages background task processes for TalkBridge."""

    def __init__(self):
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._records: list[TaskRecord] = self._load_status()

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def is_busy(self) -> bool:
        """Check if all concurrent task slots are occupied."""
        return self.get_active_count() >= MAX_CONCURRENT_TASKS

    def dispatch(
        self,
        msg,
        workspace_dir: Path,
        *,
        attempt_count: int = 1,
        max_attempts: int | None = None,
        depth: int = 0,
        task_chain: list[str] | None = None,
    ) -> str:
        """Spawn a background worker for a message. Returns task_id.

        Supports up to MAX_CONCURRENT_TASKS parallel workers.
        Returns "" if all slots occupied — caller should leave the message for next cycle.

        Args:
            msg: talk_bridge.Message instance
            workspace_dir: directory for task output files
            depth: current subtask nesting depth (0 = top-level)
            task_chain: ordered list of ancestor task IDs for observability
        """
        chain = list(task_chain or [])
        if depth >= MAX_SUBTASK_DEPTH:
            chain_str = " -> ".join(chain) if chain else "(empty)"
            log.error(
                "TaskDepthExceeded: depth=%d >= MAX_SUBTASK_DEPTH=%d for msg %s; chain: %s",
                depth,
                MAX_SUBTASK_DEPTH,
                msg.id,
                chain_str,
            )
            raise TaskDepthExceeded(f"Subtask depth {depth} reached limit {MAX_SUBTASK_DEPTH}. Chain: {chain_str}")

        if self.is_busy():
            log.info(
                "TaskManager busy (%d/%d slots), deferring task for msg %s",
                self.get_active_count(),
                MAX_CONCURRENT_TASKS,
                msg.id,
            )
            return ""

        task_id = msg.id
        workflow_id = derive_workflow_id(
            task_id=task_id,
            thread_id=getattr(msg, "thread_id", "") or "",
            workflow_id=getattr(msg, "workflow_id", "") or "",
        )
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Clean stale results from previous runs so worker doesn't skip execution
        for stale in ("result.json", "result.tmp", "output.md", "summary.txt"):
            f = workspace_dir / stale
            if f.exists():
                f.unlink()

        # Write message to a temp file for the worker to read
        msg_file = workspace_dir / "message.json"
        msg_payload = dict(msg.to_dict())
        msg_payload["workflow_id"] = workflow_id
        msg_payload["user_id"] = getattr(msg, "user_id", "ang") or "ang"
        msg_payload["subtask_depth"] = depth
        msg_payload["task_chain"] = chain + [msg.id]
        msg_file.write_text(json.dumps(msg_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        # Spawn worker as a detached process
        cmd = [
            sys.executable,
            str(WORKER_SCRIPT),
            "--msg-file",
            str(msg_file),
            "--workspace",
            str(workspace_dir),
            "--task-id",
            task_id,
        ]
        if msg.thread_id:
            cmd.extend(["--thread-id", msg.thread_id])

        stderr_log = workspace_dir / "worker_stderr.log"
        try:
            stderr_fh = open(stderr_log, "w", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh,
                start_new_session=True,
            )
        except Exception as e:
            log.error("Failed to dispatch task %s: %s", task_id, e)
            return ""

        record = TaskRecord(
            task_id=task_id,
            workflow_id=workflow_id,
            msg_id=msg.id,
            thread_id=msg.thread_id,
            user_id=getattr(msg, "user_id", "ang") or "ang",
            sender=msg.sender,
            content_preview=msg.content[:80],
            pid=proc.pid,
            status="dispatched",
            started_at=_utc_iso(),
            workspace=str(workspace_dir),
            tags=classify_task(msg.content),
            attempt_count=attempt_count,
            max_attempts=max_attempts or TASK_MAX_RETRIES,
        )
        self._records.append(record)
        self._save_status()

        log.info(
            "Dispatched task %s workflow=%s user=%s (PID %d): %s",
            task_id,
            workflow_id,
            record.user_id,
            proc.pid,
            msg.content[:60],
        )
        return task_id

    # ------------------------------------------------------------------
    # Check / collect
    # ------------------------------------------------------------------

    def check_tasks(self) -> list[TaskRecord]:
        """Check all running tasks. Returns list of newly completed records."""
        completed = []
        changed = False

        for rec in self._records:
            if rec.status in ("done", "failed", "timeout", "needs-input", "blocked"):
                continue

            # Check if process is still alive
            try:
                os.kill(rec.pid, 0)  # signal 0 = check existence
                alive = True
            except OSError:
                alive = False

            if not alive:
                # Process exited — check for result
                result = self._collect_result(rec)
                completed.append(rec)
            else:
                # Check timeout
                started = datetime.fromisoformat(rec.started_at.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                timeout = _resolve_timeout(rec.tags or [])
                if elapsed > timeout:
                    # Don't kill — pause and ask user
                    log.warning(
                        "Task %s exceeded timeout (PID %d, %ds/%ds) — notifying user",
                        rec.task_id,
                        rec.pid,
                        int(elapsed),
                        int(timeout),
                    )
                    # Only notify once (check if already notified)
                    if not rec.timeout_alerted_at:
                        rec.timeout_alerted_at = _utc_iso()
                        changed = True
                        try:
                            from bridge import Mira

                            bridge = Mira(MIRA_DIR, user_id=rec.user_id)
                            elapsed_str = f"{int(elapsed//60)}分钟"
                            bridge.create_item(
                                f"timeout-{rec.task_id}",
                                "alert",
                                f"任务超时: {rec.content_preview}",
                                f"任务已运行{elapsed_str}，超过预期时间。\n\n"
                                f"回复 'kill' 终止任务，或 'wait' 继续等待。\n\n"
                                f"任务ID: {rec.task_id}",
                                sender="agent",
                                tags=["timeout", "alert"],
                                origin="agent",
                            )
                        except Exception as e:
                            log.warning("Failed to send timeout notification: %s", e)
                    # Check if user replied 'kill' to the timeout alert
                    try:
                        from bridge import Mira

                        bridge = Mira(MIRA_DIR, user_id=rec.user_id)
                        timeout_item = bridge.get_item(f"timeout-{rec.task_id}")
                        if timeout_item:
                            msgs = timeout_item.get("messages", [])
                            user_replies = [m for m in msgs if m.get("sender") in ("ang", "iphone", "user")]
                            if user_replies:
                                last_reply = user_replies[-1].get("content", "").lower().strip()
                                if "kill" in last_reply or "停" in last_reply or "stop" in last_reply:
                                    log.info("User requested kill for timed-out task %s", rec.task_id)
                                    self._kill_task(rec)
                                    rec.status = "timeout"
                                    rec.completed_at = _utc_iso()
                                    rec.summary = "Task killed by user after timeout"
                                    completed.append(rec)
                                # If user said 'wait', just let it keep running
                    except Exception:
                        pass
                else:
                    if rec.status != "running":
                        rec.status = "running"
                        changed = True

        if completed or changed:
            self._save_status()
        if completed:
            self._append_history(completed)

        return completed

    def _collect_result(self, rec: TaskRecord) -> bool:
        """Read result from a completed task's workspace."""
        import time as _time

        ws = Path(rec.workspace)
        result_file = ws / "result.json"

        # Grace period: filesystem may not have flushed yet
        if not result_file.exists():
            _time.sleep(0.5)

        if result_file.exists():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
                rec.status = normalize_task_status(data.get("status", "done"))
                rec.summary = data.get("summary", "")
                rec.completed_at = data.get("completed_at", _utc_iso())
                rec.failure_class = data.get("failure_class", "")
                rec.workflow_id = data.get("workflow_id", rec.workflow_id) or rec.workflow_id
                if data.get("tags"):
                    rec.tags = data["tags"]
                return True
            except (json.JSONDecodeError, OSError) as e:
                log.error("Failed to read result for task %s: %s", rec.task_id, e)

        # No result file — check if output.md exists as fallback
        output_file = ws / "output.md"
        if output_file.exists():
            rec.status = "done"
            rec.summary = output_file.read_text(encoding="utf-8")[:200]
            rec.completed_at = _utc_iso()
            return True

        # Process died without producing output — check stderr for crash info
        rec.status = "failed"
        rec.completed_at = _utc_iso()
        rec.failure_class = "worker_crash"
        stderr_file = ws / "worker_stderr.log"
        crash_info = ""
        if stderr_file.exists():
            try:
                stderr_text = stderr_file.read_text(encoding="utf-8").strip()
                if stderr_text:
                    # Extract last line (usually the exception message)
                    last_lines = stderr_text.strip().split("\n")[-2:]
                    crash_info = " ".join(l.strip() for l in last_lines)[:200]
            except OSError:
                pass
        if crash_info:
            rec.summary = f"Worker crashed: {crash_info}"
        else:
            rec.summary = "Worker exited without producing output"
        return False

    def _kill_task(self, rec: TaskRecord):
        """Kill a running task process."""
        try:
            os.killpg(os.getpgid(rec.pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(rec.pid, signal.SIGKILL)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Get results for replying
    # ------------------------------------------------------------------

    def get_reply_content(self, rec: TaskRecord) -> str:
        """Get the content to send back as a reply for a completed task.

        Send output.md directly — that's the actual result the user wants to read.
        Only truncate + attach file link for very long outputs (>4000 chars).
        summary.txt is a meta-description; only use as fallback when no output.md.
        """
        ws = Path(rec.workspace)

        # Build a relative path from bridge root (for iOS file links)
        bridge_root = MIRA_DIR
        try:
            rel_ws = ws.relative_to(bridge_root)
        except ValueError:
            rel_ws = ws

        output_file = ws / "output.md"
        if output_file.exists():
            content = output_file.read_text(encoding="utf-8").strip()
            if content:
                if len(content) <= 4000:
                    return content
                return (
                    content[:3000] + f"\n\n... (全文太长，已截断)\n\n"
                    f"完整内容: [{rel_ws / 'output.md'}](file://{rel_ws / 'output.md'})"
                )

        # No output.md — try summary.txt
        summary_file = ws / "summary.txt"
        if summary_file.exists():
            summary = summary_file.read_text(encoding="utf-8").strip()
            if summary:
                return summary

        return rec.summary or "任务完成，但没有产生输出。"

    # ------------------------------------------------------------------
    # Pending tasks (for skipping already-dispatched messages)
    # ------------------------------------------------------------------

    def is_dispatched(self, msg_id: str) -> bool:
        """Check if a message already has a task (any status)."""
        return any(r.msg_id == msg_id for r in self._records)

    def find_failed_task(self, task_id: str) -> TaskRecord | None:
        """Find a terminal task by task_id (for retry or inspection)."""
        for r in self._records:
            if r.task_id == task_id and r.status in ("done", "failed", "timeout", "needs-input", "blocked"):
                return r
        return None

    def can_retry(self, rec: TaskRecord) -> bool:
        """Return whether a terminal task still has retry budget."""
        return rec.status in ("failed", "timeout", "blocked") and rec.attempt_count < rec.max_attempts

    def reset_for_retry(self, task_id: str) -> TaskRecord | None:
        """Remove a completed/failed task record so it can be re-dispatched.

        Returns the removed record if found.
        """
        for i, r in enumerate(self._records):
            if r.task_id == task_id:
                removed = self._records.pop(i)
                self._save_status()
                log.info("Reset task %s for retry", task_id)
                return removed
        return None

    def get_active_count(self) -> int:
        """Number of currently running tasks."""
        return sum(1 for r in self._records if r.status in ("dispatched", "running"))

    def get_status_summary(self) -> dict:
        """Return agent status summary for heartbeat/display.

        Returns dict with:
            busy: bool — whether any tasks are active
            active_count: int — number of running tasks
            active_tasks: list — preview of active task content
            last_completed: str — timestamp of most recent completion
        """
        active = [r for r in self._records if r.status in ("dispatched", "running")]
        completed = [r for r in self._records if r.completed_at]
        completed.sort(key=lambda r: r.completed_at, reverse=True)

        return {
            "busy": len(active) > 0,
            "active_count": len(active),
            "active_tasks": [
                {
                    "task_id": r.task_id,
                    "workflow_id": r.workflow_id,
                    "preview": r.content_preview,
                    "started_at": r.started_at,
                    "tags": r.tags,
                }
                for r in active
            ],
            "last_completed": completed[0].completed_at if completed else "",
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_status(self) -> list[TaskRecord]:
        if not STATUS_FILE.exists():
            return []
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            records = []
            for rec in data:
                # Backfill tags for old records
                if "tags" not in rec:
                    rec["tags"] = []
                if "user_id" not in rec:
                    rec["user_id"] = "ang"
                if "workflow_id" not in rec:
                    rec["workflow_id"] = derive_workflow_id(
                        task_id=rec.get("task_id", ""),
                        thread_id=rec.get("thread_id", ""),
                    )
                if "attempt_count" not in rec:
                    rec["attempt_count"] = 1
                if "max_attempts" not in rec:
                    rec["max_attempts"] = TASK_MAX_RETRIES
                if "failure_class" not in rec:
                    rec["failure_class"] = ""
                if "timeout_alerted_at" not in rec:
                    rec["timeout_alerted_at"] = ""
                rec["status"] = normalize_task_status(rec.get("status", ""))
                records.append(TaskRecord(**rec))
            return records
        except (json.JSONDecodeError, OSError, TypeError) as e:
            log.warning("Failed to load task status: %s", e)
            return []

    def _save_status(self):
        """Save task records atomically with file lock."""
        data = [asdict(r) for r in self._records]
        lock_path = STATUS_FILE.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                fd, tmp = tempfile.mkstemp(dir=STATUS_FILE.parent, suffix=".tmp", prefix=".tasks_")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, STATUS_FILE)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _append_history(self, records: list[TaskRecord]):
        """Append completed task records to history.jsonl with lock."""
        lock_path = HISTORY_FILE.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                    for rec in records:
                        line = json.dumps(asdict(rec), ensure_ascii=False)
                        f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

        # Record task outcomes in evolution experience log
        try:
            from evolution import record_task_outcome

            for rec in records:
                if rec.status in ("done", "failed", "timeout"):
                    agent = rec.tags[0] if rec.tags else "general"
                    record_task_outcome(
                        task_id=rec.task_id,
                        agent=agent,
                        action=rec.content_preview[:200],
                        status=rec.status,
                        summary=rec.summary[:300] if rec.summary else "",
                    )
        except Exception as e:
            log.debug("Evolution recording failed (non-critical): %s", e)

    def cleanup_old_records(self, max_age_days: int = 7):
        """Remove completed task records older than max_age_days."""
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        before = len(self._records)
        self._records = [
            r
            for r in self._records
            if r.status in ("dispatched", "running")
            or not r.completed_at
            or datetime.fromisoformat(r.completed_at.replace("Z", "+00:00")).timestamp() > cutoff
        ]
        if len(self._records) < before:
            log.info("Cleaned up %d old task records", before - len(self._records))
            self._save_status()


# ---------------------------------------------------------------------------
# Timing percentile utilities — read timing_stats.jsonl, compute p50/p95
# ---------------------------------------------------------------------------


def _percentile(sorted_data: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in 0–100) over a sorted list."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    k = (n - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, n - 1)
    return sorted_data[lo] + (k - lo) * (sorted_data[hi] - sorted_data[lo])


def get_timing_percentiles(task_type: str | None = None, window_days: int = 7) -> dict:
    """Read timing_stats.jsonl and return p50/p95 per task_type for the last window_days.

    Returns:
        {task_type: {"p50": float, "p95": float, "count": int, "configured_timeout_s": float}}
    """
    if not TIMING_STATS_FILE.exists():
        return {}

    cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400
    samples: dict[str, list[float]] = {}
    configured: dict[str, float] = {}

    try:
        for line in TIMING_STATS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_str = entry.get("ts", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                    if ts < cutoff:
                        continue
                except ValueError:
                    pass
            ttype = entry.get("task_type", "unknown")
            if task_type and ttype != task_type:
                continue
            dur = entry.get("actual_duration_s")
            if dur is None:
                continue
            samples.setdefault(ttype, []).append(float(dur))
            configured[ttype] = entry.get("configured_timeout_s", 0.0)
    except OSError:
        return {}

    result = {}
    for ttype, durs in samples.items():
        sorted_durs = sorted(durs)
        result[ttype] = {
            "p50": round(_percentile(sorted_durs, 50), 1),
            "p95": round(_percentile(sorted_durs, 95), 1),
            "count": len(durs),
            "configured_timeout_s": configured.get(ttype, 0.0),
        }
    return result


def get_timing_summary(window_days: int = 7) -> str:
    """Return a human-readable timing summary for inclusion in the daily journal.

    Example:
        Task timing (7-day, act phase):
          writer       p50=45s   p95=280s  budget=600s  n=12
          coder        p50=120s  p95=490s  budget=600s  n=5   ⚠ p95>80%
    """
    percentiles = get_timing_percentiles(window_days=window_days)
    if not percentiles:
        return ""

    lines = [f"Task timing ({window_days}-day, act phase):"]
    for ttype, stats in sorted(percentiles.items()):
        budget = stats["configured_timeout_s"]
        p95_pct = (stats["p95"] / budget * 100) if budget else 0
        warning = "  ⚠ p95>80%" if p95_pct > 80 else ""
        lines.append(
            f"  {ttype:<12} p50={stats['p50']:.0f}s"
            f"  p95={stats['p95']:.0f}s"
            f"  budget={int(budget)}s"
            f"  n={stats['count']}{warning}"
        )
    return "\n".join(lines)
