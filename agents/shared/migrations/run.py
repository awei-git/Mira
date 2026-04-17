"""Idempotent database migration runner for Mira persistent memory.

Usage:
    python -m agents.shared.migrations.run          # from Mira/
    python agents/shared/migrations/run.py           # direct

Reads DATABASE_URL from config.py, tracks applied migrations in schema_migrations table.
"""

import logging
import sys
from pathlib import Path

log = logging.getLogger("mira.migrations")

MIGRATIONS_DIR = Path(__file__).parent


def _get_conn():
    """Get a psycopg2 connection using config DATABASE_URL."""
    import psycopg2

    # Add shared to path for config import
    shared_dir = MIGRATIONS_DIR.parent
    if str(shared_dir) not in sys.path:
        sys.path.insert(0, str(shared_dir))

    from config import DATABASE_URL

    return psycopg2.connect(DATABASE_URL)


def run_migrations():
    """Apply all pending SQL migrations in order."""
    conn = _get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    # Ensure tracking table exists
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(100) PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT now()
        )
    """
    )

    # Get already-applied migrations
    cur.execute("SELECT version FROM schema_migrations ORDER BY version")
    applied = {row[0] for row in cur.fetchall()}

    # Find and sort migration files
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    applied_count = 0
    for sql_file in sql_files:
        version = sql_file.stem  # e.g. "001_memory_tables"
        if version in applied:
            continue

        log.info("Applying migration: %s", version)
        sql = sql_file.read_text("utf-8")

        try:
            cur.execute(sql)
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_count += 1
            log.info("Applied: %s", version)
        except Exception as e:
            log.error("Migration %s failed: %s", version, e)
            raise

    cur.close()
    conn.close()

    if applied_count:
        log.info("Applied %d migration(s)", applied_count)
    else:
        log.info("All migrations up to date")

    return applied_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_migrations()
