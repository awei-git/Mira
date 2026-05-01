from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

from config import CONTROL_DATABASE_URL


def connect():
    """Open a Postgres connection for Mira's canonical control database."""
    conn = psycopg2.connect(CONTROL_DATABASE_URL)
    conn.autocommit = False
    return conn


@contextmanager
def transaction() -> Iterator[psycopg2.extensions.connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
