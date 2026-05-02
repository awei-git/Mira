from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2.extras

from execution.runtime_contract import normalize_task_status

from .audit import AuditLogger
from .db import dict_cursor, schema_name, transaction
from .projection import item_from_rows


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _read_json(path: Path) -> dict | list | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_newer_iso(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return str(left) > str(right)


def _is_human_review_draft(item_id: str, item: dict | None = None) -> bool:
    """Return True for agent-created drafts that need human action, not workers."""
    if item_id.startswith("x_reply_"):
        return True
    tags = _as_json_list((item or {}).get("tags"))
    return "x_reply" in tags or "needs-human" in tags


def _clip_status_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _worker_status_card(rec: dict, status: str) -> dict | None:
    if status not in {"dispatched", "running", "working"}:
        return None
    workspace = rec.get("workspace")
    heartbeat: dict = {}
    if workspace:
        data = _read_json(Path(workspace) / "heartbeat.json")
        if isinstance(data, dict):
            heartbeat = data
    text = heartbeat.get("status_text")
    if not text:
        preview = rec.get("content_preview") or rec.get("task_id") or "request"
        elapsed = heartbeat.get("elapsed_text")
        eta = heartbeat.get("eta_text")
        suffix = ""
        if elapsed or eta:
            suffix = f" Elapsed {elapsed or '?'}; {eta or 'ETA unknown'}."
        text = f"Working on: {_clip_status_text(preview, 120)}.{suffix}"
    return {"type": "status", "text": _clip_status_text(text), "icon": heartbeat.get("status_icon") or "hourglass"}


_BACKLOG_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class ControlRepository:
    def __init__(self, conn):
        self.conn = conn
        self.schema = schema_name()

    def _record_event(
        self, task_id: str, user_id: str, event_type: str, *, status: str | None = None, payload=None
    ) -> int:
        payload = payload or {}
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.schema}.task_events (task_id, user_id, event_type, status, payload, created_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (task_id, user_id, event_type, status, json.dumps(payload), _utc_iso()),
            )
            event_id = int(cur.fetchone()[0])
        AuditLogger(self.conn).append(
            event_type,
            task_id=task_id,
            workflow_id=task_id,
            user_id=user_id,
            payload={"status": status, **payload},
        )
        return event_id

    def record_task_event(
        self,
        user_id: str,
        task_id: str,
        event_type: str,
        *,
        status: str | None = None,
        payload=None,
    ) -> int:
        return self._record_event(task_id, user_id, event_type, status=status, payload=payload)

    def update_task_status(
        self,
        user_id: str,
        task_id: str,
        status: str,
        *,
        summary: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        agent_message: str | None = None,
        message_kind: str = "text",
        verification: dict | None = None,
        task_type: str | None = None,
        outcome_verified: bool | None = None,
        verification_method: str | None = None,
    ) -> dict | None:
        now = _utc_iso()
        normalized = normalize_task_status(status) or status
        failed = normalized in ("failed", "timeout", "blocked")
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET status = %s,
                    updated_at = %s,
                    completed_at = CASE
                        WHEN %s IN ('done', 'verified', 'completed_unverified', 'failed', 'timeout', 'blocked', 'needs-input') THEN COALESCE(completed_at, %s)
                        WHEN %s IN ('queued', 'dispatched', 'running') THEN NULL
                        ELSE completed_at
                    END,
                    error_code = CASE WHEN %s THEN %s ELSE NULL END,
                    error_message = CASE WHEN %s THEN %s ELSE NULL END,
                    retryable = %s,
                    result_summary = COALESCE(%s, result_summary),
                    verification = COALESCE(%s::jsonb, verification),
                    task_type = COALESCE(%s, task_type),
                    outcome_verified = COALESCE(%s, outcome_verified),
                    verification_method = COALESCE(%s, verification_method)
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (
                    normalized,
                    now,
                    normalized,
                    now,
                    normalized,
                    failed,
                    error_code or (normalized if failed else None),
                    failed,
                    error_message or (summary if failed else None),
                    failed,
                    summary,
                    json.dumps(verification) if verification is not None else None,
                    task_type,
                    outcome_verified,
                    verification_method,
                    task_id,
                    user_id,
                ),
            )
            if cur.fetchone() is None:
                return None
            if normalized in (
                "done",
                "verified",
                "completed_unverified",
                "failed",
                "timeout",
                "blocked",
                "needs-input",
            ):
                cur.execute(
                    f"""
                    DELETE FROM {self.schema}.messages
                    WHERE task_id = %s AND sender = 'agent' AND kind = 'status_card'
                    """,
                    (task_id,),
                )
            if agent_message:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.messages (
                        id, task_id, user_id, sender, kind, content, image_path, created_at
                    )
                    VALUES (%s, %s, %s, 'agent', %s, %s, NULL, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        sender = EXCLUDED.sender,
                        kind = EXCLUDED.kind,
                        content = EXCLUDED.content,
                        created_at = EXCLUDED.created_at
                    """,
                    (f"{task_id}_agent_terminal", task_id, user_id, message_kind, agent_message, now),
                )
        self._record_event(task_id, user_id, "task.status", status=normalized, payload={"summary": summary or ""})
        return self.get_item(user_id, task_id)

    def create_task(
        self,
        *,
        user_id: str,
        task_id: str,
        message_id: str,
        title: str,
        content: str,
        sender: str,
        item_type: str = "request",
        quick: bool = False,
        tags: list[str] | None = None,
        origin: str = "user",
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.schema}.tasks (
                    id, user_id, type, title, status, origin, quick, pinned, parent_id,
                    tags, created_at, updated_at
                )
                VALUES (
                    %(id)s, %(user_id)s, %(type)s, %(title)s, 'queued', %(origin)s,
                    %(quick)s, FALSE, NULL, %(tags)s::jsonb, %(created_at)s, %(created_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    type = EXCLUDED.type,
                    title = EXCLUDED.title,
                    status = 'queued',
                    origin = EXCLUDED.origin,
                    quick = EXCLUDED.quick,
                    pinned = FALSE,
                    parent_id = NULL,
                    tags = EXCLUDED.tags,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    error_code = NULL,
                    error_message = NULL,
                    retryable = FALSE,
                    result_path = NULL
                """,
                {
                    "id": task_id,
                    "user_id": user_id,
                    "type": item_type,
                    "title": title,
                    "origin": origin,
                    "quick": quick,
                    "tags": json.dumps(tags or []),
                    "created_at": now,
                },
            )
            cur.execute(
                f"""
                INSERT INTO {self.schema}.messages (
                    id, task_id, user_id, sender, kind, content, image_path, created_at
                )
                VALUES (%s, %s, %s, %s, 'text', %s, NULL, %s)
                ON CONFLICT (id) DO UPDATE SET
                    task_id = EXCLUDED.task_id,
                    user_id = EXCLUDED.user_id,
                    sender = EXCLUDED.sender,
                    kind = EXCLUDED.kind,
                    content = EXCLUDED.content,
                    image_path = EXCLUDED.image_path,
                    created_at = EXCLUDED.created_at
                """,
                (message_id, task_id, user_id, sender, content, now),
            )
        self._record_event(task_id, user_id, "task.created", status="queued", payload={"message_id": message_id})
        return self.get_item(user_id, task_id) or {}

    def append_user_reply(
        self,
        *,
        user_id: str,
        task_id: str,
        message_id: str,
        sender: str,
        content: str,
        created_at: str | None = None,
    ) -> dict:
        now = created_at or _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {self.schema}.tasks WHERE id = %s AND user_id = %s", (task_id, user_id))
            if cur.fetchone() is None:
                raise KeyError(task_id)
            cur.execute(
                f"""
                INSERT INTO {self.schema}.messages (
                    id, task_id, user_id, sender, kind, content, image_path, created_at
                )
                VALUES (%s, %s, %s, %s, 'text', %s, NULL, %s)
                ON CONFLICT (id) DO UPDATE SET
                    task_id = EXCLUDED.task_id,
                    user_id = EXCLUDED.user_id,
                    sender = EXCLUDED.sender,
                    content = EXCLUDED.content,
                    created_at = EXCLUDED.created_at
                """,
                (message_id, task_id, user_id, sender, content, now),
            )
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET updated_at = %s,
                    status = CASE
                        WHEN status IN (
                            'queued',
                            'dispatched',
                            'running',
                            'working'
                        ) THEN status
                        ELSE 'queued'
                    END,
                    origin = 'user',
                    completed_at = NULL,
                    archived_at = NULL,
                    error_code = NULL,
                    error_message = NULL,
                    retryable = FALSE
                WHERE id = %s AND user_id = %s
                """,
                (now, task_id, user_id),
            )
        self._record_event(task_id, user_id, "message.created", status=None, payload={"message_id": message_id})
        return self.get_item(user_id, task_id) or {}

    def set_pinned(self, user_id: str, task_id: str, pinned: bool) -> dict | None:
        now = _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET pinned = %s, updated_at = %s
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (pinned, now, task_id, user_id),
            )
            if cur.fetchone() is None:
                return None
        self._record_event(task_id, user_id, "task.pinned", payload={"pinned": pinned})
        return self.get_item(user_id, task_id)

    def archive_task(self, user_id: str, task_id: str) -> dict | None:
        now = _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET status = 'archived', archived_at = %s, updated_at = %s
                WHERE id = %s AND user_id = %s
                RETURNING id
                """,
                (now, now, task_id, user_id),
            )
            if cur.fetchone() is None:
                return None
        self._record_event(task_id, user_id, "task.archived", status="archived")
        return self.get_item(user_id, task_id)

    def upsert_backlog_item(
        self,
        *,
        item_id: str,
        user_id: str,
        kind: str,
        executor: str,
        title: str,
        description: str,
        task_id: str | None = None,
        status: str = "proposed",
        priority: str = "medium",
        payload: dict | None = None,
        verification_summary: str | None = None,
        last_error: str | None = None,
    ) -> dict:
        now = _utc_iso()
        normalized_priority = priority if priority in _BACKLOG_PRIORITY_ORDER else "medium"
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                INSERT INTO {self.schema}.backlog_items (
                    id, user_id, task_id, kind, executor, status, priority, title, description,
                    payload, created_at, updated_at, verification_summary, last_error
                )
                VALUES (
                    %(id)s, %(user_id)s, %(task_id)s, %(kind)s, %(executor)s, %(status)s,
                    %(priority)s, %(title)s, %(description)s, %(payload)s::jsonb, %(now)s,
                    %(now)s, %(verification_summary)s, %(last_error)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    task_id = EXCLUDED.task_id,
                    kind = EXCLUDED.kind,
                    executor = EXCLUDED.executor,
                    status = CASE
                        WHEN {self.schema}.backlog_items.status IN ('verified', 'rejected', 'in_progress')
                            THEN {self.schema}.backlog_items.status
                        ELSE EXCLUDED.status
                    END,
                    priority = EXCLUDED.priority,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at,
                    verification_summary = COALESCE(EXCLUDED.verification_summary, {self.schema}.backlog_items.verification_summary),
                    last_error = COALESCE(EXCLUDED.last_error, {self.schema}.backlog_items.last_error)
                RETURNING *
                """,
                {
                    "id": item_id,
                    "user_id": user_id,
                    "task_id": task_id,
                    "kind": kind,
                    "executor": executor,
                    "status": status,
                    "priority": normalized_priority,
                    "title": title,
                    "description": description,
                    "payload": json.dumps(payload or {}),
                    "now": now,
                    "verification_summary": verification_summary,
                    "last_error": last_error,
                },
            )
            row = dict(cur.fetchone())
        AuditLogger(self.conn).append(
            "backlog.upserted",
            task_id=task_id,
            user_id=user_id,
            payload={"backlog_id": item_id, "kind": kind, "executor": executor, "status": status},
        )
        return row

    def enqueue_request_verify(self, task: dict) -> dict | None:
        task_id = str(task.get("id") or "").strip()
        user_id = str(task.get("user_id") or "").strip()
        if not task_id or not user_id:
            return None
        status = str(task.get("status") or "")
        if status not in {"verified", "completed_unverified", "done"}:
            return None
        verification = task.get("verification") if isinstance(task.get("verification"), dict) else {}
        verified = bool(task.get("outcome_verified")) or bool(verification.get("verified"))
        backlog_status = "verified" if verified else "proposed"
        summary = str(verification.get("summary") or task.get("result_summary") or "")[:500]
        return self.upsert_backlog_item(
            item_id=f"request_verify:{task_id}",
            user_id=user_id,
            task_id=task_id,
            kind="request_verify",
            executor="request_verify.apply",
            status=backlog_status,
            priority="high" if not verified else "medium",
            title=f"Verify request outcome: {task.get('title') or task_id}",
            description="Confirm that the user-visible outcome matches the original request.",
            payload={
                "task_id": task_id,
                "task_status": status,
                "task_type": task.get("task_type") or "",
                "expected_observable_outcome": verification.get("expected_observable_outcome") or "",
                "verification": verification,
            },
            verification_summary=summary if verified else None,
            last_error=None if verified else summary,
        )

    def claim_backlog_item(self, executor: str) -> dict | None:
        now = _utc_iso()
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                SELECT *
                FROM {self.schema}.backlog_items
                WHERE status = 'proposed' AND executor = %s
                ORDER BY
                    CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                    created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                (executor,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cur.execute(
                f"""
                UPDATE {self.schema}.backlog_items
                SET status = 'in_progress', claimed_at = %s, updated_at = %s, last_error = NULL
                WHERE id = %s
                RETURNING *
                """,
                (now, now, row["id"]),
            )
            claimed = dict(cur.fetchone())
        AuditLogger(self.conn).append(
            "backlog.claimed",
            task_id=claimed.get("task_id"),
            user_id=claimed.get("user_id"),
            payload={"backlog_id": claimed["id"], "executor": executor},
        )
        return claimed

    def finish_backlog_item(
        self,
        item_id: str,
        *,
        success: bool,
        verification_summary: str = "",
        last_error: str = "",
    ) -> bool:
        now = _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.backlog_items
                SET status = %s,
                    completed_at = %s,
                    updated_at = %s,
                    verification_summary = %s,
                    last_error = %s
                WHERE id = %s
                RETURNING task_id, user_id
                """,
                (
                    "verified" if success else "rejected",
                    now,
                    now,
                    verification_summary,
                    "" if success else last_error,
                    item_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                return False
        AuditLogger(self.conn).append(
            "backlog.finished",
            task_id=row[0],
            user_id=row[1],
            payload={"backlog_id": item_id, "success": success},
        )
        return True

    def list_backlog_items(self, user_id: str, *, status: str | None = None, limit: int = 100) -> list[dict]:
        where = "user_id = %s"
        params: list[Any] = [user_id]
        if status:
            where += " AND status = %s"
            params.append(status)
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                SELECT *
                FROM {self.schema}.backlog_items
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                params + [limit],
            )
            return [dict(row) for row in cur.fetchall()]

    def upsert_bridge_item(self, user_id: str, item: dict) -> None:
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            return
        now = _utc_iso()
        messages = item.get("messages") if isinstance(item.get("messages"), list) else []
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        incoming_created_at = item.get("created_at") or item.get("updated_at") or now
        incoming_updated_at = item.get("updated_at") or item.get("created_at") or now
        status = normalize_task_status(item.get("status")) or "queued"
        item_type = item.get("type") or "request"
        origin = item.get("origin") or "agent"
        if _is_human_review_draft(item_id, item):
            item_type = "discussion"
            origin = "agent"
            if status in {"queued", "dispatched", "running", "working"}:
                status = "needs-input"
        elif item_type == "feed" and origin == "agent" and status == "queued":
            status = "done"
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT updated_at, status, origin FROM {self.schema}.tasks WHERE id = %s AND user_id = %s",
                (item_id, user_id),
            )
            existing = cur.fetchone()
            if (
                existing
                and existing[2] == "user"
                and origin == "agent"
                and status in {"queued", "dispatched", "running", "working"}
            ):
                origin = "user"
            should_update_task = not (existing and _is_newer_iso(existing[0], incoming_updated_at))
            if should_update_task:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.tasks (
                        id, user_id, type, title, status, origin, quick, pinned, parent_id,
                        tags, created_at, updated_at, error_code, error_message, retryable, result_path
                    )
                    VALUES (
                        %(id)s, %(user_id)s, %(type)s, %(title)s, %(status)s, %(origin)s, %(quick)s,
                        %(pinned)s, %(parent_id)s, %(tags)s::jsonb, %(created_at)s, %(updated_at)s,
                        %(error_code)s, %(error_message)s, %(retryable)s, %(result_path)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        user_id = EXCLUDED.user_id,
                        type = EXCLUDED.type,
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        origin = EXCLUDED.origin,
                        quick = EXCLUDED.quick,
                        pinned = EXCLUDED.pinned,
                        parent_id = EXCLUDED.parent_id,
                        tags = EXCLUDED.tags,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at,
                        error_code = EXCLUDED.error_code,
                        error_message = EXCLUDED.error_message,
                        retryable = EXCLUDED.retryable,
                        result_path = EXCLUDED.result_path
                    """,
                    {
                        "id": item_id,
                        "user_id": user_id,
                        "type": item_type,
                        "title": item.get("title") or item_id,
                        "status": status,
                        "origin": origin,
                        "quick": bool(item.get("quick")),
                        "pinned": bool(item.get("pinned")),
                        "parent_id": item.get("parent_id"),
                        "tags": json.dumps(_as_json_list(item.get("tags"))),
                        "created_at": incoming_created_at,
                        "updated_at": incoming_updated_at,
                        "error_code": error.get("code"),
                        "error_message": error.get("message"),
                        "retryable": bool(error.get("retryable")),
                        "result_path": item.get("result_path"),
                    },
                )
            incoming_message_ids = [
                str(msg.get("id") or f"{item_id}_{idx}") for idx, msg in enumerate(messages) if isinstance(msg, dict)
            ]
            if should_update_task and item_type == "feed" and origin == "agent" and incoming_message_ids:
                cur.execute(
                    f"""
                    DELETE FROM {self.schema}.messages
                    WHERE task_id = %s
                      AND user_id = %s
                      AND sender <> %s
                      AND NOT (id = ANY(%s))
                    """,
                    (item_id, user_id, user_id, incoming_message_ids),
                )
            for idx, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                msg_id = str(msg.get("id") or f"{item_id}_{idx}")
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.messages (
                        id, task_id, user_id, sender, kind, content, image_path, created_at
                    )
                    VALUES (
                        %(id)s, %(task_id)s, %(user_id)s, %(sender)s, %(kind)s, %(content)s,
                        %(image_path)s, %(created_at)s
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        task_id = EXCLUDED.task_id,
                        user_id = EXCLUDED.user_id,
                        sender = EXCLUDED.sender,
                        kind = EXCLUDED.kind,
                        content = EXCLUDED.content,
                        image_path = EXCLUDED.image_path,
                        created_at = EXCLUDED.created_at
                    """,
                    {
                        "id": msg_id,
                        "task_id": item_id,
                        "user_id": user_id,
                        "sender": msg.get("sender") or msg.get("role") or "agent",
                        "kind": msg.get("kind") or "text",
                        "content": msg.get("content") or "",
                        "image_path": msg.get("image_path"),
                        "created_at": msg.get("timestamp") or item.get("updated_at") or now,
                    },
                )

    def overlay_task_record(self, rec: dict) -> None:
        task_id = str(rec.get("task_id") or "").strip()
        if not task_id:
            return
        now = _utc_iso()
        status = normalize_task_status(rec.get("status")) or "queued"
        if _is_human_review_draft(task_id) and status in {"queued", "dispatched", "running", "working"}:
            status = "needs-input"
        failed = status in ("failed", "timeout", "blocked")
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self.schema}.tasks (
                    id, user_id, type, title, status, origin, tags, created_at, updated_at,
                    started_at, completed_at, worker_pid, workspace, workflow_id, attempt_count,
                    max_attempts, failure_class, error_code, error_message, retryable, result_summary,
                    task_type, verification, outcome_verified, verification_method
                )
                VALUES (
                    %(id)s, %(user_id)s, 'request', %(title)s, %(status)s, 'user',
                    %(tags)s::jsonb, %(created_at)s, %(updated_at)s, %(started_at)s, %(completed_at)s,
                    %(worker_pid)s, %(workspace)s, %(workflow_id)s, %(attempt_count)s,
                    %(max_attempts)s, %(failure_class)s, %(error_code)s, %(error_message)s,
                    %(retryable)s, %(summary)s, %(task_type)s, %(verification)s::jsonb,
                    %(outcome_verified)s, %(verification_method)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    user_id = COALESCE(EXCLUDED.user_id, {self.schema}.tasks.user_id),
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    started_at = COALESCE(EXCLUDED.started_at, {self.schema}.tasks.started_at),
                    completed_at = COALESCE(EXCLUDED.completed_at, {self.schema}.tasks.completed_at),
                    worker_pid = EXCLUDED.worker_pid,
                    workspace = COALESCE(EXCLUDED.workspace, {self.schema}.tasks.workspace),
                    workflow_id = COALESCE(EXCLUDED.workflow_id, {self.schema}.tasks.workflow_id),
                    attempt_count = EXCLUDED.attempt_count,
                    max_attempts = EXCLUDED.max_attempts,
                    failure_class = COALESCE(EXCLUDED.failure_class, {self.schema}.tasks.failure_class),
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    retryable = EXCLUDED.retryable,
                    result_summary = COALESCE(EXCLUDED.result_summary, {self.schema}.tasks.result_summary),
                    task_type = COALESCE(EXCLUDED.task_type, {self.schema}.tasks.task_type),
                    verification = COALESCE(EXCLUDED.verification, {self.schema}.tasks.verification),
                    outcome_verified = EXCLUDED.outcome_verified,
                    verification_method = COALESCE(EXCLUDED.verification_method, {self.schema}.tasks.verification_method)
                """,
                {
                    "id": task_id,
                    "user_id": rec.get("user_id") or "ang",
                    "title": rec.get("content_preview") or task_id,
                    "status": status,
                    "tags": json.dumps(_as_json_list(rec.get("tags"))),
                    "created_at": rec.get("started_at") or now,
                    "updated_at": rec.get("completed_at") or now,
                    "started_at": rec.get("started_at"),
                    "completed_at": rec.get("completed_at"),
                    "worker_pid": rec.get("pid"),
                    "workspace": rec.get("workspace"),
                    "workflow_id": rec.get("workflow_id") or rec.get("thread_id") or task_id,
                    "attempt_count": int(rec.get("attempt_count") or 0),
                    "max_attempts": int(rec.get("max_attempts") or 2),
                    "failure_class": rec.get("failure_class") or None,
                    "error_code": status if failed else None,
                    "error_message": rec.get("summary") if failed else None,
                    "retryable": failed,
                    "summary": rec.get("summary") or None,
                    "task_type": rec.get("task_type") or None,
                    "verification": (
                        json.dumps(rec.get("verification")) if isinstance(rec.get("verification"), dict) else None
                    ),
                    "outcome_verified": bool(rec.get("outcome_verified", False)),
                    "verification_method": rec.get("verification_method") or None,
                },
            )
            status_card = _worker_status_card(rec, status)
            if status_card:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.messages (
                        id, task_id, user_id, sender, kind, content, image_path, created_at
                    )
                    VALUES (%s, %s, %s, 'agent', 'status_card', %s, NULL, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        task_id = EXCLUDED.task_id,
                        user_id = EXCLUDED.user_id,
                        sender = EXCLUDED.sender,
                        kind = EXCLUDED.kind,
                        content = EXCLUDED.content,
                        image_path = EXCLUDED.image_path,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        f"{task_id}_status",
                        task_id,
                        rec.get("user_id") or "ang",
                        json.dumps(status_card, ensure_ascii=False),
                        now,
                    ),
                )
            elif status in {
                "done",
                "verified",
                "completed_unverified",
                "failed",
                "timeout",
                "blocked",
                "needs-input",
            }:
                cur.execute(
                    f"""
                    DELETE FROM {self.schema}.messages
                    WHERE task_id = %s
                      AND user_id = %s
                      AND sender = 'agent'
                      AND kind = 'status_card'
                    """,
                    (task_id, rec.get("user_id") or "ang"),
                )

    def list_items(
        self,
        user_id: str,
        *,
        include_archived: bool = False,
        limit: int = 200,
        messages_per_item: int | None = 20,
    ) -> list[dict]:
        where = "user_id = %s"
        params: list[Any] = [user_id]
        if not include_archived:
            where += " AND status <> 'archived'"
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                SELECT * FROM {self.schema}.tasks
                WHERE {where}
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                params + [limit],
            )
            tasks = list(cur.fetchall())
            if not tasks:
                return []
            task_ids = [t["id"] for t in tasks]
            cur.execute(
                f"""
                SELECT * FROM {self.schema}.messages
                WHERE task_id = ANY(%s)
                ORDER BY created_at ASC
                """,
                (task_ids,),
            )
            messages_by_task: dict[str, list[dict]] = {}
            for msg in cur.fetchall():
                messages_by_task.setdefault(msg["task_id"], []).append(dict(msg))
            if messages_per_item is not None:
                per_item = max(1, int(messages_per_item))
                for task_id, messages in list(messages_by_task.items()):
                    if len(messages) > per_item:
                        messages_by_task[task_id] = messages[-per_item:]
            return [item_from_rows(dict(task), messages_by_task.get(task["id"], [])) for task in tasks]

    def list_dispatchable_tasks(self, user_id: str | None = None, *, limit: int = 20) -> list[dict]:
        where = "status = 'queued' AND origin = 'user' AND id NOT LIKE 'x_reply_%%'"
        params: list[Any] = []
        if user_id:
            where += " AND user_id = %s"
            params.append(user_id)
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                SELECT * FROM {self.schema}.tasks
                WHERE {where}
                ORDER BY created_at ASC
                LIMIT %s
                """,
                params + [limit],
            )
            tasks = [dict(row) for row in cur.fetchall()]
            if not tasks:
                return []
            task_ids = [t["id"] for t in tasks]
            cur.execute(
                f"""
                SELECT * FROM {self.schema}.messages
                WHERE task_id = ANY(%s)
                ORDER BY created_at ASC
                """,
                (task_ids,),
            )
            messages_by_task: dict[str, list[dict]] = {}
            for msg in cur.fetchall():
                messages_by_task.setdefault(msg["task_id"], []).append(dict(msg))
            items = []
            for task in tasks:
                item = item_from_rows(task, messages_by_task.get(task["id"], []))
                item["user_id"] = task.get("user_id")
                items.append(item)
            return items

    def claim_task_for_dispatch(self, user_id: str, task_id: str) -> bool:
        """Atomically claim a queued task before spawning a worker.

        The worker mirror will update pid/workspace immediately after spawn.
        This pre-claim closes the overlap window where two launchd cycles can
        list the same queued task and both dispatch it.
        """
        now = _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET status = 'dispatched',
                    updated_at = %s,
                    started_at = COALESCE(started_at, %s),
                    heartbeat_at = %s,
                    attempt_count = GREATEST(attempt_count, 1)
                WHERE id = %s
                  AND user_id = %s
                  AND status = 'queued'
                  AND origin = 'user'
                RETURNING id
                """,
                (now, now, now, task_id, user_id),
            )
            claimed = cur.fetchone() is not None
        if claimed:
            self._record_event(task_id, user_id, "task.dispatch_claimed", status="dispatched")
        return claimed

    def release_dispatch_claim(self, user_id: str, task_id: str, *, reason: str) -> None:
        """Return a pre-claimed task to queued when worker spawn does not happen."""
        now = _utc_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {self.schema}.tasks
                SET status = 'queued',
                    updated_at = %s,
                    started_at = NULL,
                    heartbeat_at = NULL,
                    error_message = %s
                WHERE id = %s
                  AND user_id = %s
                  AND status = 'dispatched'
                  AND worker_pid IS NULL
                """,
                (now, reason, task_id, user_id),
            )
        self._record_event(
            task_id, user_id, "task.dispatch_claim_released", status="queued", payload={"reason": reason}
        )

    def get_item(self, user_id: str, task_id: str, messages_per_item: int | None = None) -> dict | None:
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"SELECT * FROM {self.schema}.tasks WHERE user_id = %s AND id = %s",
                (user_id, task_id),
            )
            task = cur.fetchone()
            if not task:
                return None
            cur.execute(
                f"SELECT * FROM {self.schema}.messages WHERE task_id = %s ORDER BY created_at ASC",
                (task_id,),
            )
            messages = [dict(msg) for msg in cur.fetchall()]
            if messages_per_item is not None and len(messages) > messages_per_item:
                messages = messages[-max(1, int(messages_per_item)) :]
            return item_from_rows(dict(task), messages)

    def last_event_id(self, user_id: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {self.schema}.task_events WHERE user_id = %s", (user_id,))
            return int(cur.fetchone()[0] or 0)

    def list_events_since(self, user_id: str, last_event_id: int, *, limit: int = 100) -> list[dict]:
        with dict_cursor(self.conn) as cur:
            cur.execute(
                f"""
                SELECT id, task_id, user_id, event_type, status, payload, created_at
                FROM {self.schema}.task_events
                WHERE user_id = %s AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (user_id, last_event_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]


def sync_user_from_legacy(user_id: str, *, user_dir: Path, task_status_file: Path) -> None:
    """Project legacy bridge/task files into Postgres.

    This function is read-only with respect to the legacy files.
    """
    with transaction() as conn:
        repo = ControlRepository(conn)
        items_dir = user_dir / "items"
        if items_dir.exists():
            for path in sorted(items_dir.glob("*.json")):
                item = _read_json(path)
                if isinstance(item, dict):
                    repo.upsert_bridge_item(user_id, item)
        records = _read_json(task_status_file)
        if isinstance(records, list):
            for rec in records:
                if isinstance(rec, dict) and (rec.get("user_id") or "ang") == user_id:
                    repo.overlay_task_record(rec)
