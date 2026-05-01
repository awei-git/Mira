from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


LOCK_DISPATCH_LOOP = 1
LOCK_MEMORY_WRITE = 2
LOCK_SELF_EVOLVE_COMMIT = 3
LOCK_BACKUP = 4
LOCK_PUBLISH_DISPATCH = 5

LOCK_IDS = {
    "dispatch_loop": LOCK_DISPATCH_LOOP,
    "memory_write": LOCK_MEMORY_WRITE,
    "self_evolve_commit": LOCK_SELF_EVOLVE_COMMIT,
    "backup": LOCK_BACKUP,
    "publish_dispatch": LOCK_PUBLISH_DISPATCH,
}


class AdvisoryLockTimeout(TimeoutError):
    pass


def _resolve_lock_id(lock_id: int | str) -> int:
    if isinstance(lock_id, str):
        try:
            return LOCK_IDS[lock_id]
        except KeyError as exc:
            raise ValueError(f"unknown advisory lock id: {lock_id}") from exc
    return int(lock_id)


@contextmanager
def advisory_lock(
    lock_id: int | str,
    *,
    conn=None,
    timeout_s: float = 30,
    poll_s: float = 0.25,
) -> Iterator[None]:
    """Acquire a Postgres advisory lock and release it with try/finally discipline."""
    if timeout_s < 0:
        raise ValueError("timeout_s must be >= 0")
    if poll_s <= 0:
        raise ValueError("poll_s must be > 0")

    resolved = _resolve_lock_id(lock_id)
    owns_conn = conn is None
    if owns_conn:
        from db.connection import connect

        owned_conn = connect()
        try:
            with advisory_lock(resolved, conn=owned_conn, timeout_s=timeout_s, poll_s=poll_s):
                yield
            owned_conn.commit()
        except Exception:
            owned_conn.rollback()
            raise
        finally:
            owned_conn.close()
        return

    acquired = False
    deadline = time.monotonic() + timeout_s
    with conn.cursor() as cur:
        while True:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (resolved,))
            row = cur.fetchone()
            acquired = bool(row[0] if row else False)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise AdvisoryLockTimeout(f"timed out acquiring advisory lock {resolved}")
            time.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))

        try:
            yield
        finally:
            if acquired:
                cur.execute("SELECT pg_advisory_unlock(%s)", (resolved,))
