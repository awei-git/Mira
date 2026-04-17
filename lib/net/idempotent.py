"""Idempotent-retry cache — key → response, TTL-bounded.

External write operations (Substack publish, Notes creation, RSS
update, podcast upload) are unsafe to blindly retry: same logical
publish issued twice could double-post.

Pattern:
    from net import cached_call

    def _do_publish(...):
        return substack_api.publish(...)

    result = cached_call(
        key=f"substack_publish:{task_id}:{stage}",
        fn=_do_publish,
        ttl_seconds=7 * 86400,
    )

Second call with the same key within TTL returns the cached value
without invoking `fn`. Storage: a single SQLite file at
`data/net/idempotency.db` — atomic writes, process-safe.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, TypeVar

from config import DATA_DIR

log = logging.getLogger("mira.net.idempotent")

DB_FILE = DATA_DIR / "net" / "idempotency.db"

T = TypeVar("T")

_write_lock = threading.Lock()  # sqlite on network fs can flake under concurrency


@contextmanager
def _connect(path: Path | None = None):
    target = path or DB_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idempotency (
            key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )


def cached_call(
    *,
    key: str,
    fn: Callable[[], T],
    ttl_seconds: float = 7 * 86400,
    path: Path | None = None,
) -> T:
    """Return the cached value for `key` if valid, else invoke `fn` once.

    The return of `fn` must be JSON-serializable (dict / list / str /
    int / float / bool / None). For richer types, serialize in the
    caller.

    Thread- and process-safe: IO is serialized via a module-level
    lock plus SQLite's own locking.
    """
    now = time.time()
    with _write_lock:
        # Fast path: return cached value if still valid
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
            if row and row[1] > now:
                try:
                    return json.loads(row[0])
                except (json.JSONDecodeError, TypeError):
                    log.warning("idempotent cache payload corrupt for %s — recomputing", key)

    # Slow path outside write-lock so fn() can do network I/O without blocking readers.
    value = fn()

    try:
        payload = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        log.warning("idempotent cache cannot serialize %s result (%s) — returning uncached", key, e)
        return value
    expires = now + ttl_seconds
    with _write_lock:
        with _connect(path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO idempotency (key, payload_json, expires_at) VALUES (?, ?, ?)",
                (key, payload, expires),
            )
    return value


def clear_expired(path: Path | None = None) -> int:
    """Delete rows with expires_at <= now. Returns the count deleted."""
    now = time.time()
    with _write_lock:
        with _connect(path) as conn:
            cur = conn.execute("DELETE FROM idempotency WHERE expires_at <= ?", (now,))
            return cur.rowcount or 0


def cached_value(key: str, *, path: Path | None = None) -> Any:
    """Return the cached payload for `key` if still valid, else None.

    Non-mutating; used for inspection / debug.
    """
    now = time.time()
    with _write_lock:
        with _connect(path) as conn:
            row = conn.execute(
                "SELECT payload_json, expires_at FROM idempotency WHERE key = ?",
                (key,),
            ).fetchone()
    if not row or row[1] <= now:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None
