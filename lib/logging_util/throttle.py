"""Throttled log helpers — dedup identical messages inside a window."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

_lock = threading.Lock()


@dataclass
class _Entry:
    last_emitted: float
    suppressed: int


_cache: dict[tuple[str, str], _Entry] = {}


def _emit(log: logging.Logger, level: int, msg: str, args: tuple, key: str, interval: float) -> None:
    now = time.monotonic()
    composite = (log.name, key)
    with _lock:
        entry = _cache.get(composite)
        if entry is None:
            _cache[composite] = _Entry(last_emitted=now, suppressed=0)
            log.log(level, msg, *args)
            return
        if now - entry.last_emitted >= interval:
            if entry.suppressed > 0:
                log.log(level, f"(×{entry.suppressed} suppressed since last) " + msg, *args)
            else:
                log.log(level, msg, *args)
            entry.last_emitted = now
            entry.suppressed = 0
            return
        entry.suppressed += 1


def throttled_warning(
    log: logging.Logger,
    msg: str,
    *args,
    key: str,
    interval_seconds: float = 3600.0,
) -> None:
    """Emit a WARNING at most once per `interval_seconds` per (logger, key)."""
    _emit(log, logging.WARNING, msg, args, key, interval_seconds)


def throttled_info(
    log: logging.Logger,
    msg: str,
    *args,
    key: str,
    interval_seconds: float = 3600.0,
) -> None:
    _emit(log, logging.INFO, msg, args, key, interval_seconds)


def throttled_error(
    log: logging.Logger,
    msg: str,
    *args,
    key: str,
    interval_seconds: float = 3600.0,
) -> None:
    _emit(log, logging.ERROR, msg, args, key, interval_seconds)


def suppressed_stats() -> dict[str, int]:
    """Diagnostic: {key → count} of currently-suppressed messages."""
    with _lock:
        return {f"{logger}:{key}": entry.suppressed for (logger, key), entry in _cache.items()}


def reset() -> None:
    """Test helper — clear all cache entries."""
    with _lock:
        _cache.clear()
