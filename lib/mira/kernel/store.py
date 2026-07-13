"""Memory Kernel persistence backends."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol

from .schema import MemoryKernel


class KernelStore(Protocol):
    def load(self) -> MemoryKernel: ...

    def save(self, kernel: MemoryKernel) -> None: ...


class JsonKernelStore:
    """Small local kernel store used for development and tests."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> MemoryKernel:
        if not self.path.exists():
            return MemoryKernel()
        return MemoryKernel.from_dict(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self, kernel: MemoryKernel) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(kernel.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class SQLiteKernelStore:
    """SQLite backend for the kernel document.

    Postgres can mirror this contract later; SQLite is the reliable local
    fallback the architecture requires.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kernel_state " "(id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT NOT NULL)"
            )

    def load(self) -> MemoryKernel:
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT body FROM kernel_state WHERE id = 1").fetchone()
        if row is None:
            return MemoryKernel()
        return MemoryKernel.from_dict(json.loads(row[0]))

    def save(self, kernel: MemoryKernel) -> None:
        body = json.dumps(kernel.to_dict(), sort_keys=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO kernel_state (id, body) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET body = excluded.body",
                (body,),
            )


class PostgresKernelStore:
    """Postgres kernel document store.

    The table is intentionally a single JSONB document for Phase 1. The ledger
    remains append-only; richer relational projections can be added without
    changing the KernelStore protocol.
    """

    def __init__(self, dsn: str, schema: str = "mira_v3"):
        self.dsn = dsn
        self.schema = schema
        self._init()

    def _connect(self):
        import psycopg2

        return psycopg2.connect(self.dsn)

    def _init(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {self.schema}.kernel_state "
                    "(id INTEGER PRIMARY KEY CHECK (id = 1), body JSONB NOT NULL)"
                )

    def load(self) -> MemoryKernel:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT body FROM {self.schema}.kernel_state WHERE id = 1")
                row = cur.fetchone()
        if row is None:
            return MemoryKernel()
        return MemoryKernel.from_dict(row[0])

    def save(self, kernel: MemoryKernel) -> None:
        body = json.dumps(kernel.to_dict(), sort_keys=True)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self.schema}.kernel_state (id, body) VALUES (1, %s::jsonb) "
                    "ON CONFLICT(id) DO UPDATE SET body = excluded.body",
                    (body,),
                )


class DualKernelStore:
    """Primary store with a fallback mirror.

    Writes go to both stores. Reads prefer primary and fall back if primary is
    unavailable. This gives the engine the dual-backend shape from the spec
    without coupling local tests to Postgres.
    """

    def __init__(self, primary: KernelStore, fallback: KernelStore):
        self.primary = primary
        self.fallback = fallback

    def load(self) -> MemoryKernel:
        try:
            return self.primary.load()
        except Exception:
            return self.fallback.load()

    def save(self, kernel: MemoryKernel) -> None:
        primary_error: Exception | None = None
        try:
            self.primary.save(kernel)
        except Exception as exc:
            primary_error = exc
        self.fallback.save(kernel)
        if primary_error:
            raise primary_error
