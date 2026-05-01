from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from config import CONTROL_DATABASE_URL, CONTROL_DB_SCHEMA


_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def schema_name() -> str:
    schema = CONTROL_DB_SCHEMA or "mira_control"
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"Invalid Postgres schema name: {schema!r}")
    return schema


def connect():
    conn = psycopg2.connect(CONTROL_DATABASE_URL)
    conn.autocommit = False
    return conn


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.connection]:
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
                archived_at TEXT
            )
            """
        )
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_user_updated ON {schema}.tasks(user_id, updated_at DESC)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON {schema}.tasks(user_id, status)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_worker_pid ON {schema}.tasks(worker_pid)")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat ON {schema}.tasks(status, heartbeat_at)")
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


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
