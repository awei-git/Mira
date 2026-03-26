"""TaskManager — non-blocking background task dispatch with PID tracking.

The super agent dispatches tasks here; sub-agents run as separate processes.
Each launchd cycle: dispatch new tasks + collect completed results.
"""
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from config import MIRA_DIR, TASK_TIMEOUT, TASK_TIMEOUT_LONG, MAX_CONCURRENT_TASKS

# Timeout resolution: first check registry manifest, then fall back to tag keywords
_LONG_TIMEOUT_TAGS = {"writing", "write", "novel", "essay", "blog", "research",
                      "深度研究", "coding", "podcast", "audio", "tts", "generate"}

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
    except Exception:
        pass  # Registry not available, fall back to tags

    task_tags = set(tags or [])
    if task_tags & _LONG_TIMEOUT_TAGS:
        return TASK_TIMEOUT_LONG
    return TASK_TIMEOUT

log = logging.getLogger("mira")

# Task workspaces stored locally (NOT on iCloud bridge)
from config import MIRA_ROOT
TASKS_DIR = MIRA_ROOT / "tasks"
STATUS_FILE = TASKS_DIR / "status.json"
HISTORY_FILE = TASKS_DIR / "history.jsonl"

# Path to the worker script (same directory as this file)
WORKER_SCRIPT = Path(__file__).resolve().parent / "task_worker.py"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class TaskRecord:
    task_id: str
    msg_id: str
    thread_id: str
    sender: str
    content_preview: str   # first 80 chars of message
    pid: int
    status: str            # dispatched | running | done | error | timeout
    started_at: str
    completed_at: str = ""
    workspace: str = ""
    summary: str = ""
    tags: list[str] | None = None  # task type tags for classification

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

    _WRITING_KW = ["写", "文章", "essay", "blog", "write", "novel", "research",
                   "深度研究", "rewrite", "改写", "重写", "稿", "研究"]
    _CODE_KW = ["修改", "implement", "fix", "code", "refactor", "改进",
                "framework", "pipeline", "publish", "发布"]
    _MEDIA_KW = ["podcast", "音频", "tts", "audio", "generate", "生成",
                 "transcript", "episode", "跑"]

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

    def dispatch(self, msg, workspace_dir: Path) -> str:
        """Spawn a background worker for a message. Returns task_id.

        Supports up to MAX_CONCURRENT_TASKS parallel workers.
        Returns "" if all slots occupied — caller should leave the message for next cycle.

        Args:
            msg: talk_bridge.Message instance
            workspace_dir: directory for task output files
        """
        if self.is_busy():
            log.info("TaskManager busy (%d/%d slots), deferring task for msg %s",
                     self.get_active_count(), MAX_CONCURRENT_TASKS, msg.id)
            return ""

        task_id = msg.id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Clean stale results from previous runs so worker doesn't skip execution
        for stale in ("result.json", "result.tmp", "output.md", "summary.txt"):
            f = workspace_dir / stale
            if f.exists():
                f.unlink()

        # Write message to a temp file for the worker to read
        msg_file = workspace_dir / "message.json"
        msg_file.write_text(
            json.dumps(msg.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Spawn worker as a detached process
        cmd = [
            sys.executable,
            str(WORKER_SCRIPT),
            "--msg-file", str(msg_file),
            "--workspace", str(workspace_dir),
            "--task-id", task_id,
        ]
        if msg.thread_id:
            cmd.extend(["--thread-id", msg.thread_id])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            log.error("Failed to dispatch task %s: %s", task_id, e)
            return ""

        record = TaskRecord(
            task_id=task_id,
            msg_id=msg.id,
            thread_id=msg.thread_id,
            sender=msg.sender,
            content_preview=msg.content[:80],
            pid=proc.pid,
            status="dispatched",
            started_at=_utc_iso(),
            workspace=str(workspace_dir),
            tags=classify_task(msg.content),
        )
        self._records.append(record)
        self._save_status()

        log.info("Dispatched task %s (PID %d): %s", task_id, proc.pid, msg.content[:60])
        return task_id

    # ------------------------------------------------------------------
    # Check / collect
    # ------------------------------------------------------------------

    def check_tasks(self) -> list[TaskRecord]:
        """Check all running tasks. Returns list of newly completed records."""
        completed = []

        for rec in self._records:
            if rec.status in ("done", "error", "timeout", "needs-input"):
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
                    log.warning("Task %s exceeded timeout (PID %d, %ds/%ds) — notifying user",
                                rec.task_id, rec.pid, int(elapsed), int(timeout))
                    # Only notify once (check if already notified)
                    if rec.status != "timeout_pending":
                        rec.status = "timeout_pending"
                        try:
                            from mira import Mira
                            bridge = Mira(MIRA_DIR)
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
                        from mira import Mira
                        bridge = Mira(MIRA_DIR)
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
                    rec.status = "running"

        if completed:
            self._save_status()
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
                rec.status = data.get("status", "done")
                rec.summary = data.get("summary", "")
                rec.completed_at = data.get("completed_at", _utc_iso())
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

        # Process died without producing output
        rec.status = "error"
        rec.completed_at = _utc_iso()
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
                    content[:3000]
                    + f"\n\n... (全文太长，已截断)\n\n"
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
        """Find a failed or needs-input task by task_id (for retry)."""
        for r in self._records:
            if r.task_id == task_id and r.status in ("done", "error", "timeout", "needs-input"):
                return r
        return None

    def reset_for_retry(self, task_id: str) -> bool:
        """Remove a completed/failed task record so it can be re-dispatched.

        Returns True if the record was found and removed.
        """
        for i, r in enumerate(self._records):
            if r.task_id == task_id:
                self._records.pop(i)
                self._save_status()
                log.info("Reset task %s for retry", task_id)
                return True
        return False

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
                {"task_id": r.task_id, "preview": r.content_preview,
                 "started_at": r.started_at, "tags": r.tags}
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
                records.append(TaskRecord(**rec))
            return records
        except (json.JSONDecodeError, OSError, TypeError) as e:
            log.warning("Failed to load task status: %s", e)
            return []

    def _save_status(self):
        data = [asdict(r) for r in self._records]
        STATUS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _append_history(self, records: list[TaskRecord]):
        """Append completed task records to history.jsonl."""
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            for rec in records:
                line = json.dumps(asdict(rec), ensure_ascii=False)
                f.write(line + "\n")

    def cleanup_old_records(self, max_age_days: int = 7):
        """Remove completed task records older than max_age_days."""
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_days * 86400
        before = len(self._records)
        self._records = [
            r for r in self._records
            if r.status in ("dispatched", "running")
            or not r.completed_at
            or datetime.fromisoformat(r.completed_at.replace("Z", "+00:00")).timestamp() > cutoff
        ]
        if len(self._records) < before:
            log.info("Cleaned up %d old task records", before - len(self._records))
            self._save_status()
