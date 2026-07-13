from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DATA_DIR
from control.db import schema_name


AUDIT_JSONL_PATH = DATA_DIR / "audit" / "events.jsonl"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class AuditEvent:
    type: str
    task_id: str | None = None
    workflow_id: str | None = None
    user_id: str | None = None
    payload: dict[str, Any] | None = None
    schema_version: int = 1
    ts: str = ""
    event_id: str = ""

    def normalized(self) -> dict[str, Any]:
        data = asdict(self)
        data["ts"] = self.ts or _utc_iso()
        data["event_id"] = self.event_id or uuid.uuid4().hex
        data["payload"] = self.payload or {}
        return data


class AuditLogger:
    """Append-only audit log with Postgres query path and JSONL cold storage."""

    def __init__(self, conn, *, jsonl_path: Path = AUDIT_JSONL_PATH):
        self.conn = conn
        self.jsonl_path = jsonl_path

    def append(
        self,
        event_type: str,
        *,
        task_id: str | None = None,
        workflow_id: str | None = None,
        user_id: str | None = None,
        payload: dict[str, Any] | None = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        event = AuditEvent(
            type=event_type,
            task_id=task_id,
            workflow_id=workflow_id,
            user_id=user_id,
            payload=payload or {},
            schema_version=schema_version,
        ).normalized()
        schema = schema_name()
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema}.audit_events (
                    ts, type, task_id, workflow_id, user_id, payload, schema_version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING event_id
                """,
                (
                    event["ts"],
                    event["type"],
                    event["task_id"],
                    event["workflow_id"],
                    event["user_id"],
                    json.dumps(event["payload"]),
                    event["schema_version"],
                ),
            )
            row = cur.fetchone()
            if row:
                event["event_id"] = str(row[0])
        self._append_jsonl(event)
        return event

    def _append_jsonl(self, event: dict[str, Any]) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
