from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

from config import CONTROL_DB_SCHEMA
from db.connection import connect, dict_cursor


_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def schema_name() -> str:
    schema = CONTROL_DB_SCHEMA or "mira_control"
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"Invalid Postgres schema name: {schema!r}")
    return schema


@contextmanager
def transaction() -> Iterator:
    conn = connect()
    try:
        ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(conn) -> None:
    schema = schema_name()
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.control_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                origin TEXT NOT NULL,
                quick BOOLEAN NOT NULL DEFAULT FALSE,
                pinned BOOLEAN NOT NULL DEFAULT FALSE,
                parent_id TEXT,
                tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                queued_at TEXT,
                started_at TEXT,
                heartbeat_at TEXT,
                completed_at TEXT,
                worker_pid INTEGER,
                workspace TEXT,
                workflow_id TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 2,
                failure_class TEXT,
                error_code TEXT,
                error_message TEXT,
                retryable BOOLEAN NOT NULL DEFAULT FALSE,
                result_path TEXT,
                result_summary TEXT,
                task_type TEXT,
                verification JSONB,
                outcome_verified BOOLEAN NOT NULL DEFAULT FALSE,
                verification_method TEXT,
                archived_at TEXT
            )
            """
        )
        cur.execute(f"ALTER TABLE {schema}.tasks ADD COLUMN IF NOT EXISTS task_type TEXT")
        cur.execute(f"ALTER TABLE {schema}.tasks ADD COLUMN IF NOT EXISTS verification JSONB")
        cur.execute(
            f"ALTER TABLE {schema}.tasks ADD COLUMN IF NOT EXISTS outcome_verified BOOLEAN NOT NULL DEFAULT FALSE"
        )
        cur.execute(f"ALTER TABLE {schema}.tasks ADD COLUMN IF NOT EXISTS verification_method TEXT")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_user_updated ON {schema}.tasks(user_id, updated_at DESC)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON {schema}.tasks(user_id, status)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_worker_pid ON {schema}.tasks(worker_pid)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat ON {schema}.tasks(status, heartbeat_at)")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_tasks_outcome_verified ON {schema}.tasks(user_id, outcome_verified, updated_at DESC)"
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.threads (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_threads_user_updated ON {schema}.threads(user_id, updated_at DESC)"
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.messages (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES {schema}.tasks(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                sender TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'text',
                content TEXT NOT NULL,
                image_path TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_messages_task_created ON {schema}.messages(task_id, created_at)")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.audit_events (
                event_id BIGSERIAL PRIMARY KEY,
                ts TEXT NOT NULL,
                type TEXT NOT NULL,
                task_id TEXT,
                workflow_id TEXT,
                user_id TEXT,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                schema_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON {schema}.audit_events(ts)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_audit_events_task_id ON {schema}.audit_events(task_id, event_id)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_audit_events_user_id ON {schema}.audit_events(user_id, event_id)")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.backlog_items (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                task_id TEXT,
                kind TEXT NOT NULL,
                executor TEXT NOT NULL,
                status TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                claimed_at TEXT,
                completed_at TEXT,
                verification_summary TEXT,
                last_error TEXT
            )
            """
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_backlog_user_status ON {schema}.backlog_items(user_id, status, updated_at DESC)"
        )
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS idx_backlog_executor_status ON {schema}.backlog_items(executor, status, priority, created_at)"
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_backlog_task_kind ON {schema}.backlog_items(task_id, kind)")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.task_events (
                id BIGSERIAL PRIMARY KEY,
                task_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_events_user_id ON {schema}.task_events(user_id, id)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_events_task_id ON {schema}.task_events(task_id, id)")
