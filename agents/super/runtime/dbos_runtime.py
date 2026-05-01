from __future__ import annotations

import logging
from typing import Any
from threading import Lock

try:
    from dbos import DBOS
except ModuleNotFoundError:  # pragma: no cover - exercised through runtime error path
    DBOS = None

from config import (
    CONTROL_DATABASE_URL,
    DBOS_APPLICATION_VERSION,
    DBOS_RUN_ADMIN_SERVER,
    DBOS_SYSTEM_SCHEMA,
)


log = logging.getLogger("mira.dbos")

_dbos: Any | None = None
_lock = Lock()


def _config() -> dict[str, Any]:
    return {
        "name": "mira",
        "system_database_url": CONTROL_DATABASE_URL,
        "application_database_url": CONTROL_DATABASE_URL,
        "dbos_system_schema": DBOS_SYSTEM_SCHEMA,
        "application_version": DBOS_APPLICATION_VERSION,
        "run_admin_server": DBOS_RUN_ADMIN_SERVER,
    }


def get_dbos() -> Any:
    """Return the process-wide DBOS instance, launched on first use.

    DBOS defaults to a separate `<database>_dbos_sys` database. Mira runs as a
    single-user Mac service, so V2 keeps DBOS system tables in the same
    Postgres database under a dedicated schema. That avoids an extra database
    bootstrap step and matches the local Postgres control-plane plan.
    """
    global _dbos
    if _dbos is not None:
        return _dbos
    with _lock:
        if _dbos is not None:
            return _dbos
        if DBOS is None:
            raise RuntimeError(
                "DBOS dependency is not installed; run the project dependency install before DBOS workflows"
            )
        instance = DBOS(config=_config())
        instance.launch()
        _dbos = instance
        log.info("DBOS runtime launched schema=%s version=%s", DBOS_SYSTEM_SCHEMA, DBOS_APPLICATION_VERSION)
        return instance


def destroy_dbos() -> None:
    global _dbos
    with _lock:
        if _dbos is not None:
            _dbos.destroy()
            _dbos = None
