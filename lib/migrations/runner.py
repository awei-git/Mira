from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from config import CONTROL_DB_SCHEMA
from db.connection import transaction


_MIGRATIONS_DIR = Path(__file__).resolve().parent
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def schema_name() -> str:
    schema = CONTROL_DB_SCHEMA or "mira_control"
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"Invalid Postgres schema name: {schema!r}")
    return schema


def _migration_files() -> list[Path]:
    return sorted(p for p in _MIGRATIONS_DIR.glob("*.sql") if p.name[:4].isdigit())


def apply_migrations() -> list[str]:
    """Apply pending SQL migrations and return the applied version list."""
    schema = schema_name()
    applied: list[str] = []
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            cur.execute(f"SELECT version FROM {schema}.schema_migrations")
            seen = {row[0] for row in cur.fetchall()}
            for path in _migration_files():
                version = path.stem
                if version in seen:
                    continue
                sql = path.read_text(encoding="utf-8").format(schema=schema)
                cur.execute(sql)
                cur.execute(
                    f"INSERT INTO {schema}.schema_migrations (version, applied_at) VALUES (%s, %s)",
                    (version, _utc_iso()),
                )
                applied.append(version)
    return applied


def main() -> int:
    applied = apply_migrations()
    if applied:
        print("applied: " + ", ".join(applied))
    else:
        print("up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
