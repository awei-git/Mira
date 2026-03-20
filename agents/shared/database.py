"""PostgreSQL connection manager for Mira — localhost only.

Provides a simple connection pool for persistent memory (pgvector)
and any future structured storage needs. All connections stay on 127.0.0.1.
"""
import logging
from contextlib import contextmanager

log = logging.getLogger("mira.database")

_pool = None


def _get_pool():
    """Lazy-init a psycopg2 connection pool."""
    global _pool
    if _pool is not None:
        return _pool

    try:
        import psycopg2
        from psycopg2 import pool as pg_pool
    except ImportError:
        log.error("psycopg2 not installed — run: pip install psycopg2-binary")
        raise

    from config import DATABASE_URL
    _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=4, dsn=DATABASE_URL)
    log.info("PostgreSQL pool created (%s)", DATABASE_URL.split("@")[-1])
    return _pool


@contextmanager
def get_conn():
    """Get a connection from the pool. Auto-returns on exit."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def execute(sql: str, params=None) -> list:
    """Execute SQL and return all rows."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                return cur.fetchall()
            return []


def execute_one(sql: str, params=None):
    """Execute SQL and return first row or None."""
    rows = execute(sql, params)
    return rows[0] if rows else None


def close():
    """Close all connections in the pool."""
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
        log.info("PostgreSQL pool closed")
